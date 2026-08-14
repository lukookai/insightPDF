"""CLI: TableModel -> locked English HTML -> PDF -> geometry QA.

Usage:
    python run_render.py --model page_013_table_model.json \
        --pdf <source.pdf> --page 13 --out <dir>

Produces (in --out):
    page_013_table.html
    page_013_table_rendered.pdf
    page_013_table_rendered.png
    page_013_table_compare.png
    page_013_table_render_report.json

No translation, no Chinese, no font auto-shrink, no overflow repair, no
modification of the model's column/table boundaries.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "实现源码" / "pdf_translator"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from render import build_html, font_weight_style, _safe_font_stack
from qa import extract_rendered, compute_metrics, render_compare


def main():
    ap = argparse.ArgumentParser(description="TableModel -> locked HTML -> PDF.")
    ap.add_argument("--model", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    model = json.load(open(args.model, "r", encoding="utf-8"))
    base = "page_%03d" % args.page

    # page size from the source PDF (PDF points)
    import pymupdf
    doc = pymupdf.open(Path(args.pdf).resolve())
    pg = doc[args.page - 1]
    page_w = float(pg.rect.width)
    page_h = float(pg.rect.height)
    doc.close()

    print("[1/5] build locked HTML (absolute-positioned cells, pt page) ...")
    html = build_html(model, page_w, page_h)
    html_path = out / (base + "_table.html")
    html_path.write_text(html, encoding="utf-8")

    print("[2/5] render HTML -> PDF (headless Chromium) ...")
    from _chromium_pdf import render_html_to_pdf
    pdf_path = out / (base + "_table_rendered.pdf")
    render_html_to_pdf(html_path, pdf_path)

    print("[3/5] rasterise rendered PDF -> PNG ...")
    rdoc = pymupdf.open(pdf_path)
    rpg = rdoc[0]
    png_path = out / (base + "_table_rendered.png")
    pix = rpg.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0))
    pix.save(str(png_path))
    rdoc.close()

    print("[4/5] geometry QA (extract rendered PDF, compare to model) ...")
    rendered_texts, rendered_rules = extract_rendered(pdf_path)
    metrics = compute_metrics(model, rendered_texts, rendered_rules)

    requested_fonts = sorted({
        (s.get("font") or "") for c in model["cells"] for s in c["spans"]
        if s.get("font")})
    report = {
        "page": args.page,
        "table_type": model["table_type"],
        "row_count": len(model["rows"]),
        "column_count": len(model["columns"]),
        "renderer_method": "absolute-positioned <div> per original PDF span "
                           "(no <table>, no table-layout, no reflow); "
                           "rules as SVG <line> strokes",
        "uses_table_element": False,
        "vertical_rules_drawn": sum(
            1 for r in model.get("rules", []) if r["orientation"] == "vertical"),
        "rules_drawn": [
            {"role": r["role"], "y": r["y"], "x0": r["x0"], "x1": r["x1"],
             "thickness": r.get("thickness")}
            for r in model.get("rules", [])],
        "rule_count_rendered": metrics["rendered_rule_count"],
        "table_bbox_model_pt": model["bbox"],
        "table_width_pt": round(model["bbox"][2] - model["bbox"][0], 3),
        "column_boundaries_pt": [
            [c["x0"], c["x1"]] for c in model["columns"]],
        "row_layout_boundaries_pt": model["row_layout_boundaries"],
        "alignment_per_column": {
            str(c["index"]): {"alignment": c.get("alignment"),
                              "anchor_x": c.get("anchor_x")}
            for c in model["columns"]},
        "font_requested": requested_fonts,
        "font_weight_style": {
            f: {"weight": font_weight_style(f)[0],
                "style": font_weight_style(f)[1]}
            for f in requested_fonts},
        "font_fallback_observed": {
            f: {"css_stack": _safe_font_stack(f),
                "note": "embedded PDF font not exposed to the browser; "
                        "Chromium substitutes Times New Roman (metric-"
                        "compatible Times clone) without any size change"}
            for f in requested_fonts},
        "fonts_rendered_in_pdf": metrics["rendered_fonts"],
        "metrics": {k: v for k, v in metrics.items()
                    if k not in ("rendered_fonts",)},
    }
    report_path = out / (base + "_table_render_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")

    print("[5/5] compare overlay (original vs round-trip) ...")
    compare_path = out / (base + "_table_compare.png")
    render_compare(model, rendered_texts, rendered_rules,
                   Path(args.pdf).resolve(), args.page, compare_path)

    m = metrics
    print("\n========== Table Reconstruction Phase 2A: HTML Round-trip QA ==========")
    print("renderer method        : absolute-positioned <div> per PDF span (+SVG rule strokes)")
    print("uses <table>?          : NO  (table-layout:auto forbidden by design)")
    print("page size              : %.3f x %.3f pt (CSS @page, fixed)" % (page_w, page_h))
    print("table_type             :", model["table_type"])
    print("rows / columns         : %d / %d" % (len(model["rows"]), len(model["columns"])))
    print("rules drawn            : %d (sparse_rule: only original h-rules)"
          % len(model.get("rules", [])))
    print("vertical rules drawn   :", report["vertical_rules_drawn"])
    print("table width (pt)       :", report["table_width_pt"])
    print("alignment per column   :", report["alignment_per_column"])
    print("fonts requested        :", requested_fonts)
    print("fonts rendered in PDF  :", m["rendered_fonts"])
    print("\n--- geometry QA (pt) ---")
    for k in ("table_bbox_max_error_pt", "column_boundary_max_error_pt",
              "row_boundary_max_error_pt", "median_text_center_error_pt",
              "max_text_center_error_pt", "text_baseline_max_error_pt",
              "rule_position_max_error_pt", "rule_thickness_max_error_pt",
              "overflow_cells", "unmatched_model_spans"):
        print("  %-30s : %s" % (k, m[k]))
    if m.get("overflow_span_details"):
        print("  overflow details     :", m["overflow_span_details"])
    print("\noutputs:")
    for p in (html_path, pdf_path, png_path, compare_path, report_path):
        print("  ", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
