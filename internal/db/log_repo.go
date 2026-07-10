package db

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

func LogInfo(ctx context.Context, pool *pgxpool.Pool, siteCode, patternName, message string, detail interface{}) {
	writeLog(ctx, pool, "INFO", siteCode, patternName, message, detail)
}

func LogWarn(ctx context.Context, pool *pgxpool.Pool, siteCode, patternName, message string, detail interface{}) {
	writeLog(ctx, pool, "WARN", siteCode, patternName, message, detail)
}

func LogError(ctx context.Context, pool *pgxpool.Pool, siteCode, patternName, message string, detail interface{}) {
	writeLog(ctx, pool, "ERROR", siteCode, patternName, message, detail)
}

func writeLog(ctx context.Context, pool *pgxpool.Pool, level, siteCode, patternName, message string, detail interface{}) {
	var detailJSON []byte
	if detail != nil {
		b, err := json.Marshal(detail)
		if err == nil {
			detailJSON = b
		}
	}

	sitePtr := &siteCode
	if siteCode == "" {
		sitePtr = nil
	}
	patternPtr := &patternName
	if patternName == "" {
		patternPtr = nil
	}

	_, err := pool.Exec(ctx, `
		INSERT INTO scrape_logs (level, site_code, pattern_name, message, detail)
		VALUES ($1, $2, $3, $4, $5)`,
		level, sitePtr, patternPtr, message, detailJSON,
	)
	if err != nil {
		fmt.Printf("[LOG ERROR] failed to write log: %v\n", err)
	}
}
