package config

import (
	"fmt"
	"os"
	"path/filepath"

	"gopkg.in/yaml.v3"
)

type SearchConfig struct {
	Name           string           `yaml:"name"`
	PropertyType   string           `yaml:"property_type"`
	SiteIDs        []string         `yaml:"site_ids"`
	DiscordWebhook string           `yaml:"discord_webhook"`
	Conditions     SearchConditions `yaml:"conditions"`
}

type SearchConditions struct {
	Area     AreaCondition     `yaml:"area"`
	Price    PriceCondition    `yaml:"price"`
	Building BuildingCondition `yaml:"building"`
	Features []string          `yaml:"features"`
}

type AreaCondition struct {
	Prefectures    []string `yaml:"prefectures"`
	Cities         []string `yaml:"cities"`
	Stations       []string `yaml:"stations"`
	WalkMinutesMax *int     `yaml:"walk_minutes_max"`
}

type PriceCondition struct {
	RentMax       *int64  `yaml:"rent_max"`
	PriceMax      *int64  `yaml:"price_max"`
	IncludeMgmtFee bool   `yaml:"include_mgmt_fee"`
	NoReikin       bool   `yaml:"no_reikin"`
	NoShikikin     bool   `yaml:"no_shikikin"`
}

type BuildingCondition struct {
	Layouts  []string `yaml:"layouts"`
	AreaMin  *float64 `yaml:"area_min"`
	AreaMax  *float64 `yaml:"area_max"`
	AgeMax   *int     `yaml:"age_max"`
	FloorMin *int     `yaml:"floor_min"`
}

func LoadSearchConfigs(dir string) ([]*SearchConfig, []error) {
	pattern := filepath.Join(dir, "*.yaml")
	files, err := filepath.Glob(pattern)
	if err != nil {
		return nil, []error{fmt.Errorf("glob configs: %w", err)}
	}

	var configs []*SearchConfig
	var errs []error
	for _, f := range files {
		cfg, err := loadSearchConfig(f)
		if err != nil {
			errs = append(errs, fmt.Errorf("load %s: %w", f, err))
			continue
		}
		configs = append(configs, cfg)
	}
	return configs, errs
}

func loadSearchConfig(path string) (*SearchConfig, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var cfg SearchConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, err
	}
	if cfg.Name == "" {
		return nil, fmt.Errorf("name is required")
	}
	if cfg.PropertyType == "" {
		return nil, fmt.Errorf("property_type is required")
	}
	if cfg.DiscordWebhook == "" {
		return nil, fmt.Errorf("discord_webhook is required")
	}
	return &cfg, nil
}
