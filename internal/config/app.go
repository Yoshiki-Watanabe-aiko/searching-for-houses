package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/joho/godotenv"
)

type AppConfig struct {
	DatabaseURL         string
	ErrorDiscordWebhook string
	FullScanMaxPages    int
	ConfigsDir          string
}

func LoadAppConfig() (*AppConfig, error) {
	_ = godotenv.Load(".env")

	dbURL := os.Getenv("DATABASE_URL")
	if dbURL == "" {
		return nil, fmt.Errorf("DATABASE_URL is required")
	}
	dbURL = strings.Replace(dbURL, "postgresql+psycopg2://", "postgresql://", 1)

	maxPages := 5
	if v := os.Getenv("FULL_SCAN_MAX_PAGES"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			maxPages = n
		}
	}

	configsDir := os.Getenv("CONFIGS_DIR")
	if configsDir == "" {
		exe, err := os.Executable()
		if err != nil {
			configsDir = "configs"
		} else {
			configsDir = filepath.Join(filepath.Dir(exe), "configs")
		}
	}

	return &AppConfig{
		DatabaseURL:         dbURL,
		ErrorDiscordWebhook: os.Getenv("ERROR_DISCORD_WEBHOOK"),
		FullScanMaxPages:    maxPages,
		ConfigsDir:          configsDir,
	}, nil
}
