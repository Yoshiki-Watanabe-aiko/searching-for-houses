package db

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"house-notifier/internal/model"
)

func RecordNotification(
	ctx context.Context,
	pool *pgxpool.Pool,
	propertyID int,
	patternName string,
	notifType model.NotificationType,
	price *int64,
) error {
	_, err := pool.Exec(ctx, `
		INSERT INTO notifications (property_id, pattern_name, notification_type, price_at_notify)
		VALUES ($1, $2, $3, $4)`,
		propertyID, patternName, string(notifType), price,
	)
	if err != nil {
		return fmt.Errorf("record notification: %w", err)
	}
	return nil
}

func GetLastNotification(
	ctx context.Context,
	pool *pgxpool.Pool,
	propertyID int,
	patternName string,
	notifType model.NotificationType,
) (*model.Notification, error) {
	n := &model.Notification{}
	err := pool.QueryRow(ctx, `
		SELECT id, property_id, pattern_name, notification_type, price_at_notify, notified_at
		FROM notifications
		WHERE property_id=$1 AND pattern_name=$2 AND notification_type=$3
		ORDER BY notified_at DESC
		LIMIT 1`,
		propertyID, patternName, string(notifType),
	).Scan(&n.ID, &n.PropertyID, &n.PatternName, &n.NotificationType, &n.PriceAtNotify, &n.NotifiedAt)

	if err == pgx.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("get last notification: %w", err)
	}
	return n, nil
}

func HasSoldNotification(ctx context.Context, pool *pgxpool.Pool, propertyID int, patternName string) (bool, error) {
	var count int
	err := pool.QueryRow(ctx, `
		SELECT COUNT(*) FROM notifications
		WHERE property_id=$1 AND pattern_name=$2 AND notification_type='sold'`,
		propertyID, patternName,
	).Scan(&count)
	if err != nil {
		return false, fmt.Errorf("check sold notification: %w", err)
	}
	return count > 0, nil
}
