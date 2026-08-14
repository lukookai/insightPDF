# -*- coding: utf-8 -*-
"""Phase 3B finalize: placeholder protection demo, negative sample,
BabelDOC formula dependency audit, overlays, verdicts.

1. placeholder demo: an English text line + [FORMULA] placeholder + the real
   formula image placed back -- no DeepSeek, no translation. Verifies the
   formula keeps its position and the surrounding text never overlaps it.
2. negative sample: run both detectors on the no-formula page (p6) and count
   false positives.
3. dependency audit + BabelDOC / Direct overlays for the formula page.
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
                                  classify_source_mode, is_math_font,
                                  is_math_char)


def placeholder_demo(pdf: Path, page: int, formula_bbox, out_dir: Path):
    """Text line with [FORMULA] + atomic image placed back at its bbox."""
    # sample English text spans from the same page, left of the formula bbox
    doc = pymupdf.open(str(pdf))
    pg = doc[page - 1]
    x0, y0, x1, y1 = formula_bbox
    left_spans = []
    for block in pg.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "")
                if not t.strip():
                    continue
                b = [float(v) for v in span["bbox"]]
                if b[1] > y0 - 8 and b[3] < y1 + 8 and b[2] < x0 - 2:
                    left_spans.append({"text": t, "bbox": b,
                                       "font": span.get("font"),
                                       "size": float(span.get("size") or 0)})
    page_w, page_h = float(pg.rect.width), float(pg.rect.height)
    doc.close()

    # clip formula image (Strategy 2)
    img = out_dir / "placeholder_formula.png"
    rdoc = pymupdf.open(str(pdf))
    rpg = rdoc[page - 1]
    pix = rpg.get_pixmap(matrix=pymupdf.Matrix(3.0, 3.0),
                         clip=pymupdf.Rect(*formula_bbox))
    pix.save(str(img))
    rdoc.close()

    parts = []
    # left text spans (original English, absolute)
    for s in left_spans[:6]:
        b = s["bbox"]
        fam = "'Times New Roman',serif"
        parts.append(
            '<div style="position:absolute;left:%.3fpt;top:%.3fpt;'
            'width:%.3fpt;height:%.3fpt;white-space:nowrap;overflow:visible;'
            'font-family:%s;font-size:%.3fpt;line-height:%.3fpt;color:#000;">%s</div>'
            % (b[0], b[1], b[2] - b[0], b[3] - b[1], fam, s["size"],
               b[3] - b[1], s["text"]))
    # the formula itself (atomic image at locked bbox)
    parts.append(
        '<img src="%s" style="position:absolute;left:%.3fpt;top:%.3fpt;'
        'width:%.3fpt;height:%.3fpt;"/>'
        % (img.name, x0, y0, x1 - x0, y1 - y0))
    # right text (short synthetic tail, absolute)
    ry = y0 + 2.0
    parts.append(
        '<div style="position:absolute;left:%.3fpt;top:%.3fpt;'
        'white-space:nowrap;overflow:visible;font-family:\'Times New Roman\',serif;'
        'font-size:10.909pt;line-height:10.909pt;color:#000;">where the '
        'definitions follow the standard formulation.</div>' % (x1 + 3.0, ry))

    html = ("<!doctype html><html><head><meta charset=\"utf-8\"><style>"
            f"@page{{size:{page_w:.3f}pt {page_h:.3f}pt;margin:0;}}"
            "*{margin:0;padding:0;box-sizing:border-box;}"
            f"html,body{{width:{page_w:.3f}pt;height:{page_h:.3f}pt;}}"
            "</style></head><body>" + "".join(parts) + "</body></html>")
    h = out_dir / "placeholder_roundtrip.html"
    h.write_text(html, encoding="utf-8")

    from _chromium_pdf import render_html_to_pdf
    p = out_dir / "placeholder_roundtrip.pdf"
    render_html_to_pdf(h, p)

    # verify: image rect matches formula bbox; no text overlaps the image rect
    rdoc = pymupdf.open(p)
    rpg = rdoc[0]
    imgs = []
    for imgref in rpg.get_images(full=True):
        for r in rpg.get_image_rects(imgref[0]):
            imgs.append([float(v) for v in r])
    spans = []
    for block in rpg.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if (span.get("text") or "").strip():
                    spans.append([float(v) for v in span["bbox"]])
    rdoc.close()

    overlap = 0
    img_bbox = imgs[0] if imgs else None
    if img_bbox:
        for b in spans:
            if not (b[2] < img_bbox[0] or b[0] > img_bbox[2]
                    or b[3] < img_bbox[1] or b[1] > img_bbox[3]):
                overlap += 1
    return {
        "formula_image_rect": [round(v, 3) for v in img_bbox] if img_bbox else None,
        "target_bbox": formula_bbox,
        "image_bbox_max_error_pt": (round(max(
            abs(img_bbox[i] - formula_bbox[i]) for i in range(4)), 3)
            if img_bbox else None),
        "text_spans_overlapping_formula": overlap,
        "surrounding_text_spans": len(spans),
    }


def negative_sample(pdf: Path, page: int):
    raw = collect_page_raw(pdf, page - 1)
    # strict: math fonts or true math chars only (no bare digits)
    strict = [c for c in custom_formula_detection(raw, include_digits=False)
              if any(is_math_font(f) for f in c["fonts"])]
    loose = custom_formula_detection(raw, include_digits=True)
    # digits-only candidates (the classic false-positive source)
    digit_only = [c for c in loose
                  if c not in strict and all(ch.isdigit() or ch in "[]\u2022. "
                                             for ch in c["text"])]
    return {
        "page": page,
        "strict_formula_candidates": len(strict),
        "loose_candidates": len(loose),
        "digit_only_false_positives": len(digit_only),
        "strict_samples": [{"text": c["text"][:30], "bbox": c["bbox"],
                            "fonts": c["fonts"]} for c in strict[:8]],
        "digit_only_samples": [{"text": c["text"][:30], "bbox": c["bbox"]}
                               for c in digit_only[:8]],
    }


def babeldoc_negative_counts(ref_json: Path):
    ref = json.load(open(ref_json, encoding="utf-8"))
    formulas = ref.get("formulas", [])
    text_fonts = [f for f in formulas
                  if all("NimbusRom" in (x or "") for x in f["fonts"])]
    return {"babeldoc_formula_count": len(formulas),
            "babeldoc_nimbus_text_false_positives": len(text_fonts),
            "samples": [{"text": f["text_or_repr"][:30], "bbox": f["bbox"],
                         "fonts": f["fonts"]} for f in text_fonts[:6]]}


def make_overlay(pdf: Path, page: int, boxes, out_png, color, title):
    from PIL import Image, ImageDraw
    doc = pymupdf.open(str(pdf))
    pg = doc[page - 1]
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    draw = ImageDraw.Draw(img)
    Z = 2.0
    for b in boxes:
        draw.rectangle([b[0] * Z, b[1] * Z, b[2] * Z, b[3] * Z],
                       outline=color, width=2)
    draw.text((10, 10), title, fill=(200, 0, 0))
    doc.close()
    img.save(out_png)
    return out_png


def main():
    pdf = Path("runs/diag_src_2504.pdf")
    out = Path("runs/fyresults/formula_p11")
    out.mkdir(parents=True, exist_ok=True)

    # ---- placeholder demo (p11 inline formula inside a text line) ----
    inline_bbox = [89.645, 108.627, 102.038, 116.078]
    ph = placeholder_demo(pdf, 11, inline_bbox, out)
    print("[placeholder]", json.dumps(ph, ensure_ascii=False))

    # ---- negative sample p6 ----
    neg = negative_sample(pdf, 6)
    bab_neg = babeldoc_negative_counts(out.parent / "formula_p6" / "formula_reference.json")
    print("[negative p6] strict=%d loose=%d digit_fp=%d | babeldoc=%d (%d nimbus fp)"
          % (neg["strict_formula_candidates"], neg["loose_candidates"],
             neg["digit_only_false_positives"],
             bab_neg["babeldoc_formula_count"],
             bab_neg["babeldoc_nimbus_text_false_positives"]))

    # ---- overlays on p11 ----
    ref = json.load(open(out / "formula_reference.json", encoding="utf-8"))
    bab_boxes = [f["bbox"] for f in ref["formulas"]]
    raw = collect_page_raw(pdf, 10)
    direct_cands = [c for c in custom_formula_detection(raw, include_digits=False)
                    if any(is_math_font(f) for f in c["fonts"])]
    make_overlay(pdf, 11, bab_boxes, out / "formula_babeldoc_overlay.png",
                 (255, 120, 0), "BabelDOC formulas (orange)")
    make_overlay(pdf, 11, [c["bbox"] for c in direct_cands],
                 out / "formula_direct_overlay.png",
                 (0, 150, 255), "Direct formula candidates (blue)")
    print("[overlay] babeldoc boxes=%d direct candidates=%d"
          % (len(bab_boxes), len(direct_cands)))

    # ---- dependency audit ----
    audit = {
        "scope": "formula chain (Path A: BabelDOC StylesAndFormulas; Path B: PyMuPDF custom)",
        "fields": [
            {"field": "formula detection (character rules)",
             "produced_by": "babeldoc/format/pdf/document_il/utils/formular_helper.py::is_formulas_start_char / is_formulas_middle_char",
             "actually_used_downstream": True,
             "purpose": "tag formula chars; drives PdfFormula creation",
             "possible_replacement": "custom span-level rules (math fonts + Sm/Mn/Sk + Greek + CID + digit-start, same heuristics)",
             "replacement_risk": "low (same rule set; false positives on digits in both paths)"},
            {"field": "formula bbox (PdfFormula.box)",
             "produced_by": "formular_helper.update_formula_data (char bbox union)",
             "actually_used_downstream": True,
             "purpose": "clip/place the formula as a visual object in HTML (project document.py uses it)",
             "possible_replacement": "PyMuPDF span union over the formula region",
             "replacement_risk": "low"},
            {"field": "inline vs display classification",
             "produced_by": "ParagraphFinder composition structure",
             "actually_used_downstream": False,
             "purpose": "typesetting decisions in BabelDOC backend",
             "possible_replacement": "line-occupancy heuristic (math-only line == display)",
             "replacement_risk": "low"},
            {"field": "formula characters / fonts / sizes",
             "produced_by": "PdfFormula.pdf_character (from native parse)",
             "actually_used_downstream": True,
             "purpose": "alt text + glyph-preserving rebuild (Strategy 1)",
             "possible_replacement": "PyMuPDF spans (text/font/size/bbox identical)",
             "replacement_risk": "low"},
            {"field": "formula curves / forms (vector + XObject)",
             "produced_by": "PdfFormula.pdf_curve / pdf_form",
             "actually_used_downstream": False,
             "purpose": "BabelDOC backend rendering of formula strokes",
             "possible_replacement": "PyMuPDF get_drawings + get_images",
             "replacement_risk": "low"},
            {"field": "formula x_offset / y_offset",
             "produced_by": "update_formula_data (char-bbox derived)",
             "actually_used_downstream": False,
             "purpose": "BabelDOC typesetting.py restores formula position in its own PDF layout",
             "possible_replacement": "not needed: HTML route places the formula at its locked bbox directly",
             "replacement_risk": "low for HTML route"},
            {"field": "formula translation exclusion",
             "produced_by": "ILTranslator skips formula compositions",
             "actually_used_downstream": True,
             "purpose": "never translate formula text",
             "possible_replacement": "translation_policy='protect' in the Formula Protection Model",
             "replacement_risk": "low"},
            {"field": "formula rendering offset (typesetting)",
             "produced_by": "babeldoc typesetting.py (uses offsets + fonts)",
             "actually_used_downstream": False,
             "purpose": "BabelDOC PDF backend only",
             "possible_replacement": "n/a for HTML route (Strategy 2 image / Strategy 1 locked spans)",
             "replacement_risk": "low"},
            {"field": "formula placeholder / protection",
             "produced_by": "IL composition replacement during translation",
             "actually_used_downstream": False,
             "purpose": "BabelDOC translation flow keeps formulas out of LLM text",
             "possible_replacement": "placeholder demo (this phase) - [FORMULA] + put back",
             "replacement_risk": "low"},
        ],
        "negative_sample": {"p6_strict": neg["strict_formula_candidates"],
                            "p6_babeldoc_false_positives": bab_neg["babeldoc_nimbus_text_false_positives"]},
        "placeholder_demo": ph,
    }
    (out / "formula_dependency_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[audit] wrote formula_dependency_audit.json")


if __name__ == "__main__":
    main()
