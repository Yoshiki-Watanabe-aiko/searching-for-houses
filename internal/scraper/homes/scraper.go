package homes

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

const siteCode = "HOMES"
const baseURL = "https://www.homes.co.jp"
const pageSize = 30

var prefSlug = map[string]string{
	"北海道": "hokkaido", "青森県": "aomori", "岩手県": "iwate", "宮城県": "miyagi",
	"秋田県": "akita", "山形県": "yamagata", "福島県": "fukushima",
	"茨城県": "ibaraki", "栃木県": "tochigi", "群馬県": "gunma",
	"埼玉県": "saitama", "千葉県": "chiba", "東京都": "tokyo", "神奈川県": "kanagawa",
	"新潟県": "niigata", "富山県": "toyama", "石川県": "ishikawa", "福井県": "fukui",
	"山梨県": "yamanashi", "長野県": "nagano", "岐阜県": "gifu", "静岡県": "shizuoka",
	"愛知県": "aichi", "三重県": "mie", "滋賀県": "shiga", "京都府": "kyoto",
	"大阪府": "osaka", "兵庫県": "hyogo", "奈良県": "nara", "和歌山県": "wakayama",
	"鳥取県": "tottori", "島根県": "shimane", "岡山県": "okayama", "広島県": "hiroshima",
	"山口県": "yamaguchi", "徳島県": "tokushima", "香川県": "kagawa", "愛媛県": "ehime",
	"高知県": "kochi", "福岡県": "fukuoka", "佐賀県": "saga", "長崎県": "nagasaki",
	"熊本県": "kumamoto", "大分県": "oita", "宮崎県": "miyazaki",
	"鹿児島県": "kagoshima", "沖縄県": "okinawa",
}

var rePriceMan = regexp.MustCompile(`([\d.]+)万円`)
var reLayout = regexp.MustCompile(`(\d+(?:LDK|DK|K|R)|ワンルーム)`)
var reAreaSqm = regexp.MustCompile(`([\d.]+)(?:m2|m²|㎡)`)
var reAge = regexp.MustCompile(`築(\d+)年`)
var reRoomHash = regexp.MustCompile(`/chintai/room/([0-9a-f]+)/`)

type Scraper struct{}

func New() *Scraper { return &Scraper{} }

func (s *Scraper) SiteCode() string { return siteCode }

func (s *Scraper) Scrape(ctx context.Context, cfg *config.SearchConfig, fullScan bool, maxPages int) ([]*model.ScrapedProperty, error) {
	slug, params, err := buildParams(cfg)
	if err != nil {
		return nil, err
	}

	pages := 1
	if fullScan && maxPages > 0 {
		pages = maxPages
	}

	slugs := cfg.Conditions.Area.Cities
	if len(slugs) == 0 {
		slugs = []string{slug}
	}

	var results []*model.ScrapedProperty
	for _, sl := range slugs {
		base := fmt.Sprintf("%s/chintai/%s/list/?%s", baseURL, sl, params)
		for page := 1; page <= pages; page++ {
			doc, err := scraper.FetchHTML(fmt.Sprintf("%s&page=%d", base, page))
			if err != nil {
				return results, fmt.Errorf("fetch slug=%s page=%d: %w", sl, page, err)
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
		return "", "", fmt.Errorf("unknown prefecture: %s", pref)
	}

	params := url.Values{}
	c := cfg.Conditions
	for _, l := range c.Building.Layouts {
		params.Add("bkz", l)
	}
	if c.Price.RentMax != nil {
		params.Set("prct", strconv.FormatInt(*c.Price.RentMax/10000, 10))
	}
	if c.Building.AgeMax != nil {
		params.Set("ckzn", strconv.Itoa(*c.Building.AgeMax))
	}
	if c.Area.WalkMinutesMax != nil {
		params.Set("exn", strconv.Itoa(*c.Area.WalkMinutesMax))
	}
	params.Set("sort", "2")
	return slug, params.Encode(), nil
}

func parseListings(doc *goquery.Document) []*model.ScrapedProperty {
	var props []*model.ScrapedProperty
	doc.Find("[class*=mod-mergeBuilding]").Each(func(_ int, s *goquery.Selection) {
		href, ok := s.Find("a[href*='/chintai/room/']").First().Attr("href")
		if !ok {
			return
		}
		if !strings.HasPrefix(href, "http") {
			href = baseURL + href
		}
		m := reRoomHash.FindStringSubmatch(href)
		if len(m) < 2 {
			return
		}
		text := s.Text()
		prop := &model.ScrapedProperty{
			ExternalID:  m[1],
			URL:         href,
			Title:       strings.TrimSpace(s.Find(".prg-bukkenNameAnchor").First().Text()),
			Address:     parseAddress(s),
			StationInfo: strings.TrimSpace(s.Find(".prg-stationText").First().Text()),
			Price:       parsePrice(text),
			Layout:      parseLayout(text),
			AreaSqm:     parseArea(text),
			AgeYears:    parseAge(text),
		}
		if src, ok := s.Find(".bukkenPhoto img").First().Attr("src"); ok && !strings.HasPrefix(src, "data:") {
			prop.ImageURL = src
		}
		props = append(props, prop)
	})
	return props
}

func parseAddress(s *goquery.Selection) string {
	text := strings.TrimSpace(s.Find(".sec-specB").First().Text())
	text = strings.TrimPrefix(text, "所在地")
	if idx := strings.Index(text, "交通"); idx >= 0 {
		text = text[:idx]
	}
	return strings.TrimSpace(text)
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