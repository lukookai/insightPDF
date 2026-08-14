"""Phase 2B QA: Layout Fidelity / Anchor Fidelity / Containment.

Compared with the English round-trip, "rendered text bbox == original English
text bbox" is NOT a valid metric here (translation changes width by design).
Instead we check:

  A. Layout Fidelity -- column/row/rule/table geometry must match the model
     just like the English round-trip did (the HTML keeps them frozen).
  B. Anchor Fidelity  -- left-aligned cells: |rendered.x0 - anchor_x|,
     centre-aligned cells: |rendered.centre - anchor_x|, plus baseline error.
  C. Containment       -- rendered text bbox inside the cell layout_bbox
     (overflow / cross-column / clipped counts).

Rendered spans are matched back to cells by text equality (Chinese translations
are unique per cell), with concatenation for Chromium's span splitting.
"""

from __future__ import annotations

import json

import pymupdf

_OVERFLOW_TOL = 0.75   # pt Chromium print grid rounding


def extract_zh_rendered(pdf_path):
    """Return rendered text spans (bbox + text) from the translated PDF."""
    doc = pymupdf.open(pdf_path)
    pg = doc[0]
    spans = []
    for block in pg.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = (span.get("text") or "").strip()
                if not txt:
                    continue
                b = [float(v) for v in span["bbox"]]
                spans.append({"text": txt, "bbox": b,
                              "font": span.get("font")})
    # rules
    rules = []
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
                    rules.append({"y": (ay + by) / 2.0,
                                  "x0": min(ax, bx), "x1": max(ax, bx),
                                  "thickness": float(item.get("width") or 0.0)})
                    continue
        if kind in ("f", "fs", "s") and (y1 - y0) <= 1.0 and (x1 - x0) > 4.0:
            rules.append({"y": (y0 + y1) / 2.0, "x0": x0, "x1": x1,
                          "thickness": max(y1 - y0,
                                           float(item.get("width") or 0.0))})
    doc.close()
    return spans, rules


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def cell_text(model, cell):
    st = cell.get("translation_status")
    if st == "translated" and cell.get("translated_text"):
        return cell["translated_text"]
    return cell.get("source_text") or ""


def match_spans_to_cells(model, rendered_spans):
    """{cell_id: {"rendered_bbox": [..], "fonts": [...], "spans": [..]}}."""
    by_id = {}
    for cell in model["cells"]:
        cid = "R%dC%d" % (cell["row"], cell["col"])
        text = cell_text(model, cell)
        by_id[cid] = {"text": text, "cell": cell, "rendered": [],
                      "bbox": None, "fonts": set()}
    for sp in rendered_spans:
        t = sp["text"]
        hit = None
        # exact whole-cell text match
        for cid, info in by_id.items():
            if info["text"] and t == info["text"] and not info["rendered"]:
                hit = cid
                break
        if hit is None:
            # span is a piece of a cell (Chromium split the line)
            for cid, info in by_id.items():
                if info["text"] and info["text"] in t:
                    hit = cid
                    break
        if hit is None:
            # cell text contained in the span (merged spans)
            for cid, info in by_id.items():
                if info["text"] and t in info["text"]:
                    hit = cid
                    break
        if hit is None:
            continue
        by_id[hit]["rendered"].append(sp)
        by_id[hit]["fonts"].add(sp.get("font") or "")
    for info in by_id.values():
        if info["rendered"]:
            info["bbox"] = _union([s["bbox"] for s in info["rendered"]])
    return by_id


