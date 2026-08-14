# -*- coding: utf-8 -*-
"""PageCapacityModel (Phase 4E.1B).

Per-column available-flow-interval planning.  A page's usable vertical space is
split by LOCKED obstacles (figure / table / display formula / caption /
footnote reserved region).  An obstacle only consumes capacity in the
column(s) it actually overlaps -- a full-width figure reduces BOTH columns, a
single-column figure reduces only that column.

Also classifies page complexity from region composition (never page number).
"""
from __future__ import annotations

from typing import Any


def _intervals(usable_y0, usable_y1, obstacles_y):
    """y intervals left after removing obstacle y-ranges (sorted, merged)."""
    spans = sorted([(max(usable_y0, y0), min(usable_y1, y1))
                    for y0, y1 in obstacles_y if y1 > usable_y0 and y0 < usable_y1])
    merged = []
    for y0, y1 in spans:
        if merged and y0 <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], y1))
        else:
            merged.append((y0, y1))
    out = []
    cursor = usable_y0
    for y0, y1 in merged:
        if y0 > cursor:
            out.append([round(cursor, 2), round(y0, 2)])
        cursor = max(cursor, y1)
    if cursor < usable_y1:
        out.append([round(cursor, 2), round(usable_y1, 2)])
    return out


def _overlap_x(a, b):
    return min(a[2], b[2]) - max(a[0], b[0])


def column_capacity(column, grid, obstacles, required_text_height):
    """Capacity for one column given the locked obstacle boxes."""
    cx0, cx1 = column["x0"], column["x1"]
    cf = (grid or {}).get("content_frame") or {}
    usable_y0 = float(cf.get("y0", 0.0))
    usable_y1 = float(cf.get("y1", 792.0))
    col_obs = []
    for ob in obstacles:
        if _overlap_x([ob[0], 0, ob[2], 1], [cx0, 0, cx1, 1]) > 1.0:
            col_obs.append(ob)
    intervals = _intervals(usable_y0, usable_y1, [(ob[1], ob[3]) for ob in col_obs])
    available = sum(b - a for a, b in intervals)
    required = max(float(required_text_height or 0.0), 0.0)
    overflow = max(0.0, required - available)
    return {
        "column_id": column.get("column_id"),
        "usable_y0": round(usable_y0, 2),
        "usable_y1": round(usable_y1, 2),
        "obstacle_count": len(col_obs),
        "available_intervals": intervals,
        "available_height": round(available, 2),
        "required_text_height": round(required, 2),
        "capacity_ratio": round(available / max(required, 1e-6), 3),
        "overflow_amount": round(overflow, 2),
    }


def classify_complexity(regions, semantic_role="normal_body"):
    """Mixed-content / density classification from region composition."""
    types = {}
    for r in regions:
        types[r.get("type")] = types.get(r.get("type"), 0) + 1
    n_formula = types.get("formula", 0)
    n_table = types.get("table", 0)
    n_fig = types.get("figure", 0) + types.get("image", 0)
    if semantic_role == "references":
        return "reference_dense"
    has_math = n_formula >= 1
    has_fig = n_fig >= 1
    has_tab = n_table >= 1
    if has_fig and has_tab and has_math:
        return "mixed_content"
    if has_fig and has_math:
        return "mixed_content"
    if has_tab and has_math:
        return "mixed_content"
    if has_fig and has_tab:
        return "mixed_content"
    if n_formula >= 8:
        return "formula_dense"
    if n_table >= 2:
        return "table_dense"
    if n_fig >= 2:
        return "figure_dense"
    if n_table >= 1:
        return "table_dense"
    if n_formula >= 1:
        return "formula_dense"
    if n_fig >= 1:
        return "figure_dense"
    return "text_only"
