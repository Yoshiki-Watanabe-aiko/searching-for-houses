package db

import (
	"context"
	"crypto/sha256"
	"fmt"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"house-notifier/internal/model"
)

func GetSiteID(ctx context.Context, pool *pgxpool.Pool, code string) (int, error) {
	var id int
	err := pool.QueryRow(ctx, "SELECT id FROM site_master WHERE code=$1", code).Scan(&id)
	if err != nil {
		return 0, fmt.Errorf("site %q not found: %w", code, err)
	}
	return id, nil
}

func GetPropertyTypeID(ctx context.Context, pool *pgxpool.Pool, code string) (int, error) {
	var id int
	err := pool.QueryRow(ctx, "SELECT id FROM property_type_master WHERE code=$1", code).Scan(&id)
	if err != nil {
		return 0, fmt.Errorf("property_type %q not found: %w", code, err)
	}
	return id, nil
}

func UpsertProperty(
	ctx context.Context,
	pool *pgxpool.Pool,
	siteID, propTypeID int,
	s *model.ScrapedProperty,
) (*model.UpsertResult, error) {
	addrHash := computeAddressHash(s)

	var existingID int
	var existingPrice *int64
	var existingStatus string
	err := pool.QueryRow(ctx,
		`SELECT id, price, status FROM properties WHERE site_id=$1 AND external_id=$2`,
		siteID, s.ExternalID,
	).Scan(&existingID, &existingPrice, &existingStatus)

	isNew := err == pgx.ErrNoRows
	if err != nil && !isNew {
		return nil, fmt.Errorf("select property: %w", err)
	}

	isRelisted := !isNew && existingStatus != string(model.StatusActive)
	priceChanged := !isNew && !pricesEqual(existingPrice, s.Price)

	if isNew {
		var newID int
		err = pool.QueryRow(ctx, `
			INSERT INTO properties
				(site_id, property_type_id, external_id, url, title, price,
				 area_sqm, layout, address, station_info, age_years, floor_num,
				 image_url, address_hash, status)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,'active')
			RETURNING id`,
			siteID, propTypeID, s.ExternalID, s.URL, s.Title, s.Price,
			s.AreaSqm, s.Layout, s.Address, s.StationInfo, s.AgeYears, s.FloorNum,
			s.ImageURL, addrHash,
		).Scan(&newID)
		if err != nil {
			return nil, fmt.Errorf("insert property: %w", err)
		}
		return &model.UpsertResult{
			ID: newID, IsNew: true, CurrentPrice: s.Price,
		}, nil
	}

	prevPriceVal := existingPrice
	if !priceChanged {
		prevPriceVal = nil
	}

	_, err = pool.Exec(ctx, `
		UPDATE properties SET
			url          = $1,
			title        = $2,
			price_prev   = CASE WHEN price IS DISTINCT FROM $3 THEN price ELSE price_prev END,
			price        = $3,
			area_sqm     = $4,
			layout       = $5,
			address      = $6,
			station_info = $7,
			age_years    = $8,
			floor_num    = $9,
			image_url    = $10,
			address_hash = $11,
			status       = 'active',
			last_seen_at = NOW(),
			updated_at   = NOW()
		WHERE id = $12`,
		s.URL, s.Title, s.Price, s.AreaSqm, s.Layout, s.Address,
		s.StationInfo, s.AgeYears, s.FloorNum, s.ImageURL, addrHash, existingID,
	)
	if err != nil {
		return nil, fmt.Errorf("update property: %w", err)
	}

	return &model.UpsertResult{
		ID:           existingID,
		IsNew:        false,
		IsRelisted:   isRelisted,
		PriceChanged: priceChanged,
		PrevPrice:    prevPriceVal,
		CurrentPrice: s.Price,
	}, nil
}

func GetActiveProperties(ctx context.Context, pool *pgxpool.Pool) ([]*model.Property, error) {
	rows, err := pool.Query(ctx, `
		SELECT p.id, p.site_id, sm.code, p.property_type_id,
		       p.external_id, p.url, p.title, p.price, p.price_prev,
		       p.area_sqm, p.layout, p.address, p.station_info,
		       p.age_years, p.floor_num, p.image_url, p.address_hash,
		       p.status, p.last_seen_at, p.first_seen_at, p.updated_at
		FROM properties p
		JOIN site_master sm ON sm.id = p.site_id
		WHERE p.status = 'active'
		ORDER BY p.id`)
	if err != nil {
		return nil, fmt.Errorf("query active properties: %w", err)
	}
	defer rows.Close()

	var props []*model.Property
	for rows.Next() {
		p := &model.Property{}
		err := rows.Scan(
			&p.ID, &p.SiteID, &p.SiteCode, &p.PropertyTypeID,
			&p.ExternalID, &p.URL, &p.Title, &p.Price, &p.PricePrev,
			&p.AreaSqm, &p.Layout, &p.Address, &p.StationInfo,
			&p.AgeYears, &p.FloorNum, &p.ImageURL, &p.AddressHash,
			&p.Status, &p.LastSeenAt, &p.FirstSeenAt, &p.UpdatedAt,
		)
		if err != nil {
			return nil, fmt.Errorf("scan property: %w", err)
		}
		props = append(props, p)
	}
	return props, rows.Err()
}

func UpdatePropertyStatus(ctx context.Context, pool *pgxpool.Pool, id int, status model.PropertyStatus) error {
	_, err := pool.Exec(ctx,
		`UPDATE properties SET status=$1, updated_at=NOW() WHERE id=$2`,
		string(status), id,
	)
	return err
}

func GetPropertyByID(ctx context.Context, pool *pgxpool.Pool, id int) (*model.Property, error) {
	p := &model.Property{}
	err := pool.QueryRow(ctx, `
		SELECT p.id, p.site_id, sm.code, p.property_type_id,
		       p.external_id, p.url, p.title, p.price, p.price_prev,
		       p.area_sqm, p.layout, p.address, p.station_info,
		       p.age_years, p.floor_num, p.image_url, p.address_hash,
		       p.status, p.last_seen_at, p.first_seen_at, p.updated_at
		FROM properties p
		JOIN site_master sm ON sm.id = p.site_id
		WHERE p.id = $1`, id,
	).Scan(
		&p.ID, &p.SiteID, &p.SiteCode, &p.PropertyTypeID,
		&p.ExternalID, &p.URL, &p.Title, &p.Price, &p.PricePrev,
		&p.AreaSqm, &p.Layout, &p.Address, &p.StationInfo,
		&p.AgeYears, &p.FloorNum, &p.ImageURL, &p.AddressHash,
		&p.Status, &p.LastSeenAt, &p.FirstSeenAt, &p.UpdatedAt,
	)
	if err != nil {
		return nil, fmt.Errorf("get property %d: %w", id, err)
	}
	return p, nil
}

func pricesEqual(a, b *int64) bool {
	if a == nil && b == nil {
		return true
	}
	if a == nil || b == nil {
		return false
	}
	return *a == *b
}

func computeAddressHash(s *model.ScrapedProperty) string {
	parts := []string{s.Address, s.Layout}
	if s.AreaSqm != nil {
		parts = append(parts, fmt.Sprintf("%.2f", *s.AreaSqm))
	}
	h := sha256.Sum256([]byte(strings.Join(parts, "|")))
	return fmt.Sprintf("%x", h)
}
