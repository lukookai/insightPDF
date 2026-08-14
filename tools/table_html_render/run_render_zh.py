"""Phase 2B CLI: Cell-level Chinese translation + locked HTML.

Usage:
    python run_render_zh.py --model page_013_table_model.json \
        --pdf <source.pdf> --page 13 --out <dir> [--config runs/config.json]

Pipeline (no main-Pipeline changes, no span-level translation):

  1. extend the TableModel: per-cell translation fields, detected_bbox vs
     visual_bbox (rule geometry), source_font_size, fit state;
  2. classify cell content -- pure numbers / percentages stay unchanged and
     never reach DeepSeek;
  3. batch-translate text cells (cell_id back-fill, temperature=0, JSON);
  4. render round 1 at the original font size, measure overflow (unclipped);
  5. for overflowed cells only: DeepSeek short-translation retry, render
     round 2, re-measure;
  6. for still-overflowing cells only: font shrink (recorded ratio),
     render round 3, final measurement;
  7. QA (layout / anchor / containment) + debug overlay + report.

Outputs: page_013_table_zh.html / .pdf / .png, _zh_debug.png,
_zh_report.json, _translated_model.json
"""

from __future__ import annotations

import argparse
import copy
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "实现源码" / "pdf_translator"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from render_zh import build_zh_html, collect_source_font_size, _cell_id
from qa_zh import (extract_zh_rendered, compute_metrics,
                   render_debug_overlay, cell_text)
from cjk_fonts import detect_cjk_serif
import translate as tr

MIN_FONT_RATIO = 0.70
SHRINK_STEP = 0.9


def load_config(path=None):
    if path and Path(path).exists():
        cfg = json.load(open(path, "r", encoding="utf-8"))
        return (cfg.get("DEEPSEEK_API_KEY", ""),
                cfg.get("BASE_URL", tr.DEFAULT_BASE_URL),
                cfg.get("MODEL", tr.DEFAULT_MODEL))
    import os
    return (os.environ.get("DEEPSEEK_API_KEY", "").strip(),
            tr.DEFAULT_BASE_URL, tr.DEFAULT_MODEL)


def extend_model(model):
    """Add Phase 2B fields to a deep copy of the TableModel."""
    m = copy.deepcopy(model)
    # detected vs visual bbox (rule geometry) -- never conflated again
    m["detected_bbox"] = [float(v) for v in m["bbox"]]
    rules = [r for r in m.get("rules", [])
             if r["orientation"] == "horizontal"]
    if rules:
        vb = [min(r["x0"] for r in rules), min(r["y"] for r in rules),
              max(r["x1"] for r in rules), max(r["y"] for r in rules)]
        m["visual_bbox"] = [round(v, 3) for v in vb]
    else:
        m["visual_bbox"] = list(m["detected_bbox"])
    m["bbox_semantics"].update({
        "detected_bbox": "DocLayout detected ROI (may include padding)",
        "visual_bbox": "extent of the original visible table rules",
    })
    for cell in m["cells"]:
        cid = _cell_id(cell["row"], cell["col"])
        cell["cell_id"] = cid
        cell["source_font_size"] = round(collect_source_font_size(cell), 3)
        cell["render_font_size"] = cell["source_font_size"]
        cell["translated_text"] = None
        cell["translation_status"] = "pending"
        cell["translation_retry_count"] = 0
        cell["fit_strategy"] = "original"
        cell["overflow"] = False
        cls = tr.classify_cell_content(cell["source_text"])
        cell["content_class"] = cls
        if cls in ("numeric", "percent"):
            cell["translation_status"] = "unchanged"
            cell["fit_strategy"] = "original"
        elif cls == "empty":
            cell["translation_status"] = "empty"
    return m


def translate_model(model, token, base_url, model_name):
    batch = []
    for cell in model["cells"]:
        if cell["translation_status"] == "pending":
            batch.append({"cell_id": cell["cell_id"],
                          "source": cell["source_text"]})
    n_translated = 0
    if batch:
        print("[translate] sending %d text cells to DeepSeek (temp=0, batch=%d)..."
              % (len(batch), tr.BATCH_SIZE))
        result = tr.translate_batch(batch, base_url=base_url, token=token,
                                    model=model_name)
        for cell in model["cells"]:
            if cell["cell_id"] in result:
                cell["translated_text"] = result[cell["cell_id"]]
                cell["translation_status"] = "translated"
                n_translated += 1
    return n_translated


