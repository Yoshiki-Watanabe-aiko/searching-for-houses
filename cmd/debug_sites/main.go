package main

import (
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/PuerkitoBio/goquery"
	"golang.org/x/text/encoding/japanese"
	"golang.org/x/text/transform"
)

func fetch(url string) (int, string) {
	c := &http.Client{Timeout: 15 * time.Second}
	req, _ := http.NewRequest("GET", url, nil)
	req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
	req.Header.Set("Accept-Language", "ja-JP,ja;q=0.9")
	resp, err := c.Do(req)
	if err != nil { return 0, "" }
	body, _ := io.ReadAll(resp.Body); resp.Body.Close()
	return resp.StatusCode, string(body)
}

func fetchSJIS(url string) (int, string) {
	c := &http.Client{Timeout: 15 * time.Second}
	req, _ := http.NewRequest("GET", url, nil)
	req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
	req.Header.Set("Accept-Language", "ja-JP,ja;q=0.9")
	resp, err := c.Do(req)
	if err != nil { return 0, "" }
	defer resp.Body.Close()
	body, _ := io.ReadAll(transform.NewReader(resp.Body, japanese.ShiftJIS.NewDecoder()))
	return resp.StatusCode, string(body)
}

func clsList(root *goquery.Selection, max int) {
	root.Find("[class]").Each(func(i int, s *goquery.Selection) {
		if i >= max { return }
		c, _ := s.Attr("class"); txt := strings.TrimSpace(s.Text())
		if len(txt) > 50 { txt = txt[:50] }
		fmt.Printf("  .%s → %q\n", c, txt)
	})
}

func main() {
	// MINIMINI bukken structure
	fmt.Println("=== MINIMINI ===")
	_, mmHTML := fetchSJIS("https://minimini.jp/list/?pref=13&dp=1")
	mmDoc, _ := goquery.NewDocumentFromReader(strings.NewReader(mmHTML))
	fmt.Printf("万円:%d bukken:%d\n", strings.Count(mmHTML,"万円"), mmDoc.Find("[class*=bukken]").Length())
	mmDoc.Find("[class*=bukken]").First().Each(func(_ int, s *goquery.Selection) {
		cls, _ := s.Attr("class")
		fmt.Printf("first bukken: class=%s\n", cls)
		clsList(s, 20)
	})
	// Also check list items
	mmDoc.Find("li[class]").First().Each(func(_ int, s *goquery.Selection) {
		cls, _ := s.Attr("class")
		fmt.Printf("first li: class=%s\n", cls)
		clsList(s, 15)
	})
	// Links to property pages
	mmDoc.Find("a[href]").Each(func(i int, s *goquery.Selection) {
		href, _ := s.Attr("href")
		if strings.Contains(href, "minimini") && strings.Contains(href, "/detail") {
			txt := strings.TrimSpace(s.Text())
			if len(txt) > 30 { txt = txt[:30] }
			fmt.Printf("  detail: %s → %q\n", href, txt)
		}
	})

	// GOO - investigate property elements
	fmt.Println("\n=== GOO property elements ===")
	_, gooHTML := fetch("https://house.goo.ne.jp/rent/?pref=13&eki=10&madori[]=1LDK&madori[]=2K&madori[]=2DK&yachin_max=20&chiku_max=15&page=1")
	gooDoc, _ := goquery.NewDocumentFromReader(strings.NewReader(gooHTML))
	// find the property class
	gooDoc.Find("[class*=property]").First().Each(func(_ int, s *goquery.Selection) {
		cls, _ := s.Attr("class"); fmt.Printf("first property: %s\n", cls)
		clsList(s, 15)
	})
	// Links with chintai or rent
	cnt := 0
	gooDoc.Find("a[href]").Each(func(i int, s *goquery.Selection) {
		href, _ := s.Attr("href")
		if (strings.Contains(href, "chintai") || strings.Contains(href, "/rent/bukken/")) && cnt < 5 {
			txt := strings.TrimSpace(s.Text())
			if len(txt) > 40 { txt = txt[:40] }
			fmt.Printf("  %s → %q\n", href, txt)
			cnt++
		}
	})

	// ABLE: try with Referer header
	fmt.Println("\n=== ABLE with headers ===")
	c := &http.Client{Timeout: 15 * time.Second}
	ableURLs := []string{
		"https://www.able.co.jp/chintai/list/",
		"https://www.able.co.jp/chintai/tokyo/",
	}
	for _, u := range ableURLs {
		req, _ := http.NewRequest("GET", u, nil)
		req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
		req.Header.Set("Referer", "https://www.able.co.jp/")
		req.Header.Set("Accept-Language", "ja-JP,ja;q=0.9")
		resp, err := c.Do(req)
		if err != nil { fmt.Printf("  ERR %s: %v\n", u, err); continue }
		body, _ := io.ReadAll(resp.Body); resp.Body.Close()
		m := strings.Count(string(body), "万円")
		fmt.Printf("  %d 万円:%d %s\n", resp.StatusCode, m, u)
	}
	
	// HOMES: check price/layout within unit
	fmt.Println("\n=== HOMES unit detail ===")
	_, homesHTML := fetch("https://www.homes.co.jp/chintai/tokyo/list/?bkz=1LDK&bkz=2K&bkz=2DK&prct=20&prch=0&ckzn=15&exn=10&page=1")
	homesDoc, _ := goquery.NewDocumentFromReader(strings.NewReader(homesHTML))
	firstBldg := homesDoc.Find("[class*=mod-mergeBuilding]").First()
	// All li elements in unit area
	fmt.Printf("unitList li count: %d\n", firstBldg.Find(".unitList li, .unitListBody li").Length())
	firstBldg.Find(".unitList li, .unitListBody li").Each(func(i int, s *goquery.Selection) {
		if i > 10 { return }
		cls, _ := s.Attr("class"); txt := strings.TrimSpace(s.Text())
		if len(txt) > 60 { txt = txt[:60] }
		fmt.Printf("  li.%s → %q\n", cls, txt)
	})
	// price & layout text
	fmt.Printf("  .price text: %q\n", strings.TrimSpace(firstBldg.Find(".price").First().Text())[:80])
	fmt.Printf("  .layout text: %q\n", strings.TrimSpace(firstBldg.Find(".layout").First().Text()))
}