"""Geometry QA for the HTML -> PDF round-trip.

Re-extracts the rendered PDF with PyMuPDF and compares it, point-for-point,
against the original TableModel.  All deltas are in PDF points (pt).

Matching is *cell-grouped*, not nearest-neighbour-over-the-whole-page:

  1. every rendered span is first pinned to a model row via its centre-y
     against ``row_layout_boundaries`` (so a span can never be matched to a
     different row, which previously produced phantom 13-16 pt errors);
  2. within that row it matches a model *span* by exact text, else a model
     *cell* (multiple spans merged into a union bbox) by concatenated text,
     else by nearest centre.  Chromium merges two adjacent spans of the same
     logical cell into one rendered span ("The Efficiency Spectrum ..."),
     which the union bbox handles correctly.

Rules: the renderer emits SVG strokes (type "s"), but we also accept thin
filled rectangles (type "f"/"fs"), so rule extraction is geometry-based rather
than type-based.
"""

from __future__ import annotations

import bisect

import pymupdf

_RULE_Y_TOL = 3.0          # pt: matching tolerance between model/rendered rules
_OVERFLOW_TOL = 0.75       # pt: Chromium print grid rounding allowance


def extract_rendered(pdf_path):
    """Return (rendered_texts, rendered_h_rules) from a rendered PDF page."""
    doc = pymupdf.open(pdf_path)
    pg = doc[0]
    texts = []
    for block in pg.get_text("dict").get("blocks", []):
        if block.get("type") == 1:  # image block
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = span.get("text") or ""
                if not txt.strip():
                    continue
                x0, y0, x1, y1 = span["bbox"]
                texts.append({
                    "text": txt,
                    "bbox": [float(x0), float(y0), float(x1), float(y1)],
                    "font": span.get("font"),
                })
    h_rules = []
    for item in pg.get_drawings():
        rect = item.get("rect")
        if rect is None:
            continue
        x0, y0, x1, y1 = (float(rect[0]), float(rect[1]),
                          float(rect[2]), float(rect[3]))
        kind = item.get("type")
        if kind in ("l", "s") and item.get("points"):
            pts = item["points"]
            if len(pts) >= 2:
                (ax, ay), (bx, by) = pts[0], pts[-1]
                if abs(ay - by) <= 1.0 and abs(ax - bx) > 1.0:
                    h_rules.append({
                        "y": (ay + by) / 2.0,
                        "x0": min(ax, bx), "x1": max(ax, bx),
                        "thickness": float(item.get("width") or 0.0),
                    })
                    continue
        # thin horizontal filled rectangle == a rule
        if kind in ("f", "fs", "s") and (y1 - y0) <= 1.0 and (x1 - x0) > 4.0:
            h_rules.append({
                "y": (y0 + y1) / 2.0,
                "x0": x0, "x1": x1,
                "thickness": max(y1 - y0, float(item.get("width") or 0.0)),
            })
    doc.close()
    return texts, h_rules


def _extent(items):
    xs = [b[0] for b in items] + [b[2] for b in items]
    ys = [b[1] for b in items] + [b[3] for b in items]
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _build_units(model):
    """Per-row matching units: (span units, cell units).

    span unit: one original PDF span (bbox + text).
    cell unit : one logical cell with several spans merged (union bbox +
                concatenated text) -- what Chromium usually prints as one span.
    """
    rows = model.get("row_layout_boundaries")
    if not rows:
        ys = [r["y0"] for r in model["rows"]] + \
             [model["rows"][-1]["y1"]] if model["rows"] else []
        rows = ys
    span_units = {}   # row_idx -> list of units
    cell_units = {}
    for c in model["cells"]:
        r = c["row"]
        cell_units.setdefault(r, []).append({
            "text": c.get("source_text") or "",
            "bbox": c["content_bbox"],
            "layout_bbox": c.get("layout_bbox") or c["content_bbox"],
            "cell": (r, c["col"]),
        })
        for sp in c["spans"]:
            span_units.setdefault(r, []).append({
                "text": sp["text"] or "",
                "bbox": sp["bbox"],
                "cell": (r, c["col"]),
            })
    return rows, span_units, cell_units


