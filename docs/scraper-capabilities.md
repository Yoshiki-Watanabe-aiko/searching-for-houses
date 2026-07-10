# スクレイパー検索条件サポート一覧

**更新日**: 2026-06-20  
**対象スタック**: Go / goquery / go-rod

---

## 1. 基本検索条件サポート

| 条件 | SUUMO | HOMES | ATHOME | GOO | ABLE | MINIMINI | EHEYA | NIFTY | APAMAN | SMOCCA |
|------|:-----:|:-----:|:------:|:---:|:----:|:--------:|:-----:|:-----:|:------:|:------:|
| 都道府県 | ✅ | ✅ | ✅ | ✅ | ❌¹ | ✅ | ✅ | ✅ | ✅ | ❌¹ |
| 市区町村 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 賃料上限 | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ |
| 間取り | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ |
| 徒歩分数上限 | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ |
| 築年数上限 | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ |
| 面積下限 | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 面積上限 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 階数下限 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 礼金なし | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ |
| 敷金なし | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ |
| 設備条件数 | 10 | 0 | 8 | 0 | 19 | 0 | 0 | 44 | 0 | 0 |

> ¹ ABLE・SMOCCA は市区町村指定が必須。都道府県のみでは検索不可（0件になる）。

### エリア検索方式

| サイト | 都道府県のみ | 市区町村指定 | 備考 |
|--------|:----------:|:-----------:|------|
| SUUMO | ✅ | ✅ | 都道府県→JIS2桁+arコード、市区→スラグ |
| HOMES | ✅ | ✅ | 都道府県→スラグ、市区→スラグ |
| ATHOME | ✅ | ✅ | 都道府県→JIS5桁、市区→JIS5桁 |
| GOO | ✅ | ✅ | 都道府県→`g=pref&v=XX`、市区→`g=city&v=XX` |
| ABLE | ❌ | ✅ | 市区JIS5桁必須。都道府県のみ=0件 |
| MINIMINI | ✅ | ✅ | フィルタパラメータは一切なし |
| EHEYA | ✅ | ✅ | フィルタパラメータは一切なし（Playwright） |
| NIFTY | ✅ | ✅ | 最多の条件対応（Playwright） |
| APAMAN | ✅ | ✅ | 都道府県→スラグ、市区→JIS5桁 |
| SMOCCA | ❌ | ✅ | 市区スラグ必須。都道府県のみ=0件（Playwright） |

---

## 2. 設備・条件（features）サポート詳細

各条件コードがどのサイトのURLパラメータに反映されるかの一覧。

