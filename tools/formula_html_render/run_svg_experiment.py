# -*- coding: utf-8 -*-
"""Phase 3D: atomic SVG formula round-trip experiment (page 3 focus).

Samples: F1 (simple inline), F33 (long inline), F36 (complex) on page 3, and
one display formula from p11. For every block:

  1. export vector SVG (get_svg_image text_as_path=True, viewBox crop);
  2. place as an atomic <img src=*.svg> locked at the original bbox;
  3. Chromium -> PDF -> re-extract vector drawings -> QA;
  4. inline blocks additionally get an in-text-line placement test.

Outputs (runs/fyresults/formula_svg/):
  formula_svg_source_compare.png / formula_svg_overlay.png
  inline_formula_svg_compare.png / display_formula_svg_compare.png
  complex_formula_svg_compare.png
  page_003_svg_report.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "实现源码" / "pdf_translator"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from _chromium_pdf import render_html_to_pdf
from svg_formula import export_formula_svg, write_svg_file

PDF = "runs/diag_src_2504.pdf"
OUT = Path("runs/fyresults/formula_svg")
PAGE_W, PAGE_H = 595.276, 841.89

SAMPLES = [
    {"id": "simple_inline_F1", "page": 3, "kind": "inline",
     "bbox": [101.428, 74.374, 114.697, 81.825]},
    {"id": "long_inline_F33", "page": 3, "kind": "inline",
     "bbox": [408.804, 518.605, 504.593, 529.514]},
    {"id": "complex_F36", "page": 3, "kind": "complex",
     "bbox": [306.927, 549.881, 526.219, 572.286]},
    {"id": "display_p11", "page": 11, "kind": "display",
     "bbox": [319.07, 97.519, 511.483, 108.569]},
]


def wrap_html(page_w, page_h, body):
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><style>"
            f"@page{{size:{page_w:.3f}pt {page_h:.3f}pt;margin:0;}}"
            "*{margin:0;padding:0;box-sizing:border-box;}"
            f"html,body{{width:{page_w:.3f}pt;height:{page_h:.3f}pt;}}"
            "</style></head><body>" + body + "</body></html>")


def collect_text_spans(pdf, page_idx, bbox, margin_y=10.0):
    """Original text spans on the same line band as the formula bbox."""
    doc = pymupdf.open(pdf)
    pg = doc[page_idx]
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
                if b[1] > y0 - margin_y and b[3] < y1 + margin_y \
                        and (b[2] < x0 - 1 or b[0] > x1 + 1):
                    spans.append({"text": t, "bbox": b,
                                  "size": float(span.get("size") or 0)})
    doc.close()
    return spans


def render_abs(page_w, page_h, img_tag, out_html, out_pdf):
    html = wrap_html(page_w, page_h, img_tag)
    out_html.write_text(html, encoding="utf-8")
    render_html_to_pdf(out_html, out_pdf)


def extract_vector_bbox(pdf_path):
    """Union bbox of vector drawings on the (formula-only) rendered page."""
    doc = pymupdf.open(pdf_path)
    pg = doc[0]
    boxes = []
    for d in pg.get_drawings():
        r = d.get("rect")
        if r is None:
            continue
        if r.width > 0.2 or r.height > 0.2:
            boxes.append([float(v) for v in r])
    imgs = []
    for iref in pg.get_images(full=True):
        for r in pg.get_image_rects(iref[0]):
            imgs.append([float(v) for v in r])
    doc.close()
    if boxes:
        b = [min(x[0] for x in boxes), min(x[1] for x in boxes),
             max(x[2] for x in boxes), max(x[3] for x in boxes)]
    else:
        b = None
    return b, len(boxes), imgs


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    results = {"tested_formula_blocks": len(SAMPLES),
               "formulas": [], "svgs": {}}

    for s in SAMPLES:
        fid = s["id"]
        bbox = s["bbox"]
        page_idx = s["page"] - 1
        res = export_formula_svg(PDF, page_idx, bbox)
        svg_path = OUT / ("%s.svg" % fid)
        write_svg_file(res["svg"], svg_path)

        # ---- absolute placement at locked bbox ----
        x0, y0, x1, y1 = bbox
        img_abs = ('<img src="%s" style="position:absolute;left:%.3fpt;'
                   'top:%.3fpt;width:%.3fpt;height:%.3fpt;"/>'
                   % (svg_path.name, x0, y0, x1 - x0, y1 - y0))
        render_abs(PAGE_W, PAGE_H, img_abs, OUT / ("%s_abs.html" % fid),
                   OUT / ("%s_abs.pdf" % fid))
        vb, n_vec, imgs = extract_vector_bbox(OUT / ("%s_abs.pdf" % fid))

        bbox_err = None
        center_err = None
        base_err = None
        render_ok = vb is not None and n_vec > 0
        if vb:
            bbox_err = max(abs(vb[i] - bbox[i]) for i in range(4))
            oc = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]
            rc = [(vb[0] + vb[2]) / 2, (vb[1] + vb[3]) / 2]
            center_err = max(abs(oc[0] - rc[0]), abs(oc[1] - rc[1]))
            base_err = abs(vb[3] - bbox[3])

        # ---- inline placement: text line + atomic svg back ----
        placement_ok = True
        text_overlap = False
        if s["kind"] in ("inline", "complex"):
            ctx_spans = collect_text_spans(PDF, page_idx, bbox)
            parts = []
            for sp in ctx_spans[:8]:
                b = sp["bbox"]
                parts.append(
                    '<div style="position:absolute;left:%.3fpt;top:%.3fpt;'
                    'white-space:nowrap;font-family:\'Times New Roman\',serif;'
                    'font-size:%.3fpt;line-height:%.3fpt;color:#000;">%s</div>'
                    % (b[0], b[1], sp["size"], sp["size"],
                       (sp["text"] or "").replace("<", "&lt;")))
            parts.append(img_abs)
            render_abs(PAGE_W, PAGE_H, "".join(parts),
                       OUT / ("%s_inline.html" % fid),
                       OUT / ("%s_inline.pdf" % fid))
            vb2, n2, imgs2 = extract_vector_bbox(OUT / ("%s_inline.pdf" % fid))
            # overlap: text spans in rendered PDF vs formula vector bbox
            rdoc = pymupdf.open(OUT / ("%s_inline.pdf" % fid))
            rpg = rdoc[0]
            text_boxes = []
            for block in rpg.get_text("dict").get("blocks", []):
                if block.get("type") == 1:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if (span.get("text") or "").strip():
                            text_boxes.append([float(v) for v in span["bbox"]])
            rdoc.close()
            if vb2:
                for tb in text_boxes:
                    if not (tb[2] < vb2[0] or tb[0] > vb2[2]
                            or tb[3] < vb2[1] or tb[1] > vb2[3]):
                        text_overlap = True
                        break
            placement_ok = len(text_boxes) > 0

        entry = {
            "formula_block_id": fid,
            "type": s["kind"],
            "placement": "inline" if s["kind"] in ("inline", "complex") else "block",
            "render_policy": "atomic_svg",
            "page": s["page"],
            "bbox": bbox,
            "svg_mode": res.get("svg_mode"),
            "svg_file": svg_path.name,
            "bbox_error_pt": round(bbox_err, 3) if bbox_err is not None else None,
            "center_error_pt": round(center_err, 3) if center_err is not None else None,
            "baseline_error_pt": round(base_err, 3) if base_err is not None else None,
            "placement_ok": placement_ok,
            "text_overlap": text_overlap,
            "render_success": render_ok,
            "rendered_vector_drawings": n_vec,
            "fallback_used": res.get("fallback_used", "none"),
        }
        results["formulas"].append(entry)
        results["svgs"][fid] = res.get("svg_mode")
        print("[%s] mode=%-16s bbox_err=%s center_err=%s base_err=%s "
              "vec=%d overlap=%s fallback=%s"
              % (fid, res.get("svg_mode"), entry["bbox_error_pt"],
                 entry["center_error_pt"], entry["baseline_error_pt"],
                 n_vec, text_overlap, entry["fallback_used"]))

    results["svg_success_count"] = sum(1 for f in results["formulas"]
                                       if f["svg_mode"] == "vector_preserved")
    results["png_fallback_count"] = sum(1 for f in results["formulas"]
                                        if f["fallback_used"] == "png")
    results["inline_overlap_count"] = sum(1 for f in results["formulas"]
                                          if f["text_overlap"])
    results["display_layout_break_count"] = sum(1 for f in results["formulas"]
                                                if not f["placement_ok"])
    errs = [f["bbox_error_pt"] for f in results["formulas"]
            if f["bbox_error_pt"] is not None]
    cents = [f["center_error_pt"] for f in results["formulas"]
             if f["center_error_pt"] is not None]
    results["max_bbox_error_pt"] = max(errs) if errs else None
    results["max_center_error_pt"] = max(cents) if cents else None

    # ---- visualisations ----
    make_visuals(results)
    (OUT / "page_003_svg_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nsummary:", {k: results[k] for k in
                         ("svg_success_count", "png_fallback_count",
                          "inline_overlap_count", "display_layout_break_count",
                          "max_bbox_error_pt", "max_center_error_pt")})
    print("wrote page_003_svg_report.json")


def crop_img(img, bbox, pad=10, Z=2.0):
    x0, y0, x1, y1 = [int(v * Z) for v in bbox]
    return img.crop((max(0, x0 - pad), max(0, y0 - pad),
                     min(img.width, x1 + pad), min(img.height, y1 + pad)))


def panel_with_label(img, label):
    from PIL import ImageDraw
    w, h = img.size
    out = Image.new("RGB", (w, h + 22), (245, 245, 245))
    out.paste(img, (0, 22))
    d = ImageDraw.Draw(out)
    d.text((6, 3), label, fill=(200, 0, 0))
    return out


def make_visuals(results):
    Z = 2.0
    doc = pymupdf.open(PDF)

    def src_img(page_idx):
        pix = doc[page_idx].get_pixmap(matrix=pymupdf.Matrix(Z, Z))
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    pages = {s["page"]: src_img(s["page"] - 1) for s in SAMPLES}

    # 1) source vs svg round-trip (per formula)
    for f in results["formulas"]:
        fid = f["formula_block_id"]
        src = pages[f["page"]].copy()
        rdoc = pymupdf.open(OUT / ("%s_abs.pdf" % fid))
        pix = rdoc[0].get_pixmap(matrix=pymupdf.Matrix(Z, Z))
        rnd = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        rdoc.close()
        c1 = crop_img(src, f["bbox"])
        c2 = crop_img(rnd, f["bbox"])
        c1 = panel_with_label(c1, "SOURCE")
        c2 = panel_with_label(c2, "SVG round-trip")
        h = max(c1.size[1], c2.size[1])
        w = c1.size[0] + c2.size[0] + 20
        canvas = Image.new("RGB", (w, h), (255, 255, 255))
        canvas.paste(c1, (0, 0))
        canvas.paste(c2, (c1.size[0] + 20, 0))
        canvas.save(OUT / ("%s_compare.png" % fid))

    # 2) formula_svg_overlay.png: page3 with original bbox + svg-rendered bbox
    pg3 = pages[3].copy()
    d = ImageDraw.Draw(pg3)
    for f in results["formulas"]:
        if f["page"] != 3:
            continue
        b = f["bbox"]
        d.rectangle([b[0] * Z, b[1] * Z, b[2] * Z, b[3] * Z],
                    outline=(255, 120, 0), width=3)
        vb = extract_vector_bbox(OUT / ("%s_abs.pdf" % f["formula_block_id"]))[0]
        if vb:
            d.rectangle([vb[0] * Z, vb[1] * Z, vb[2] * Z, vb[3] * Z],
                        outline=(0, 200, 80), width=2)
    d.rectangle([10, 10, 320, 64], fill=(255, 255, 255), outline=(60, 60, 60))
    d.line([18, 28, 60, 28], fill=(255, 120, 0), width=5)
    d.text((66, 20), "original formula bbox", fill=(0, 0, 0))
    d.line([18, 48, 60, 48], fill=(0, 200, 80), width=3)
    d.text((66, 40), "SVG-rendered bbox", fill=(0, 0, 0))
    pg3.save(OUT / "formula_svg_overlay.png")

    # 3) inline compare: text line with vs without formula (F33)
    f33 = next(f for f in results["formulas"] if f["formula_block_id"] == "long_inline_F33")
    c_src = crop_img(pages[3], f33["bbox"], pad=40)
    rdoc = pymupdf.open(OUT / "long_inline_F33_inline.pdf")
    pix = rdoc[0].get_pixmap(matrix=pymupdf.Matrix(Z, Z))
    rnd = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    rdoc.close()
    c_rnd = crop_img(rnd, f33["bbox"], pad=40)
    canvas = Image.new("RGB", (c_src.size[0] * 2 + 30, max(c_src.size[1], c_rnd.size[1])),
                       (255, 255, 255))
    canvas.paste(panel_with_label(c_src, "SOURCE text line + formula"),
                 (0, 0))
    canvas.paste(panel_with_label(c_rnd, "INLINE svg placed back"), (c_src.size[0] + 30, 0))
    canvas.save(OUT / "inline_formula_svg_compare.png")

    # 4) display compare (p11)
    fdisp = next(f for f in results["formulas"] if f["formula_block_id"] == "display_p11")
    c_src = crop_img(pages[11], fdisp["bbox"], pad=20)
    rdoc = pymupdf.open(OUT / "display_p11_abs.pdf")
    pix = rdoc[0].get_pixmap(matrix=pymupdf.Matrix(Z, Z))
    rnd = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    rdoc.close()
    c_rnd = crop_img(rnd, fdisp["bbox"], pad=20)
    canvas = Image.new("RGB", (c_src.size[0] * 2 + 30, max(c_src.size[1], c_rnd.size[1])),
                       (255, 255, 255))
    canvas.paste(panel_with_label(c_src, "SOURCE display formula"), (0, 0))
    canvas.paste(panel_with_label(c_rnd, "DISPLAY svg at locked bbox"), (c_src.size[0] + 30, 0))
    canvas.save(OUT / "display_formula_svg_compare.png")

    # 5) complex compare (F36)
    f36 = next(f for f in results["formulas"] if f["formula_block_id"] == "complex_F36")
    c_src = crop_img(pages[3], f36["bbox"], pad=20)
    rdoc = pymupdf.open(OUT / "complex_F36_abs.pdf")
    pix = rdoc[0].get_pixmap(matrix=pymupdf.Matrix(Z, Z))
    rnd = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    rdoc.close()
    c_rnd = crop_img(rnd, f36["bbox"], pad=20)
    canvas = Image.new("RGB", (c_src.size[0] * 2 + 30, max(c_src.size[1], c_rnd.size[1])),
                       (255, 255, 255))
    canvas.paste(panel_with_label(c_src, "SOURCE F36 complex"), (0, 0))
    canvas.paste(panel_with_label(c_rnd, "COMPLEX svg atomic"), (c_src.size[0] + 30, 0))
    canvas.save(OUT / "complex_formula_svg_compare.png")
    doc.close()


if __name__ == "__main__":
    run()
