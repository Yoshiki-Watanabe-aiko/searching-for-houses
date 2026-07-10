package scraper

import (
	"context"

	"house-notifier/internal/config"
	"house-notifier/internal/model"
)

type Scraper interface {
	SiteCode() string
	Scrape(ctx context.Context, cfg *config.SearchConfig, fullScan bool, maxPages int) ([]*model.ScrapedProperty, error)
	CheckSold(ctx context.Context, url string) (bool, error)
}