| 条件コード | 条件名 | SUUMO | ATHOME | ABLE | NIFTY |
|-----------|--------|:-----:|:------:|:----:|:-----:|
| **位置・向き** |||||
| `LOC_FLOOR_2UP` | 2階以上 | ✅ | ✅ | ✅ | ✅ |
| `LOC_TOP_FLOOR` | 最上階 | ❌ | ❌ | ❌ | ✅ |
| `LOC_CORNER` | 角部屋 | ❌ | ❌ | ❌ | ✅ |
| `LOC_SOUTH_FACING` | 南向き | ❌ | ❌ | ✅ | ✅ |
| **室内設備** |||||
| `INT_LAUNDRY` | 室内洗濯機置場 | ✅ | ✅ | ✅ | ✅ |
| `INT_WASHROOM` | 洗面所独立 | ❌ | ❌ | ❌ | ✅ |
| `INT_FLOORING` | フローリング | ✅ | ✅ | ✅ | ✅ |
| `INT_LOFT` | ロフト | ❌ | ❌ | ❌ | ✅ |
| **冷暖房** |||||
| `HVAC_AC` | エアコン付き | ✅ | ✅ | ✅ | ✅ |
| `HVAC_FLOOR_HEAT` | 床暖房 | ❌ | ❌ | ❌ | ✅ |
| `HVAC_KEROSENE` | 灯油暖房 | ❌ | ❌ | ❌ | ✅ |
| `HVAC_GAS_HEAT` | ガス暖房 | ❌ | ❌ | ❌ | ✅ |
| **バス・トイレ** |||||
| `BATH_SEPARATE` | バス・トイレ別 | ✅ | ✅ | ✅ | ✅ |
| `BATH_WASHLET` | 温水洗浄便座 | ❌ | ❌ | ❌ | ✅ |
| `BATH_DRY` | 浴室乾燥機 | ❌ | ❌ | ❌ | ✅ |
| `BATH_REHEATING` | 追い焚き風呂 | ❌ | ❌ | ❌ | ✅ |
| **キッチン** |||||
| `KITCHEN_GAS` | ガスコンロ対応 | ❌ | ❌ | ❌ | ✅ |
| `KITCHEN_IH` | IHコンロ | ❌ | ❌ | ✅ | ✅ |
| `KITCHEN_2BURNER` | コンロ2口以上 | ❌ | ❌ | ❌ | ✅ |
| `KITCHEN_ALL_ELEC` | オール電化 | ❌ | ❌ | ❌ | ✅ |
| `KITCHEN_SYSTEM` | システムキッチン | ❌ | ❌ | ✅ | ✅ |
| `KITCHEN_COUNTER` | カウンターキッチン | ❌ | ❌ | ❌ | ✅ |
| **建物設備** |||||
| `EQUIP_PARKING` | 駐車場あり | ✅ | ✅ | ✅ | ✅ |
| `EQUIP_BICYCLE` | 駐輪場あり | ❌ | ❌ | ❌ | ✅ |
| `EQUIP_BIKE` | バイク置場 | ❌ | ❌ | ❌ | ✅ |
| `EQUIP_ELEVATOR` | エレベーター | ❌ | ❌ | ✅ | ✅ |
| `EQUIP_DELIVERY_BOX` | 宅配ボックス | ❌ | ❌ | ❌ | ✅ |
| `EQUIP_BALCONY` | バルコニー | ❌ | ❌ | ✅ | ✅ |
| `EQUIP_CITY_GAS` | 都市ガス | ❌ | ❌ | ❌ | ✅ |
| `EQUIP_LPG` | プロパンガス | ❌ | ❌ | ❌ | ✅ |
| `EQUIP_BARRIER_FREE` | バリアフリー | ❌ | ❌ | ❌ | ✅ |
| **セキュリティ** |||||
| `SEC_AUTOLOCK` | オートロック | ✅ | ✅ | ✅ | ✅ |
| `SEC_MONITOR_INTERCOM` | TVモニタ付インターホン | ❌ | ❌ | ❌ | ✅ |
| **テレビ・通信** |||||
| `COMM_NET_AVAILABLE` | ネット接続可 | ❌ | ❌ | ❌ | ✅ |
| `COMM_FIBER` | 光ファイバー | ❌ | ❌ | ❌ | ✅ |
| `COMM_BS` | BSアンテナ | ❌ | ❌ | ✅ | ✅ |
| `COMM_CABLE_TV` | ケーブルTV | ❌ | ❌ | ❌ | ✅ |
| **収納** |||||
| `STORAGE_WIC` | ウォークインクローゼット | ❌ | ❌ | ❌ | ✅ |
| `STORAGE_TRUNK` | トランクルーム | ❌ | ❌ | ❌ | ✅ |
| `STORAGE_UNDER_FLOOR` | 床下収納 | ❌ | ❌ | ❌ | ✅ |
| **入居条件** |||||
| `MOVEIN_PET` | ペット相談可 | ✅ | ✅ | ✅ | ✅ |
| `MOVEIN_FEMALE_ONLY` | 女性限定 | ❌ | ❌ | ✅ | ✅ |
| `MOVEIN_INSTRUMENT` | 楽器相談可 | ❌ | ❌ | ✅ | ✅ |
| `MOVEIN_OFFICE_USE` | 事務所利用可 | ❌ | ❌ | ✅ | ✅ |
| `MOVEIN_ROOMSHARE` | ルームシェア可 | ❌ | ❌ | ❌ | ✅ |
| `MOVEIN_NO_FIXED_TERM` | 定期借家を含まない | ✅ | ❌ | ❌ | ❌ |
| `MOVEIN_IMMEDIATE` | 即入居可 | ❌ | ❌ | ✅ | ❌ |
| **物件特性** |||||
| `FEAT_REFORMED` | リフォーム済み | ❌ | ❌ | ❌ | ✅ |
| `FEAT_DESIGNER` | デザイナーズ | ❌ | ❌ | ❌ | ✅ |
| `FEAT_SUBLEASE` | 分譲賃貸 | ❌ | ❌ | ❌ | ✅ |
| `FEAT_WITH_LAYOUT` | 間取り図付き | ✅ | ❌ | ❌ | ✅ |
| `FEAT_TODAY_NEW` | 本日の新着 | ❌ | ❌ | ❌ | ✅ |
| `FEAT_ONLINE_CONSULT` | オンライン相談可 | ❌ | ❌ | ❌ | ✅ |