def short_translate(model, overflow_ids, token, base_url, model_name):
    batch = []
    for cell in model["cells"]:
        if cell["cell_id"] in overflow_ids and \
                cell["translation_status"] == "translated":
            batch.append({"cell_id": cell["cell_id"],
                          "source": cell["translated_text"]})
    if not batch:
        return 0
    print("[fit] short-translation retry for %d overflow cell(s) ..."
          % len(batch))
    result = tr.translate_short(batch, base_url=base_url, token=token,
                                model=model_name)
    n = 0
    for cell in model["cells"]:
        if cell["cell_id"] in result and result[cell["cell_id"]]:
            cell["translated_text"] = result[cell["cell_id"]]
            cell["translation_retry_count"] += 1
            cell["fit_strategy"] = "short_translation"
            n += 1
    return n


def render_round(model, page_w, page_h, cjk_css, out_dir, base, overrides,
                 clip=False, tag=""):
    html = build_zh_html(model, page_w, page_h, cjk_css,
                         overrides=overrides, clip=clip)
    if tag:
        # intermediate fit-round artifacts live in a _fit/ subdir (kept for
        # debugging; deletion is blocked by the sandbox's safe-delete shim)
        fit_dir = out_dir / "_fit"
        fit_dir.mkdir(exist_ok=True)
        html_path = fit_dir / (base + "_table_zh_%s.html" % tag)
    else:
        html_path = out_dir / (base + "_table_zh.html")
    html_path.write_text(html, encoding="utf-8")

    from _chromium_pdf import render_html_to_pdf
    pdf_path = html_path.with_suffix(".pdf")
    render_html_to_pdf(html_path, pdf_path)
    spans, rules = extract_zh_rendered(pdf_path)
    return html_path, pdf_path, spans, rules


def compute_fit_overrides(model, metrics, overrides, shrink_ids):
    """One-shot shrink ratios for still-overflowing cells (recorded)."""
    new_ov = dict(overrides)
    for cid in shrink_ids:
        cell = next((c for c in model["cells"] if c["cell_id"] == cid), None)
        info = metrics.get("per_cell", {}).get(cid)
        if cell is None or info is None or info.get("rendered_bbox") is None:
            continue
        layout = cell["layout_bbox"]
        avail_w = max(layout[2] - layout[0], 1.0)
        rb = info["rendered_bbox"]
        need_w = max(rb[2] - rb[0], 1.0)
        # shrink so text width fits the layout, with a 5% safety margin
        ratio = max(min(avail_w / need_w, 1.0), MIN_FONT_RATIO) * 0.95
        base_size = cell["source_font_size"]
        new_size = max(base_size * ratio, base_size * MIN_FONT_RATIO)
        new_size = min(new_size, base_size)  # never grow
        new_ov[cid] = round(new_size, 3)
    return new_ov


