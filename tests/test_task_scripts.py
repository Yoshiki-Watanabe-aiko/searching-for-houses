"""タスク登録スクリプトと実行スクリプトの整合を機械的に固定する（課題#49 Step 8）。

⚠ **タスクを足すときは3箇所を同時に直す必要がある**（登録の `TaskArg`・実行側の
`ValidateSet`・`switch` の分岐）。どれか1つを忘れても登録自体は通り、
**実行されるまで気づけない**。四半期タスクなら最大3ヶ月先になる。

⚠ `tests/test_scraper_protocol.py`（アダプタの必須メソッド）と同じ考え方で、
「増やしたときに黙って穴が空く」ものを突き合わせている。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REGISTER = SCRIPTS / "register_tasks.ps1"
RUNNER = SCRIPTS / "task_runner.ps1"


def _read(path: Path) -> str:
    # ⚠ .ps1 は BOM 付き UTF-8（PowerShell 5.1 は BOM が無いと cp932 で読む）
    return path.read_text(encoding="utf-8-sig")


@pytest.fixture(scope="module")
def register_text() -> str:
    return _read(REGISTER)


@pytest.fixture(scope="module")
def runner_text() -> str:
    return _read(RUNNER)


def _registered_args(text: str) -> list[str]:
    return re.findall(r'TaskArg\s*=\s*"([^"]+)"', text)


def _validate_set(text: str) -> set[str]:
    block = re.search(r"\[ValidateSet\((.*?)\)\]", text, re.S)
    assert block, "task_runner.ps1 の ValidateSet が読めません"
    return set(re.findall(r'"([^"]+)"', block.group(1)))


def _switch_branches(text: str) -> set[str]:
    body = text.split("switch ($Task) {", 1)
    assert len(body) == 2, "task_runner.ps1 の switch が読めません"
    return set(re.findall(r'^\s{4}"([a-z-]+)"\s', body[1], re.M))


def test_登録するタスクは実行側が受け付ける(register_text: str, runner_text: str) -> None:
    """⚠ ValidateSet に無い値を渡すと実行時に落ちる（登録は通ってしまう）。"""
    missing = set(_registered_args(register_text)) - _validate_set(runner_text)

    assert not missing, f"task_runner.ps1 の ValidateSet に無いタスク: {sorted(missing)}"


def test_受け付けるタスクには実行内容がある(runner_text: str) -> None:
    """⚠ switch に分岐が無いと $steps が空のまま「成功」で終わる（黙って何もしない）。"""
    missing = _validate_set(runner_text) - _switch_branches(runner_text)

    assert not missing, f"switch に分岐が無いタスク: {sorted(missing)}"


def test_実行内容のあるタスクは受け付ける(runner_text: str) -> None:
    """逆向き。分岐だけ足して ValidateSet を忘れると、そのタスクは起動できない。"""
    extra = _switch_branches(runner_text) - _validate_set(runner_text)

    assert not extra, f"ValidateSet に無いのに分岐だけあるタスク: {sorted(extra)}"


def test_委譲先のスクリプトが実在する(runner_text: str) -> None:
    """⚠ backup / market-rates 系は別の .ps1 へ委譲する。名前を間違えると実行時に落ちる。"""
    for name in re.findall(r"Join-Path \$PSScriptRoot '([^']+\.ps1)'", runner_text):
        assert (SCRIPTS / name).exists(), f"委譲先が見つかりません: {name}"


NIGHTLY = SCRIPTS / "run_market_then_drain.ps1"


def test_夜間バッチの委譲先が実在する() -> None:
    """相場の更新 → 売買の掃き出しを順に呼ぶ。⚠ 名前を間違えると実行時に落ちる。"""
    text = _read(NIGHTLY)
    names = re.findall(r'Join-Path \$PSScriptRoot "([^"]+\.ps1)"', text)

    assert names, "委譲先が読めません（呼び出しの書き方が変わった？）"
    for name in names:
        assert (SCRIPTS / name).exists(), f"委譲先が見つかりません: {name}"


def test_夜間バッチは掃き出しを同期実行で呼ぶ() -> None:
    """⚠⚠ `-Worker` を付けないと `run_initial_scan.ps1` はランチャーとして働き、

    Start-Process で切り離して**即座に戻る**。すると相場の直後に掃き出しが
    起動して取得ロックの取り合いになり、片方が「他の取得処理が実行中」で
    何もせずに終わる（⚠ 終了コードは 0 なので気づけない）。
    """
    text = _read(NIGHTLY)

    assert '$drainArgs = @("-Worker"' in text, "掃き出しの呼び出しに -Worker が無い"


def test_売買相場は毎月チェックする(register_text: str) -> None:
    """課題#49 Step 8 → 2026-09-09 に四半期から毎月へ（ユーザー判断）。

    ⚠ 国交省の公開は四半期終了後2〜3ヶ月で時期が読めない。四半期に絞ると
    公開から**最大3ヶ月**反映が遅れる。毎月見に行き、新しい四半期が無ければ
    CSVもDBも触らずに終わる（空振りは正常であってエラーではない）。
    """
    assert "HouseSearch-BuyMarketRates" in register_text
    assert "buy-market-rates" in _registered_args(register_text)
    # ⚠ 四半期に絞る書き方（Months を4つだけ並べる）へ戻っていないこと
    assert "<January /><April /><July /><October />" not in register_text
    assert "MonthlyDay  = 2" in register_text


def test_家賃相場の上限は全国取得に足りる(register_text: str) -> None:
    """⚠ 全国化で所要が約5分 → 約97分になった（2026-09-09）。

    ⚠ 上限で強制終了されると終了コードが取れず後処理も飛ぶ
    （→ 課題#26 で check-sold が 267014 で切られた実例がある）。
    """
    block = re.search(
        r'Name\s*=\s*"HouseSearch-MarketRates".*?TimeLimit\s*=\s*"([^"]+)"', register_text, re.S
    )
    assert block, "MarketRates の TimeLimit が読めません"

    assert block.group(1) == "PT3H", "全国取得（約97分）に対して上限が短すぎます"


def test_相場の更新タスクどうしは同じ日に走らない(register_text: str) -> None:
    """⚠ どちらも m_market_rates へ書き込むうえ、家賃相場は全国で約97分かかる。"""
    starts = dict(
        zip(
            re.findall(r'Name\s*=\s*"([^"]+)"', register_text),
            re.findall(r'StartAt\s*=\s*"([^"]+)"', register_text),
            strict=True,
        )
    )
    rent_day = starts["HouseSearch-MarketRates"].split("T")[0].split("-")[2]
    buy_day = starts["HouseSearch-BuyMarketRates"].split("T")[0].split("-")[2]

    assert rent_day != buy_day, "家賃相場と売買相場の更新日が同じです"


def test_取得を伴うタスクは無効で登録される(register_text: str) -> None:
    """⚠ 国交省APIを叩くので取得タスク扱い（-EnableScraping の対象に入れる）。"""
    block = re.search(r'Name\s*=\s*"HouseSearch-BuyMarketRates".*?TimeLimit', register_text, re.S)
    assert block and "Scraping    = $true" in block.group(0)
