package minimini

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/PuerkitoBio/goquery"
	"golang.org/x/text/encoding/japanese"
	"golang.org/x/text/transform"

	"house-notifier/internal/config"
	"house-notifier/internal/model"
	"house-notifier/internal/scraper"
)

const siteCode = "MINIMINI"
const baseURL = "https://minimini.jp"
const pageSize = 20

// URL format: /list/pref/{prefSlug}/{wardSlug}/?dp={page}
var prefSlug = map[string]string{
	"東京都": "tokyo", "神奈川県": "kanagawa", "埼玉県": "saitama", "千葉県": "chiba",
	"大阪府": "osaka", "京都府": "kyoto", "兵庫県": "hyogo", "愛知県": "aichi",
	"福岡県": "fukuoka", "北海道": "hokkaido",
}

var rePriceMan = regexp.MustCompile(`([\d.]+)万`)
var reLayout = regexp.MustCompile(`(\d+(?:LDK|DK|K|R)|ワンルーム)`)
var reAreaSqm = regexp.MustCompile(`([\d.]+)(?:m2|m²|㎡)`)
var reAge = regexp.MustCompile(`築(\d+)年`)

// Property URLs: /hylist/{typeCode}/{propertyId}/
var reHylistID = regexp.MustCompile(`/hylist/([^/]+)/([^/]+)/`)

var httpClient = &http.Client{Timeout: 30 * time.Second}

type Scraper struct{}

func New() *Scraper { return &Scraper{} }

func (s *Scraper) SiteCode() string { return siteCode }

func (s *Scraper) Scrape(ctx context.Context, cfg *config.SearchConfig, fullScan bool, maxPages int) ([]*model.ScrapedProperty, error) {
	if len(cfg.Conditions.Area.Prefectures) == 0 {
		return nil, fmt.Errorf("prefecture required")
	}
	pref := cfg.Conditions.Area.Prefectures[0]
	slug, ok := prefSlug[pref]
	if !ok {
		return nil, fmt.Errorf("unsupported prefecture for MINIMINI: %s", pref)
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
	for _, citySlug := range cities {
		var base string
		if citySlug == "" {
			base = fmt.Sprintf("%s/list/pref/%s/", baseURL, slug)
		} else {
			base = fmt.Sprintf("%s/list/pref/%s/%s/", baseURL, slug, citySlug)
		}
		for page := 1; page <= pages; page++ {
			pageURL := fmt.Sprintf("%s?dp=%d&sort=2", base, page)
			doc, err := fetchShiftJIS(pageURL)
			if err != nil {
				if err == scraper.ErrNotFound {
					break
				}
				return results, fmt.Errorf("fetch city=%s page=%d: %w", citySlug, page, err)
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

func fetchShiftJIS(rawURL string) (*goquery.Document, error) {
	req, err := http.NewRequest("GET", rawURL, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
	req.Header.Set("Accept-Language", "ja-JP,ja;q=0.9")

	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("http get: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == 404 {
		return nil, scraper.ErrNotFound
	}
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("http status %d", resp.StatusCode)
	}

	reader := transform.NewReader(resp.Body, japanese.ShiftJIS.NewDecoder())
	body, err := io.ReadAll(reader)
	if err != nil {
		return nil, fmt.Errorf("read body: %w", err)
	}

	doc, err := goquery.NewDocumentFromReader(strings.NewReader(string(body)))
	if err != nil {
		return nil, fmt.Errorf("parse html: %w", err)
	}
	return doc, nil
}

func parseListings(doc *goquery.Document) []*model.ScrapedProperty {
	var props []*model.ScrapedProperty
	seen := map[string]bool{}

	hylistLinks := doc.Find("a[href*='/hylist/']")
	hylistLinks.Each(func(_ int, a *goquery.Selection) {
		href, ok := a.Attr("href")
		if !ok {
			return
		}
		m := reHylistID.FindStringSubmatch(href)
		if len(m) < 3 {
			return
		}
		if !strings.HasPrefix(href, "http") {
			href = baseURL + href
		}
		id := m[1] + "_" + m[2]
		if seen[id] {
			return
		}
		seen[id] = true

		// Walk up to find the property container (li, tr, article, or class with list/item/bukken)
		container := a.Parent()
		for i := 0; i < 6; i++ {
			cls, _ := container.Attr("class")
			tag := goquery.NodeName(container)
			if strings.Contains(cls, "property") || strings.Contains(cls, "item") ||
				strings.Contains(cls, "bukken") || strings.Contains(cls, "list") ||
				tag == "li" || tag == "tr" || tag == "article" || tag == "section" {
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
