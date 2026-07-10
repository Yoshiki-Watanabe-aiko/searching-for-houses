package scraper

import (
	"fmt"
	"strings"
	"time"

	"github.com/PuerkitoBio/goquery"
	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/launcher"
	"github.com/go-rod/rod/lib/proto"
)

func NewBrowser() (*rod.Browser, error) {
	u, err := launcher.New().Headless(true).Leakless(false).Launch()
	if err != nil {
		return nil, fmt.Errorf("launch browser: %w", err)
	}
	b := rod.New().ControlURL(u)
	if err := b.Connect(); err != nil {
		return nil, fmt.Errorf("connect browser: %w", err)
	}
	return b, nil
}

func FetchHTMLWithBrowser(browser *rod.Browser, url string) (*goquery.Document, error) {
	page, err := browser.Page(proto.TargetCreateTarget{URL: url})
	if err != nil {
		return nil, fmt.Errorf("open page %s: %w", url, err)
	}
	defer page.Close()

	if err := page.WaitLoad(); err != nil {
		return nil, fmt.Errorf("wait load: %w", err)
	}
	// Wait for network to settle so SPAs finish rendering their content
	page.WaitRequestIdle(2*time.Second, nil, nil, nil)()

	html, err := page.HTML()
	if err != nil {
		return nil, fmt.Errorf("get html: %w", err)
	}

	doc, err := goquery.NewDocumentFromReader(strings.NewReader(html))
	if err != nil {
		return nil, fmt.Errorf("parse html: %w", err)
	}
	return doc, nil
}

func CheckURLExistsWithBrowser(browser *rod.Browser, url string) (bool, error) {
	page, err := browser.Page(proto.TargetCreateTarget{URL: url})
	if err != nil {
		return false, err
	}
	defer page.Close()

	if err := page.WaitLoad(); err != nil {
		return false, err
	}

	info, err := page.Info()
	if err != nil {
		return false, fmt.Errorf("page info: %w", err)
	}

	u := info.URL
	if strings.Contains(u, "404") || strings.Contains(u, "not_found") ||
		strings.Contains(u, "close") || strings.Contains(u, "end") {
		return false, nil
	}

	t := info.Title
	if strings.Contains(t, "成約") || strings.Contains(t, "掲載終了") ||
		strings.Contains(t, "削除") || strings.Contains(t, "お探しの物件") {
		return false, nil
	}

	return true, nil
}