> HOMES・GOO・APAMAN・MINIMINI・EHEYA・SMOCCA は設備条件を一切URLに反映しない。

---

## 3. 現在の `chintai_alone.yaml` の分析

### 3.1 全対応サイトで効果が出る条件

| 条件 | 有効サイト |
|------|-----------|
| `prefectures` | SUUMO・HOMES・ATHOME・GOO・MINIMINI・EHEYA・NIFTY・APAMAN |
| `rent_max` | SUUMO・HOMES・ATHOME・GOO・NIFTY・APAMAN |
| `layouts` | SUUMO・HOMES・ATHOME・GOO・NIFTY・APAMAN |
| `walk_minutes_max` | SUUMO・HOMES・ATHOME・GOO・NIFTY・APAMAN |

### 3.2 一部サイトのみで機能する条件

| 条件 | 有効サイト | 無効サイト（条件無視） |
|------|-----------|----------------------|
| `area_min: 30.0` | SUUMO のみ | 他9サイト全て |
| `BATH_SEPARATE` | SUUMO・ATHOME・ABLE・NIFTY | HOMES・GOO・MINIMINI・EHEYA・APAMAN・SMOCCA |
| `INT_LAUNDRY` | SUUMO・ATHOME・ABLE・NIFTY | 同上 |
| `INT_FLOORING` | SUUMO・ATHOME・ABLE・NIFTY | 同上 |
| `HVAC_AC` | SUUMO・ATHOME・ABLE・NIFTY | 同上 |
| `LOC_FLOOR_2UP` | SUUMO・ATHOME・ABLE・NIFTY | 同上 |
| `SEC_AUTOLOCK` | SUUMO・ATHOME・ABLE・NIFTY | 同上 |
| `EQUIP_PARKING` | SUUMO・ATHOME・ABLE・NIFTY | 同上 |
| `MOVEIN_IMMEDIATE` | ABLE のみ | 他9サイト |
| `MOVEIN_INSTRUMENT` | ABLE・NIFTY | 他8サイト |
| `INT_WASHROOM` | NIFTY のみ | 他9サイト |
| `KITCHEN_GAS`, `KITCHEN_2BURNER` | NIFTY のみ | 他9サイト |
| `EQUIP_BICYCLE`, `EQUIP_DELIVERY_BOX`, `EQUIP_CITY_GAS`, `EQUIP_TRASH_24H` | NIFTY のみ | 他9サイト |
| `SEC_MONITOR_INTERCOM` | NIFTY のみ | 他9サイト |
| `COMM_NET_FREE`, `COMM_FIBER` | NIFTY のみ | 他9サイト |

### 3.3 どのサイトにも反映されない条件（未実装）

