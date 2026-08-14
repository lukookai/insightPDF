# BASELINE MANIFEST

冻结时的基线信息。

## Baseline name

    base

## 描述

Shared architecture baseline before:
- fixed-canvas visual alignment development（视觉对齐开发）
- continuous flow-layout development（流式排版开发）

`base` 是 **shared architecture baseline**，不是 final visual production release，
也不是 final flow production release。

## 已包含的能力（base 冻结时）

- PDF → HTML middleware pipeline
- Direct layout path（Chromium PDF 渲染）
- PageModel / DocumentModel
- DocumentSemanticState
- Formula ownership / atomic SVG（FormulaOwnershipGraph）
- Table pipeline
- MathDenseTranslationRouter（数学密集正文保护翻译）
- balanced typography
- Physical PDF QA
- fail-closed QA execution gate（QAExecutionManifest / QAIntegrityGate）

旧 Phase 报告与实验产物不进入本仓库（见 `.gitignore`：outputs/ 等）。

## Branches derived from base

    visual
    flow

## Versioning policy

    base
    visual-v01 ~ visual-v99
    flow-v01   ~ flow-v99

禁止新的 A / B / C 版本编号。详见 [README_VERSIONING.md](README_VERSIONING.md)。

## Main pipeline entry

    python -m tools.page_pipeline.run_document

（另见 `tools/page_pipeline/` 下的各 QA 模块与编排器。）

## Environment

- Python: 3.11 / 3.13（开发环境），翻译后端 DeepSeek（`DEEPSEEK_API_KEY` 环境变量，见 `.env.example`）
- 关键依赖见各子目录 requirements / 说明

## 冻结记录

- date: 2026-08-14
- git commit SHA: 见 `GIT_BASELINE_REPORT.md`（base commit，tag `base` 唯一指向）
- baseline tag: base

> 本文件不记录 API key、用户名、本机绝对路径等私人信息。
> 旧 OCR API key 视为 compromised，已从新历史中移除；请用户在 provider 后台 revoke / rotate。
