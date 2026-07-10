package db

import (
	"context"

	"github.com/jackc/pgx/v5/pgxpool"
)

// ResolveCities は canonical_name のスライスを、複数の都道府県にまたがって
// サイト固有の検索値スライスに変換する。重複は除外し、未登録は nil を返す。
// 同じ canonical_name が複数の都道府県に存在する場合は両方の値を返す。
func ResolveCities(ctx context.Context, pool *pgxpool.Pool, prefectures []string, canonicalNames []string, siteCode string) []string {
	col := siteCodeToColumn(siteCode)
	if col == "" || len(canonicalNames) == 0 {
		return nil
	}
	seen := make(map[string]bool)
	var values []string
	q := `SELECT ` + col + ` FROM cities c JOIN city_site_mappings csm ON csm.city_id = c.id WHERE c.prefecture = $1 AND c.canonical_name = $2`
	for _, name := range canonicalNames {
		for _, pref := range prefectures {
			var val *string
			_ = pool.QueryRow(ctx, q, pref, name).Scan(&val)
			if val != nil && *val != "" && !seen[*val] {
				seen[*val] = true
				values = append(values, *val)
			}
		}
	}
	return values
}

func siteCodeToColumn(siteCode string) string {
	switch siteCode {
	case "SUUMO":
		return "csm.suumo"
	case "HOMES":
		return "csm.homes"
	case "ATHOME":
		return "csm.athome"
	case "GOO":
		return "csm.goo"
	case "ABLE":
		return "csm.able"
	case "MINIMINI":
		return "csm.minimini"
	case "EHEYA":
		return "csm.eheya"
	case "NIFTY":
		return "csm.nifty"
	case "APAMAN":
		return "csm.apaman"
	case "SMOCCA":
		return "csm.smocca"
	default:
		return ""
	}
}