# InsightPDF — 学术 PDF 简体中文翻译

将学术 PDF 翻译为简体中文，保留布局、公式、表格与图表。

## Current baseline

    base

这是一个 **shared architecture baseline**（共享架构基线），
**不是** final visual production release，
也**不是** final flow production release。

版本命名与分支规则见 [README_VERSIONING.md](README_VERSIONING.md)。

## 已包含的能力

- PDF → HTML middleware pipeline
- Direct layout path（Chromium PDF 渲染）
- PageModel / DocumentModel
- DocumentSemanticState
- Formula ownership / atomic SVG
- Table handling
- MathDenseTranslationRouter（数学密集正文保护翻译）
- balanced typography
- Physical PDF QA
- fail-closed QA execution gate

## 已知问题（简述）

- dense formula PDFs still expose render-boundary issues
- fixed-layout visual alignment remains future work
- flow-layout architecture remains future work

## 运行

```bash
python -m tools.page_pipeline.run_document \
  --pdf <input.pdf> \
  --out <output-dir> \
  --config runs/config.json \
  --typography-profile outputs/phase4d2a_typography_audit/balanced_chinese_profile.json
```

翻译后端为 DeepSeek；API key 通过环境变量 `DEEPSEEK_API_KEY` 提供
（或写入本机 `runs/config.json`，该文件已被 `.gitignore` 排除）。

参考 `.env.example` 配置本机环境变量，不要提交真实 key。
