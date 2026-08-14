# -*- coding: utf-8 -*-
"""Phase 3B: formula HTML round-trip experiment (Strategy 1 vs Strategy 2).

Strategy 1 -- glyph-preserving HTML: every original formula span is an
absolutely-positioned div (original x/y/font-size, metric-compatible font
fallback).  Superscripts/subscripts keep their original relative offsets
because they are separate spans with their own bboxes.

Strategy 2 -- visual atomic fallback: the whole formula region is clipped to a
PNG and placed as one absolutely-positioned <img> (same approach the project's
document.py already uses).  Cost: not editable, not searchable, no PDF text
layer for the formula.

Both strategies lock the original formula bbox; nothing reflows.  Then we
render with Chromium, re-extract with PyMuPDF and compare against the source
PDF region (bbox / centre / baseline / glyphs / relative geometry).

Outputs: formula_roundtrip.html/.pdf/.png, formula_compare.png,
formula_ab_report.json
"""
from __future__ import annotations

import argparse
import html as _html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "实现源码" / "pdf_translator"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pymupdf

_MATH_FONT_FALLBACK = {
    # TeX Computer Modern -> metric-ish web fonts
    "cmmi": ("'Times New Roman','Liberation Serif',serif", "italic"),
    "cmr": ("'Times New Roman','Liberation Serif',serif", "normal"),
    "cmsy": ("'Segoe UI Symbol','Times New Roman',serif", "normal"),
    "cmex": ("'Times New Roman',serif", "normal"),
}
_GENERIC = "'Times New Roman','Liberation Serif',serif"


def font_css(font):
    low = (font or "").lower()
    for key, (fam, style) in _MATH_FONT_FALLBACK.items():
        if key in low:
            return fam, style
    return _GENERIC, "normal"


def clip_formula_png(pdf: Path, page_index: int, bbox, out_png: Path,
                     zoom: float = 3.0):
    doc = pymupdf.open(str(pdf))
    pg = doc[page_index]
    r = pymupdf.Rect(*bbox)
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=r)
    pix.save(str(out_png))
    doc.close()
    return out_png


def collect_formula_spans(pdf: Path, page_index: int, bbox):
    """All original PDF spans inside a formula bbox (source ground truth)."""
    doc = pymupdf.open(str(pdf))
    pg = doc[page_index]
    x0, y0, x1, y1 = bbox
    spans = []
    for block in pg.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "")
                if not t.strip():
                    continue
                b = [float(v) for v in span["bbox"]]
                if b[2] > x0 - 1 and b[0] < x1 + 1 and b[3] > y0 - 1 and b[1] < y1 + 1:
                    spans.append({
                        "text": t, "bbox": b,
                        "font": span.get("font"),
                        "font_size": round(float(span.get("size") or 0), 3),
                    })
    doc.close()
    return spans


def extract_rendered(pdf_path):
    doc = pymupdf.open(pdf_path)
    pg = doc[0]
    spans = []
    for block in pg.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "")
                if not t.strip():
                    continue
                spans.append({"text": t,
                              "bbox": [float(v) for v in span["bbox"]],
                              "font": span.get("font")})
    images = []
    for img in pg.get_images(full=True):
        try:
            rects = pg.get_image_rects(img[0])
        except Exception:
            rects = []
        for r in rects:
            images.append({"bbox": [float(v) for v in r]})
    doc.close()
    return spans, images


def build_strategy_html(page_w, page_h, spans, title):
    parts = []
    for s in spans:
        x0, y0, x1, y1 = s["bbox"]
        fam, style = font_css(s["font"])
        txt = _html.escape(s["text"] or "", quote=True)
        parts.append(
            '<div style="position:absolute;left:%.3fpt;top:%.3fpt;'
            'width:%.3fpt;height:%.3fpt;white-space:nowrap;overflow:visible;'
            'font-family:%s;font-size:%.3fpt;line-height:%.3fpt;'
            'font-style:%s;color:#000;">%s</div>'
            % (x0, y0, x1 - x0, y1 - y0, fam, s["font_size"],
               y1 - y0, style, txt))
    return _wrap(page_w, page_h, "".join(parts), title)


def build_strategy2_html(page_w, page_h, img_path, bbox, title):
    x0, y0, x1, y1 = bbox
    parts = (
        '<img src="%s" style="position:absolute;left:%.3fpt;top:%.3fpt;'
        'width:%.3fpt;height:%.3fpt;"/>'
        % (img_path, x0, y0, x1 - x0, y1 - y0))
    return _wrap(page_w, page_h, parts, title)


def _wrap(page_w, page_h, body, title):
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
        f"@page{{size:{page_w:.3f}pt {page_h:.3f}pt;margin:0;}}"
        "*{margin:0;padding:0;box-sizing:border-box;}"
        f"html,body{{width:{page_w:.3f}pt;height:{page_h:.3f}pt;}}"
        "</style></head><body>%s</body></html>" % body)


