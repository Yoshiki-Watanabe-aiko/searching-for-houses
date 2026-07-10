package scraper

import (
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/PuerkitoBio/goquery"
)

var defaultHTTPClient = &http.Client{
	Timeout: 30 * time.Second,
}

var defaultHeaders = map[string]string{
	"User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
	"Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
	"Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

func FetchHTML(url string) (*goquery.Document, error) {
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}
	for k, v := range defaultHeaders {
		req.Header.Set(k, v)
	}

	resp, err := defaultHTTPClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("http get %s: %w", url, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == 404 {
		return nil, ErrNotFound
	}
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("http status %d for %s", resp.StatusCode, url)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read body: %w", err)
	}

	doc, err := goquery.NewDocumentFromReader(io.NopCloser(
		func() io.Reader {
			return newBytesReader(body)
		}(),
	))
	if err != nil {
		return nil, fmt.Errorf("parse html: %w", err)
	}
	return doc, nil
}

func CheckURLExists(url string) (bool, error) {
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return false, err
	}
	for k, v := range defaultHeaders {
		req.Header.Set(k, v)
	}
	resp, err := defaultHTTPClient.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case 200:
		return true, nil
	case 404:
		return false, nil
	default:
		return false, fmt.Errorf("unexpected status %d", resp.StatusCode)
	}
}

var ErrNotFound = fmt.Errorf("page not found (404)")

type bytesReader struct {
	data []byte
	pos  int
}

func newBytesReader(data []byte) *bytesReader {
	return &bytesReader{data: data}
}

func (r *bytesReader) Read(p []byte) (n int, err error) {
	if r.pos >= len(r.data) {
		return 0, io.EOF
	}
	n = copy(p, r.data[r.pos:])
	r.pos += n
	return n, nil
}
