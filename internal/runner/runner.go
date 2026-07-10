package runner

import (
	"context"
	"fmt"
	"sort"

	"github.com/jackc/pgx/v5/pgxpool"

	"house-notifier/internal/config"
	"house-notifier/internal/db"
	"house-notifier/internal/model"
	"house-notifier/internal/notifier"
	"house-notifier/internal/scraper"
)

type pendingNotif struct {
	prop      *model.Property
	notifType model.NotificationType
}

type Runner struct {
	pool     *pgxpool.Pool
	discord  *notifier.Discord
	scrapers map[string]scraper.Scraper
	errHook  string
}

func New(pool *pgxpool.Pool, discord *notifier.Discord, scrapers map[string]scraper.Scraper, errWebhook string) *Runner {
	return &Runner{
		pool:     pool,
		discord:  discord,
		scrapers: scrapers,
		errHook:  errWebhook,
	}
}

func (r *Runner) Run(ctx context.Context, cfg *config.SearchConfig, fullScan bool, maxPages int) {
	propTypeID, err := db.GetPropertyTypeID(ctx, r.pool, cfg.PropertyType)
	if err != nil {
		r.reportError(ctx, "", cfg.Name, "property_type lookup failed", err)
		return
	}

	var pending []pendingNotif

	for _, siteCode := range cfg.SiteIDs {
		sc, ok := r.scrapers[siteCode]
		if !ok {
			db.LogWarn(ctx, r.pool, siteCode, cfg.Name, fmt.Sprintf("scraper not registered: %s", siteCode), nil)
			continue
		}

		siteID, err := db.GetSiteID(ctx, r.pool, siteCode)
		if err != nil {
			r.reportError(ctx, siteCode, cfg.Name, "site lookup failed", err)
			continue
		}

		fmt.Printf("[%s] %s: スクレイプ開始\n", cfg.Name, siteCode)
		db.LogInfo(ctx, r.pool, siteCode, cfg.Name, "scraping started", nil)

		resolvedCfg := r.resolveForSite(ctx, cfg, siteCode)
		props, err := sc.Scrape(ctx, resolvedCfg, fullScan, maxPages)
		if err != nil {
			fmt.Printf("[%s] %s: エラー: %v\n", cfg.Name, siteCode, err)
			r.reportError(ctx, siteCode, cfg.Name, "scrape failed", err)
			continue
		}

		fmt.Printf("[%s] %s: %d件取得\n", cfg.Name, siteCode, len(props))
		db.LogInfo(ctx, r.pool, siteCode, cfg.Name,
			fmt.Sprintf("scraped %d properties", len(props)), nil)

		for _, sp := range props {
			action, err := r.upsertAndPlan(ctx, cfg, siteID, propTypeID, sp)
			if err != nil {
				db.LogError(ctx, r.pool, siteCode, cfg.Name,
					fmt.Sprintf("process property %s failed", sp.ExternalID),
					map[string]string{"error": err.Error(), "url": sp.URL})
				continue
			}
			if action != nil {
				pending = append(pending, *action)
			}
		}
	}

	// Send notifications sorted by price ascending so the cheapest properties appear first in Discord.
	sort.SliceStable(pending, func(i, j int) bool {
		pi := pending[i].prop.Price
		pj := pending[j].prop.Price
		if pi == nil {
			return false
		}
		if pj == nil {
			return true
		}
		return *pi < *pj
	})

	for _, a := range pending {
		if err := r.notify(ctx, cfg, a.prop, a.notifType); err != nil {
			db.LogError(ctx, r.pool, a.prop.SiteCode, cfg.Name, "notify failed",
				map[string]string{"error": err.Error()})
		}
	}
}

