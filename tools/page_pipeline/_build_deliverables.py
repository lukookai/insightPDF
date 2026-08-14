# -*- coding: utf-8 -*-
"""Build outputs/deliverables_phase4c2r1/ from outputs/phase4c2r1_run/."""
import json, shutil, sys
from pathlib import Path

sys.path.insert(0, "tools/page_pipeline")
from heading_duplicate_qa import heading_duplicate_qa
from residual_source_language_qa import residual_source_language_qa
from formula_crop_qa import formula_crop_qa

SRC = Path("outputs/phase4c2r1_run")
OLD = Path("outputs/phase4c2r_run")
DST = Path("outputs/deliverables_phase4c2r1")
DST.mkdir(parents=True, exist_ok=True)

# 1. top-level files
for name in ("full_zh_preview.pdf", "full_zh_debug_preview.pdf",
             "delivery_gate.json", "document_report.json", "defect_pages.json",
             "complexity_pages.json", "page_assertion_details.json",
             "token_split_taxonomy.json", "preflight.json"):
    p = SRC / name
    if p.exists():
        shutil.copy2(p, DST / name)

shutil.copy2(SRC / "PHASE4C2R1_REPORT.md", DST / "PHASE4C2R1_REPORT.md")

# 2. fixture pages (p011 heading dup, p014 residual + formula crop)
fixture_dir = DST / "fixture_evidence"
fixture_dir.mkdir(parents=True, exist_ok=True)
fixture_evidence = {}

def qa_on(old_dir, fn, name):
    model = json.loads((old_dir / "stitched_page_model.json").read_text(encoding="utf-8"))
    pdf = old_dir / "zh.pdf"
    out = fixture_dir / old_dir.name
    out.mkdir(parents=True, exist_ok=True)
    if fn is residual_source_language_qa:
        r = fn(model, str(pdf), out_dir=str(out))
    else:
        r = fn(model, str(pdf), out_dir=str(out))
    return r

# OLD p011 -> HeadingDuplicateQA must FAIL
old = OLD / "pages" / "p011"
r = qa_on(old, heading_duplicate_qa, "heading")
fixture_evidence["p011_heading_duplicate_qa"] = {
    "page": 11, "expected": "FAIL", "clean": r["heading_qa_clean"],
    "count": r["heading_duplicate_count"],
    "details": r["heading_duplicate_details"][:4]}
print("fixture p011 heading_dup: clean=%s count=%d" % (r["heading_qa_clean"], r["heading_duplicate_count"]))

# OLD p014 -> ResidualLanguageQA must FAIL (", and")
old = OLD / "pages" / "p014"
r = qa_on(old, residual_source_language_qa, "residual")
fixture_evidence["p014_residual_qa"] = {
    "page": 14, "expected": "FAIL", "clean": r["residual_clean"],
    "short_tokens": r["short_residual_token_count"],
    "details": r["untranslated_residual_token_details"][:4]}
print("fixture p014 residual: clean=%s short=%d" % (r["residual_clean"], r["short_residual_token_count"]))

# OLD p014 -> FormulaCropQA must FAIL (B11/B12)
r = qa_on(old, formula_crop_qa, "crop")
fixture_evidence["p014_formula_crop_qa"] = {
    "page": 14, "expected": "FAIL", "clean": r["formula_crop_clean"],
    "violations": r["formula_crop_violation_count"],
    "details": r["formula_crop_details"][:6]}
print("fixture p014 crop: clean=%s violations=%d" % (r["formula_crop_clean"], r["formula_crop_violation_count"]))

(fixture_dir / "fixture_fail_evidence.json").write_text(
    json.dumps(fixture_evidence, ensure_ascii=False, indent=1), encoding="utf-8")

# 3. key pages' QA detail files (p011/p014) into pages/
pages_dst = DST / "pages"
for pno in (11, 14):
    src_dir = SRC / "pages" / ("p%03d" % pno)
    dst_dir = pages_dst / ("p%03d" % pno)
    dst_dir.mkdir(parents=True, exist_ok=True)
    for name in ("heading_duplicate_qa.json", "formula_crop_qa.json",
                 "residual_source_language_qa.json",
                 "rendered_region_collision_qa.json",
                 "formula_exclusivity_qa.json", "physical_qa.json"):
        p = src_dir / name
        if p.exists():
            shutil.copy2(p, dst_dir / name)

# 4. README
readme = """# Phase 4C.2R.1 交付物 — 残留清理

**delivery_gate = pass** | 三项硬计数全 0：heading_duplicate_count=0 / short_residual_token_count=0 / formula_crop_violation_count=0 | coverage=1.0 | 19 物理页

## 文件
- `full_zh_preview.pdf` — 正式交付版（19 物理页，仅 PASS 后生成）
- `full_zh_debug_preview.pdf` — 调试预览（含全部页）
- `PHASE4C2R1_REPORT.md` — 完成报告（根因链 + 修复 + 证据）
- `delivery_gate.json` / `document_report.json` / `defect_pages.json` / `complexity_pages.json` / `page_assertion_details.json` / `token_split_taxonomy.json` / `preflight.json`
- `pages/p011/`, `pages/p014/` — 关键页 QA 明细（heading_duplicate / formula_crop / residual / collision / exclusivity / physical）
- `fixture_evidence/` — 旧产物判红证据（p011 标题重复、p014 短残留 token、p014 公式裁剪，均为 FAIL）

## 本阶段修复内容
1. HeadingDuplicateQA（新）：单行标题重复语义 token 判定（列过滤 + y 窗口 + 编号前缀 + 大偏移 fallback）；p011 标题 BOLD 拆分去重清理。
2. ResidualLanguageQA：短连接词 token 计入（para_scoped + span 层检测）；AUTHOR_OPEN_RE 重写（杀 5 个散文误判 + AUTHORLINE_RE 保作者行）；微连接词确定性翻译（`, and`→`，以及`）。
3. FormulaCropQA（新）+ render_viewbox 修复：每段 crop 覆盖全部组件 ink（收养条件词/方程号），安全边距仅 ink 近边界时加 1.5pt；孤儿归同行段。
4. 连锁修复：收养跳过 mono CodeRun；flow 避障盒 = viewBox 并集；exclusivity QA 段落侧改用渲染坐标 + 词边界。

## 像素验证（源 vs 最终）
- p014 "otherwise" 完整 bbox ink：1482 → 1483（1:1）；原被裁右缘条带：235 → 218
- p003 B10 cases 条件区：1496 → 17380（条件行可见）
- p011 方程号 "(15)"：0 → 568（源 584）
"""
(DST / "README.md").write_text(readme, encoding="utf-8")

print("deliverables written to", DST)
for f in sorted(DST.iterdir()):
    print("  ", f.name)