def _pin_row(bounds, y_center):
    """Row index whose layout band contains ``y_center`` (bisect on the n+1
    boundaries).  Falls back to the nearest band edge if outside."""
    if len(bounds) < 2:
        return 0
    i = bisect.bisect_right(bounds, y_center) - 1
    return max(0, min(len(bounds) - 2, i))


def compute_metrics(model, rendered_texts, rendered_rules):
    mbbox = model["bbox"]
    bounds, span_units, cell_units = _build_units(model)
    model_rules = [r for r in model.get("rules", [])
                   if r["orientation"] == "horizontal"]

    # ground-truth extent = bounding box of the model's own content + rules
    model_content = [c["content_bbox"] for c in model["cells"]]
    model_rule_boxes = [[r["x0"], r["y"] - 0.2, r["x1"], r["y"] + 0.2]
                        for r in model_rules]
    m_ext = _extent(model_content + model_rule_boxes)

    # ---- (1) table bbox deviation (replicated extent vs original extent) ----
    rtext_ext = _extent([t["bbox"] for t in rendered_texts])
    rrule_ext = _extent([[r["x0"], r["y"] - 0.2, r["x1"], r["y"] + 0.2]
                         for r in rendered_rules])
    r_ext = None
    if rtext_ext and rrule_ext:
        r_ext = [min(rtext_ext[0], rrule_ext[0]), min(rtext_ext[1], rrule_ext[1]),
                 max(rtext_ext[2], rrule_ext[2]), max(rtext_ext[3], rrule_ext[3])]
    else:
        r_ext = rtext_ext or rrule_ext
    table_bbox_max_error = 0.0
    if r_ext and m_ext:
        table_bbox_max_error = max(
            abs(r_ext[0] - m_ext[0]), abs(r_ext[1] - m_ext[1]),
            abs(r_ext[2] - m_ext[2]), abs(r_ext[3] - m_ext[3]))

    # ---- (2..5,8) cell-grouped matching ----
    used_spans = set()
    err_x0, err_x1, err_y0, err_y1, center_err, baseline_err = [], [], [], [], [], []
    overflow_spans = []        # (cell, how much beyond the column layout box)
    overflow_cells = set()
    matched = []
    for t in rendered_texts:
        rb = t["bbox"]
        rc = ((rb[0] + rb[2]) / 2.0, (rb[1] + rb[3]) / 2.0)
        r = _pin_row(bounds, rc[1])
        sps = span_units.get(r, [])
        cus = cell_units.get(r, [])
        if not sps and not cus:
            continue

        # 1) exact span-text match within the row
        hit = next((u for u in sps
                    if u["text"] == t["text"] and id(u) not in used_spans),
                   None)
        # 2) exact concatenated cell-text match (merged spans)
        if hit is None:
            hit = next((u for u in cus if u["text"] == t["text"]), None)
        # 3) nearest centre within the row (span units first, then cells)
        if hit is None:
            cand = [(u, ((u["bbox"][0] + u["bbox"][2]) / 2.0 - rc[0]) ** 2
                     + ((u["bbox"][1] + u["bbox"][3]) / 2.0 - rc[1]) ** 2)
                    for u in sps] + \
                   [(u, ((u["bbox"][0] + u["bbox"][2]) / 2.0 - rc[0]) ** 2
                     + ((u["bbox"][1] + u["bbox"][3]) / 2.0 - rc[1]) ** 2)
                    for u in cus]
            cand.sort(key=lambda kv: kv[1])
            hit = cand[0][0]
        if hit is None:
            continue

        if hit in sps:
            used_spans.add(id(hit))
        else:
            # merged-span hit at cell level: all spans of that cell are covered
            for u in sps:
                if u["cell"] == hit["cell"]:
                    used_spans.add(id(u))
        mb = hit["bbox"]
        lbox = hit.get("layout_bbox", mb)
        dx = rc[0] - (mb[0] + mb[2]) / 2.0
        dy = rc[1] - (mb[1] + mb[3]) / 2.0
        err_x0.append(rb[0] - mb[0])
        err_x1.append(rb[2] - mb[2])
        err_y0.append(rb[1] - mb[1])
        err_y1.append(rb[3] - mb[3])
        center_err.append((dx ** 2 + dy ** 2) ** 0.5)
        baseline_err.append(abs(rb[3] - mb[3]))
        matched.append((t, hit, (dx, dy)))
        # overflow = rendered text crossing the logical cell column boundary
        if lbox:
            ox = 0.0
            if rb[0] < lbox[0] - _OVERFLOW_TOL:
                ox = max(ox, lbox[0] - _OVERFLOW_TOL - rb[0])
            if rb[2] > lbox[2] + _OVERFLOW_TOL:
                ox = max(ox, rb[2] - (lbox[2] + _OVERFLOW_TOL))
            if ox > 0:
                overflow_spans.append((hit["cell"], round(ox, 3)))
                overflow_cells.add(hit["cell"])

    def _mx(v):
        return max((abs(x) for x in v), default=0.0)

    col_boundary_max_error = max(_mx(err_x0), _mx(err_x1))
    row_boundary_max_error = max(_mx(err_y0), _mx(err_y1))
    center_err.sort()
    median_text_center_error = (center_err[len(center_err) // 2]
                                if center_err else 0.0)
    max_text_center_error = center_err[-1] if center_err else 0.0
    text_baseline_max_error = max(baseline_err) if baseline_err else 0.0
    unmatched_model_spans = sum(len(v) for v in span_units.values()) - len(used_spans)

    # ---- (6) rule position deviation ----
    rule_position_max = 0.0
    for mr in model_rules:
        cand = [r for r in rendered_rules if abs(r["y"] - mr["y"]) < _RULE_Y_TOL]
        if not cand:
            continue
        r = min(cand, key=lambda r: abs(r["y"] - mr["y"]))
        rule_position_max = max(rule_position_max,
                                abs(r["y"] - mr["y"]),
                                abs(r["x0"] - mr["x0"]),
                                abs(r["x1"] - mr["x1"]))

    # ---- (7) rule thickness deviation ----
    rule_thickness_max = 0.0
    for mr in model_rules:
        cand = [r for r in rendered_rules if abs(r["y"] - mr["y"]) < _RULE_Y_TOL]
        if not cand:
            continue
        r = min(cand, key=lambda r: abs(r["y"] - mr["y"]))
        rule_thickness_max = max(rule_thickness_max,
                                 abs(r["thickness"] - mr.get("thickness", 0.0)))

    return {
        "table_bbox_max_error_pt": round(table_bbox_max_error, 3),
        "column_boundary_max_error_pt": round(col_boundary_max_error, 3),
        "row_boundary_max_error_pt": round(row_boundary_max_error, 3),
        "median_text_center_error_pt": round(median_text_center_error, 3),
        "max_text_center_error_pt": round(max_text_center_error, 3),
        "rule_position_max_error_pt": round(rule_position_max, 3),
        "rule_thickness_max_error_pt": round(rule_thickness_max, 3),
        "overflow_cells": len(overflow_cells),
        "overflow_span_details": sorted(overflow_spans),
        "text_baseline_max_error_pt": round(text_baseline_max_error, 3),
        "unmatched_model_spans": unmatched_model_spans,
        "rendered_text_count": len(rendered_texts),
        "rendered_rule_count": len(rendered_rules),
        "rendered_fonts": sorted({t.get("font") or "" for t in rendered_texts}),
    }


def render_compare(model, rendered_texts, rendered_rules,
                   src_pdf, page, out_png, zoom=2.0):
    """Draw original (model) vs HTML round-trip (rendered) on the source page.

    Legend (drawn top-right):
      orange = original table bbox + cell content bboxes
      blue   = original rules
      magenta= round-trip rendered text bboxes (+extent)
      red    = round-trip rendered rules
      yellow = deviation connectors for spans whose centre moved > 1.5 pt
    """
    from PIL import Image, ImageDraw

    doc = pymupdf.open(src_pdf)
    pg = doc[page - 1]
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    draw = ImageDraw.Draw(img)
    Z = zoom
    OR = (255, 170, 0)      # original: table bbox / text
    OB = (0, 120, 255)      # original: rules
    RP = (255, 0, 200)      # rendered: text
    RB = (220, 0, 0)        # rendered: rules
    YL = (255, 220, 0)      # deviation connectors

    def rect(b, color, w=2, dash=None):
        x0, y0, x1, y1 = [v * Z for v in b]
        if dash:
            _dashed_rect(draw, x0, y0, x1, y1, color, w, dash)
        else:
            draw.rectangle([x0, y0, x1, y1], outline=color, width=w)

    def hline(y, x0, x1, color, w=3, dash=None):
        y *= Z
        x0 *= Z
        x1 *= Z
        if dash:
            _dashed_line(draw, (x0, y), (x1, y), color, w, dash)
        else:
            draw.line([x0, y, x1, y], fill=color, width=w)

    # original: table bbox (dashed) + text bboxes + rules
    rect(model["bbox"], OR, 2, dash=(10, 6))
    for c in model["cells"]:
        rect(c["content_bbox"], OR, 1)
    for r in model.get("rules", []):
        if r["orientation"] == "horizontal":
            hline(r["y"], r["x0"], r["x1"], OB, 4)

    # rendered: extent + text bboxes + rules
    if rendered_texts:
        ext = _extent([t["bbox"] for t in rendered_texts])
        if ext:
            rect(ext, RP, 2, dash=(10, 6))
        for t in rendered_texts:
            rect(t["bbox"], RP, 1)
    for r in rendered_rules:
        hline(r["y"], r["x0"], r["x1"], RB, 4)

    # deviation connectors for spans that shifted noticeably
    bounds, span_units, cell_units = _build_units(model)
    for t in rendered_texts:
        rb = t["bbox"]
        rc = ((rb[0] + rb[2]) / 2.0, (rb[1] + rb[3]) / 2.0)
        r = _pin_row(bounds, rc[1])
        sps = span_units.get(r, []) + cell_units.get(r, [])
        if not sps:
            continue
        hit = next((u for u in sps if u["text"] == t["text"]), None)
        if hit is None:
            cand = sorted(
                sps,
                key=lambda u: ((u["bbox"][0] + u["bbox"][2]) / 2.0 - rc[0]) ** 2
                + ((u["bbox"][1] + u["bbox"][3]) / 2.0 - rc[1]) ** 2)
            hit = cand[0]
        mb = hit["bbox"]
        mc = ((mb[0] + mb[2]) / 2.0, (mb[1] + mb[3]) / 2.0)
        if abs(rc[0] - mc[0]) > 1.5 or abs(rc[1] - mc[1]) > 1.5:
            draw.line([rc[0] * Z, rc[1] * Z, mc[0] * Z, mc[1] * Z],
                      fill=YL, width=1)

    _draw_legend(draw, img.width)
    doc.close()
    img.save(out_png)
    return out_png


def _dashed_line(draw, p0, p1, color, w, dash=(8, 6)):
    x0, y0 = p0
    x1, y1 = p1
    seg, gap = dash
    dx, dy = x1 - x0, y1 - y0
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0:
        return
    ux, uy = dx / length, dy / length
    pos = 0.0
    while pos < length:
        a = min(pos + seg, length)
        draw.line([x0 + ux * pos, y0 + uy * pos,
                   x0 + ux * a, y0 + uy * a], fill=color, width=w)
        pos = a + gap


def _dashed_rect(draw, x0, y0, x1, y1, color, w, dash):
    _dashed_line(draw, (x0, y0), (x1, y0), color, w, dash)
    _dashed_line(draw, (x1, y0), (x1, y1), color, w, dash)
    _dashed_line(draw, (x1, y1), (x0, y1), color, w, dash)
    _dashed_line(draw, (x0, y1), (x0, y0), color, w, dash)


def _draw_legend(draw, img_w):
    x = img_w - 250
    y = 10
    draw.rectangle([x, y, x + 240, y + 110], fill=(255, 255, 255),
                   outline=(60, 60, 60), width=1)
    items = [
        ((255, 170, 0), "original: table bbox + text bboxes"),
        ((0, 120, 255), "original: rules"),
        ((255, 0, 200), "round-trip: rendered text bboxes"),
        ((220, 0, 0), "round-trip: rendered rules"),
        ((255, 220, 0), "deviation connector (>1.5 pt)"),
    ]
    yy = y + 14
    for color, label in items:
        draw.line([x + 10, yy, x + 34, yy], fill=color, width=5)
        draw.text((x + 40, yy - 6), label, fill=(0, 0, 0))
        yy += 19
