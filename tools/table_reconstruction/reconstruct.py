"""Build the unified TableModel from a detected table ROI + PDF geometry.

Consumes the same JSON shape produced by the diagnostic tool
(``path_geometry.extract_geometry`` / ``path_babeldoc.extract_babeldoc``):

    {
      "bbox": [x0, y0, x1, y1],           # detected table ROI
      "texts": [{text, bbox, font, font_size}, ...],   # full page
      "horizontal_lines": [{x0,y0,x1,y1}, ...],
      "vertical_lines":   [{x0,y0,x1,y1}, ...],
      "rectangles":       [{x0,y0,x1,y1}, ...],
      "has_image": bool
    }

Produces the TableModel with columns / rows / cells, the inferred
``geometry_source``, header flags and a QA block.
"""

from __future__ import annotations

import statistics

try:  # package import
    from .classify import classify_table
    from .columns import reconstruct_columns
    from .rows import reconstruct_rows
except ImportError:  # direct script run
    from classify import classify_table
    from columns import reconstruct_columns
    from rows import reconstruct_rows


def _roi_filter(texts, roi, margin=0.5):
    rx0, ry0, rx1, ry1 = roi
    out = []
    for t in texts:
        b = t["bbox"]
        if (b[1] >= ry0 - margin and b[3] <= ry1 + margin and
                b[0] >= rx0 - margin and b[2] <= rx1 + margin):
            out.append(t)
    return out


def _intersect(bbox, roi, margin=2.0):
    x0, y0, x1, y1 = bbox
    rx0, ry0, rx1, ry1 = roi
    return not (x1 < rx0 - margin or x0 > rx1 + margin or
                y1 < ry0 - margin or y0 > ry1 + margin)


def _compute_qa(model, texts_in, n_assigned):
    total = len(texts_in)
    assigned_ratio = n_assigned / total if total else 0.0

    # per-column alignment: a column is "aligned" on whichever edge is
    # consistent (left edge for left-aligned text, right edge for right-aligned
    # numbers).  Using the tighter of the two edges avoids penalising a wide
    # left-aligned text column whose center naturally varies.
    per_col_edge_var = []
    cols = sorted({c["col"] for c in model["cells"]})
    for ci in cols:
        cells_in = [c for c in model["cells"] if c["col"] == ci]
        x0s = [c["bbox"][0] for c in cells_in]
        x1s = [c["bbox"][2] for c in cells_in]
        if len(x0s) > 1:
            v0 = statistics.pvariance(x0s)
            v1 = statistics.pvariance(x1s)
            per_col_edge_var.append(min(v0, v1))
    col_align_var = (round(statistics.mean(per_col_edge_var), 4)
                     if per_col_edge_var else 0.0)

    # per-row center-y variance (should be ~0: same line)
    row_cy = {}
    for c in model["cells"]:
        row_cy.setdefault(c["row"], []).append(
            (c["bbox"][1] + c["bbox"][3]) / 2.0)
    row_var = [statistics.pvariance(v) for v in row_cy.values() if len(v) > 1]
    row_align_var = round(statistics.mean(row_var), 4) if row_var else 0.0

    multi = sum(1 for c in model["cells"] if len(c["spans"]) > 1)
    avg_col_conf = (statistics.mean(model["column_analysis"]["column_confidence"])
                    if model["column_analysis"]["column_confidence"] else 0.0)
    # confidence blends column confidence, assignment coverage and low variance
    conf = (0.4 * avg_col_conf + 0.4 * assigned_ratio +
            0.2 * (1.0 - min(1.0, (col_align_var + row_align_var) / 50.0)))
    conf = round(max(0.0, min(1.0, conf)), 3)

    return {
        "table_type": model["table_type"],
        "row_count": len(model["rows"]),
        "column_count": len(model["columns"]),
        "assigned_text_ratio": round(assigned_ratio, 4),
        "unassigned_text_count": total - n_assigned,
        "column_alignment_variance": col_align_var,
        "row_alignment_variance": row_align_var,
        "cells_with_multiple_spans": multi,
        "reconstruction_confidence": conf,
    }


