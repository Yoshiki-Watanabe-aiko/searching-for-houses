package suumo

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

const siteCode = "SUUMO"
const baseURL = "https://suumo.jp"
const pageSize = 30

// Prefecture name → SUUMO URL path segment (for ward-level search)
var prefectureSlug = map[string]string{
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

// Prefecture name → JIS 2-digit code for SUUMO `ta` query param
var prefectureJISCode = map[string]string{
	"北海道": "01", "青森県": "02", "岩手県": "03", "宮城県": "04",
	"秋田県": "05", "山形県": "06", "福島県": "07",
	"茨城県": "08", "栃木県": "09", "群馬県": "10",
	"埼玉県": "11", "千葉県": "12", "東京都": "13", "神奈川県": "14",
	"新潟県": "15", "富山県": "16", "石川県": "17", "福井県": "18",
	"山梨県": "19", "長野県": "20", "岐阜県": "21", "静岡県": "22",
	"愛知県": "23", "三重県": "24", "滋賀県": "25", "京都府": "26",
	"大阪府": "27", "兵庫県": "28", "奈良県": "29", "和歌山県": "30",
	"鳥取県": "31", "島根県": "32", "岡山県": "33", "広島県": "34",
	"山口県": "35", "徳島県": "36", "香川県": "37", "愛媛県": "38",
	"高知県": "39", "福岡県": "40", "佐賀県": "41", "長崎県": "42",
	"熊本県": "43", "大分県": "44", "宮崎県": "45", "鹿児島県": "46", "沖縄県": "47",
}

// Prefecture name → SUUMO area region code for `ar` query param
var prefectureRegion = map[string]string{
	"北海道": "010",
	"青森県": "020", "岩手県": "020", "宮城県": "020", "秋田県": "020", "山形県": "020", "福島県": "020",
	"茨城県": "030", "栃木県": "030", "群馬県": "030", "埼玉県": "030", "千葉県": "030", "東京都": "030", "神奈川県": "030",
	"新潟県": "040", "富山県": "040", "石川県": "040", "福井県": "040", "山梨県": "040", "長野県": "040",
	"岐阜県": "050", "静岡県": "050", "愛知県": "050", "三重県": "050",
	"滋賀県": "060", "京都府": "060", "大阪府": "060", "兵庫県": "060", "奈良県": "060", "和歌山県": "060",
	"鳥取県": "070", "島根県": "070", "岡山県": "070", "広島県": "070", "山口県": "070",
	"徳島県": "080", "香川県": "080", "愛媛県": "080", "高知県": "080",
	"福岡県": "090", "佐賀県": "090", "長崎県": "090", "熊本県": "090", "大分県": "090", "宮崎県": "090", "鹿児島県": "090", "沖縄県": "090",
}

// Condition code → SUUMO tc= equipment parameter value (7-digit code).
// SUUMO URL-based filtering only supports these 10 conditions via tc=.
// Other conditions are not filterable through URL parameters on SUUMO.
var featureCode = map[string]string{
	"LOC_FLOOR_2UP":        "0400101", // 2階以上住戸
	"BATH_SEPARATE":        "0400301", // バス・トイレ別
	"INT_LAUNDRY":          "0400501", // 室内洗濯機置場
	"INT_FLOORING":         "0400503", // フローリング
	"HVAC_AC":              "0400601", // エアコン付
	"SEC_AUTOLOCK":         "0400801", // オートロック
	"EQUIP_PARKING":        "0400901", // 駐車場あり
	"MOVEIN_PET":           "0401102", // ペット相談
	"MOVEIN_NO_FIXED_TERM": "0401106", // 定期借家を含まない
	"FEAT_WITH_LAYOUT":     "0401301", // 間取り図付
}

// Layout name → SUUMO md parameter value
var layoutCode = map[string]string{
	"ワンルーム": "01", "1R": "01",
	"1K": "02", "1DK": "03", "1LDK": "04", "1SLDK": "04",
	"2K": "05", "2DK": "06", "2LDK": "07", "2SLDK": "07",
	"3K": "09", "3DK": "10", "3LDK": "11", "3SLDK": "11",
	"4K": "12", "4DK": "13", "4LDK": "14",
}

var rePriceMan = regexp.MustCompile(`([\d.]+)万円`)
var reAreaSqm = regexp.MustCompile(`([\d.]+)m2`)
var reAge = regexp.MustCompile(`築(\d+)年`)
var reBcParam = regexp.MustCompile(`bc=(\d+)`)

type Scraper struct{}

func New() *Scraper { return &Scraper{} }

func (s *Scraper) SiteCode() string { return siteCode }

func (s *Scraper) Scrape(ctx context.Context, cfg *config.SearchConfig, fullScan bool, maxPages int) ([]*model.ScrapedProperty, error) {
	if len(cfg.Conditions.Area.Prefectures) == 0 {
		return nil, fmt.Errorf("at least one prefecture required")
	}

	pages := 1
	if fullScan && maxPages > 0 {
		pages = maxPages
	}

	queryParams := buildQueryParams(cfg)
	var results []*model.ScrapedProperty

	wards := cfg.Conditions.Area.Cities
	if len(wards) == 0 {
		for _, pref := range cfg.Conditions.Area.Prefectures {
			ar, ok := prefectureRegion[pref]
			if !ok {
				return results, fmt.Errorf("unknown prefecture: %s", pref)
			}
			ta, ok := prefectureJISCode[pref]
			if !ok {
				return results, fmt.Errorf("unknown prefecture code: %s", pref)
			}
			base := fmt.Sprintf("%s/jj/chintai/ichiran/FR301FC001/?ar=%s&bs=040&ta=%s&%s", baseURL, ar, ta, queryParams)
			props, err := s.fetchPages(base, pages)
			if err != nil {
				return results, fmt.Errorf("fetch pref=%s: %w", pref, err)
			}
			results = append(results, props...)
		}
	} else {
		pref := cfg.Conditions.Area.Prefectures[0]
		prefSlug, ok := prefectureSlug[pref]
		if !ok {
			return results, fmt.Errorf("unknown prefecture: %s", pref)
		}
		for _, ward := range wards {
			base := fmt.Sprintf("%s/chintai/%s/%s/?%s", baseURL, prefSlug, ward, queryParams)
			props, err := s.fetchPages(base, pages)
			if err != nil {
				return results, fmt.Errorf("fetch ward=%s: %w", ward, err)
			}
			results = append(results, props...)
		}
	}

	return results, nil
}

func (s *Scraper) fetchPages(base string, pages int) ([]*model.ScrapedProperty, error) {
	var results []*model.ScrapedProperty
	for page := 1; page <= pages; page++ {
		pageURL := fmt.Sprintf("%s&pc=%d&pn=%d", base, pageSize, page)
		doc, err := scraper.FetchHTML(pageURL)
		if err != nil {
			return results, err
		}
		props := parseListings(doc)
		results = append(results, props...)
		if len(props) < pageSize {
			break
		}
	}
	return results, nil
}

func (s *Scraper) CheckSold(ctx context.Context, propertyURL string) (bool, error) {
	exists, err := scraper.CheckURLExists(propertyURL)
	if err != nil {
		return false, err
	}
	return !exists, nil
}

func buildQueryParams(cfg *config.SearchConfig) string {
	params := url.Values{}
	c := cfg.Conditions
	if c.Area.WalkMinutesMax != nil {
		params.Set("et", strconv.Itoa(*c.Area.WalkMinutesMax))
	}
	if c.Building.AgeMax != nil {
		params.Set("cn", strconv.Itoa(*c.Building.AgeMax))
	}
	if c.Building.AreaMin != nil {
		params.Set("mb", strconv.Itoa(int(*c.Building.AreaMin)))
	}
	if c.Price.RentMax != nil {
		params.Set("ct", strconv.FormatFloat(float64(*c.Price.RentMax)/10000, 'f', 1, 64))
	}
	if c.Price.NoReikin {
		params.Add("co", "3")
	}
	if c.Price.NoShikikin {
		params.Add("co", "4")
	}
	params.Set("sort", "2")
	for _, layout := range c.Building.Layouts {
		if code, ok := layoutCode[layout]; ok {
			params.Add("md", code)
		}
	}
	for _, feat := range c.Features {
		if code, ok := featureCode[feat]; ok {
			params.Add("tc", code)
		}
	}
	return params.Encode()
}

func parseListings(doc *goquery.Document) []*model.ScrapedProperty {
	var props []*model.ScrapedProperty

	doc.Find("div.cassetteitem").Each(func(_ int, building *goquery.Selection) {
		name := strings.TrimSpace(building.Find(".cassetteitem_content-title").First().Text())
		address := strings.TrimSpace(building.Find(".cassetteitem_detail-col1").First().Text())
		station := strings.TrimSpace(building.Find(".cassetteitem_detail-text").First().Text())
		ageYears := parseAge(building.Find(".cassetteitem_detail-col3").First().Text())
		imageURL := buildingImageURL(building)

		building.Find(".js-cassette_link").Each(func(_ int, room *goquery.Selection) {
			href, ok := room.Find("a.js-cassette_link_href").Attr("href")
			if !ok || href == "" {
				return
			}
			if !strings.HasPrefix(href, "http") {
				href = baseURL + href
			}

			m := reBcParam.FindStringSubmatch(href)
			if len(m) < 2 {
				return
			}

			prop := &model.ScrapedProperty{
				ExternalID:  m[1],
				URL:         href,
				Title:       name,
				Address:     address,
				StationInfo: station,
				AgeYears:    ageYears,
				ImageURL:    imageURL,
				Price:       parseManYen(room.Find(".cassetteitem_price--rent").First().Text()),
				Layout:      strings.TrimSpace(room.Find(".cassetteitem_madori").First().Text()),
				AreaSqm:     parseAreaSqm(room.Find(".cassetteitem_menseki").First().Text()),
			}
			props = append(props, prop)
		})
	})

	return props
}

func parseManYen(s string) *int64 {
	m := rePriceMan.FindStringSubmatch(s)
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

func parseAreaSqm(s string) *float64 {
	m := reAreaSqm.FindStringSubmatch(s)
	if len(m) < 2 {
		return nil
	}
	f, err := strconv.ParseFloat(m[1], 64)
	if err != nil {
		return nil
	}
	return &f
}

func parseAge(s string) *int {
	m := reAge.FindStringSubmatch(s)
	if len(m) < 2 {
		return nil
	}
	n, err := strconv.Atoi(m[1])
	if err != nil {
		return nil
	}
	return &n
}

func buildingImageURL(building *goquery.Selection) string {
	img := building.Find(".cassetteitem_object img").First()
	if src, ok := img.Attr("data-src"); ok && src != "" {
		return src
	}
	src, _ := img.Attr("src")
	if strings.HasPrefix(src, "data:") {
		return ""
	}
	return src
}