def compute_metrics(model, rendered_spans, rendered_rules, *, state):
    """state: dict from the fit pipeline (overflow_before/after, shrinks...)."""
    matched = match_spans_to_cells(model, rendered_spans)
    cols = {c["index"]: c for c in model["columns"]}

    layout_left_errors, layout_right_errors = [], []
    layout_top_errors, layout_bottom_errors = [], []
    left_anchor_errors, center_anchor_errors, baseline_errors = [], [], []
    overflow_cells, cross_column_cells = set(), set()
    clipped = set()
    per_cell = {}
    for cid, info in matched.items():
        cell = info["cell"]
        rb = info["bbox"]
        if rb is None:
            continue
        layout = cell["layout_bbox"]
        col = cols.get(cell["col"], {})
        align = cell.get("alignment") or col.get("alignment", "left")
        anchor = cell.get("anchor_x")
        if anchor is None:
            anchor = col.get("anchor_x", layout[0])
        baseline = cell.get("baseline")
        if baseline is None:
            baseline = layout[3]
        tolx = _OVERFLOW_TOL

        # containment
        ox0 = max(0.0, layout[0] - tolx - rb[0])
        ox1 = max(0.0, rb[2] - (layout[2] + tolx))
        oy0 = max(0.0, layout[1] - tolx - rb[1])
        oy1 = max(0.0, rb[3] - (layout[3] + tolx))
        overflow = (ox0 + ox1 + oy0 + oy1) > 0
        if ox0 + ox1 > 0:
            cross_column_cells.add(cid)
        if overflow:
            overflow_cells.add(cid)
        layout_left_errors.append(ox0)
        layout_right_errors.append(ox1)
        layout_top_errors.append(oy0)
        layout_bottom_errors.append(oy1)

        # anchor fidelity
        if align == "left":
            left_anchor_errors.append(abs(rb[0] - anchor))
        elif align == "center":
            center_anchor_errors.append(abs((rb[0] + rb[2]) / 2.0 - anchor))
        baseline_errors.append(abs(rb[3] - baseline))

        # shrink/clip state
        per_cell[cid] = {
            "row": cell["row"], "col": cell["col"],
            "alignment": align,
            "text": cell_text(model, cell),
            "rendered_bbox": [round(v, 3) for v in rb],
            "layout_bbox": [round(v, 3) for v in layout],
            "overflow": bool(overflow),
            "cross_column": (ox0 + ox1) > 0,
            "render_font_size": cell.get("render_font_size"),
            "fit_strategy": cell.get("fit_strategy"),
            "fonts": sorted(info["fonts"]),
        }

    # ---- layout fidelity: rules + table geometry ----
    model_rules = [r for r in model.get("rules", [])
                   if r["orientation"] == "horizontal"]
    rule_position_max = 0.0
    rule_thickness_max = 0.0
    for mr in model_rules:
        cand = [r for r in rendered_rules if abs(r["y"] - mr["y"]) < 3.0]
        if not cand:
            continue
        r = min(cand, key=lambda r: abs(r["y"] - mr["y"]))
        rule_position_max = max(rule_position_max, abs(r["y"] - mr["y"]),
                                abs(r["x0"] - mr["x0"]), abs(r["x1"] - mr["x1"]))
        rule_thickness_max = max(rule_thickness_max,
                                 abs(r["thickness"] - mr.get("thickness", 0.0)))

    # table geometry: rendered text+rules extent vs model visual bbox
    vb = model.get("visual_bbox")
    if vb:
        boxes = [info["bbox"] for info in matched.values() if info["bbox"]]
        boxes += [[r["x0"], r["y"] - 0.2, r["x1"], r["y"] + 0.2]
                  for r in rendered_rules]
        if boxes:
            ext = _union(boxes)
            table_bbox_max_error = max(abs(ext[0] - vb[0]), abs(ext[1] - vb[1]),
                                       abs(ext[2] - vb[2]), abs(ext[3] - vb[3]))
        else:
            table_bbox_max_error = 0.0
    else:
        table_bbox_max_error = None

    return {
        "table_bbox_max_error_pt": (round(table_bbox_max_error, 3)
                                    if table_bbox_max_error is not None else None),
        "column_boundary_max_error_pt": round(
            max((max(layout_left_errors + [0.0])),
                (max(layout_right_errors + [0.0]))), 3),
        "row_boundary_max_error_pt": round(
            max((max(layout_top_errors + [0.0])),
                (max(layout_bottom_errors + [0.0]))), 3),
        "rule_position_max_error_pt": round(rule_position_max, 3),
        "rule_thickness_max_error_pt": round(rule_thickness_max, 3),
        "left_anchor_max_error_pt": round(max(left_anchor_errors or [0.0]), 3),
        "center_anchor_max_error_pt": round(max(center_anchor_errors or [0.0]), 3),
        "baseline_max_error_pt": round(max(baseline_errors or [0.0]), 3),
        "overflow_cells": sorted(overflow_cells),
        "cross_column_cells": sorted(cross_column_cells),
        "clipped_cells": sorted(clipped),
        "final_overflow_count": len(overflow_cells),
        "cross_column_count": len(cross_column_cells),
        "rendered_rule_count": len(rendered_rules),
        "rendered_fonts": sorted({sp.get("font") or "" for sp in rendered_spans}),
        "per_cell": per_cell,
    }