def _build_rules(h_in, v_in, roi):
    """Visible rules inside the ROI, from the real PDF vector drawings only.

    A sparse-rule / booktabs table yields just top, header separator and bottom
    horizontal lines -- no invented verticals.  Each rule carries its original
    span (x0..x1 / y) and stroke thickness so the HTML renderer can reproduce
    exactly what the PDF drew.
    """
    rules = []
    hs = sorted(h_in, key=lambda l: (l["y0"] + l["y1"]) / 2.0)
    n = len(hs)
    for i, l in enumerate(hs):
        y = round((l["y0"] + l["y1"]) / 2.0, 3)
        x0 = round(min(l["x0"], l["x1"]), 3)
        x1 = round(max(l["x0"], l["x1"]), 3)
        if n == 1:
            role = "single"
        elif i == 0:
            role = "top"
        elif i == n - 1:
            role = "bottom"
        else:
            role = "header_separator" if n == 3 else "inner"
        rules.append({
            "orientation": "horizontal",
            "role": role,
            "x0": x0, "x1": x1, "y": y,
            "thickness": round(float(l.get("width", 0.0) or 0.0), 3),
        })
    vs = sorted(v_in, key=lambda l: (l["x0"] + l["x1"]) / 2.0)
    m = len(vs)
    for i, l in enumerate(vs):
        x = round((l["x0"] + l["x1"]) / 2.0, 3)
        y0 = round(min(l["y0"], l["y1"]), 3)
        y1 = round(max(l["y0"], l["y1"]), 3)
        if m == 1:
            role = "single"
        elif i == 0:
            role = "left"
        elif i == m - 1:
            role = "right"
        else:
            role = "inner"
        rules.append({
            "orientation": "vertical",
            "role": role,
            "y0": y0, "y1": y1, "x": x,
            "thickness": round(float(l.get("width", 0.0) or 0.0), 3),
        })
    return rules


def _row_layout_boundaries(rows, top_rule_y, bottom_rule_y, header_sep_y,
                            header_idx, roi):
    """True row layout boundaries from row centers + the visible rules.

    boundary[0] = top rule (or ROI top); boundary[n] = bottom rule (or ROI
    bottom); interior boundaries = midpoints between adjacent row centers, with
    the header/body divider pinned to the real header-separator rule.
    """
    n = len(rows)
    if n == 0:
        return []
    centers = [(r["y0"] + r["y1"]) / 2.0 for r in rows]
    top = top_rule_y if top_rule_y is not None else roi[1]
    bottom = bottom_rule_y if bottom_rule_y is not None else roi[3]
    bounds = [top]
    for i in range(n - 1):
        bounds.append((centers[i] + centers[i + 1]) / 2.0)
    bounds.append(bottom)
    if (header_idx is not None and 0 <= header_idx < n - 1
            and header_sep_y is not None):
        bounds[header_idx + 1] = header_sep_y
    return [round(b, 3) for b in bounds]


def _infer_alignment(columns, cells):
    """Infer left/center/right per column from body-row text-edge stability.

    The decisive signal is which *edge* is consistent across rows (low
    variance), not its distance to the logical column boundary.  A right-aligned
    numeric column is inset inside its logical column (e.g. digits end at ~392
    while the column boundary is 409) yet its right edge is rock-steady -> it is
    right-aligned with anchor = the stable right edge.  No hardcoding of column
    names or content -- purely geometric (variance of left / right / center).
    """
    result = {}
    for c in columns:
        ci = c["index"]
        x0, x1 = c["x0"], c["x1"]
        body = [cc for cc in cells if cc["col"] == ci and not cc["is_header"]]
        if not body:
            body = [cc for cc in cells if cc["col"] == ci]
        if not body:
            result[ci] = {"alignment": "left", "anchor_x": round(x0, 3)}
            continue
        lefts = [cc["content_bbox"][0] for cc in body]
        rights = [cc["content_bbox"][2] for cc in body]
        cents = [(cc["content_bbox"][0] + cc["content_bbox"][2]) / 2.0
                 for cc in body]
        vL = statistics.pvariance(lefts) if len(lefts) > 1 else 0.0
        vR = statistics.pvariance(rights) if len(rights) > 1 else 0.0
        vC = statistics.pvariance(cents) if len(cents) > 1 else 0.0
        if vL <= vR and vL <= vC:
            align, anchor = "left", round(sum(lefts) / len(lefts), 3)
        elif vR <= vC:
            align, anchor = "right", round(sum(rights) / len(rights), 3)
        else:
            align, anchor = "center", round(sum(cents) / len(cents), 3)
        result[ci] = {"alignment": align, "anchor_x": anchor}
    return result


