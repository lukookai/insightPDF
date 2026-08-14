# -*- coding: utf-8 -*-
"""Dual-renderer final-PDF ink verification for formula placements.

The audit renders the *final PDF* with both MuPDF and PDFium and measures the
exact target box plus a geometry-derived safety margin.  It never treats a
DOM node, an SVG file, or a FormulaModel as proof of final visible ink.
"""
from __future__ import annotations

import json
from pathlib import Path

import pymupdf
from PIL import Image

try:
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover - reported as hard unavailability
    pdfium = None


INK_THRESHOLD = 235
DEFAULT_SCALE = 6.0


def _box(box):
    return [float(v) for v in box]


def _safety_margin(box):
    """Evidence-based margin from the smaller target dimension.

    The margin is proportional to the actual formula box rather than a page,
    document, or formula-specific constant.  It is used only for inspection;
    it never changes production geometry.
    """
    b = _box(box)
    short = max(0.0, min(b[2] - b[0], b[3] - b[1]))
    return round(min(1.5, max(0.2, short * 0.08)), 3)


def _expand(box, margin, page_size):
    b = _box(box)
    w, h = page_size
    return [max(0.0, b[0] - margin), max(0.0, b[1] - margin),
            min(w, b[2] + margin), min(h, b[3] + margin)]


def _crop(image, box, scale):
    b = _box(box)
    x0 = max(0, int(b[0] * scale))
    y0 = max(0, int(b[1] * scale))
    x1 = min(image.width, max(x0 + 1, int(round(b[2] * scale))))
    y1 = min(image.height, max(y0 + 1, int(round(b[3] * scale))))
    return image.crop((x0, y0, x1, y1)).convert("RGB")


def _ink(image):
    pixels = image.load()
    xs, ys = [], []
    for y in range(image.height):
        for x in range(image.width):
            if min(pixels[x, y]) < INK_THRESHOLD:
                xs.append(x)
                ys.append(y)
    if not xs:
        return {"ink_pixel_count": 0, "ink_bbox_px": None,
                "ink_edge_sides": [], "ink_touches_edge": False}
    bbox = [min(xs), min(ys), max(xs) + 1, max(ys) + 1]
    sides = []
    if bbox[0] <= 1:
        sides.append("left")
    if bbox[1] <= 1:
        sides.append("top")
    if bbox[2] >= image.width - 1:
        sides.append("right")
    if bbox[3] >= image.height - 1:
        sides.append("bottom")
    return {"ink_pixel_count": len(xs), "ink_bbox_px": bbox,
            "ink_edge_sides": sides, "ink_touches_edge": bool(sides)}


def _page_ink_bbox(local_bbox, target_bbox, scale):
    if not local_bbox:
        return None
    return [round(float(target_bbox[0]) + float(local_bbox[0]) / scale, 4),
            round(float(target_bbox[1]) + float(local_bbox[1]) / scale, 4),
            round(float(target_bbox[0]) + float(local_bbox[2]) / scale, 4),
            round(float(target_bbox[1]) + float(local_bbox[3]) / scale, 4)]


def _render_mupdf(pdf_path, page_index, scale):
    doc = pymupdf.open(str(pdf_path))
    page = doc[int(page_index)]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    size = (float(page.rect.width), float(page.rect.height))
    doc.close()
    return image, size


def _render_pdfium(pdf_path, page_index, scale):
    if pdfium is None:
        return None, None, "pypdfium2 unavailable"
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
        page = doc[int(page_index)]
        image = page.render(scale=scale).to_pil().convert("RGB")
        width, height = page.get_size()
        doc.close()
        return image, (float(width), float(height)), None
    except Exception as exc:  # noqa: BLE001
        return None, None, "%s: %s" % (type(exc).__name__, exc)


