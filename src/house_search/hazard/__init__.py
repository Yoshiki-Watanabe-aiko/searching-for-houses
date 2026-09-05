"""ハザード評価（洪水・土砂災害）の同期と参照。

⚠ ポリゴンの交差計算は ``scripts/tools/build_hazard_levels.py``（オフライン）で
終わっている。このパッケージが扱うのは集計済みの数値だけで、幾何ライブラリには
依存しない（→ 課題#46・``tests/test_no_geo_runtime_deps.py``）。
"""