// upsertAndPlan saves the property to the DB and returns a pending notification if one is warranted,
// without sending it yet. Returns nil when no notification is needed.
func (r *Runner) upsertAndPlan(
	ctx context.Context,
	cfg *config.SearchConfig,
	siteID, propTypeID int,
	sp *model.ScrapedProperty,
) (*pendingNotif, error) {
	result, err := db.UpsertProperty(ctx, r.pool, siteID, propTypeID, sp)
	if err != nil {
		return nil, fmt.Errorf("upsert: %w", err)
	}

	prop, err := db.GetPropertyByID(ctx, r.pool, result.ID)
	if err != nil {
		return nil, fmt.Errorf("get property: %w", err)
	}

	if result.IsNew || result.IsRelisted {
		return &pendingNotif{prop: prop, notifType: model.NotificationNew}, nil
	}

	if result.PriceChanged && result.CurrentPrice != nil && result.PrevPrice != nil {
		var notifType model.NotificationType
		if *result.CurrentPrice > *result.PrevPrice {
			notifType = model.NotificationPriceUp
		} else {
			notifType = model.NotificationPriceDown
		}
		if ok, err := r.shouldNotifyPrice(ctx, cfg.Name, prop.ID, notifType, result.CurrentPrice); err == nil && ok {
			return &pendingNotif{prop: prop, notifType: notifType}, nil
		}
	}

	return nil, nil
}

func (r *Runner) shouldNotifyPrice(
	ctx context.Context,
	patternName string,
	propID int,
	notifType model.NotificationType,
	currentPrice *int64,
) (bool, error) {
	last, err := db.GetLastNotification(ctx, r.pool, propID, patternName, notifType)
	if err != nil {
		return false, err
	}
	if last == nil {
		return true, nil
	}
	if last.PriceAtNotify == nil || currentPrice == nil {
		return true, nil
	}
	return *last.PriceAtNotify != *currentPrice, nil
}

func (r *Runner) notify(ctx context.Context, cfg *config.SearchConfig, prop *model.Property, notifType model.NotificationType) error {
	if err := r.discord.Send(cfg.DiscordWebhook, prop, notifType); err != nil {
		return err
	}
	return db.RecordNotification(ctx, r.pool, prop.ID, cfg.Name, notifType, prop.Price)
}

func (r *Runner) CheckSold(ctx context.Context, patterns []*config.SearchConfig) {
	props, err := db.GetActiveProperties(ctx, r.pool)
	if err != nil {
		r.reportError(ctx, "", "", "get active properties failed", err)
		return
	}

	db.LogInfo(ctx, r.pool, "", "", fmt.Sprintf("check-sold: checking %d active properties", len(props)), nil)

	for _, prop := range props {
		sc, ok := r.scrapers[prop.SiteCode]
		if !ok {
			continue
		}

		sold, err := sc.CheckSold(ctx, prop.URL)
		if err != nil {
			db.LogWarn(ctx, r.pool, prop.SiteCode, "", fmt.Sprintf("check-sold error for %s", prop.URL),
				map[string]string{"error": err.Error()})
			continue
		}

		if !sold {
			continue
		}

		if err := db.UpdatePropertyStatus(ctx, r.pool, prop.ID, model.StatusSold); err != nil {
			db.LogError(ctx, r.pool, prop.SiteCode, "", "update status failed",
				map[string]string{"error": err.Error(), "id": fmt.Sprint(prop.ID)})
			continue
		}

		for _, cfg := range patterns {
			hasSold, err := db.HasSoldNotification(ctx, r.pool, prop.ID, cfg.Name)
			if err != nil || hasSold {
				continue
			}
			for _, sid := range cfg.SiteIDs {
				if sid == prop.SiteCode {
					if err := r.notify(ctx, cfg, prop, model.NotificationSold); err != nil {
						db.LogError(ctx, r.pool, prop.SiteCode, cfg.Name, "notify sold failed",
							map[string]string{"error": err.Error()})
					}
					break
				}
			}
		}
	}
}

func (r *Runner) resolveForSite(ctx context.Context, cfg *config.SearchConfig, siteCode string) *config.SearchConfig {
	if len(cfg.Conditions.Area.Cities) == 0 {
		return cfg
	}
	resolved := db.ResolveCities(ctx, r.pool, cfg.Conditions.Area.Prefectures, cfg.Conditions.Area.Cities, siteCode)
	conditions := cfg.Conditions
	conditions.Area.Cities = resolved
	clone := *cfg
	clone.Conditions = conditions
	return &clone
}

func (r *Runner) reportError(ctx context.Context, siteCode, patternName, msg string, err error) {
	detail := map[string]string{"error": err.Error()}
	db.LogError(ctx, r.pool, siteCode, patternName, msg, detail)
	r.discord.SendError(r.errHook, fmt.Sprintf("[%s/%s] %s", siteCode, patternName, msg), err)
}