def main():
    ap = argparse.ArgumentParser(description="Phase 2B cell translation + locked HTML")
    ap.add_argument("--model", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="classify + build HTML only, skip DeepSeek")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = "page_%03d" % args.page

    model = json.load(open(args.model, "r", encoding="utf-8"))
    m = extend_model(model)

    import pymupdf
    doc = pymupdf.open(Path(args.pdf).resolve())
    pg = doc[args.page - 1]
    page_w, page_h = float(pg.rect.width), float(pg.rect.height)
    doc.close()

    cjk = detect_cjk_serif()
    print("[cjk-font] chosen =", cjk["chosen"], "| stack =", cjk["css_stack"])

    # ---------- translate ----------
    token, base_url, model_name = load_config(args.config)
    n_translated = 0
    if args.dry_run:
        for cell in m["cells"]:
            if cell["translation_status"] == "pending":
                cell["translation_status"] = "translated"
                cell["translated_text"] = cell["source_text"]
        n_translated = sum(1 for c in m["cells"]
                           if c["translation_status"] == "translated")
        print("[dry-run] no DeepSeek; using source as pseudo-translation")
    else:
        if not token:
            print("[ERROR] no DEEPSEEK_API_KEY (env or --config runs/config.json)")
            return 2
        n_translated = translate_model(m, token, base_url, model_name)
    unchanged = [c for c in m["cells"] if c["translation_status"] == "unchanged"]
    print("[translate] translated=%d unchanged(numeric/percent)=%d"
          % (n_translated, len(unchanged)))

    # ---------- render round 1: original size, unclipped ----------
    overrides = {}
    state = {
        "overflow_before_retry": [],
        "overflow_after_retry": [],
        "short_translation_cells": [],
        "font_shrink_cells": [],
        "minimum_font_ratio": 1.0,
        "cjk_font_requested": cjk["chosen"],
        "cjk_font_rendered": None,
    }

    print("[fit] round 1: original font size ...")
    _, _, spans1, rules1 = render_round(
        m, page_w, page_h, cjk["css_family"], out, base, overrides, tag="r1")
    metrics1 = compute_metrics(m, spans1, rules1, state=state)
    ov1 = set(metrics1["overflow_cells"])

    # baseline calibration (CJK line box) if systematically off: only the
    # CJK-rendered (translated) cells carry the font-specific offset, numeric
    # cells use Times whose line box already matches.
    base_errs = [info["rendered_bbox"][3] - cell["baseline"]
                 for cell, info in ((c, metrics1["per_cell"].get(c["cell_id"]))
                                    for c in m["cells"])
                 if info and info.get("rendered_bbox") and cell.get("baseline")
                 and cell["translation_status"] == "translated"]
    if base_errs:
        med = statistics.median(base_errs)
        if abs(med) > 0.30:
            # correct the CJK line-box factor only (numeric cells keep the
            # Times 1.0em factor and must not be dragged along)
            sizes = [c["source_font_size"] for c in m["cells"]
                     if c["translation_status"] == "translated"
                     and c["source_font_size"]]
            med_size = statistics.median(sizes) if sizes else 6.6
            delta = med / med_size
            import render_zh as rz
            old = rz.LINE_BOX_EM_CJK
            rz.LINE_BOX_EM_CJK = max(0.85, min(1.15, old + delta))
            print("[fit] baseline correction (CJK cells, n=%d, med=%.3f): "
                  "cjk line-box %.3f -> %.3f"
                  % (len(base_errs), med, old, rz.LINE_BOX_EM_CJK))
            _, _, spans1, rules1 = render_round(
                m, page_w, page_h, cjk["css_family"], out, base, overrides,
                tag="r1")
            metrics1 = compute_metrics(m, spans1, rules1, state=state)
            ov1 = set(metrics1["overflow_cells"])

    state["overflow_before_retry"] = sorted(ov1)
    print("[fit] round 1 overflow cells:", sorted(ov1) or "none")

    # ---------- round 2: short translation retry (overflow only) ----------
    if ov1 and not args.dry_run:
        n_short = short_translate(m, ov1, token, base_url, model_name)
        state["short_translation_cells"] = sorted(
            c["cell_id"] for c in m["cells"] if c["translation_retry_count"] > 0)
        _, _, spans2, rules2 = render_round(
            m, page_w, page_h, cjk["css_family"], out, base, overrides, tag="r2")
        metrics2 = compute_metrics(m, spans2, rules2, state=state)
        ov2 = set(metrics2["overflow_cells"])
        state["overflow_after_retry"] = sorted(ov2)
        print("[fit] round 2 overflow cells:", sorted(ov2) or "none")
    else:
        metrics2, rules2, ov2 = metrics1, rules1, ov1
        if not args.dry_run:
            state["short_translation_cells"] = []

    # ---------- round 3: font shrink (still overflowing only) ----------
    final_metrics, final_rules = metrics2, rules2
    if ov2:
        overrides = compute_fit_overrides(m, final_metrics, overrides,
                                          ov2)
        state["font_shrink_cells"] = sorted(ov2)
        state["minimum_font_ratio"] = round(
            min(overrides.get(cid, 1.0) / c["source_font_size"]
                for c in m["cells"] for cid in [c["cell_id"]]
                if cid in overrides), 3)
        print("[fit] round 3: shrink cells=%s ratios=%s"
              % (sorted(ov2),
                 {cid: overrides[cid] for cid in sorted(ov2)}))
        _, _, spans3, rules3 = render_round(
            m, page_w, page_h, cjk["css_family"], out, base, overrides, tag="r3")
        final_metrics = compute_metrics(m, spans3, rules3, state=state)
        final_rules = rules3

    # ---------- persist fit state into the model ----------
    import render_zh as rz_persist
    m["fit"] = {
        "line_box_em_cjk": rz_persist.LINE_BOX_EM_CJK,
        "line_box_em_latin": rz_persist.LINE_BOX_EM_LATIN,
        "overflow_before_retry": state["overflow_before_retry"],
        "overflow_after_retry": state["overflow_after_retry"],
        "font_shrink_cells": state["font_shrink_cells"],
        "minimum_font_ratio": state["minimum_font_ratio"],
    }
    for cell in m["cells"]:
        cid = cell["cell_id"]
        if cid in overrides:
            cell["render_font_size"] = overrides[cid]
            cell["fit_strategy"] = "font_shrink"
        cell["overflow"] = cid in set(final_metrics["overflow_cells"])
    translated_model_path = out / (base + "_table_translated_model.json")
    translated_model_path.write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- final deliverables (clip only if something still overflows) ----------
    need_clip = len(final_metrics["overflow_cells"]) > 0
    html_path, pdf_path, spans_f, rules_f = render_round(
        m, page_w, page_h, cjk["css_family"], out, base, overrides,
        clip=need_clip)
    # final QA measured on the *unclipped* detection (already computed)
    state["cjk_font_rendered"] = (
        final_metrics["rendered_fonts"] or None)

    import pymupdf
    rdoc = pymupdf.open(pdf_path)
    pix = rdoc[0].get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0))
    png_path = out / (base + "_table_zh.png")
    pix.save(str(png_path))
    rdoc.close()

    debug_path = out / (base + "_table_zh_debug.png")
    render_debug_overlay(m, spans_f, debug_path, Path(args.pdf).resolve(),
                         args.page)

    # ---------- report ----------
    counts = {}
    for st in ("translated", "unchanged", "empty", "pending"):
        counts[st] = sum(1 for c in m["cells"]
                         if c["translation_status"] == st)
    report = {
        "page": args.page,
        "table_type": m["table_type"],
        "row_count": len(m["rows"]),
        "column_count": len(m["columns"]),
        "translated_cells": counts.get("translated", 0),
        "unchanged_numeric_cells": counts.get("unchanged", 0),
        "empty_cells": counts.get("empty", 0),
        "cell_classification": {
            c["cell_id"]: c["content_class"] for c in m["cells"]},
        "renderer_method": "one .translated-cell div per logical cell "
                           "(layout_bbox, anchor-preserving, frozen geometry)",
        "uses_span_renderer": False,
        "rules_drawn": [
            {"role": r["role"], "y": r["y"], "x0": r["x0"], "x1": r["x1"],
             "thickness": r.get("thickness")}
            for r in m.get("rules", [])],
        "detected_bbox": m["detected_bbox"],
        "visual_bbox": m["visual_bbox"],
        "table_width_pt": round(m["visual_bbox"][2] - m["visual_bbox"][0], 3),
        "column_boundaries_pt": [[c["x0"], c["x1"]] for c in m["columns"]],
        "row_layout_boundaries_pt": m["row_layout_boundaries"],
        "alignment_per_column": {
            str(c["index"]): {"alignment": c.get("alignment"),
                              "anchor_x": c.get("anchor_x")}
            for c in m["columns"]},
        "overflow_before_retry": state["overflow_before_retry"],
        "overflow_after_retry": state["overflow_after_retry"],
        "short_translation_cells": state["short_translation_cells"],
        "font_shrink_cells": state["font_shrink_cells"],
        "minimum_font_ratio": state["minimum_font_ratio"],
        "clip_enabled_in_final_html": need_clip,
        "cjk_font_requested": cjk["chosen"],
        "cjk_font_css_stack": cjk["css_stack"],
        "cjk_font_rendered": state["cjk_font_rendered"],
        "fonts_requested_span": sorted({
            s.get("font") for c in m["cells"] for s in c.get("spans", [])
            if s.get("font")}),
        "metrics": {k: v for k, v in final_metrics.items()
                    if k not in ("per_cell",)},
    }
    report_path = out / (base + "_table_zh_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")

    print("\n========== Phase 2B: Cell-level Chinese translation QA ==========")
    print("translated cells         : %d" % counts.get("translated", 0))
    print("unchanged numeric/percent: %d (kept verbatim)"
          % counts.get("unchanged", 0))
    print("empty cells              : %d" % counts.get("empty", 0))
    print("overflow before retry    : %s" % (state["overflow_before_retry"] or "none"))
    print("overflow after  retry    : %s" % (state["overflow_after_retry"] or "none"))
    print("short translation cells  : %s" % (state["short_translation_cells"] or "none"))
    print("font shrink cells        : %s" % (state["font_shrink_cells"] or "none"))
    print("minimum font ratio       : %.3f" % state["minimum_font_ratio"])
    print("CJK font requested       :", cjk["chosen"])
    print("CJK font rendered        :", state["cjk_font_rendered"])
    print("\n--- geometry QA (pt) ---")
    fm = final_metrics
    for k in ("table_bbox_max_error_pt", "column_boundary_max_error_pt",
              "row_boundary_max_error_pt", "rule_position_max_error_pt",
              "rule_thickness_max_error_pt", "left_anchor_max_error_pt",
              "center_anchor_max_error_pt", "baseline_max_error_pt"):
        print("  %-30s : %s" % (k, fm[k]))
    print("  overflow cells         : %s" % (fm["overflow_cells"] or "none"))
    print("  cross-column cells     : %s" % (fm["cross_column_cells"] or "none"))
    print("\noutputs:")
    for p in (html_path, pdf_path, png_path, debug_path, report_path,
              translated_model_path):
        print("  ", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
