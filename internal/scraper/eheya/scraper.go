package eheya

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/PuerkitoBio/goquery"
	"github.com/go-rod/rod"

	"house-notifier/internal/config"
	"house-notifier/internal/model"
	"house-notifier/internal/scraper"
)

const siteCode = "EHEYA"
const baseURL = "https://www.eheya.net"
const pageSize = 20

// URL format: /{prefSlug}/area/{jisCode}/search/?page={page}
var prefSlug = map[string]string{
	"東京都": "tokyo", "神奈川県": "kanagawa", "埼玉県": "saitama", "千葉県": "chiba",
	"大阪府": "osaka", "京都府": "kyoto", "兵庫県": "hyogo", "愛知県": "aichi",
	"福岡県": "fukuoka", "北海道": "hokkaido",
}

var rePriceMan = regexp.MustCompile(`([\d.]+)万円`)
var reLayout = regexp.MustCompile(`(\d+(?:LDK|DK|K|R)|ワンルーム)`)
var reAreaSqm = regexp.MustCompile(`([\d.]+)(?:m2|m²|㎡)`)
var reAge = regexp.MustCompile(`築(\d+)年`)
var reEheyaID = regexp.MustCompile(`/detail/([^/?]+)`)

type Scraper struct{ browser *rod.Browser }

func New(browser *rod.Browser) *Scraper { return &Scraper{browser: browser} }

func (s *Scraper) SiteCode() string { return siteCode }

func (s *Scraper) Scrape(ctx context.Context, cfg *config.SearchConfig, fullScan bool, maxPages int) ([]*model.ScrapedProperty, error) {
	if len(cfg.Conditions.Area.Prefectures) == 0 {
		return nil, fmt.Errorf("prefecture required")
	}
	pref := cfg.Conditions.Area.Prefectures[0]
	slug, ok := prefSlug[pref]
	if !ok {
		return nil, fmt.Errorf("unsupported prefecture for EHEYA: %s", pref)
	}

	pages := 1
	if fullScan && maxPages > 0 {
		pages = maxPages
	}

	cities := cfg.Conditions.Area.Cities
	if len(cities) == 0 {
		cities = []string{""}
	}

	var results []*model.ScrapedProperty
	for _, cityCode := range cities {
		var base string
		if cityCode == "" {
			base = fmt.Sprintf("%s/%s/search/", baseURL, slug)
		} else {
			base = fmt.Sprintf("%s/%s/area/%s/search/", baseURL, slug, cityCode)
		}
		for page := 1; page <= pages; page++ {
			pageURL := fmt.Sprintf("%s?page=%d&sort=2", base, page)
			doc, err := scraper.FetchHTMLWithBrowser(s.browser, pageURL)
			if err != nil {
				return results, fmt.Errorf("fetch city=%s page=%d: %w", cityCode, page, err)
			}
			props := parseListings(doc)
			results = append(results, props...)
			if len(props) < pageSize {
				break
			}
		}
	}
	return results, nil
}

func (s *Scraper) CheckSold(ctx context.Context, propertyURL string) (bool, error) {
	exists, err := scraper.CheckURLExistsWithBrowser(s.browser, propertyURL)
	return !exists, err
}

func parseListings(doc *goquery.Document) []*model.ScrapedProperty {
	var props []*model.ScrapedProperty
	seen := map[string]bool{}

	roomLinks := doc.Find("a[href*='/detail/']")
	roomLinks.Each(func(_ int, a *goquery.Selection) {
		href, ok := a.Attr("href")
		if !ok {
			return
		}
		if strings.Contains(href, "/library/") {
			return
		}
		m := reEheyaID.FindStringSubmatch(href)
		if len(m) < 2 {
			return
		}
		if !strings.HasPrefix(href, "http") {
			href = baseURL + href
		}
		id := m[1]
		if seen[id] {
			return
		}
		seen[id] = true

		container := a.Parent()
		for i := 0; i < 6; i++ {
			cls, _ := container.Attr("class")
			tag := goquery.NodeName(container)
			if strings.Contains(cls, "property") || strings.Contains(cls, "item") ||
				strings.Contains(cls, "bukken") || strings.Contains(cls, "card") ||
				tag == "li" || tag == "article" || tag == "section" {
				break
			}
			parent := container.Parent()
			if parent.Is("body") || parent.Length() == 0 {
				break
			}
			container = parent
		}

		text := container.Text()
		prop := &model.ScrapedProperty{
			ExternalID: id,
			URL:        href,
			Price:      parsePrice(text),
			Layout:     parseLayout(text),
			AreaSqm:    parseArea(text),
			AgeYears:   parseAge(text),
		}
		if src, ok := container.Find("img").First().Attr("src"); ok && !strings.HasPrefix(src, "data:") {
			if !strings.HasPrefix(src, "http") {
				src = baseURL + src
			}
			prop.ImageURL = src
		}
		props = append(props, prop)
	})
	return props
}

func parsePrice(text string) *int64 {
	m := rePriceMan.FindStringSubmatch(text)
	if len(m) < 2 {
		return nil
	}
	f, err := strconv.ParseFloat(m[1], 64)
	if err != nil {
		return nil
	}
	v := int64(f * 10000)
	return &v
}

func parseLayout(text string) string {
	m := reLayout.FindStringSubmatch(text)
	if len(m) < 2 {
		return ""
	}
	return m[1]
}

func parseArea(text string) *float64 {
	m := reAreaSqm.FindStringSubmatch(text)
	if len(m) < 2 {
		return nil
	}
	f, err := strconv.ParseFloat(m[1], 64)
	if err != nil {
		return nil
	}
	return &f
}

func parseAge(text string) *int {
	m := reAge.FindStringSubmatch(text)
	if len(m) < 2 {
		return nil
	}
	n, err := strconv.Atoi(m[1])
	if err != nil {
		return nil
	}
	return &n
}
