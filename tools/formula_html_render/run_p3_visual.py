# -*- coding: utf-8 -*-
"""Page-3 complex-formula visualisation experiment (Phase 3B, page 3 only).

Classifies every BabelDOC formula on page 3 (inline / display / complex),
matches it against Direct (PyMuPDF) candidates, runs both HTML round-trip
strategies for every complex formula, and writes:

  page_003_original.png
  page_003_formula_babeldoc_overlay.png
  page_003_formula_direct_overlay.png
  page_003_formula_compare_overlay.png
  formula_<id>_source.png / _glyph_roundtrip.png / _atomic_roundtrip.png
  formula_<id>_compare.png
  page_003_formula_report.json

No translation, no OCR, no LaTeX, no MathJax, no main-pipeline changes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "实现源码" / "pdf_translator"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pymupdf

from raw_formula_analysis import (collect_page_raw, custom_formula_detection,
                                  is_math_font, is_math_char)

PAGE = 3
PDF = "runs/diag_src_2504.pdf"
OUT = Path("runs/fyresults/formula_p3")

MATH_FONTS_ALL = ("cmmi", "cmsy", "cmr", "cmex", "cmm", "msam", "msbm",
                  "mtsy", "eufm")
SMALL_MATH_FONTS = ("cmmi8", "cmsy8", "cmr8", "cmm8")  # subscript sizes


def iou(a, b):
    x0 = max(a[0], b[0]); y0 = max(a[1], b[1])
    x1 = min(a[2], b[2]); y1 = min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    ua = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    ub = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    return inter / (ua + ub - inter) if (ua + ub - inter) else 0.0


def classify_formula(f, raw, page_spans):
    """Type + structure flags for one BabelDOC formula on the page."""
    x0, y0, x1, y1 = f["bbox"]
    fonts = f["fonts"]
    sizes = f["font_sizes"]
    low_fonts = " ".join((x or "").lower() for x in fonts)

    has_cmex = "cmex" in low_fonts
    has_cmsy_big = "cmsy" in low_fonts and any(
        c in f["text_or_repr"] for c in "\u2211\u222b\u220f\u2223")  # big ops
    multi_size = len(sizes) > 1

    # fraction bar: a thin vector line inside the formula bbox
    has_fraction_bar = False
    for d in raw["drawings"]:
        db = d["bbox"]
        if (db[2] > x0 + 1 and db[0] < x1 - 1 and db[3] > y0 + 1
                and db[1] < y1 - 1):
            h = db[3] - db[1]
            w = db[2] - db[0]
            if 0 < h <= 1.2 and w >= 8:
                has_fraction_bar = True
                break

    # superscript/subscript: small math font mixed with base size, or
    # vertical span extension beyond a single line height
    has_super = multi_size and any(any(sm in (x or "").lower()
                                       for sm in SMALL_MATH_FONTS)
                                   for x in fonts)
    height = y1 - y0
    line_h = max((sizes or [10])[0] * 1.35, 12.0)
    multi_line = height > line_h

    # display: the formula's line contains only math fonts
    cy = (y0 + y1) / 2.0
    same_line = [s for s in page_spans
                 if s["bbox"][1] < cy < s["bbox"][3] or
                 (s["bbox"][3] > y0 and s["bbox"][1] < y1)]
    non_math = [s for s in same_line
                if not is_math_font(s["font"])
                and not any(is_math_char(c) for c in (s["text"] or ""))
                and not any(c.isdigit() for c in (s["text"] or ""))]
    is_display = len(non_math) == 0

    complex_flags = [has_cmex or has_cmsy_big, has_fraction_bar,
                     has_super or multi_line]
    is_complex = (has_fraction_bar or has_cmex
                  or (multi_size and multi_line))
    if is_complex:
        ftype = "complex"
    else:
        ftype = "display" if is_display else "inline"

    return {
        "type": ftype,
        "source_mode": ("mixed" if has_fraction_bar else "text_glyphs"),
        "has_cmex_or_large_symbol": has_cmex or has_cmsy_big,
        "has_fraction_bar": has_fraction_bar,
        "has_superscript_or_subscript": has_super or multi_line,
        "multi_font_size": multi_size,
        "height_pt": round(height, 2),
        "line_h_pt": round(line_h, 2),
        "display_like": is_display,
    }


def match_direct(f, direct_cands, tol=0.5):
    """Direct detected = Direct candidates cover >= 50% of the formula bbox
    (Direct splits a large formula into per-line/per-run pieces, so single-box
    IoU is not the right test)."""
    fx0, fy0, fx1, fy1 = f["bbox"]
    area = max(1.0, (fx1 - fx0) * (fy1 - fy0))
    covered = 0.0
    best_iou = 0.0
    for c in direct_cands:
        b = c["bbox"]
        ix = max(0.0, min(fx1, b[2]) - max(fx0, b[0]))
        iy = max(0.0, min(fy1, b[3]) - max(fy0, b[1]))
        covered += ix * iy
        best_iou = max(best_iou, iou(f["bbox"], b))
    return covered / area >= tol, round(best_iou, 3)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ref = json.load(open(OUT / "formula_reference.json", encoding="utf-8"))
    raw = json.load(open(OUT / "formula_geometry_raw.json", encoding="utf-8"))
    # raw json stores spans under all_* keys; align with the detector API
    raw["spans"] = raw.get("all_spans") or raw.get("spans") or []
    raw["drawings"] = raw.get("all_drawings") or raw.get("drawings") or []
    raw["images"] = raw.get("all_images") or raw.get("images") or []

    # Direct candidates (strict: math fonts or math chars, digits allowed as
    # BabelDOC does, but drop pure text/digit runs)
    cands = custom_formula_detection(raw)
    cands = [c for c in cands
             if any(is_math_font(f) for f in c["fonts"])]
    page_spans = raw["all_spans"]

    formulas = []
    complex_ids = []
    for f in ref["formulas"]:
        cls = classify_formula(f, raw, page_spans)
        direct_detected, direct_iou = match_direct(f, cands)
        formulas.append({
            "formula_id": f["formula_id"],
            "page": PAGE,
            "type": cls["type"],
            "source_mode": cls["source_mode"],
            "bbox": f["bbox"],
            "text": f["text_or_repr"],
            "fonts": f["fonts"],
            "font_sizes": f["font_sizes"],
            "has_cmex_or_large_symbol": cls["has_cmex_or_large_symbol"],
            "has_fraction_bar": cls["has_fraction_bar"],
            "has_superscript_or_subscript": cls["has_superscript_or_subscript"],
            "babeldoc_detected": True,
            "direct_detected": direct_detected,
            "direct_iou": direct_iou,
        })
        if cls["type"] == "complex":
            complex_ids.append(f["formula_id"])

    n_direct = len(cands)
    n_bab = len(formulas)
    matched = sum(1 for f in formulas if f["direct_detected"])
    print("page %d: babeldoc=%d direct=%d matched=%d complex=%d"
          % (PAGE, n_bab, n_direct, matched, len(complex_ids)))
    for f in formulas:
        print("  %s %-8s cmex=%s bar=%s super=%s direct=%s" % (
            f["formula_id"], f["type"], f["has_cmex_or_large_symbol"],
            f["has_fraction_bar"], f["has_superscript_or_subscript"],
            f["direct_detected"]))

    # ---- overlays ----
    from PIL import Image, ImageDraw
    Z = 2.0
    COL = {"inline": (80, 200, 120), "display": (255, 160, 0),
           "complex": (230, 40, 40)}

    doc = pymupdf.open(PDF)
    pg = doc[PAGE - 1]
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(Z, Z))
    base = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    base.save(OUT / "page_003_original.png")

    def overlay(boxes_colors, labels, path, legend):
        img = base.copy()
        d = ImageDraw.Draw(img)
        for b, c, lab in boxes_colors:
            d.rectangle([b[0] * Z, b[1] * Z, b[2] * Z, b[3] * Z],
                        outline=c, width=3 if c == COL["complex"] else 2)
            if lab:
                d.text((b[0] * Z + 2, b[1] * Z - 12), lab, fill=c)
        d.rectangle([10, 10, 330, 10 + 20 * len(legend) + 10], fill=(255, 255, 255),
                    outline=(60, 60, 60))
        yy = 18
        for c, t in legend:
            d.line([18, yy, 52, yy], fill=c, width=4)
            d.text((58, yy - 7), t, fill=(0, 0, 0))
            yy += 20
        img.save(OUT / path)

    bab_boxes = [(f["bbox"], COL[f["type"]], f["formula_id"])
                 for f in formulas]
    overlay(bab_boxes, None, "page_003_formula_babeldoc_overlay.png",
            [(COL["inline"], "inline"), (COL["display"], "display"),
             (COL["complex"], "complex")])

    # Direct overlay: colour by whether it matched a BabelDOC formula
    direct_boxes = []
    for i, c in enumerate(cands):
        matched_f = next((f for f in formulas if f["direct_detected"]
                          and iou(f["bbox"], c["bbox"]) >= 0.5), None)
        col = COL["complex"] if matched_f and matched_f["type"] == "complex" \
            else COL["display"]
        direct_boxes.append((c["bbox"], col, None))
    overlay(direct_boxes, None, "page_003_formula_direct_overlay.png",
            [(COL["display"], "direct candidate"), (COL["complex"], "complex-ish")])

    # compare overlay: BabelDOC orange thick + Direct blue thin
    img = base.copy()
    d = ImageDraw.Draw(img)
    for f in formulas:
        b = f["bbox"]
        d.rectangle([b[0] * Z, b[1] * Z, b[2] * Z, b[3] * Z],
                    outline=(255, 120, 0), width=3)
    for c in cands:
        b = c["bbox"]
        d.rectangle([b[0] * Z, b[1] * Z, b[2] * Z, b[3] * Z],
                    outline=(0, 130, 255), width=1)
    d.rectangle([10, 10, 310, 60], fill=(255, 255, 255), outline=(60, 60, 60))
    d.line([18, 28, 60, 28], fill=(255, 120, 0), width=5)
    d.text((66, 20), "BabelDOC (%d)" % n_bab, fill=(0, 0, 0))
    d.line([18, 48, 60, 48], fill=(0, 130, 255), width=2)
    d.text((66, 40), "Direct (%d)" % len(cands), fill=(0, 0, 0))
    img.save(OUT / "page_003_formula_compare_overlay.png")
    print("wrote overlays")

    # ---- per-complex-formula round trips ----
    import html as _html
    from _chromium_pdf import render_html_to_pdf

    page_w, page_h = 595.276, 841.89

    def render_s1(bbox, spans, tag):
        parts = []
        for s in spans:
            b = s["bbox"]
            low = (s["font"] or "").lower()
            fam = ("'Segoe UI Symbol','Times New Roman',serif" if "cmsy" in low
                   else "'Times New Roman','Liberation Serif',serif")
            style = "italic" if "cmmi" in low else "normal"
            parts.append(
                '<div style="position:absolute;left:%.3fpt;top:%.3fpt;'
                'width:%.3fpt;height:%.3fpt;white-space:nowrap;overflow:visible;'
                'font-family:%s;font-size:%.3fpt;line-height:%.3fpt;'
                'font-style:%s;color:#000;">%s</div>'
                % (b[0], b[1], b[2] - b[0], b[3] - b[1], fam, s["font_size"],
                   b[3] - b[1], style, _html.escape(s["text"] or "", quote=True)))
        html = _wrap(page_w, page_h, "".join(parts))
        h = OUT / ("formula_%s_glyph_roundtrip.html" % tag)
        h.write_text(html, encoding="utf-8")
        p = OUT / ("formula_%s_glyph_roundtrip.pdf" % tag)
        render_html_to_pdf(h, p)
        return p

    def render_s2(bbox, tag):
        img = OUT / ("formula_%s_source.png" % tag)
        doc = pymupdf.open(PDF)
        pix = doc[PAGE - 1].get_pixmap(matrix=pymupdf.Matrix(3.0, 3.0),
                                       clip=pymupdf.Rect(*bbox))
        pix.save(str(img))
        doc.close()
        x0, y0, x1, y1 = bbox
        html = _wrap(page_w, page_h,
                     '<img src="%s" style="position:absolute;left:%.3fpt;'
                     'top:%.3fpt;width:%.3fpt;height:%.3fpt;"/>'
                     % (img.name, x0, y0, x1 - x0, y1 - y0))
        h = OUT / ("formula_%s_atomic_roundtrip.html" % tag)
        h.write_text(html, encoding="utf-8")
        p = OUT / ("formula_%s_atomic_roundtrip.pdf" % tag)
        render_html_to_pdf(h, p)
        return p

    def extract(pdf_path):
        doc = pymupdf.open(pdf_path)
        pg = doc[0]
        spans = []
        for block in pg.get_text("dict").get("blocks", []):
            if block.get("type") == 1:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if (span.get("text") or "").strip():
                        spans.append({"text": span["text"],
                                      "bbox": [float(v) for v in span["bbox"]]})
        imgs = []
        for iref in pg.get_images(full=True):
            for r in pg.get_image_rects(iref[0]):
                imgs.append({"bbox": [float(v) for v in r]})
        doc.close()
        return spans, imgs

    def ext(items):
        return [min(b[0] for b in items), min(b[1] for b in items),
                max(b[2] for b in items), max(b[3] for b in items)]

    def collect_spans(bbox):
        doc = pymupdf.open(PDF)
        pg = doc[PAGE - 1]
        x0, y0, x1, y1 = bbox
        out = []
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
                        out.append({"text": t, "bbox": b,
                                    "font": span.get("font"),
                                    "font_size": float(span.get("size") or 0)})
        doc.close()
        return out

    for f in formulas:
        if f["type"] != "complex":
            f["strategy_1"] = None
            f["strategy_2"] = None
            f["verdict"] = "direct_safe" if f["type"] == "inline" else "direct_visual_only"
            continue
        fid = f["formula_id"].lower()
        bbox = f["bbox"]
        orig_spans = collect_spans(bbox)
        orig_text = "".join(s["text"] for s in
                            sorted(orig_spans, key=lambda s: s["bbox"][0]))

        p1 = render_s1(bbox, orig_spans, fid)
        rs1, ri1 = extract(p1)
        r1_bbox = ext([s["bbox"] for s in rs1]) if rs1 else None
        r1_err = (max(abs(r1_bbox[i] - bbox[i]) for i in range(4))
                  if r1_bbox else None)
        r1_center = (max(abs((r1_bbox[0] + r1_bbox[2]) / 2 - (bbox[0] + bbox[2]) / 2),
                         abs((r1_bbox[1] + r1_bbox[3]) / 2 - (bbox[1] + bbox[3]) / 2))
                     if r1_bbox else None)
        rend_text = "".join(s["text"] for s in
                            sorted(rs1, key=lambda s: s["bbox"][0]))
        glyph = (1.0 if orig_text == rend_text else
                 0.0 if not orig_text else
                 sum(1 for a, b in zip(orig_text, rend_text) if a == b) / len(orig_text))

        p2 = render_s2(bbox, fid)
        rs2, ri2 = extract(p2)
        r2_bbox = ext([i["bbox"] for i in ri2]) if ri2 else None
        r2_err = (max(abs(r2_bbox[i] - bbox[i]) for i in range(4))
                  if r2_bbox else None)
        r2_center = (max(abs((r2_bbox[0] + r2_bbox[2]) / 2 - (bbox[0] + bbox[2]) / 2),
                         abs((r2_bbox[1] + r2_bbox[3]) / 2 - (bbox[1] + bbox[3]) / 2))
                     if r2_bbox else None)
        r2_base = abs(r2_bbox[3] - bbox[3]) if r2_bbox else None

        f["strategy_1"] = {"bbox_error_pt": round(r1_err, 3) if r1_err is not None else None,
                           "center_error_pt": round(r1_center, 3) if r1_center is not None else None,
                           "glyph_match_ratio": round(glyph, 4)}
        f["strategy_2"] = {"bbox_error_pt": round(r2_err, 3) if r2_err is not None else None,
                           "center_error_pt": round(r2_center, 3) if r2_center is not None else None,
                           "baseline_error_pt": round(r2_base, 3) if r2_base is not None else None}
        f["verdict"] = ("direct_visual_only" if r2_err is not None and r2_err <= 1.0
                        else "babeldoc_preferred")
        print("[%s] s1 bbox=%.3f glyph=%.3f | s2 bbox=%.3f center=%.3f base=%.3f"
              % (fid, r1_err or -1, glyph, r2_err or -1, r2_center or -1, r2_base or -1))

        # compare panel: source | glyph | atomic
        _make_compare(fid, bbox)

    report = {
        "page": PAGE,
        "formula_count_babeldoc": n_bab,
        "formula_count_direct": len(cands),
        "matched_formula_count": matched,
        "complex_formula_count": len(complex_ids),
        "formulas": formulas,
    }
    (OUT / "page_003_formula_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote page_003_formula_report.json")


def _wrap(page_w, page_h, body):
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><style>"
            f"@page{{size:{page_w:.3f}pt {page_h:.3f}pt;margin:0;}}"
            "*{margin:0;padding:0;box-sizing:border-box;}"
            f"html,body{{width:{page_w:.3f}pt;height:{page_h:.3f}pt;}}"
            "</style></head><body>" + body + "</body></html>")


def _make_compare(fid, bbox):
    from PIL import Image, ImageDraw
    Z = 2.0
    # source is already a tight clip (3x); glyph/atomic renders need a crop
    # from the full page at 2x
    src = Image.open(OUT / ("formula_%s_source.png" % fid)).convert("RGB")
    g = OUT / ("formula_%s_glyph_roundtrip.pdf" % fid)
    a = OUT / ("formula_%s_atomic_roundtrip.pdf" % fid)
    doc = pymupdf.open(g)
    g_pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0))
    g_img = Image.frombytes("RGB", (g_pix.width, g_pix.height), g_pix.samples)
    doc.close()
    doc = pymupdf.open(a)
    a_pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0))
    a_img = Image.frombytes("RGB", (a_pix.width, a_pix.height), a_pix.samples)
    doc.close()

    def crop_page(img, b, pad=12):
        x0, y0, x1, y1 = [int(v * Z) for v in b]
        return img.crop((max(0, x0 - pad), max(0, y0 - pad),
                         min(img.width, x1 + pad), min(img.height, y1 + pad)))

    c1 = src
    c2 = crop_page(g_img, bbox)
    c3 = crop_page(a_img, bbox)
    h = max(c1.size[1], c2.size[1], c3.size[1])
    w = sum(p.size[0] for p in (c1, c2, c3)) + 40
    canvas = Image.new("RGB", (w, h + 26), (250, 250, 250))
    x = 0
    for c, lab in ((c1, "SOURCE"), (c2, "STRATEGY1 glyph"), (c3, "STRATEGY2 atomic")):
        canvas.paste(c, (x, 26))
        d = ImageDraw.Draw(canvas)
        d.text((x + 4, 4), lab, fill=(200, 0, 0))
        x += c.size[0] + 20
    canvas.save(OUT / ("formula_%s_compare.png" % fid))


if __name__ == "__main__":
    main()
