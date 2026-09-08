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


def test_売買相場の四半期タスクが登録される(register_text: str) -> None:
    """課題#49 Step 8。⚠ 家賃相場（毎月1日）と同じ日に置かない。"""
    assert "HouseSearch-BuyMarketRates" in register_text
    assert "buy-market-rates" in _registered_args(register_text)
    # 1/4/7/10月の2日（タスクスケジューラに「四半期ごと」の区分は無いので月で表す）
    assert "<January /><April /><July /><October />" in register_text
    assert "<DaysOfMonth><Day>2</Day></DaysOfMonth>" in register_text


def test_相場の更新タスクどうしは同じ日に走らない(register_text: str) -> None:
    """⚠ どちらも m_market_rates を全置換するので並走させない。"""
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
    block = re.search(
        r'Name\s*=\s*"HouseSearch-BuyMarketRates".*?TimeLimit', register_text, re.S
    )
    assert block and "Scraping    = $true" in block.group(0)