| 条件コード | 条件名 |
|-----------|--------|
| `STRUCT_RC`, `STRUCT_SRC` | 構造（RC・SRC） |
| `FEAT_RENOVATED` | リノベーション物件 |
| `FEAT_WITH_PHOTO` | 写真付き |
| `STORAGE_SHOE` | シューズボックス |

### 3.4 現状の問題点

1. **ABLE・SMOCCA が 0件**  
   `cities` 未指定のため機能しない。市区町村を追加すれば解消できる。

2. **MINIMINI・EHEYA はフィルタが効かない**  
   賃料・間取り・徒歩分数を指定しても URLパラメータに渡せないため、サイト側でのフィルタが行われない。
   条件外の物件も取得されDBに登録される。

3. **設備条件の多くが NIFTY 専用**  
   `chintai_alone.yaml` 記載の設備条件20項目超のうち、NIFTY 以外では大半が無効。
   ATHOMEでは設備条件はAND検索となるため、条件を重ねすぎると 0件になりやすい。

4. **`area_min` は SUUMO のみ有効**

---

## 4. 汎用性向上のための推奨対応

### 4.1 優先度高：ABLE・SMOCCA を動かす

`chintai_alone.yaml` の `conditions.area` に `cities` を追加する。
市区の正規名は `cities` テーブルの `canonical_name` カラムを参照。

```yaml
conditions:
  area:
    prefectures:
      - "東京都"
    cities:
      - "新宿区"
      - "渋谷区"
      - "港区"
```

### 4.2 優先度中：各スクレイパーへの条件追加実装

現在未実装だが追加可能な条件。

| サイト | 追加可能な条件 |
|--------|--------------|
| HOMES | 礼金なし・敷金なし |
| ATHOME | 礼金なし・敷金なし・面積下限 |
| GOO | 面積下限・礼金なし |
| APAMAN | 礼金なし |
| ABLE | 賃料上限・間取り・徒歩分数・築年数（要パラメータ調査） |
| SMOCCA | 賃料上限・間取り・築年数（要パラメータ調査） |

### 4.3 設備条件の絞り込み推奨（ATHOME 向け）

ATHOME は設備条件が **AND 条件**のため、多く指定すると 0件になる。
現在の `chintai_alone.yaml` では ATHOME に対して以下が同時に指定される：

- `BATH_SEPARATE`, `INT_LAUNDRY`, `INT_FLOORING`, `HVAC_AC`, `SEC_AUTOLOCK`, `LOC_FLOOR_2UP`, `EQUIP_PARKING`

7条件同時AND → 該当物件数が極端に少なくなる可能性がある。  
重要度の低い条件（`INT_FLOORING`, `EQUIP_PARKING` など）を無効化することを推奨。

---

## 5. 実装メモ（スクレイパー別）

| サイト | 方式 | 特記事項 |
|--------|------|----------|
| SUUMO | HTTP + goquery | `ta`（JIS2桁）+ `ar`（地域コード）で都道府県検索 |
| HOMES | HTTP + goquery | 複数都道府県ループ未実装（第1都道府県のみ使用） |
| ATHOME | HTTP + goquery | 設備条件は AND 条件。重ねすぎ注意 |
| GOO | HTTP + goquery | `g=pref` 検索は他府県も混入するため後段で prefecture フィルタ済 |
| ABLE | HTTP + goquery | 市区JIS5桁必須。都道府県のみでは0件 |
| MINIMINI | HTTP + goquery | Shift-JIS エンコーディング対応済。フィルタなし |
| EHEYA | Playwright (go-rod) | フィルタなし。Chrome 要インストール |
| NIFTY | Playwright (go-rod) | 最多の検索条件対応（44条件）。Chrome 要インストール |
| APAMAN | Playwright (go-rod) | 市区URLは `/kensaku/list/?search_type=area&target[0]={code}` 形式 |
| SMOCCA | Playwright (go-rod) | 市区スラグ必須。礼金なし・敷金なしのみ対応 |