def formula_final_ink_qa(final_pdf, placements, *, page_index=0,
                         scale=DEFAULT_SCALE, out_path=None) -> dict:
    """Measure formula placements in one final physical PDF page.

    Each placement should contain ``formula_trace_id``, ``formula_id``,
    ``segment_id`` and ``target_bbox``.  Optional SVG evidence is used for
    source->SVG->PDF loss classification.
    """
    mupdf_image, page_size = _render_mupdf(final_pdf, page_index, scale)
    pdfium_image, pdfium_size, pdfium_error = _render_pdfium(
        final_pdf, page_index, scale)
    records = []
    for placement in placements or []:
        target = placement.get("target_bbox")
        if not target or len(target) != 4:
            records.append({
                **placement,
                "final_placement_missing": True,
                "final_pymupdf_ink_pixels": 0,
                "final_pdfium_ink_pixels": 0,
                "renderer_disagreement": False,
                "final_blank": True,
                "final_crop": False,
            })
            continue
        target = _box(target)
        margin = _safety_margin(target)
        expanded = _expand(target, margin, page_size)
        ma = _ink(_crop(mupdf_image, target, scale))
        ma_margin = _ink(_crop(mupdf_image, expanded, scale))
        if pdfium_image is not None:
            pb = _expand(target, margin, pdfium_size)
            mb = _ink(_crop(pdfium_image, target, scale))
            mb_margin = _ink(_crop(pdfium_image, pb, scale))
        else:
            mb = {"ink_pixel_count": 0, "ink_bbox_px": None,
                  "ink_touches_edge": False, "ink_edge_sides": []}
            mb_margin = dict(mb)
        a_nonzero = ma["ink_pixel_count"] > 0
        b_nonzero = mb["ink_pixel_count"] > 0
        disagreement = (pdfium_image is not None and a_nonzero != b_nonzero)
        blank = not a_nonzero or not b_nonzero
        svg_pixels = int(placement.get("svg_ink_pixels") or 0)
        svg_scale = float(placement.get("svg_raster_scale") or 1.0)
        svg_norm = svg_pixels / max(svg_scale * svg_scale, 1.0)
        a_norm = ma["ink_pixel_count"] / max(scale * scale, 1.0)
        b_norm = mb["ink_pixel_count"] / max(scale * scale, 1.0)
        min_ratio = (min(a_norm, b_norm) / svg_norm
                     if svg_norm > 0 and a_nonzero and b_nonzero else 0.0)
        # Conservative crop signal: strong upstream ink, severe loss in both
        # renderers, and final ink pinned to an edge while the SVG raster was
        # not.  Tiny superscripts have renderer-dependent antialiasing and are
        # not called cropped merely because their dark-pixel count is low.
        svg_edge = bool(placement.get("svg_ink_touches_edge"))
        edge = ma["ink_touches_edge"] and mb["ink_touches_edge"]
        crop = (not blank and svg_norm >= 4.0 and min_ratio < 0.04
                and edge and not svg_edge)
        records.append({
            **placement,
            "target_bbox": target,
            "safety_margin_pt": margin,
            "inspection_bbox": expanded,
            "final_placement_missing": False,
            "final_pymupdf_ink_pixels": ma["ink_pixel_count"],
            "final_pdfium_ink_pixels": mb["ink_pixel_count"],
            "final_pymupdf_margin_ink_pixels": ma_margin["ink_pixel_count"],
            "final_pdfium_margin_ink_pixels": mb_margin["ink_pixel_count"],
            "final_pymupdf_ink_bbox_px": ma["ink_bbox_px"],
            "final_pdfium_ink_bbox_px": mb["ink_bbox_px"],
            "final_pymupdf_ink_bbox": _page_ink_bbox(
                ma["ink_bbox_px"], target, scale),
            "final_pdfium_ink_bbox": _page_ink_bbox(
                mb["ink_bbox_px"], target, scale),
            "final_pymupdf_edge_sides": ma["ink_edge_sides"],
            "final_pdfium_edge_sides": mb["ink_edge_sides"],
            "svg_to_final_min_normalized_ink_ratio": round(min_ratio, 4),
            "renderer_disagreement": disagreement,
            "final_blank": blank,
            "final_crop": crop,
        })
    blanks = [r for r in records if r["final_blank"]]
    crops = [r for r in records if r["final_crop"]]
    parity = [r for r in records if r["renderer_disagreement"]]
    embed_missing = [r for r in records if r["final_placement_missing"]]
    result = {
        "schema_version": "phase4e1b.c2.formula_final_ink_qa.v1",
        "final_pdf": str(Path(final_pdf).resolve()),
        "page_index": int(page_index),
        "raster_scale": float(scale),
        "renderer_a": "PyMuPDF",
        "renderer_b": "PDFium",
        "renderer_b_available": pdfium_image is not None,
        "renderer_b_error": pdfium_error,
        "formula_placement_count": len(records),
        "formula_embed_missing_count": len(embed_missing),
        "formula_blank_count": len(blanks),
        "formula_crop_count": len(crops),
        "formula_renderer_disagreement_count": len(parity),
        "records": records,
        "hard": {
            "formula_embed_missing_count": len(embed_missing),
            "formula_blank_count": len(blanks),
            "formula_crop_count": len(crops),
            "formula_renderer_disagreement_count": len(parity),
        },
        "decision": ("pass" if pdfium_image is not None
                     and not (embed_missing or blanks or crops or parity)
                     else "fail"),
    }
    if out_path:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return result