def build_table_model(input_data):
    roi = [float(v) for v in input_data["bbox"]]
    all_texts = input_data["texts"]
    all_h = input_data["horizontal_lines"]
    all_v = input_data["vertical_lines"]
    all_r = input_data.get("rectangles", [])
    has_image = input_data.get("has_image", False)

    texts_in = _roi_filter(all_texts, roi)
    h_in = [l for l in all_h
            if _intersect([l["x0"], l["y0"], l["x1"], l["y1"]], roi)]
    v_in = [l for l in all_v
            if _intersect([l["x0"], l["y0"], l["x1"], l["y1"]], roi)]
    r_in = [r for r in all_r if _intersect(r["bbox"], roi)]

    table_type, cls_info = classify_table(
        roi, texts_in, h_in, v_in, r_in, has_image)

    col_res = reconstruct_columns(roi, texts_in, h_in, v_in)
    columns = col_res["columns"]
    row_res = reconstruct_rows(roi, texts_in, h_in)
    rows = row_res["rows"]
    spans_by_row = row_res["spans_by_row"]
    header_idx = row_res["header_row_index"]

    def assign_col(cx):
        for c in columns:
            if c["x0"] <= cx < c["x1"]:
                return c["index"]
        # center falls in a gap -> nearest column
        return min(columns, key=lambda c: abs(cx - (c["x0"] + c["x1"]) / 2.0))["index"]

    # ---- visible rules (real PDF vector geometry only) ----
    rules = _build_rules(h_in, v_in, roi)
    top_rule_y = next((r["y"] for r in rules if r["role"] == "top"), None)
    bottom_rule_y = next((r["y"] for r in rules if r["role"] == "bottom"), None)
    header_sep_y = next((r["y"] for r in rules
                         if r["role"] == "header_separator"), None)

    # ---- true row layout boundaries (row centers + rules) ----
    row_bounds = _row_layout_boundaries(
        rows, top_rule_y, bottom_rule_y, header_sep_y, header_idx, roi)
    columns_by_idx = {c["index"]: c for c in columns}

    cells = []
    n_assigned = 0
    for r_idx, spans in spans_by_row.items():
        groups = {}
        for t in spans:
            cx = (t["bbox"][0] + t["bbox"][2]) / 2.0
            groups.setdefault(assign_col(cx), []).append(t)
        for ci, grp in groups.items():
            grp_sorted = sorted(grp, key=lambda t: t["bbox"][0])
            x0 = min(t["bbox"][0] for t in grp_sorted)
            y0 = min(t["bbox"][1] for t in grp_sorted)
            x1 = max(t["bbox"][2] for t in grp_sorted)
            y1 = max(t["bbox"][3] for t in grp_sorted)
            source = " ".join(t["text"] for t in grp_sorted)
            spans_out = [{
                "text": t["text"], "bbox": t["bbox"],
                "font": t.get("font"), "font_size": t.get("font_size"),
            } for t in grp_sorted]
            content_bbox = [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]
            # layout bbox = column x  x  row layout y (logical cell region)
            col = columns_by_idx[ci]
            ry0 = row_bounds[r_idx]
            ry1 = row_bounds[r_idx + 1]
            layout_bbox = [round(col["x0"], 3), ry0,
                           round(col["x1"], 3), ry1]
            baseline = round(max(t["bbox"][3] for t in grp_sorted), 3)
            cells.append({
                "row": r_idx,
                "col": ci,
                "rowspan": 1,
                "colspan": 1,
                "bbox": content_bbox,            # legacy alias == content bbox
                "content_bbox": content_bbox,
                "layout_bbox": layout_bbox,
                "source_text": source,
                "spans": spans_out,
                "is_header": (r_idx == header_idx),
                "baseline": baseline,
            })
            n_assigned += len(grp_sorted)

    # ---- per-column / per-cell horizontal alignment (geometric) ----
    col_align = _infer_alignment(columns, cells)
    for c in columns:
        c["alignment"] = col_align[c["index"]]["alignment"]
        c["anchor_x"] = col_align[c["index"]]["anchor_x"]
    for cc in cells:
        cc["alignment"] = columns_by_idx[cc["col"]]["alignment"]

    geometry_source = ("explicit_border" if table_type == "full_grid"
                       else "inferred_from_alignment")

    model = {
        "bbox": [round(v, 3) for v in roi],
        "table_type": table_type,
        "geometry_source": geometry_source,
        "columns": columns,
        "rows": rows,
        "cells": cells,
        "rules": rules,
        "row_layout_boundaries": row_bounds,
        "classification": cls_info,
        "column_analysis": {
            "column_centers": col_res["column_centers"],
            "column_boundaries": col_res["column_boundaries"],
            "column_confidence": col_res["column_confidence"],
            "gaps": col_res["gaps"],
        },
        "row_analysis": {
            "row_centers": row_res.get("row_centers", []),
            "header_row_index": header_idx,
        },
        "bbox_semantics": {
            "bbox": "alias of content_bbox (PDF text span union)",
            "content_bbox": "union of original PDF text spans",
            "layout_bbox": "column x-range x row layout y-range (logical cell)",
        },
    }

    model["qa"] = _compute_qa(model, texts_in, n_assigned)
    return model
