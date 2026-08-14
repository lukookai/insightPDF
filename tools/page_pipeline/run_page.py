# -*- coding: utf-8 -*-
"""Phase 4A main runner: page 3 / page 13 / auto-selected normal page.

PDF -> Unified PageModel -> translation (placeholders) -> unified HTML
     -> Chromium PDF -> Unified QA + visual overlay.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "实现源码" / "pdf_translator"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from _chromium_pdf import render_html_to_pdf  # noqa: E402
from html_render import build_unified_html  # noqa: E402
from page_model import build_page_model, ownership_stats  # noqa: E402
from qa_unified import capture_browser_layout, unified_qa  # noqa: E402
import translate_batch as tb  # noqa: E402

PDF = "runs/diag_src_2504.pdf"
OUT = Path("outputs/phase4b1")
TABLE_MODEL_P13 = ("runs/fyresults/recon_2504_p13/"
                   "page_013_table_translated_model.json")

REGION_COLOR = {"text": (90, 180, 90), "table": (70, 130, 255),
                "formula": (255, 150, 20), "image": (200, 60, 200)}


def load_token(path=None):
    if path:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        t = (cfg.get("api_key") or cfg.get("deepseek_api_key")
             or cfg.get("DEEPSEEK_API_KEY") or "")
        if t:
            return t
    return os.environ.get("DEEPSEEK_API_KEY", "")


def select_normal_page(doc_pages, skip=(), max_formulas=2):
    """Auto-select a normal two-column prose page (no table, few formulas)."""
    for p in range(doc_pages):
        if p in skip:
            continue
        try:
            m = build_page_model(PDF, p, OUT / "scan", run_doclayout=True)
        except Exception:  # noqa: BLE001
            continue
        types = {}
        for r in m["regions"]:
            types[r["type"]] = types.get(r["type"], 0) + 1
        if types.get("table", 0) == 0 and types.get("formula", 0) <= max_formulas:
            return p + 1, types
    return None, None


def run_page(page_no, tag, *, token, dry_run=False):
    page_idx = page_no - 1
    out_dir = OUT / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    table_path = TABLE_MODEL_P13 if page_no == 13 else None

    print("\n===== page %d (%s) =====" % (page_no, tag))
    model = build_page_model(PDF, page_idx, out_dir, run_doclayout=True,
                             table_model_path=table_path)
    print("  regions:", len(model["regions"]),
          "| paragraphs:", model["paragraph_count"],
          "| columns:", model["column_count"],
          "| ownership:", ownership_stats(model))

    # ---- translation items ----
    table_model = None
    for r in model["regions"]:
        if r["type"] == "table":
            table_model = r["payload"]
    paragraphs = [r["payload"] for r in model["regions"]
                  if r["type"] == "text"]
    items = tb.build_batch(paragraphs, table_model)
    print("  batch items:", len(items),
          "(paragraph=%d table_cell=%d)"
          % (sum(1 for i in items if i["type"] == "paragraph"),
             sum(1 for i in items if i["type"] == "table_cell")))
    translation_cache = out_dir / "translations.json"
    source_map = {it["item_id"]: it["source_text"] for it in items}
    translations = None
    if translation_cache.exists() and not dry_run:
        try:
            cached = json.loads(translation_cache.read_text(encoding="utf-8"))
            if cached.get("sources") == source_map:
                translations = cached.get("translations") or {}
                print("  translations: cache hit")
        except Exception:  # noqa: BLE001
            translations = None
    if translations is None:
        translations = tb.translate_batch(items, token=token, dry_run=dry_run)
        if not dry_run:
            translation_cache.write_text(json.dumps(
                {"sources": source_map, "translations": translations},
                ensure_ascii=False, indent=2), encoding="utf-8")

    # placeholder preservation
    tokens = sorted({t for p in paragraphs
                     for t in tb._tokens(p.get("translation_source_text")
                                         or p["source_text"])})
    print("  placeholders:", len(tokens))
    missing_tokens = []
    for it in items:
        if it["type"] != "paragraph":
            continue
        miss = tb.check_placeholder_preserved(translations.get(it["item_id"], ""),
                                              tb._tokens(it["source_text"]))
        if miss:
            missing_tokens.append((it["item_id"], miss))
    print("  placeholder missing in translation:", missing_tokens or "none")

    # ---- Phase 4B: column flow layout (paragraph reflow) ----
    import flow_layout as fl
    from html_render import (_display_formula_boxes, _obstacle_boxes,
                             _inline_formula_map)
    display_boxes = _display_formula_boxes(model)
    obstacles = _obstacle_boxes(model)
    inline_map = _inline_formula_map(model)
    formula_width_map = {}
    for tok, segs in inline_map.items():
        formula_width_map[tok] = max(
            sum(b[2] - b[0] for b in segs), 3.0)
    for para in paragraphs:
        fsize = para.get("base_font_size") or 10.0
        for tok, value in (para.get("protected_runs") or {}).items():
            formula_width_map[tok] = max(len(value) * fsize * 0.58, 3.0)
    flows = fl.build_column_flow(paragraphs, display_boxes, obstacles,
                                 translations, formula_width_map)
    for flow in flows:
        paras = [it for it in flow["items"] if it["kind"] == "paragraph"]
        formulas = [it for it in flow["items"] if it["kind"] == "formula"]
        print("  col %d flow: %d paragraphs + %d reserved formula boxes"
              % (flow["column"], len(paras), len(formulas)))

    # ---- unified HTML + Chromium ----
    html = build_unified_html(model, translations, PDF, out_dir,
                              table_model=table_model, flows=flows)
    html_path = out_dir / "zh.html"
    html_path.write_text(html, encoding="utf-8")
    pdf_path = out_dir / "zh.pdf"
    render_html_to_pdf(html_path, pdf_path)
    png_path = out_dir / "zh.png"
    browser_snapshot = capture_browser_layout(html_path, png_path, model)

    # ---- unified QA ----
    qa = unified_qa(model, translations, pdf_path, dry_run=dry_run,
                    flows=flows, browser_snapshot=browser_snapshot)
    source_texts = [p.get("source_text", "") for p in paragraphs]
    protected_values = [value for p in paragraphs
                        for value in (p.get("protected_runs") or {}).values()]
    page_gate = {}
    if page_no == 3:
        page_gate = {
            "inline_math_visible": qa["inline_formula_expected_count"] == 11
                                   and qa["inline_formula_blank_count"] == 0,
            "display_math_intact": qa["display_formula_expected_count"] == 3
                                   and qa["display_formula_blank_count"] == 0,
            "figure_2_restored": qa["figure_region_count"] == 1
                                 and qa["figure_rendered_count"] == 1,
            "figure_caption_restored": any(s.startswith("Figure 2:") for s in source_texts),
            "required_bold_prefixes": qa["inline_bold_run_count"] >= 3,
            "section_3_heading": any(p.get("style_role") == "heading"
                                     and p.get("source_text", "").startswith("3 LLM")
                                     for p in paragraphs),
        }
    elif page_no == 6:
        page_gate = {
            "criticalness_one_logical_paragraph": qa["cross_column_continuation_count"] == 1,
            "continuation_translated_once": qa["split_translation_continuation_count"] == 0,
            "continuation_has_no_orphan": qa["orphan_cross_column_continuation_count"] == 0,
            "method_names_italic": qa["inline_italic_run_count"] >= 3,
            "vanilla_skeleton_not_code": qa["code_expression_count"] == 0
                                         and qa["code_false_positive_count"] >= 1,
            "automatic_metrics_heading": any(p.get("style_role") == "heading"
                                               and p.get("source_text", "").startswith("4.3.1")
                                               for p in paragraphs),
        }
    elif page_no == 13:
        required_code = {
            "nomic-embed-text-v1", "convolution_layer = 6", "kernel_width = 3",
            "convolution_result_num = 10", "top_k = 6",
            "self_refine_count = 3", "self_refine_best_of = 3",
        }
        page_gate = {
            "table_105_cells": qa["table_cell_count"] == 105,
            "table_three_horizontal_rules": qa["table_horizontal_rule_count"] == 3,
            "table_zero_vertical_rules": qa["table_vertical_rule_count"] == 0,
            "c3_c4_headings": all(any(p.get("style_role") == "heading"
                                      and p.get("source_text", "").startswith(prefix)
                                      for p in paragraphs) for prefix in ("C.3", "C.4")),
            "embedding_source_reconstructed": any(
                "The embedding model is nomic-embed-text-v1, in line with the original AutoSurvey implementation."
                in source for source in source_texts),
            "all_code_exact": required_code.issubset(set(protected_values))
                              and qa["code_protected_exact_match_count"] >= len(required_code),
            "required_bold_prefixes": qa["inline_bold_run_count"] >= 2,
        }
    qa["page_assertions"] = page_gate
    qa["all_assertions_passed"] = (qa["all_assertions_passed"]
                                   and all(page_gate.values()))
    print("  QA:", json.dumps({k: v for k, v in qa.items()
                               if not k.endswith("details")}, ensure_ascii=False))

    # ---- visual overlay ----
    overlay_path = draw_overlay(PDF, page_idx, model, out_dir, page_no,
                                flows=flows)
    (out_dir / "unified_report.json").write_text(
        json.dumps({"page": page_no, "tag": tag, "qa": qa,
                    "model": model, "flows": flows,
                    "translations": translations}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return qa, html_path, pdf_path, overlay_path


def draw_overlay(pdf, page_idx, model, out_dir, page_no, flows=None):
    Z = 2.0
    doc = pymupdf.open(pdf)
    pix = doc[page_idx].get_pixmap(matrix=pymupdf.Matrix(Z, Z))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    d = ImageDraw.Draw(img)
    doc.close()
    # geometry-locked regions (table / formula / image) first
    for r in model["regions"]:
        col = REGION_COLOR.get(r["type"], (150, 150, 150))
        b = r["bbox"]
        d.rectangle([b[0] * Z, b[1] * Z, b[2] * Z, b[3] * Z],
                    outline=col, width=2)
        d.text((b[0] * Z + 2, max(0, b[1] * Z - 13)),
               "%s:%s" % (r["region_id"], r["type"]), fill=col)
    # paragraph flow bboxes (light green, dashed) + obstacle hatch
    for flow in flows or []:
        for it in flow["items"]:
            if it["kind"] == "paragraph":
                x0, x1 = flow["col_x0"], flow["col_x1"]
                y0 = it["flow_y"]
                y1 = y0 + it["est_height"]
                d.rectangle([x0 * Z, y0 * Z, x1 * Z, y1 * Z],
                            outline=(40, 200, 120), width=2)
                d.text((x0 * Z + 2, max(0, y0 * Z - 13)),
                       it["paragraph_id"] + ":" + it.get("style_role", ""),
                       fill=(0, 120, 60))
            else:
                # reserved formula box (hatched gray)
                b = it["bbox"]
                for k in range(0, int((b[2] - b[0]) * Z), 12):
                    d.line([(b[0] + k / Z) * Z, b[1] * Z,
                            (b[0] + k / Z) * Z, b[3] * Z],
                           fill=(120, 120, 120), width=1)
    yy = 16
    for t, col in REGION_COLOR.items():
        d.line([16, yy, 44, yy], fill=col, width=4)
        d.text((50, yy - 7), t, fill=(0, 0, 0))
        yy += 18
    d.line([16, yy, 44, yy], fill=(40, 200, 120), width=4)
    d.text((50, yy - 7), "paragraph flow bbox", fill=(0, 0, 0))
    p = out_dir / "debug_overlay.png"
    img.save(p)
    return p


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pages", default=None,
                    help="comma list, e.g. '3,13' (default: 3,13+auto)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    token = "" if args.dry_run else load_token(args.config)
    if not token and not args.dry_run:
        print("[ERROR] no DEEPSEEK_API_KEY (env or --config runs/config.json)")
        return 1

    doc = pymupdf.open(PDF)
    npages = doc.page_count
    doc.close()

    pages = [3, 6, 13]
    if args.pages:
        pages = [int(x) for x in args.pages.split(",") if x.strip()]
    auto_page, auto_types = None, None
    # Phase 4B.1 is intentionally limited to the ordered three-page gate.

    results = {}
    for p in pages:
        tag = "p%d" % p
        try:
            qa, hp, pp, ov = run_page(p, tag, token=token, dry_run=args.dry_run)
            results[p] = {"qa": qa, "html": str(hp), "pdf": str(pp),
                          "overlay": str(ov)}
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            results[p] = {"error": str(exc)}

    page_assertions = {
        str(p): (not r.get("error") and r.get("qa", {}).get("all_assertions_passed", False))
        for p, r in results.items()
    }
    phase_report = {
        "phase": "4B.1",
        "pages": results,
        "page_assertions": page_assertions,
        "all_pages_passed": all(page_assertions.values()) if page_assertions else False,
    }
    (OUT / "phase4b1_report.json").write_text(
        json.dumps(phase_report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== Phase 4B.1 summary ===")
    for p, r in results.items():
        if "error" in r:
            print("  page %d: ERROR %s" % (p, r["error"]))
        else:
            qa = r["qa"]
            print("  page %d: pass=%s logical=%d fragments=%d formulas=%d "
                  "code=%d figures=%d drops=%d unrendered=%d token_split=%d"
                  % (p, qa["all_assertions_passed"],
                     qa["logical_paragraph_count"], qa["flow_fragment_count"],
                     qa["math_formula_count"], qa["code_run_count"],
                     qa["figure_region_count"], qa["source_span_drop_count"],
                     qa["owned_but_unrendered_count"],
                     qa["latin_token_split_count"] + qa["code_token_split_count"]))
    print("report:", OUT / "phase4b1_report.json")
    return 0 if phase_report["all_pages_passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
