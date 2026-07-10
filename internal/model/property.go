package model

import "time"

type NotificationType string

const (
	NotificationNew       NotificationType = "new"
	NotificationSold      NotificationType = "sold"
	NotificationPriceUp   NotificationType = "price_up"
	NotificationPriceDown NotificationType = "price_down"
)

type PropertyStatus string

const (
	StatusActive  PropertyStatus = "active"
	StatusSold    PropertyStatus = "sold"
	StatusRemoved PropertyStatus = "removed"
)

type Property struct {
	ID             int
	SiteID         int
	SiteCode       string
	PropertyTypeID int
	ExternalID     string
	URL            string
	Title          string
	Price          *int64
	PricePrev      *int64
	AreaSqm        *float64
	Layout         string
	Address        string
	StationInfo    string
	AgeYears       *int
	FloorNum       *int
	ImageURL       string
	AddressHash    string
	Status         PropertyStatus
	LastSeenAt     time.Time
	FirstSeenAt    time.Time
	UpdatedAt      time.Time
}

type ScrapedProperty struct {
	ExternalID  string
	URL         string
	Title       string
	Price       *int64
	AreaSqm     *float64
	Layout      string
	Address     string
	StationInfo string
	AgeYears    *int
	FloorNum    *int
	ImageURL    string
}

type UpsertResult struct {
	ID           int
	IsNew        bool
	IsRelisted   bool
	PriceChanged bool
	PrevPrice    *int64
	CurrentPrice *int64
}

type Notification struct {
	ID               int
	PropertyID       int
	PatternName      string
	NotificationType NotificationType
	PriceAtNotify    *int64
	NotifiedAt       time.Time
}
