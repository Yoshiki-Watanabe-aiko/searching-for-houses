# ============================================================
# 運用スクリプト共通: 出力を UTF-8 に揃える
#
# ⚠ PowerShell 5.1 は自分の出力（Write-Output）を cp932 で書くが、子の python は
#   UTF-8 で書く（house_search.console.force_utf8_output）。標準出力をファイルへ
#   リダイレクトした運用スクリプトでは、揃えないと**同じログの中でエンコーディングが
#   混在し、どちらかが必ず化ける**（▶ は cp932 に無く「?」になる）。
#   2026-09-11 に「隠し起動＋リダイレクト」で実測した（指定なしだと混在・指定ありで全体 UTF-8）。
# ⚠ BOM は付けない（ログの先頭に余計なバイトが入る）。
# ⚠ ログを読むときは Get-Content -Encoding UTF8（PowerShell 5.1 の既定は cp932）。
#
# 使い方（ドットソースしてから呼ぶ）:
#   . (Join-Path $PSScriptRoot "lib\utf8_output.ps1")
#   Set-Utf8ConsoleOutput
# ============================================================

function Set-Utf8ConsoleOutput {
    try {
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    } catch {
        # 失敗しても処理は止めない（ログが cp932 と UTF-8 の混在になるだけで、取得や採点は壊れない）
        Write-Output "[warn] 出力エンコーディングを UTF-8 にできませんでした（cp932 のまま続行）: $_"
    }
}
