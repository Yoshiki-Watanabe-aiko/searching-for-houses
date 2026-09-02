"""掲載の駅表記から駅グループを同定する（純関数・DBにもネットワークにも触らない）。

入力は ``t_listings.station_info`` に保存済みの原文。**アダプタには手を入れない**。
規則を直したら ``resolve-stations`` で作り直せる（``raw_features_text`` からの
設備再抽出と同じ考え方）。

⚠ **サイトごとの表記のばらつきが本質的な難しさ**である。実データ（2026-09-03）:

- SUUMO      ``ＪＲ中央線/八王子駅 バス18分 (バス停)滝山城址 歩2分``
- ABLE       ``東武野田線<アーバンパークライン>/川間駅 徒歩5分``（区切りは空白）
- APAMAN     ``ＪＲ総武本線 千葉駅/バス乗車30分/千葉中央バス㈱ 大和田入口/徒歩2分``
- HOME'S     ``JR内房線 青堀駅 バス4分 下安知郡下車 徒歩2分``
- いい部屋   ``みなとみらい線 元町・中華街（山下公園）駅 バス8分 本牧１丁目 徒歩4分``
- ニフティ   ``指扇駅 バス8分 歩4分 （川越線）``
- goo        ``ＪＲ外房線本納駅徒歩8000m``（**路線名と駅名が地続き**）
- スモッカ   ``東京地下鉄千代田線/北綾瀬 徒歩8分つくばエクスプレス/六町 徒歩14分``
  （**「駅」が付かず区切りも無い**）
- 賃貸EX     ``高尾 バス6分 元八王子2丁目バス停から徒歩4分八王子 バス28分``（同上）

⚠ **バス停名を駅として拾わないこと**が最大の落とし穴。バス停には「栄町」「一之江五丁目」
のように駅名と紛らわしいものが実在する。そこで**先にバス停部分を消してから**駅名を拾う。

⚠ **「駅」というアンカーの有無で確度が違う**ので扱いを分ける。「◯◯駅」で拾えた候補は
同定に失敗しても記録する（規則を改善する材料になる）が、アンカーの無い第2パスは推測なので
**マスタに一致したものだけ**を採る。そうしないと ``unmatched`` がノイズで埋まる。

⚠ **都道府県で絞ると曖昧さが激減する。** 1都3県をまとめて引くと「小川町」（埼玉/東京）
「霞ケ関」（埼玉/東京）「永田」（千葉/埼玉）「平和台」（東京/千葉）「入谷」（東京/神奈川）が
すべて曖昧になるが、掲載の所在地で絞れば一意に決まる。実測で ambiguous は168件→4件になった。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from house_search.commute.normalize import normalize_key

MATCH_MATCHED = "matched"
MATCH_AMBIGUOUS = "ambiguous"
MATCH_UNMATCHED = "unmatched"

# 駅名に使われない区切り・囲み文字。駅名候補の境界になる。
_BOUNDARY = r"\s　/／<>「」（）()、,"

# バス停らしき部分を消すパターン。**適用順に意味がある**
# （「下車」で終わる語を先に消さないと、その後のパターンが飲み込んでしまう）。
_BUS_SEGMENTS = (
    # APAMAN: ＪＲ総武本線 千葉駅/バス乗車30分/千葉中央バス㈱ 大和田入口/徒歩2分
    re.compile(r"バス乗車\d+分/[^/]*/"),
    # APAMAN: 「海09：海老名」のような系統名つきのバス停
    re.compile(rf"[^{_BOUNDARY}]*[：:][^{_BOUNDARY}]*"),
    # SUUMO / スモッカ / 賃貸EX: (バス停)関宿中央ターミナル
    re.compile(rf"\(バス停\)[^{_BOUNDARY}]*"),
    # goo: 「品の木・ハイランドホテル前」バス停
    re.compile(r"「[^」]*」バス停"),
    # 賃貸EX: 元八王子2丁目バス停から徒歩4分（「から」まで消す。後ろを飲み込ませない）
    re.compile(rf"[^{_BOUNDARY}]*バス停(?:から)?"),
    # HOME'S: 大堀1丁目下車
    re.compile(rf"[^{_BOUNDARY}]*下車"),
    # いい部屋ネット: バス8分 本牧１丁目 徒歩4分（バス分数と徒歩の間に挟まる）
    re.compile(rf"バス\d+分\s+[^{_BOUNDARY}]+\s+(?=徒歩|歩)"),
)

# 駅名の直前にある補足の括弧。いい部屋ネットの「元町・中華街（山下公園）駅」など。
# ここで外しておかないと「駅」の直前が「）」になり第1パスが拾えない。
_PAREN_BEFORE_STATION = re.compile(r"[（(][^）)]{0,24}[）)](?=駅)")

# 第1パス。「◯◯駅」を拾う。中黒は駅名の一部になりうる（元町・中華街 / 大塚・帝京大学）ので、
# 区切りではなく駅名の構成文字として扱う。
_WITH_SUFFIX = re.compile(rf"([^{_BOUNDARY}・]{{1,24}}(?:・[^{_BOUNDARY}・]{{1,24}})*)駅")

# 第2パス。「駅」が付かないサイト向けに、時間表記の直前のトークンを拾う。
# 区切りが無く連結されるサイトがあるため「分」も境界として認める
# （「…徒歩4分八王子 バス28分」から「八王子」を取り出せる）。
_BEFORE_TIME = re.compile(
    rf"(?:^|[{_BOUNDARY}]|分)([^{_BOUNDARY}0-9０-９]{{1,24}})\s*(?=バス\d|徒歩|歩\d|車\d)"
)

# 候補の先頭に紛れ込む時間表記（賃貸EXの「歩10分小岩」のような連結表記）。
_LEADING_TIME = re.compile(r"^(?:徒歩|歩|バス|車)\d+分")
# 候補の先頭に紛れ込む路線名（gooの「ＪＲ外房線本納」のような地続き表記）。
# 貪欲マッチで**最後の**路線語まで飲ませる（「ＪＲ外房線」→「本納」）。
_LINE_PREFIX = re.compile(r"^.*(?:線|鉄道|ライナー|エクスプレス|モノレール|新交通)")


@dataclass(frozen=True)
class StationMatch:
    """掲載の駅表記1件の同定結果。"""

    position: int
    raw_name: str
    station_g_cd: int | None
    match_status: str


@dataclass(frozen=True)
class StationIndex:
    """照合用の索引。正規化キー → 駅グループコードの集合。

    ⚠ **必ず対象の都道府県に絞ってから作る。** 同名異駅（日本橋＝東京/大阪、
    府中＝東京/広島）が実在し、全国で引くと一意に決まらない。

    さらに掲載ごとの都道府県が分かるなら ``lookup`` に渡す。県内で引けたら
    それを採り、引けなければスコープ全体へ落とす（県境の物件は隣県の駅を挙げる）。
    """

    by_key: Mapping[str, frozenset[int]]
    by_pref_key: Mapping[tuple[int, str], frozenset[int]]

    @classmethod
    def build(cls, rows: Iterable[tuple[str, int, int]]) -> StationIndex:
        """``(station_name_key, station_g_cd, pref_cd)`` の並びから索引を作る。"""
        by_key: dict[str, set[int]] = {}
        by_pref: dict[tuple[int, str], set[int]] = {}
        for key, group_code, pref_cd in rows:
            by_key.setdefault(key, set()).add(group_code)
            by_pref.setdefault((pref_cd, key), set()).add(group_code)
        return cls(
            by_key={key: frozenset(codes) for key, codes in by_key.items()},
            by_pref_key={key: frozenset(codes) for key, codes in by_pref.items()},
        )

    def lookup(self, name_key: str, pref_cd: int | None) -> frozenset[int]:
        """正規化キーから駅グループの候補を引く。"""
        if pref_cd is not None:
            found = self.by_pref_key.get((pref_cd, name_key))
            if found:
                return found
        return self.by_key.get(name_key, frozenset())


def mask_bus_stops(station_info: str) -> str:
    """バス停・降車地の表記を消す。駅名を拾う前に必ず通す。"""
    masked = station_info
    for pattern in _BUS_SEGMENTS:
        masked = pattern.sub(" ", masked)
    return masked


def extract_station_names(station_info: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """駅名の候補を ``(第1パス, 第2パス)`` で返す。

    第1パスは「◯◯駅」というアンカーがあるので確度が高い。第2パスは
    アンカーが無く推測になるため、呼び出し側でマスタに当たったものだけを採る。
    """
    masked = mask_bus_stops(station_info)
    masked = _PAREN_BEFORE_STATION.sub("", masked)
    return _dedupe(_WITH_SUFFIX.findall(masked)), _dedupe(_BEFORE_TIME.findall(masked))


def _dedupe(names: Iterable[str]) -> tuple[str, ...]:
    """出現順を保ったまま重複を除く。"""
    seen: dict[str, None] = {}
    for name in names:
        stripped = name.strip()
        if stripped:
            seen.setdefault(stripped, None)
    return tuple(seen)


def candidate_variants(name: str) -> tuple[str, ...]:
    """1つの候補から、余計な接頭辞を剥がした派生を作る。

    ⚠ **原文を最初に試す**のが要点。「鉄道博物館」は路線名の接頭辞を剥がす規則に
    引っかかって「博物館」になってしまうが、原文で先にマスタに当たるので壊れない。
    """
    variants: list[str] = []
    for value in (
        name,
        _LEADING_TIME.sub("", name),
        _LINE_PREFIX.sub("", name),
        _LINE_PREFIX.sub("", _LEADING_TIME.sub("", name)),
    ):
        stripped = value.strip()
        if stripped and stripped not in variants:
            variants.append(stripped)
    return tuple(variants)


def _classify(
    position: int, name: str, index: StationIndex, pref_cd: int | None
) -> StationMatch:
    ambiguous: frozenset[int] | None = None
    for variant in candidate_variants(name):
        groups = index.lookup(normalize_key(variant), pref_cd)
        if len(groups) == 1:
            return StationMatch(position, variant, next(iter(groups)), MATCH_MATCHED)
        if len(groups) > 1 and ambiguous is None:
            ambiguous = groups
    if ambiguous is not None:
        # 路線名で絞ることはしない。都道府県で絞った後に残るのは
        # 徒歩数分の距離にある同名駅（浅草・早稲田・弘明寺）だけで、
        # 絞り込みの実装コストに見合わない（→ ADR 0016）。
        return StationMatch(position, name, None, MATCH_AMBIGUOUS)
    return StationMatch(position, name, None, MATCH_UNMATCHED)


def match_stations(
    station_info: str | None, index: StationIndex, pref_cd: int | None = None
) -> tuple[StationMatch, ...]:
    """掲載の駅表記を同定する。``pref_cd`` は掲載の所在都道府県コード。"""
    if not station_info or not station_info.strip():
        return ()
    with_suffix, before_time = extract_station_names(station_info)
    if with_suffix:
        return tuple(
            _classify(i, name, index, pref_cd) for i, name in enumerate(with_suffix)
        )

    matched: list[StationMatch] = []
    for name in before_time:
        result = _classify(len(matched), name, index, pref_cd)
        if result.match_status == MATCH_MATCHED:
            matched.append(result)
    return tuple(matched)
