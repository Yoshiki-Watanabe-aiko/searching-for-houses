package able

import (
	"context"
	"fmt"
	"net/url"
	"regexp"
	"strconv"
	"strings"

	"github.com/PuerkitoBio/goquery"

	"house-notifier/internal/config"
	"house-notifier/internal/model"
	"house-notifier/internal/scraper"
)

const siteCode = "ABLE"
const baseURL = "https://www.able.co.jp"
const pageSize = 30

// URL format: /{prefSlug}/area/{cityCode}/list/?page=N
var prefSlug = map[string]string{
	"東京都": "tokyo", "神奈川県": "kanagawa", "埼玉県": "saitama", "千葉県": "chiba",
	"大阪府": "osaka", "京都府": "kyoto", "兵庫県": "hyogo", "愛知県": "aichi",
	"福岡県": "fukuoka", "北海道": "hokkaido",
}

var rePriceMan = regexp.MustCompile(`([\d.]+)万円`)
var reLayout = regexp.MustCompile(`(\d+(?:LDK|DK|K|R)|ワンルーム)`)
var reAreaSqm = regexp.MustCompile(`([\d.]+)(?:m2|m²|㎡)`)
var reAge = regexp.MustCompile(`築(\d+)年`)

// Detail URL: /detail/Detail.do?bk={id}&...
var reAbleID = regexp.MustCompile(`[?&]bk=([^&]+)`)

// t= parameter values for ABLE equipment conditions.
var featureT = map[string]string{
	"BATH_SEPARATE":  "0",
	"HVAC_AC":        "1",
	"INT_LAUNDRY":    "2",
	"SEC_AUTOLOCK":   "3",
	"INT_FLOORING":   "4",
	"EQUIP_ELEVATOR": "5",
	"EQUIP_BALCONY":  "6",
	"KITCHEN_SYSTEM": "7",
	"KITCHEN_IH":     "8",
	"COMM_BS":        "9",
}

// n= parameter values for ABLE location/conditions.
var featureN = map[string]string{
	"LOC_FLOOR_2UP":      "0",
	"EQUIP_PARKING":      "1",
	"LOC_SOUTH_FACING":   "3",
	"MOVEIN_IMMEDIATE":   "4",
	"MOVEIN_INSTRUMENT":  "5",
	"MOVEIN_PET":         "6",
	"MOVEIN_OFFICE_USE":  "7",
	"MOVEIN_FEMALE_ONLY": "8",
	"MOVEIN_SENIOR":      "9",
}

type Scraper struct{}

func New() *Scraper { return &Scraper{} }

func (s *Scraper) SiteCode() string { return siteCode }

func (s *Scraper) Scrape(ctx context.Context, cfg *config.SearchConfig, fullScan bool, maxPages int) ([]*model.ScrapedProperty, error) {
	slug, queryParams, err := buildParams(cfg)
	if err != nil {
		return nil, err
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
		if cityCode == "" {
			// ABLE requires city-level: prefecture-only search not supported via URL
			continue
		}
		base := fmt.Sprintf("%s/%s/area/%s/list/", baseURL, slug, cityCode)
		for page := 1; page <= pages; page++ {
			pageURL := fmt.Sprintf("%s?page=%d&sort=2", base, page)
			if queryParams != "" {
				pageURL += "&" + queryParams
			}
			doc, err := scraper.FetchHTML(pageURL)
			if err != nil {
				if err == scraper.ErrNotFound {
					break
				}
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
	exists, err := scraper.CheckURLExists(propertyURL)
	return !exists, err
}

func buildParams(cfg *config.SearchConfig) (string, string, error) {
	if len(cfg.Conditions.Area.Prefectures) == 0 {
		return "", "", fmt.Errorf("prefecture required")
	}
	pref := cfg.Conditions.Area.Prefectures[0]
	slug, ok := prefSlug[pref]
	if !ok {
		return "", "", fmt.Errorf("unsupported prefecture for ABLE: %s", pref)
	}

	params := url.Values{}
	c := cfg.Conditions
	if c.Price.NoReikin {
		params.Set("r1", "1")
	}
	if c.Price.NoShikikin {
		params.Set("s1", "1")
	}
	for _, feat := range c.Features {
		if v, ok := featureT[feat]; ok {
			params.Add("t", v)
		}
		if v, ok := featureN[feat]; ok {
			params.Add("n", v)
		}
	}
	return slug, params.Encode(), nil
}

func parseListings(doc *goquery.Document) []*model.ScrapedProperty {
	var props []*model.ScrapedProperty
	seen := map[string]bool{}

	// Find detail links (Detail.do?bk=...) and work from anchors directly
	doc.Find("a[href*='Detail.do']").Each(func(_ int, a *goquery.Selection) {
		href, ok := a.Attr("href")
		if !ok {
			return
		}
		m := reAbleID.FindStringSubmatch(href)
		if len(m) < 2 {
			return
		}
		id := m[1]
		if seen[id] {
			return
		}
		seen[id] = true
		if !strings.HasPrefix(href, "http") {
			href = baseURL + href
		}
		// Walk up to list item container (unit list__item)
		container := a.Parent()
		for i := 0; i < 5; i++ {
			cls, _ := container.Attr("class")
			if strings.Contains(cls, "list__item") {
				break
			}
			container = container.Parent()
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
