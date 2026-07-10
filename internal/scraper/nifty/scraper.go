package nifty

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

const siteCode = "NIFTY"
const baseURL = "https://myhome.nifty.com"
const pageSize = 20

var prefSlug = map[string]string{
	"東京都": "tokyo", "神奈川県": "kanagawa", "埼玉県": "saitama", "千葉県": "chiba",
	"大阪府": "osaka", "京都府": "kyoto", "兵庫県": "hyogo", "愛知県": "aichi",
	"福岡県": "fukuoka", "北海道": "hokkaido",
}

// Condition code → NIFTY query parameter name (value is always "1").
var featureCode = map[string]string{
	// 位置
	"LOC_FLOOR_2UP":    "floors2",
	"LOC_TOP_FLOOR":    "ex24",
	"LOC_CORNER":       "ex27",
	"LOC_SOUTH_FACING": "ex25",
	// 室内設備
	"INT_LAUNDRY":  "ex7",
	"INT_WASHROOM": "ex78",
	"INT_FLOORING": "ex10",
	"INT_LOFT":     "ex71",
	// 冷暖房
	"HVAC_AC":         "ex2",
	"HVAC_FLOOR_HEAT": "ex43",
	"HVAC_KEROSENE":   "ex83",
	"HVAC_GAS_HEAT":   "ex82",
	// バス・トイレ
	"BATH_SEPARATE":  "ex1",
	"BATH_WASHLET":   "ex36",
	"BATH_DRY":       "ex34",
	"BATH_REHEATING": "ex33",
	// キッチン
	"KITCHEN_GAS":      "ex75",
	"KITCHEN_IH":       "ex32",
	"KITCHEN_2BURNER":  "ex84",
	"KITCHEN_ALL_ELEC": "ex85",
	"KITCHEN_SYSTEM":   "ex30",
	"KITCHEN_COUNTER":  "ex31",
	// 建物設備
	"EQUIP_PARKING":      "ex5",
	"EQUIP_BICYCLE":      "ex100",
	"EQUIP_BIKE":         "ex101",
	"EQUIP_ELEVATOR":     "ex11",
	"EQUIP_DELIVERY_BOX": "ex39",
	"EQUIP_BALCONY":      "ex21",
	"EQUIP_CITY_GAS":     "ex90",
	"EQUIP_LPG":          "ex91",
	"EQUIP_BARRIER_FREE": "ex20",
	// セキュリティ
	"SEC_AUTOLOCK":         "ex9",
	"SEC_MONITOR_INTERCOM": "ex40",
	// テレビ・通信
	"COMM_NET_AVAILABLE": "ex74",
	"COMM_FIBER":         "ex73",
	"COMM_BS":            "ex44",
	"COMM_CABLE_TV":      "ex46",
	// 収納
	"STORAGE_WIC":         "ex47",
	"STORAGE_TRUNK":       "ex29",
	"STORAGE_UNDER_FLOOR": "ex28",
	// 入居条件
	"MOVEIN_PET":         "ex3",
	"MOVEIN_FEMALE_ONLY": "ex76",
	"MOVEIN_INSTRUMENT":  "ex8",
	"MOVEIN_OFFICE_USE":  "ex81",
	"MOVEIN_ROOMSHARE":   "ex79",
	// 物件特性
	"FEAT_REFORMED":      "ex52",
	"FEAT_DESIGNER":      "ex88",
	"FEAT_SUBLEASE":      "ex77",
	"FEAT_ONLINE_CONSULT": "ex203",
	"FEAT_TODAY_NEW":     "ex13",
	"FEAT_WITH_LAYOUT":   "ex12",
}

var rePriceMan = regexp.MustCompile(`([\d.]+)万円`)
var reLayout = regexp.MustCompile(`(\d+(?:LDK|DK|K|R)|ワンルーム)`)
var reAreaSqm = regexp.MustCompile(`([\d.]+)(?:m2|m²|㎡)`)
var reAge = regexp.MustCompile(`築(\d+)年`)
var reNiftyID = regexp.MustCompile(`/detail_([^/?]+)`)

type Scraper struct{ browser *rod.Browser }

func New(browser *rod.Browser) *Scraper { return &Scraper{browser: browser} }

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
		base := fmt.Sprintf("%s/rent/%s/search/?%s", baseURL, sl, params)
		for page := 1; page <= pages; page++ {
			pageURL := fmt.Sprintf("%s&page=%d", base, page)
			doc, err := scraper.FetchHTMLWithBrowser(s.browser, pageURL)
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
	exists, err := scraper.CheckURLExistsWithBrowser(s.browser, propertyURL)
	return !exists, err
}

func buildParams(cfg *config.SearchConfig) (string, string, error) {
	if len(cfg.Conditions.Area.Prefectures) == 0 {
		return "", "", fmt.Errorf("prefecture required")
	}
	pref := cfg.Conditions.Area.Prefectures[0]
	slug, ok := prefSlug[pref]
	if !ok {
		return "", "", fmt.Errorf("unsupported prefecture for NIFTY: %s", pref)
	}

	params := url.Values{}
	c := cfg.Conditions
	if c.Area.WalkMinutesMax != nil {
		params.Set("eki_limit", strconv.Itoa(*c.Area.WalkMinutesMax))
	}
	if c.Building.AgeMax != nil {
		params.Set("age_limit", strconv.Itoa(*c.Building.AgeMax))
	}
	if c.Price.RentMax != nil {
		params.Set("kakaku_limit", strconv.FormatInt(*c.Price.RentMax/10000, 10))
	}
	for _, l := range c.Building.Layouts {
		params.Add("madori[]", l)
	}
	if c.Price.NoReikin {
		params.Set("ex16", "1")
	}
	if c.Price.NoShikikin {
		params.Set("ex15", "1")
	}
	for _, feat := range c.Features {
		if param, ok := featureCode[feat]; ok {
			params.Set(param, "1")
		}
	}
	params.Set("sort", "2")
	return slug, params.Encode(), nil
}

func parseListings(doc *goquery.Document) []*model.ScrapedProperty {
	var props []*model.ScrapedProperty
	seen := map[string]bool{}

	doc.Find("a[href*='/detail_']").Each(func(_ int, a *goquery.Selection) {
		href, ok := a.Attr("href")
		if !ok {
			return
		}
		if !strings.HasPrefix(href, "http") {
			href = baseURL + href
		}
		m := reNiftyID.FindStringSubmatch(href)
		if len(m) < 2 {
			return
		}
		id := m[1]
		if seen[id] {
			return
		}
		seen[id] = true

		// Walk up 7 levels to reach the card container
		container := a.Parent()
		for i := 0; i < 6; i++ {
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