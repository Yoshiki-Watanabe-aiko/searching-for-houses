package main

import (
	"context"
	"flag"
	"fmt"
	"os"

	"github.com/go-rod/rod"

	"house-notifier/internal/config"
	"house-notifier/internal/db"
	"house-notifier/internal/notifier"
	"house-notifier/internal/runner"
	"house-notifier/internal/scraper"
	"house-notifier/internal/scraper/able"
	"house-notifier/internal/scraper/apaman"
	"house-notifier/internal/scraper/athome"
	"house-notifier/internal/scraper/eheya"
	"house-notifier/internal/scraper/goo"
	"house-notifier/internal/scraper/homes"
	"house-notifier/internal/scraper/minimini"
	"house-notifier/internal/scraper/nifty"
	"house-notifier/internal/scraper/smocca"
	"house-notifier/internal/scraper/suumo"
)

func main() {
	fullScan := flag.Bool("full-scan", false, "スクレイプページ数を最大まで拡張")
	checkSold := flag.Bool("check-sold", false, "成約/削除確認モード")
	flag.Parse()

	ctx := context.Background()

	appCfg, err := config.LoadAppConfig()
	if err != nil {
		fmt.Fprintf(os.Stderr, "config error: %v\n", err)
		os.Exit(1)
	}

	pool, err := db.NewPool(ctx, appCfg.DatabaseURL)
	if err != nil {
		fmt.Fprintf(os.Stderr, "db error: %v\n", err)
		os.Exit(1)
	}
	defer pool.Close()

	discord := notifier.NewDiscord()

	searchCfgs, errs := config.LoadSearchConfigs(appCfg.ConfigsDir)
	for _, e := range errs {
		discord.SendError(appCfg.ErrorDiscordWebhook, "YAML load error", e)
		fmt.Fprintf(os.Stderr, "yaml error: %v\n", e)
	}
	if len(searchCfgs) == 0 {
		fmt.Fprintln(os.Stderr, "no search configs found")
		os.Exit(1)
	}

	// Build scraper registry
	// Playwright browser (for restricted sites)
	var browser *rod.Browser
	needsRod := needsRodScraper(searchCfgs)
	if needsRod {
		browser, err = scraper.NewBrowser()
		if err != nil {
			discord.SendError(appCfg.ErrorDiscordWebhook, "browser launch failed", err)
			fmt.Fprintf(os.Stderr, "browser error: %v\n", err)
			os.Exit(1)
		}
		defer browser.Close()
	}

	scrapers := map[string]scraper.Scraper{
		"SUUMO":   suumo.New(),
		"HOMES":   homes.New(),
		"GOO":     goo.New(),
		"ABLE":    able.New(),
		"MINIMINI": minimini.New(),
	}
	if browser != nil {
		scrapers["ATHOME"] = athome.New(browser)
		scrapers["EHEYA"]  = eheya.New(browser)
		scrapers["NIFTY"]  = nifty.New(browser)
		scrapers["APAMAN"] = apaman.New(browser)
		scrapers["SMOCCA"] = smocca.New(browser)
	}

	r := runner.New(pool, discord, scrapers, appCfg.ErrorDiscordWebhook)

	if *checkSold {
		r.CheckSold(ctx, searchCfgs)
		return
	}

	for _, cfg := range searchCfgs {
		r.Run(ctx, cfg, *fullScan, appCfg.FullScanMaxPages)
	}
}

func needsRodScraper(cfgs []*config.SearchConfig) bool {
	rodSites := map[string]bool{"ATHOME": true, "EHEYA": true, "NIFTY": true, "APAMAN": true, "SMOCCA": true}
	for _, cfg := range cfgs {
		for _, sid := range cfg.SiteIDs {
			if rodSites[sid] {
				return true
			}
		}
	}
	return false
}
