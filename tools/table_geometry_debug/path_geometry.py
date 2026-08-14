"""Path B: underlying PDF geometry via PyMuPDF (no ML, no translation).

PyMuPDF is already a first-class dependency of the project (45 source files use
it).  ``page.get_text("dict")`` gives text spans with their bbox, font and font
size; ``page.get_drawings()`` returns the vector drawing primitives (rectangles,
lines and curves) with their bounding boxes, stroke widths and colours.  Both
are already expressed in PDF points with a top-left origin, so they line up with
Path A after Path A's IL boxes are converted the same way.

This is the "ground truth" for table borders: if a table rule exists in the PDF
vector content, ``get_drawings`` exposes it.  Table *detection* (which cluster
of lines is a table) is ML-based and lives in Path A; Path B therefore reports
``detected_tables`` as empty and relies on Path A for that field.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

try:  # package import
    from .common import classify_primitives, LINE_MAX_THICKNESS_PT, LINE_MIN_LENGTH_PT
except ImportError:  # direct script run
    from common import classify_primitives, LINE_MAX_THICKNESS_PT, LINE_MIN_LENGTH_PT


def extract_geometry(pdf: Path, page: int) -> dict:
    pdf = Path(pdf).resolve()
    doc = pymupdf.open(pdf)
    if not (1 <= page <= doc.page_count):
        raise ValueError(f"page {page} out of range (1..{doc.page_count})")
    pg = doc[page - 1]
    page_width = float(pg.rect.width)
    page_height = float(pg.rect.height)

    # ---- texts ----
    texts = []
    for block in pg.get_text("dict").get("blocks", []):
        if block.get("type") == 1:  # image block
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = (span.get("text") or "").strip()
                if not txt:
                    continue
                x0, y0, x1, y1 = span["bbox"]
                texts.append(
                    {
                        "text": txt,
                        "bbox": [
                            round(x0, 3),
                            round(y0, 3),
                            round(x1, 3),
                            round(y1, 3),
                        ],
                        "font": span.get("font"),
                        "font_size": round(float(span.get("size", 0)), 3),
                    }
                )

    # ---- drawings: lines + rectangles ----
    h_lines: list[dict] = []
    v_lines: list[dict] = []
    rectangles: list[dict] = []

    drawings = pg.get_drawings()
    for item in drawings:
        kind = item.get("type")  # "f" fill, "s" stroke, "l" line, "fs" both
        rect = item.get("rect")  # fitz.Rect -> (x0, y0, x1, y1) top-left pt
        if rect is None:
            continue
        x0, y0, x1, y1 = (float(rect[0]), float(rect[1]),
                          float(rect[2]), float(rect[3]))
        stroke_w = float(item.get("width") or 0.0)
        # 1) stroke/line primitives: axis-aligned endpoints are ground truth.
        #    PDF table rules are often type "s" (stroke) with an items entry
        #    carrying the real line width -- type "l" alone misses them.
        if kind in ("l", "s") and item.get("points"):
            pts = item["points"]
            if len(pts) >= 2:
                (ax, ay), (bx, by) = pts[0], pts[-1]
                if abs(ay - by) <= 1.0 and abs(ax - bx) > 1.0:  # horizontal
                    h_lines.append(
                        {"x0": round(ax, 3), "y0": round(ay, 3),
                         "x1": round(bx, 3), "y1": round(by, 3),
                         "width": round(stroke_w, 3)})
                    continue
                if abs(ax - bx) <= 1.0 and abs(ay - by) > 1.0:  # vertical
                    v_lines.append(
                        {"x0": round(ax, 3), "y0": round(ay, 3),
                         "x1": round(bx, 3), "y1": round(by, 3),
                         "width": round(stroke_w, 3)})
                    continue
        # 2) thin filled/stroked rectangles (e.g. hairlines drawn as fills)
        rect_w = abs(x1 - x0)
        rect_h = abs(y1 - y0)
        thin = (max(rect_w, rect_h) >= LINE_MIN_LENGTH_PT
                and min(rect_w, rect_h) <= LINE_MAX_THICKNESS_PT)
        if kind in ("f", "fs") and thin:
            th = round(stroke_w or min(rect_w, rect_h), 3)
            if rect_w >= rect_h:
                h_lines.append(
                    {"x0": round(x0, 3), "y0": round((y0 + y1) / 2, 3),
                     "x1": round(x1, 3), "y1": round((y0 + y1) / 2, 3),
                     "width": th})
            else:
                v_lines.append(
                    {"x0": round((x0 + x1) / 2, 3), "y0": round(y0, 3),
                     "x1": round((x0 + x1) / 2, 3), "y1": round(y1, 3),
                     "width": th})
            continue
        # 3) fall back to rectangle/aspect-ratio classification; keep the
        #    original stroke width so rules retain their thickness.
        h, v, r = classify_primitives([(x0, y0, x1, y1)])
        for ln in h + v:
            ln["width"] = round(stroke_w, 3)
        h_lines.extend(h)
        v_lines.extend(v)
        rectangles.extend(r)

    doc.close()

    return {
        "page": {
            "width": round(page_width, 3),
            "height": round(page_height, 3),
            "coordinate_unit": "pt",
        },
        "texts": texts,
        "horizontal_lines": h_lines,
        "vertical_lines": v_lines,
        "rectangles": rectangles,
        "detected_tables": [],  # table *detection* is ML-based (Path A)
        "_meta": {
            "parser": "pymupdf-geometry",
            "drawing_count": len(drawings),
        },
    }