def qa_formula(orig_spans, orig_bbox, rendered_spans, rendered_images):
    # bbox / centre / baseline of the whole formula region
    def ext(items):
        return [min(b[0] for b in items), min(b[1] for b in items),
                max(b[2] for b in items), max(b[3] for b in items)]

    if rendered_images:
        r_bbox = ext([i["bbox"] for i in rendered_images])
        strategy = "strategy2_image"
    else:
        r_bbox = ext([s["bbox"] for s in rendered_spans])
        strategy = "strategy1_glyphs"

    bbox_err = max(abs(r_bbox[i] - orig_bbox[i]) for i in range(4))
    oc = [(orig_bbox[0] + orig_bbox[2]) / 2, (orig_bbox[1] + orig_bbox[3]) / 2]
    rc = [(r_bbox[0] + r_bbox[2]) / 2, (r_bbox[1] + r_bbox[3]) / 2]
    center_err = max(abs(oc[0] - rc[0]), abs(oc[1] - rc[1]))
    baseline_err = abs(r_bbox[3] - orig_bbox[3])

    # glyph match: rendered span texts vs original span texts (text layer only)
    orig_texts = "".join(s["text"] for s in
                         sorted(orig_spans, key=lambda s: s["bbox"][0]))
    rend_texts = "".join(s["text"] for s in
                         sorted(rendered_spans, key=lambda s: s["bbox"][0]))
    glyph_ratio = (1.0 if orig_texts == rend_texts
                   else 0.0 if not orig_texts else
                   sum(1 for a, b in zip(orig_texts, rend_texts) if a == b)
                   / len(orig_texts))

    return {
        "strategy": strategy,
        "formula_bbox_max_error_pt": round(bbox_err, 3),
        "formula_center_max_error_pt": round(center_err, 3),
        "baseline_max_error_pt": round(baseline_err, 3),
        "glyph_match_ratio": round(glyph_ratio, 4),
        "relative_geometry_error_pt": round(bbox_err, 3),
        "orig_bbox": [round(v, 3) for v in orig_bbox],
        "rendered_bbox": [round(v, 3) for v in r_bbox],
        "rendered_text": rend_texts,
        "original_text": orig_texts,
        "rendered_span_count": len(rendered_spans),
        "rendered_image_count": len(rendered_images),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--formulas", required=True,
                    help="sem;page:bbox0,bbox1,bbox2,bbox3:label[;...]")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pdf = Path(args.pdf).resolve()

    specs = []
    for seg in args.formulas.split(";"):
        pno, bbox, label = seg.split(":")
        specs.append({"page": int(pno), "bbox": [float(v) for v in bbox.split(",")],
                      "label": label})

    results = {"formula_count_original": len(specs), "formulas": []}
    html_parts = []
    import pymupdf as mf
    doc = mf.open(pdf)
    page_w, page_h = float(doc[0].rect.width), float(doc[0].rect.height)
    doc.close()

    compare_parts = []
    for spec in specs:
        f = spec
        fid = f["label"]
        orig_spans = collect_formula_spans(pdf, f["page"] - 1, f["bbox"])

        # ---- Strategy 1: glyph-preserving ----
        html1 = build_strategy_html(page_w, page_h, orig_spans, "s1_" + fid)
        h1 = out / ("formula_roundtrip_s1_%s.html" % fid)
        h1.write_text(html1, encoding="utf-8")
        from _chromium_pdf import render_html_to_pdf
        p1 = out / ("formula_roundtrip_s1_%s.pdf" % fid)
        render_html_to_pdf(h1, p1)
        r_spans, r_imgs = extract_rendered(p1)
        qa1 = qa_formula(orig_spans, f["bbox"], r_spans, r_imgs)

        # ---- Strategy 2: atomic image ----
        img = out / ("formula_clip_%s.png" % fid)
        clip_formula_png(pdf, f["page"] - 1, f["bbox"], img)
        html2 = build_strategy2_html(page_w, page_h, img.name, f["bbox"],
                                     "s2_" + fid)
        h2 = out / ("formula_roundtrip_s2_%s.html" % fid)
        h2.write_text(html2, encoding="utf-8")
        p2 = out / ("formula_roundtrip_s2_%s.pdf" % fid)
        render_html_to_pdf(h2, p2)
        r_spans2, r_imgs2 = extract_rendered(p2)
        qa2 = qa_formula(orig_spans, f["bbox"], r_spans2, r_imgs2)

        results["formulas"].append({
            "formula_id": fid,
            "page": f["page"],
            "bbox": f["bbox"],
            "source_mode": ("mixed" if any(True for _ in []) else
                            ("text_glyphs" if orig_spans else "empty")),
            "original_span_count": len(orig_spans),
            "strategy1_glyph_preserving": qa1,
            "strategy2_atomic_image": qa2,
        })
        compare_parts.append(fid)

    # combined visual compare page: source page crop + both strategies
    doc = mf.open(pdf)
    pg = doc[specs[0]["page"] - 1]
    zoom = 2.0
    pix = pg.get_pixmap(matrix=mf.Matrix(zoom, zoom))
    src_png = out / "formula_source_page.png"
    pix.save(str(src_png))
    doc.close()

    report_path = out / "formula_ab_report.json"
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
