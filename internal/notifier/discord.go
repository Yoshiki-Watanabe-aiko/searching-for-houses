package notifier

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"house-notifier/internal/model"
)

const sendInterval = 2 * time.Second

type Discord struct {
	client *http.Client
}

func NewDiscord() *Discord {
	return &Discord{client: &http.Client{Timeout: 15 * time.Second}}
}

type embed struct {
	Title       string    `json:"title"`
	URL         string    `json:"url"`
	Description string    `json:"description,omitempty"`
	Color       int       `json:"color"`
	Fields      []field   `json:"fields,omitempty"`
	Thumbnail   *thumb    `json:"thumbnail,omitempty"`
	Timestamp   string    `json:"timestamp"`
}

type field struct {
	Name   string `json:"name"`
	Value  string `json:"value"`
	Inline bool   `json:"inline"`
}

type thumb struct {
	URL string `json:"url"`
}

type message struct {
	Embeds []embed `json:"embeds"`
}

var notifColor = map[model.NotificationType]int{
	model.NotificationNew:       0x57F287,
	model.NotificationSold:      0xED4245,
	model.NotificationPriceDown: 0x5865F2,
	model.NotificationPriceUp:   0xFEE75C,
}

var notifLabel = map[model.NotificationType]string{
	model.NotificationNew:       "🆕 新着物件",
	model.NotificationSold:      "🏷️ 成約/掲載終了",
	model.NotificationPriceDown: "📉 値下がり",
	model.NotificationPriceUp:   "📈 値上がり",
}

var siteDisplayName = map[string]string{
	"SUUMO":    "SUUMO",
	"HOMES":    "LIFULL HOME'S",
	"ATHOME":   "アットホーム",
	"GOO":      "goo不動産",
	"ABLE":     "エイブル",
	"MINIMINI": "minimini",
	"EHEYA":    "いい部屋ネット",
	"NIFTY":    "ニフティ不動産",
	"APAMAN":   "アパマンショップ",
	"SMOCCA":   "スモッカ",
}

func (d *Discord) Send(webhookURL string, prop *model.Property, notifType model.NotificationType) error {
	e := buildEmbed(prop, notifType)
	msg := message{Embeds: []embed{e}}

	body, err := json.Marshal(msg)
	if err != nil {
		return fmt.Errorf("marshal embed: %w", err)
	}

	resp, err := d.client.Post(webhookURL, "application/json", bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("post discord: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == 429 {
		return fmt.Errorf("discord rate limited (429)")
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("discord status %d", resp.StatusCode)
	}

	time.Sleep(sendInterval)
	return nil
}

func (d *Discord) SendError(webhookURL, message string, err error) {
	if webhookURL == "" {
		return
	}
	text := fmt.Sprintf("```\n%s\n%v\n```", message, err)
	e := embed{
		Title:     "⚠️ エラー発生",
		Color:     0xFF0000,
		Description: text,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}
	msg := struct {
		Embeds []embed `json:"embeds"`
	}{Embeds: []embed{e}}
	body, _ := json.Marshal(msg)
	resp, httpErr := d.client.Post(webhookURL, "application/json", bytes.NewReader(body))
	if httpErr == nil {
		resp.Body.Close()
	}
}

func buildEmbed(prop *model.Property, notifType model.NotificationType) embed {
	label := notifLabel[notifType]
	color := notifColor[notifType]

	title := label
	if prop.Title != "" {
		title = label + " | " + prop.Title
	}

	var fields []field

	if prop.Price != nil {
		priceStr := formatYen(*prop.Price)
		if (notifType == model.NotificationPriceDown || notifType == model.NotificationPriceUp) && prop.PricePrev != nil {
			diff := *prop.Price - *prop.PricePrev
			sign := "+"
			if diff < 0 {
				sign = ""
			}
			priceStr += fmt.Sprintf(" (%s%s から変動)", sign, formatYen(diff))
		}
		fields = append(fields, field{Name: "💰 価格", Value: priceStr, Inline: true})
	}
	if prop.Layout != "" {
		fields = append(fields, field{Name: "🏠 間取り", Value: prop.Layout, Inline: true})
	}
	if prop.AreaSqm != nil {
		fields = append(fields, field{Name: "📐 面積", Value: fmt.Sprintf("%.2f㎡", *prop.AreaSqm), Inline: true})
	}
	if prop.Address != "" {
		fields = append(fields, field{Name: "📍 住所", Value: prop.Address, Inline: false})
	}
	if prop.StationInfo != "" {
		fields = append(fields, field{Name: "🚃 アクセス", Value: prop.StationInfo, Inline: false})
	}
	if prop.AgeYears != nil {
		fields = append(fields, field{Name: "🏗️ 築年数", Value: fmt.Sprintf("築%d年", *prop.AgeYears), Inline: true})
	}
	if prop.SiteCode != "" {
		name := prop.SiteCode
		if n, ok := siteDisplayName[prop.SiteCode]; ok {
			name = n
		}
		fields = append(fields, field{Name: "🌐 掲載サイト", Value: name, Inline: true})
	}

	e := embed{
		Title:     title,
		URL:       prop.URL,
		Color:     color,
		Fields:    fields,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}
	if prop.ImageURL != "" {
		e.Thumbnail = &thumb{URL: prop.ImageURL}
	}
	return e
}

func formatYen(v int64) string {
	if v < 0 {
		return "-" + formatYen(-v)
	}
	if v >= 100_000_000 {
		return fmt.Sprintf("%.1f億円", float64(v)/100_000_000)
	}
	if v >= 10_000 {
		return fmt.Sprintf("%.1f万円", float64(v)/10_000)
	}
	return fmt.Sprintf("%d円", v)
}
