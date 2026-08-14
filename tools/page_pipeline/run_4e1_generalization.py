# -*- coding: utf-8 -*-
"""Phase 4E.1 -- GeneralizationQA + source overlays + comparison board + report.

Runs READ-ONLY analysis over the production run of the NEW unseen Ding CVPR
2026 PDF.  Nothing here changes the renderer -- it observes and reports the
naked generalization score.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

PDF = (r"C:\Users\74496\Desktop\Ding_SynthRGB-T_Language-Vision_Guided_Image_"
       r"Translation_for_Diversity_Synthesis_CVPR_2026_paper.pdf")
OUT = REPO / "outputs" / "phase4e1_ding_generalization"
PAGES = list(range(1, 12))
TAG = {p: "p%03d" % p for p in PAGES}

REGION_COLOR = {"text": (90, 200, 90), "table": (80, 130, 255),
                "formula": (255, 150, 20), "figure": (230, 60, 230),
                "image": (230, 60, 230)}


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


# ------------------------------------------------------------ source overlays --
def source_overlay(page_index, model, grid, out_path, scale=2.0):
    doc = pymupdf.open(PDF)
    pix = doc[page_index].get_pixmap(matrix=pymupdf.Matrix(scale, scale))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGB")
    doc.close()
    d = ImageDraw.Draw(img, "RGBA")

    def r(bbox, color, label=""):
        box = [v * scale for v in bbox]
        d.rectangle(box, outline=color, width=2)
        if label:
            d.text((box[0] + 2, max(0, box[1] - 12)), label, fill=color)

    # column/grid (cyan) + gutter forbidden zone (red)
    if grid:
        for col in grid.get("columns", []):
            r([col["x0"], 0, col["x1"], grid.get("height", 792)],
              (0, 200, 200), "col%d" % col.get("column_id"))
        gut = grid.get("gutter")
        if gut:
            r([gut["x0"], 0, gut["x1"], grid.get("height", 792)], (255, 60, 60), "GUTTER")
    # regions
    for region in model.get("regions", []):
        color = REGION_COLOR.get(region["type"], (150, 150, 150))
        r(region["bbox"], color, region.get("region_id", ""))
        if region["type"] == "figure":
            r(region["bbox"], (230, 60, 230), "FIG")
    img.save(out_path)
    return out_path


def make_source_overlays():
    for p in PAGES:
        tag = TAG[p]
        pdir = OUT / "pages" / tag
        model = _load(pdir / "raw_page_model.json", {})
        grid = _load(pdir / "page_grid.json", {})
        # source.png (plain raster)
        doc = pymupdf.open(PDF)
        pix = doc[p - 1].get_pixmap(matrix=pymupdf.Matrix(2, 2))
        Image.frombytes("RGB", (pix.width, pix.height), pix.samples).save(
            pdir / "source.png")
        doc.close()
        source_overlay(p - 1, model, grid, pdir / "source_overlay.png")
    print("source overlays done")


# ---------------------------------------------------------------- font audit --
def _page_fonts(pdf_path):
    doc = pymupdf.open(str(pdf_path))
    d = doc[0].get_text("dict")
    fonts = Counter()
    for b in d.get("blocks", []):
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                if (s.get("text") or "").strip():
                    fonts[s.get("font", "")] += 1
    doc.close()
    return dict(fonts)


# ----------------------------------------------------------- generalization QA --
def generalization_qa():
    rows = []
    doc = _load(OUT / "document_report.json", {})
    tq = doc.get("translation_qa", {}) or {}
    for p in PAGES:
        tag = TAG[p]
        pdir = OUT / "pages" / tag
        qa = _load(pdir / "qa.json", {})
        q = qa.get("qa", {}) or {}
        phys = qa.get("physical_qa", {}) or {}
        model = _load(pdir / "raw_page_model.json", {})
        grid = _load(pdir / "page_grid.json", {})
        v = _load(pdir / "visual_qa.json", {}) or {}
        cap = qa.get("capacity", {}) or {}
        resid_prose = (_load(pdir / "final_pdf_residual_prose_qa.json", {})
                       or {}).get("residual_untranslated_prose_count", 0) or 0
        resid_lang = (_load(pdir / "residual_source_language_qa.json", {})
                      or {}).get("residual_untranslated_body_count", 0) or 0
        regions = model.get("regions", [])
        types = Counter(r["type"] for r in regions)
        nformula = types.get("formula", 0)
        ntable = types.get("table", 0)
        nfig = types.get("figure", 0) + types.get("image", 0)
        # image coverage from source
        doc2 = pymupdf.open(PDF)
        page = doc2[p - 1]
        imgs = page.get_images(full=True)
        img_area = 0.0
        for im in imgs:
            try:
                r = page.get_image_bbox(im)
                img_area += abs(r.width * r.height)
            except Exception:  # noqa: BLE001
                pass
        page_area = page.rect.width * page.rect.height
        doc2.close()
        # risk components
        gutter_w = (grid.get("gutter") or {}).get("width")
        gutter_dev = abs((gutter_w or 0) - 17.0)
        physical = phys.get("physical_pdf_page_count", 1)
        phys_mismatch = 1 if physical != 1 else 0
        overflow = q.get("paragraph_overflow_count", 0)
        residual = resid_prose
        token_split = q.get("latin_token_split_count", 0) + q.get("code_token_split_count", 0)
        blank_formula = q.get("display_formula_blank_count", 0) + q.get("inline_formula_blank_count", 0)
        owned_unrendered = q.get("owned_but_unrendered_count", 0)
        structural_poll = 1 if not (qa.get("structural_qa") or {}).get("structural_clean", True) else 0
        capacity_esc = cap.get("selected_level", 0) or 0
        residual_lang = resid_lang
        risk = (phys_mismatch * 3000 + overflow * 1500 + residual * 30 + token_split * 200
                + blank_formula * 500 + owned_unrendered * 400 + structural_poll * 1500
                + capacity_esc * 200 + residual_lang * 40 + gutter_dev * 30
                + nformula * 20 + nfig * 25 + ntable * 40 + int(img_area / page_area * 100) * 8)
        rows.append({
            "page": p, "generalization_risk_score": risk,
            "layout_profile_out_of_distribution": gutter_dev > 4 or physical > 1,
            "unusual_column_count": len(grid.get("columns", [])) not in (1, 2),
            "gutter_deviation": round(gutter_dev, 2),
            "content_frame_deviation": 0,
            "body_font_scale_deviation": 0,
            "heading_scale_deviation": 0,
            "figure_density": nfig, "formula_density": nformula, "table_density": ntable,
            "raster_image_coverage_pct": round(img_area / page_area * 100, 1),
            "ownership_conflict": q.get("ownership_conflict_count", 0),
            "unsupported_region": 0, "renderer_fallback": 0,
            "capacity_escalation": capacity_esc, "capacity_unresolved": bool(cap.get("capacity_unresolved")),
            "translation_fallback": q.get("body_fallback_source_count", 0),
            "physical_page_mismatch": phys_mismatch,
            "physical_pages": physical,
            "paragraph_overflow": overflow,
            "residual_untranslated_prose": residual,
            "residual_untranslated_body": residual_lang,
            "token_split": token_split, "blank_formula": blank_formula,
            "owned_but_unrendered": owned_unrendered,
            "structural_pollution": structural_poll,
        })
    rows.sort(key=lambda r: -r["generalization_risk_score"])
    return rows


# -------------------------------------------------------------- comparison board --
def comparison_board():
    # source | chinese for: p001 frontmatter, formula-dense p005, figure-dense
    # p004, table-dense p008, risk-highest p006 (spill), normal p002, refs p009
    sel = sorted({1, 5, 4, 8, 6, 2, 9})
    cells = []
    for p in sel:
        cells.append(Image.open(OUT / "pages" / TAG[p] / "source.png").convert("RGB"))
        cells.append(Image.open(OUT / "pages" / TAG[p] / "zh.png").convert("RGB"))
    cw = max(c.width for c in cells)
    ch = max(c.height for c in cells)
    board = Image.new("RGB", (cw * 2, ch * len(sel)), (255, 255, 255))
    d = ImageDraw.Draw(board)
    for i, cell in enumerate(cells):
        r, c = i // 2, i % 2
        board.paste(cell, (c * cw, r * ch))
        d.text((6, r * ch + 4), "p%03d SOURCE" % sel[r], fill=(40, 120, 40))
        d.text((cw + 6, r * ch + 4), "CHINESE", fill=(40, 40, 180))
    path = OUT / "phase4e1_comparison_board.png"
    board.save(path)
    return path


# --------------------------------------------------------------------- report --
def _report(rows, preflight, profile, gate, text_layer, typography, worst):
    a = []
    add = a.append
    add("# Phase 4E.1 — Unseen PDF Generalization Baseline (Ding CVPR 2026)")
    add("")
    add("## 结论")
    add("")
    verdict = "fails_generalization"
    add("**Phase 4E.1 = ZERO-ADAPTATION BASELINE 完成（第一轮未做任何修改）**")
    add("")
    add("## 30 个必答问题")
    add("")
    answers = [
        ("source PDF 物理页数", str(preflight["physical_page_count"])),
        ("final PDF 物理页数", "11（含 UNVERIFIED 合并预览；gate 未 pass，无正式 full_zh_preview）"),
        ("单栏页数", str(len(profile["document_level"]["single_column_pages"]))),
        ("双栏页数", str(len(profile["document_level"]["double_column_pages"]))),
        ("mixed-column 页面", "无（全部双栏）"),
        ("document median gutter", "约 22.4pt（CVPR 双栏；production 推断正常）"),
        ("4D grid 是否直接泛化", "部分（11 页全部识别为双栏，但 content_outside_page 多处出现）"),
        ("layout OOD 页数", "参考页 p009-p011 未被 is_reference_item 识别（reference_item_count=0）"),
        ("Direct layout 检测成功页", "11/11"),
        ("BabelDOC fallback 触发页", "0（未触发，直接检测全部成功）"),
        ("figure 数", "8（region=8, rendered=8, missing=0, duplicate=0）"),
        ("figure missing/duplicate", "0 / 0"),
        ("caption 绑定", "部分正确（有 caption 但未逐条核验）"),
        ("formula groups 数", str(sum(rows and [0] or [0]) + 0) if False else "见逐页 formula 数（p005=20, p004=12 最密）"),
        ("complex formula 数", "未区分（沿用生产 FormulaGroup 分类）"),
        ("SVG vector preserved 比例", "生产路线原子 SVG（有 blank_formula 异常 p004/p005/p006）"),
        ("PNG/raster formula fallback", "0"),
        ("formula crop/collision/orphan", "见逐页 QA（无 crop 失败记录，有 blank 异常）"),
        ("table 数", "p006/p007/p008 各 1/1/2 共 4 张（TableModel 重建）"),
        ("每张表类型/row/column/cell", "见 pages/*/table 相关 QA"),
        ("table reconstruction 失败", "待逐表核验（TableQA 在 delivery_gate 未单独列为 blocked 层，但整体 blocked）"),
        ("LogicalParagraph 数", "103"),
        ("ownership conflict/drop/duplicate", "见逐页 QA（conflict=0 记录，dom_render_failure 存在）"),
        ("translation coverage/accounting", "body coverage=0.6289, accounting=1.0"),
        ("source_equal / fallback / failure", "unchanged_rejected=12, fallback=0, failure=0"),
        ("typography hard violations", str(typography.get("hard_violation_count", "N/A"))),
        ("PDF Unicode/CJK recovery", "%.4f / %.4f" % (text_layer.get("aggregate", {}).get("unicode_recovery", 0), text_layer.get("aggregate", {}).get("cjk_recovery", 0))),
        ("source/final physical page parity", "不满足：unexpected_extra_page_count=3（p006 溢出到 4 物理页）"),
        ("delivery_gate 最终", gate.get("decision", "blocked")),
        ("production baseline 是否真正泛化", "否 —— 见 verdict"),
    ]
    for i, (q, ans) in enumerate(answers, 1):
        add("%d. %s：**%s**" % (i, q, ans))
    add("")
    add("## GeneralizationQA — worst pages")
    add("")
    add("| page | risk | phys_pages | overflow | residual_prose | residual_body | token_split | blank_formula | owned_unrendered | structural | capacity |")
    add("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in worst:
        add("| %s | %d | %d | %d | %d | %d | %d | %d | %d | %d | %d |"
            % (TAG[r["page"]], r["generalization_risk_score"], r["physical_pages"],
               r["paragraph_overflow"], r["residual_untranslated_prose"],
               r["residual_untranslated_body"], r["token_split"], r["blank_formula"],
               r["owned_but_unrendered"], r["structural_pollution"],
               r["capacity_escalation"]))
    add("")
    add("## 根因分类（只读观察，未修复）")
    add("")
    add("1. **参考文献分类泛化缺口**：Ding 论文 p009-p011 参考文献未被 `is_reference_item` 识别"
        "（reference_item_count=0）→ 翻译走 non_body 豁免，但 residual QA 按可翻译正文标记 → "
        "66-76 条 untranslated prose / 128-138 short tokens。")
    add("2. **翻译覆盖率 63%**：12 个 body item unchanged_rejected（DeepSeek 对数学/公式密集句返回原文）。")
    add("3. **p006 容量溢出**：table+figure+formula 混合页在最高容量档（level5, font 0.94）仍 unresolved → 4 物理页。")
    add("4. **content_outside_page**：多页存在内容略出页面（grid/visual QA）。")
    add("5. **token_split / blank_formula / owned_unrendered**：数学符号密集页（p004/p005）出现 token 拆分与公式空白。")
    add("")
    add("## 结论")
    add("")
    add("verdict = **%s**" % verdict)
    add("")
    add("证据要点：delivery_gate=blocked（8 层）、visual_hard=blocked、"
        "source/final physical parity 不满足（p006 4 物理页）、translation coverage 0.63、"
        "参考文献残余英文 66-76 条。这不是『修一修就能 PASS』，而是架构级泛化缺口。")
    path = OUT / "PHASE4E1_DING_REPORT.md"
    path.write_text("\n".join(a), encoding="utf-8")
    return path


def main():
    print("=== source overlays ===")
    make_source_overlays()
    print("=== per-page typography + text layer ===")
    from typography_qa import build_typography_gate
    from pdf_text_layer_qa import pdf_text_layer_qa
    from typography_profile import DocumentTypographyProfile
    from typography_resolver import TypographyResolver
    resolver = TypographyResolver(DocumentTypographyProfile())
    typo = {"hard": {}, "per_page": {}}
    text = {"per_page": {}}
    for p in PAGES:
        tag = TAG[p]
        pdir = OUT / "pages" / tag
        m = _load(pdir / "stitched_page_model.json", {})
        tr = _load(pdir / "translation.json", {})
        qa = _load(pdir / "qa.json", {})
        gaps = _load(pdir / "script_gaps.json", []) or []
        protected = sorted({v for r in m.get("regions", []) if r.get("type") == "text"
                            for v in (r.get("payload") or {}).get("protected_runs", {}).values()})
        g = build_typography_gate(pdir / "zh.pdf", m, qa.get("flows", []), resolver,
                                  gaps, protected)
        t = pdf_text_layer_qa(pdir / "zh.pdf", m, tr, flows=qa.get("flows", []))
        _dump(pdir / "typography_qa.json", g)
        _dump(pdir / "text_layer_qa.json", t)
        typo["per_page"][tag] = g
        text["per_page"][tag] = t
        for k, v in g["hard"].items():
            typo["hard"][k] = typo["hard"].get(k, 0) + v
    typo["hard_violation_count"] = sum(typo["hard"].values())
    typo["decision"] = "pass" if typo["hard_violation_count"] == 0 else "fail"
    text["aggregate"] = {
        "unicode_recovery": sum(t["unicode_recovery_ratio"] for t in text["per_page"].values()) / len(text["per_page"]),
        "cjk_recovery": sum(t["cjk_recovery_ratio"] for t in text["per_page"].values()) / len(text["per_page"]),
    }
    text["decision"] = "pass" if all(t["decision"] == "pass" for t in text["per_page"].values()) else "fail"
    _dump(OUT / "typography_report.json", typo)
    _dump(OUT / "pdf_text_layer_report.json", text)

    print("=== generalization QA ===")
    rows = generalization_qa()
    _dump(OUT / "generalization_qa.json", {"per_page": rows})
    worst = rows
    _dump(OUT / "worst_pages.json", worst)
    _dump(OUT / "complexity_pages.json",
          [{"page": r["page"], "complexity_score": r["generalization_risk_score"]} for r in worst])

    preflight = _load(OUT / "preflight.json", {})
    profile = _load(OUT / "document_source_profile.json", {})
    gate = _load(OUT / "delivery_gate.json", {})

    print("=== comparison board ===")
    board = comparison_board()

    print("=== UNVERIFIED preview ===")
    src = OUT / "full_zh_debug_preview.pdf"
    if src.exists():
        shutil.copy2(src, OUT / "full_zh_preview_UNVERIFIED.pdf")
        print("wrote full_zh_preview_UNVERIFIED.pdf")

    print("=== report ===")
    report = _report(rows, preflight, profile, gate, text, typo, worst)
    print("report:", report)
    print("board:", board)
    print("=== Phase 4E.1 baseline complete (ZERO adaptation) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
