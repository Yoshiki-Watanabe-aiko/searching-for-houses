package athome

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

const siteCode = "ATHOME"
const baseURL = "https://www.athome.co.jp"
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

// Condition code → ATHOME KODAWARI[] parameter value.
var featureCode = map[string]string{
	"BATH_SEPARATE": "B01", // バス・トイレ別
	"INT_LAUNDRY":   "E23", // 室内洗濯機置き場
	"INT_FLOORING":  "O03", // フローリング
	"HVAC_AC":       "A01", // エアコン
	"SEC_AUTOLOCK":  "S01", // オートロック
	"EQUIP_PARKING": "O20", // 駐車場(近隣含む)
	"MOVEIN_PET":    "C08", // ペット相談
	"LOC_FLOOR_2UP": "P02", // 2階以上
}

var rePriceMan = regexp.MustCompile(`([\d.]+)万円`)
var reLayout = regexp.MustCompile(`(\d+(?:LDK|DK|K|R)|ワンルーム)`)
var reAreaSqm = regexp.MustCompile(`([\d.]+)(?:m2|m²|㎡)`)
var reAge = regexp.MustCompile(`築(\d+)年`)
var reChintaiID = regexp.MustCompile(`/chintai/(\d+)/`)

type Scraper struct{ browser *rod.Browser }

func New(browser *rod.Browser) *Scraper { return &Scraper{browser: browser} }

func (s *Scraper) SiteCode() string { return siteCode }

func (s *Scraper) Scrape(ctx context.Context, cfg *config.SearchConfig, fullScan bool, maxPages int) ([]*model.ScrapedProperty, error) {
	_, params, err := buildParams(cfg)
	if err != nil {
		return nil, err
	}

	pages := 1
	if fullScan && maxPages > 0 {
		pages = maxPages
	}

	slugs := cfg.Conditions.Area.Cities
	if len(slugs) == 0 {
		for _, pref := range cfg.Conditions.Area.Prefectures {
			sl, ok := prefSlug[pref]
			if !ok {
				continue
			}
			slugs = append(slugs, sl)
		}
	}

	var results []*model.ScrapedProperty
	for _, sl := range slugs {
		base := fmt.Sprintf("%s/chintai/%s/list/?bukken=1&%s", baseURL, sl, params)
		for page := 1; page <= pages; page++ {
			doc, err := scraper.FetchHTMLWithBrowser(s.browser, fmt.Sprintf("%s&page=%d", base, page))
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
		return "", "", fmt.Errorf("unknown prefecture: %s", pref)
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
	for _, feat := range c.Features {
		if code, ok := featureCode[feat]; ok {
			params.Add("KODAWARI[]", code)
		}
	}
	params.Set("sort", "2")
	return slug, params.Encode(), nil
}

func parseListings(doc *goquery.Document) []*model.ScrapedProperty {
	var props []*model.ScrapedProperty
	seen := map[string]bool{}

	currentTitle := ""
	var currentAge *int
	currentImage := ""

	// ATHOME uses <h3> for building names followed by <table> rows for rooms.
	// Traverse in document order to associate rooms with their building.
	doc.Find("h3, a[href*='/chintai/']").Each(func(_ int, s *goquery.Selection) {
		if goquery.NodeName(s) == "h3" {
			if text := strings.TrimSpace(s.Text()); text != "" {
				currentTitle = text
				block := s.Parent()
				if src, ok := block.Find("img[src*='athome']").First().Attr("src"); ok {
					currentImage = src
				}
				currentAge = parseAge(block.Text())
			}
			return
		}

		href, ok := s.Attr("href")
		if !ok || href == "" {
			return
		}
		m := reChintaiID.FindStringSubmatch(href)
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

		row := s.Closest("tr")
		if row.Length() == 0 {
			row = s.Parent()
		}
		text := row.Text()

		props = append(props, &model.ScrapedProperty{
			ExternalID: id,
			URL:        fmt.Sprintf("%s/chintai/%s/", baseURL, id),
			Title:      currentTitle,
			AgeYears:   currentAge,
			ImageURL:   currentImage,
			Price:      parsePrice(text),
			Layout:     parseLayout(text),
			AreaSqm:    parseArea(text),
		})
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