def render_debug_overlay(model, rendered_spans, out_png, src_pdf, page,
                         zoom=2.0):
    """Show logical cell boundaries, rendered Chinese bbox, anchor, overflow."""
    from PIL import Image, ImageDraw

    matched = match_spans_to_cells(model, rendered_spans)
    doc = pymupdf.open(src_pdf)
    pg = doc[page - 1]
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    draw = ImageDraw.Draw(img)
    Z = zoom
    CELL_B = (0, 90, 200)        # logical cell boundary (blue dashed)
    ZH = (255, 0, 200)           # rendered Chinese bbox (magenta)
    AN = (0, 180, 0)             # anchor (green)
    OVF = (255, 0, 0)            # overflow (red)
    RULE = (255, 170, 0)         # rules (orange)

    cols = {c["index"]: c for c in model["columns"]}
    # logical cell boundaries (layout_bbox) -- dashed blue
    for cell in model["cells"]:
        x0, y0, x1, y1 = [v * Z for v in cell["layout_bbox"]]
        _dashed_rect(draw, x0, y0, x1, y1, CELL_B, 1, (6, 5))
    # rules
    for r in model.get("rules", []):
        if r["orientation"] == "horizontal":
            draw.line([r["x0"] * Z, r["y"] * Z, r["x1"] * Z, r["y"] * Z],
                      fill=RULE, width=4)
    # rendered Chinese bbox + anchor + overflow
    for cid, info in matched.items():
        cell = info["cell"]
        if info["bbox"] is None:
            continue
        rb = [v * Z for v in info["bbox"]]
        draw.rectangle(rb, outline=ZH, width=2)
        layout = cell["layout_bbox"]
        col = cols.get(cell["col"], {})
        align = cell.get("alignment") or col.get("alignment", "left")
        anchor = cell.get("anchor_x")
        if anchor is None:
            anchor = col.get("anchor_x", layout[0])
        ax = anchor * Z
        ay0 = (layout[1] - 2.0) * Z
        ay1 = (layout[3] + 2.0) * Z
        draw.line([ax, ay0, ax, ay1], fill=AN, width=2)
        # overflow flag
        tolx = _OVERFLOW_TOL
        over = (info["bbox"][0] < layout[0] - tolx
                or info["bbox"][2] > layout[2] + tolx
                or info["bbox"][1] < layout[1] - tolx
                or info["bbox"][3] > layout[3] + tolx)
        if over:
            draw.rectangle(rb, outline=OVF, width=4)

    _legend(draw, img.width)
    doc.close()
    img.save(out_png)
    return out_png


def _legend(draw, img_w):
    x, y = img_w - 265, 10
    draw.rectangle([x, y, x + 255, y + 115], fill=(255, 255, 255),
                   outline=(60, 60, 60), width=1)
    items = [
        ((0, 90, 200), "logical cell boundary (layout_bbox)"),
        ((255, 0, 200), "rendered Chinese text bbox"),
        ((0, 180, 0), "anchor_x"),
        ((255, 170, 0), "original rules"),
        ((255, 0, 0), "overflow cell (red outline)"),
    ]
    yy = y + 14
    for color, label in items:
        draw.line([x + 10, yy, x + 34, yy], fill=color, width=5)
        draw.text((x + 40, yy - 6), label, fill=(0, 0, 0))
        yy += 20


def _dashed_line(draw, p0, p1, color, w, dash=(6, 5)):
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


def save_per_cell_json(path, per_cell):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(per_cell, f, ensure_ascii=False, indent=2)
