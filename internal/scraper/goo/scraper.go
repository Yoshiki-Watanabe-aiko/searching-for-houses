package goo

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

const siteCode = "GOO"
const baseURL = "https://house.goo.ne.jp"
const pageSize = 30

// Prefecture code mapping (JIS X 0401)
var prefCode = map[string]string{
	"北海道": "1", "青森県": "2", "岩手県": "3", "宮城県": "4", "秋田県": "5",
	"山形県": "6", "福島県": "7", "茨城県": "8", "栃木県": "9", "群馬県": "10",
	"埼玉県": "11", "千葉県": "12", "東京都": "13", "神奈川県": "14",
	"新潟県": "15", "富山県": "16", "石川県": "17", "福井県": "18",
	"山梨県": "19", "長野県": "20", "岐阜県": "21", "静岡県": "22", "愛知県": "23",
	"三重県": "24", "滋賀県": "25", "京都府": "26", "大阪府": "27", "兵庫県": "28",
	"奈良県": "29", "和歌山県": "30", "鳥取県": "31", "島根県": "32", "岡山県": "33",
	"広島県": "34", "山口県": "35", "徳島県": "36", "香川県": "37", "愛媛県": "38",
	"高知県": "39", "福岡県": "40", "佐賀県": "41", "長崎県": "42", "熊本県": "43",
	"大分県": "44", "宮崎県": "45", "鹿児島県": "46", "沖縄県": "47",
}

var rePriceMan = regexp.MustCompile(`([\d.]+)万円`)
var reLayout = regexp.MustCompile(`(\d+(?:LDK|DK|K|R)|ワンルーム)`)
var reAreaSqm = regexp.MustCompile(`([\d.]+)(?:m2|m²|㎡)`)
var reAge = regexp.MustCompile(`築(\d+)年`)
var reGooID = regexp.MustCompile(`/detail/\d+/\d+/([^/]+)/`)

type Scraper struct{}

func New() *Scraper { return &Scraper{} }

func (s *Scraper) SiteCode() string { return siteCode }

func (s *Scraper) Scrape(ctx context.Context, cfg *config.SearchConfig, fullScan bool, maxPages int) ([]*model.ScrapedProperty, error) {
	params, err := buildParams(cfg)
	if err != nil {
		return nil, err
	}

	pages := 1
	if fullScan && maxPages > 0 {
		pages = maxPages
	}

	var results []*model.ScrapedProperty
	if len(cfg.Conditions.Area.Cities) > 0 {
		for _, cityCode := range cfg.Conditions.Area.Cities {
			base := fmt.Sprintf("%s/rent/?g=city&v=%s&%s", baseURL, cityCode, params)
			for page := 1; page <= pages; page++ {
				doc, err := scraper.FetchHTML(fmt.Sprintf("%s&page=%d", base, page))
				if err != nil {
					if page > 1 {
						break
					}
					return results, fmt.Errorf("fetch city=%s page=%d: %w", cityCode, page, err)
				}
				props := filterByPrefectures(parseListings(doc), cfg.Conditions.Area.Prefectures)
				results = append(results, props...)
				if len(props) < pageSize {
					break
				}
			}
		}
	} else {
		for _, pref := range cfg.Conditions.Area.Prefectures {
			code, ok := prefCode[pref]
			if !ok {
				continue
			}
			base := fmt.Sprintf("%s/rent/?g=pref&v=%s&%s", baseURL, code, params)
			for page := 1; page <= pages; page++ {
				doc, err := scraper.FetchHTML(fmt.Sprintf("%s&page=%d", base, page))
				if err != nil {
					if page > 1 {
						break
					}
					return results, fmt.Errorf("fetch pref=%s page=%d: %w", pref, page, err)
				}
				props := filterByPrefectures(parseListings(doc), cfg.Conditions.Area.Prefectures)
				results = append(results, props...)
				if len(props) < pageSize {
					break
				}
			}
		}
	}
	return results, nil
}

func (s *Scraper) CheckSold(ctx context.Context, propertyURL string) (bool, error) {
	exists, err := scraper.CheckURLExists(propertyURL)
	return !exists, err
}

func buildParams(cfg *config.SearchConfig) (string, error) {
	if len(cfg.Conditions.Area.Prefectures) == 0 {
		return "", fmt.Errorf("prefecture required")
	}

	params := url.Values{}
	c := cfg.Conditions
	if c.Area.WalkMinutesMax != nil {
		params.Set("eki", strconv.Itoa(*c.Area.WalkMinutesMax))
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
	return params.Encode(), nil
}

// filterByPrefectures removes properties whose address does not start with
// any of the requested prefectures. Properties with no address are kept.
func filterByPrefectures(props []*model.ScrapedProperty, prefectures []string) []*model.ScrapedProperty {
	if len(prefectures) == 0 {
		return props
	}
	filtered := props[:0]
	for _, p := range props {
		if p.Address == "" {
			filtered = append(filtered, p)
			continue
		}
		for _, pref := range prefectures {
			if strings.HasPrefix(p.Address, pref) {
				filtered = append(filtered, p)
				break
			}
		}
	}
	return filtered
}

func parseListings(doc *goquery.Document) []*model.ScrapedProperty {
	var props []*model.ScrapedProperty
	seen := map[string]bool{}

	doc.Find("a[href*='/rent/ap/detail/']").Each(func(_ int, a *goquery.Selection) {
		href, ok := a.Attr("href")
		if !ok {
			return
		}
		if !strings.HasPrefix(href, "http") {
			href = baseURL + href
		}
		m := reGooID.FindStringSubmatch(href)
		if len(m) < 2 {
			return
		}
		externalID := m[1]
		if seen[externalID] {
			return
		}
		seen[externalID] = true

		text := a.Text()
		parent := a.Parent()
		if parent != nil {
			text = parent.Text()
		}

		prop := &model.ScrapedProperty{
			ExternalID:  externalID,
			URL:         href,
			Price:       parsePrice(text),
			Layout:      parseLayout(text),
			AreaSqm:     parseArea(text),
			AgeYears:    parseAge(text),
			Address:     extractAddress(text),
		}
		props = append(props, prop)
	})
	return props
}

// extractAddress finds the first token containing 都/道/府/県 in text and returns
// the full address line. Uses rune iteration to avoid multi-byte boundary issues.
func extractAddress(text string) string {
	runes := []rune(text)
	for i, r := range runes {
		if r != '都' && r != '道' && r != '府' && r != '県' {
			continue
		}
		start := i
		for start > 0 && runes[start-1] != '\n' && runes[start-1] != '\t' && runes[start-1] != ' ' && runes[start-1] != '　' {
			start--
		}
		end := i + 1
		for end < len(runes) && runes[end] != '\n' && runes[end] != '\t' {
			end++
		}
		return strings.TrimSpace(string(runes[start:end]))
	}
	return ""
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