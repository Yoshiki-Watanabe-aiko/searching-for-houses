package apaman

import (
	"context"
	"fmt"
	"net/url"
	"regexp"
	"strconv"
	"strings"

	"github.com/PuerkitoBio/goquery"
	"github.com/go-rod/rod"

	"house-notifier/internal/config"
	"house-notifier/internal/model"
	"house-notifier/internal/scraper"
)

const siteCode = "APAMAN"
const baseURL = "https://www.apamanshop.com"
const pageSize = 25

var prefCode = map[string]string{
	"東京都": "13", "神奈川県": "14", "埼玉県": "11", "千葉県": "12",
	"大阪府": "27", "京都府": "26", "兵庫県": "28", "愛知県": "23",
	"福岡県": "40", "北海道": "01",
}

var prefSlug = map[string]string{
	"東京都": "tokyo", "神奈川県": "kanagawa", "埼玉県": "saitama", "千葉県": "chiba",
	"大阪府": "osaka", "京都府": "kyoto", "兵庫県": "hyogo", "愛知県": "aichi",
	"福岡県": "fukuoka", "北海道": "hokkaido",
}

var rePriceMan = regexp.MustCompile(`([\d.]+)万円`)
var reLayout = regexp.MustCompile(`(\d+(?:LDK|DK|K|R)|ワンルーム)`)
var reAreaSqm = regexp.MustCompile(`([\d.]+)(?:m2|m²|㎡)`)
var reAge = regexp.MustCompile(`築(\d+)年`)
// Property URLs: /{short_pref_slug}/{numeric_id}/ e.g. /tokyo/2161040/
// pref slug is 3-12 chars; long words like "yachinsobasearch" are excluded
var reApamanID = regexp.MustCompile(`^/[a-z]{3,12}/(\d{6,})/$`)

type Scraper struct{ browser *rod.Browser }

func New(browser *rod.Browser) *Scraper { return &Scraper{browser: browser} }

func (s *Scraper) SiteCode() string { return siteCode }

func (s *Scraper) Scrape(ctx context.Context, cfg *config.SearchConfig, fullScan bool, maxPages int) ([]*model.ScrapedProperty, error) {
	code, params, err := buildParams(cfg)
	if err != nil {
		return nil, err
	}

	prefName := ""
	if len(cfg.Conditions.Area.Prefectures) > 0 {
		prefName = cfg.Conditions.Area.Prefectures[0]
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
			// Prefecture-level: use prefecture slug URL (e.g. /tokyo/)
			base = fmt.Sprintf("%s/%s/?%s", baseURL, prefSlug[prefName], params)
		} else {
			// City-level: pref_code-city_code format (e.g. 13-13104)
			base = fmt.Sprintf("%s/kensaku/list/?search_type=area&target%%5B0%%5D=%s-%s&%s", baseURL, code, cityCode, params)
		}
		for page := 1; page <= pages; page++ {
			pageURL := fmt.Sprintf("%s&page=%d", base, page)
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

func buildParams(cfg *config.SearchConfig) (string, string, error) {
	if len(cfg.Conditions.Area.Prefectures) == 0 {
		return "", "", fmt.Errorf("prefecture required")
	}
	pref := cfg.Conditions.Area.Prefectures[0]
	code, ok := prefCode[pref]
	if !ok {
		return "", "", fmt.Errorf("unsupported prefecture for APAMAN: %s", pref)
	}

	params := url.Values{}
	c := cfg.Conditions
	if c.Area.WalkMinutesMax != nil {
		params.Set("toho_max", strconv.Itoa(*c.Area.WalkMinutesMax))
	}
	if c.Building.AgeMax != nil {
		params.Set("chiku_max", strconv.Itoa(*c.Building.AgeMax))
	}
	if c.Price.RentMax != nil {
		params.Set("yachin_max", strconv.FormatInt(*c.Price.RentMax/10000, 10))
	}
	for _, l := range c.Building.Layouts {
		params.Add("madori[]", l)
	}
	params.Set("sort", "2")
	return code, params.Encode(), nil
}

func parseListings(doc *goquery.Document) []*model.ScrapedProperty {
	var props []*model.ScrapedProperty
	seen := map[string]bool{}

	doc.Find("a[href]").Each(func(_ int, a *goquery.Selection) {
		href, ok := a.Attr("href")
		if !ok || !reApamanID.MatchString(href) {
			return
		}
		m := reApamanID.FindStringSubmatch(href)
		if len(m) < 2 {
			return
		}
		id := m[1]
		if !strings.HasPrefix(href, "http") {
			href = baseURL + href
		}
		if seen[id] {
			return
		}
		seen[id] = true

		// Container is 2 levels up from anchor (section.ranking_box_contents)
		container := a.Parent().Parent()
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
	if len(m) < 2 { return nil }
	f, err := strconv.ParseFloat(m[1], 64)
	if err != nil { return nil }
	v := int64(f * 10000)
	return &v
}

func parseLayout(text string) string {
	m := reLayout.FindStringSubmatch(text)
	if len(m) < 2 { return "" }
	return m[1]
}

func parseArea(text string) *float64 {
	m := reAreaSqm.FindStringSubmatch(text)
	if len(m) < 2 { return nil }
	f, err := strconv.ParseFloat(m[1], 64)
	if err != nil { return nil }
	return &f
}

func parseAge(text string) *int {
	m := reAge.FindStringSubmatch(text)
	if len(m) < 2 { return nil }
	n, err := strconv.Atoi(m[1])
	if err != nil { return nil }
	return &n
}