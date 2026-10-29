# 可选期刊目录

`journals.example.csv` 只有虚构示例，说明投稿管理的 CSV 格式，不会自动导入。

仓库不包含个人提供的真实期刊目录、论文或文献。可在投稿管理界面预览并导入自己的 CSV。
本地可选的 `CSSCI_2025-2026.csv` 会在首次启动时自动导入；缺失时从空目录开始，不影响使用。
该真实目录已由 `.gitignore` 排除，开发测试使用临时生成的虚构数据。

`english_journals.json.gz` 是“英文期刊库”的可选数据（JCR 2025 / 中科院 2025 / 新锐 2026，约 2.3 万本）。它由 `scripts/build_english_journals.py` 从 ShowJCR 的 `jcr.db` 生成，体积较大且来自第三方数据源，按仓库边界由 `.gitignore` 排除；缺失时“英文期刊库”页面提示数据缺失，不影响其它功能，打包时由 `--add-data "backend/data;backend/data"` 带入。
