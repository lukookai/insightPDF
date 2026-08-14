# -*- coding: utf-8 -*-
"""ColumnFlowIntervals (Phase 4E.1B-C1).

For each column, compute the contiguous usable y-intervals left after the
locked obstacles.  A full-width obstacle splits BOTH columns; a single-column
obstacle splits only its own column.  Intervals smaller than a minimum line
capacity are discarded.
"""
from __future__ import annotations


MIN_INTERVAL_HEIGHT = 12.0  # at least one body line


def _merge_spans(spans):
    spans = sorted([(y0, y1) for y0, y1 in spans if y1 > y0])
    merged = []
    for y0, y1 in spans:
        if merged and y0 <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], y1))
        else:
            merged.append((y0, y1))
    return merged


def column_flow_intervals(column, grid, obstacles, usable_y1=None,
                          min_interval=MIN_INTERVAL_HEIGHT):
    """usable y-intervals for ONE column after removing its obstacles."""
    cx0, cx1 = float(column["x0"]), float(column["x1"])
    cf = (grid or {}).get("content_frame") or {}
    y0 = float(cf.get("y0", 0.0))
    y1 = float(usable_y1 if usable_y1 is not None else cf.get("y1", 792.0))
    col_obstacle_y = []
    for ob in obstacles or []:
        b = ob["bbox"]
        if _x_overlap([b[0], 0, b[2], 1], [cx0, 0, cx1, 1]) > 1.0:
            col_obstacle_y.append((float(b[1]), float(b[3])))
    spans = _merge_spans(col_obstacle_y)
    intervals = []
    cursor = y0
    for oy0, oy1 in spans:
        if oy0 > cursor and oy1 - oy0 <= 0:
            pass
        if oy0 > cursor:
            gap = oy0 - cursor
            if gap >= min_interval:
                intervals.append([round(cursor, 2), round(oy0, 2)])
        cursor = max(cursor, oy1)
    if y1 > cursor and y1 - cursor >= min_interval:
        intervals.append([round(cursor, 2), round(y1, 2)])
    usable = sum(b - a for a, b in intervals)
    return {
        "column": column.get("column_id"),
        "track": [round(cx0, 2), round(cx1, 2)],
        "usable_y_intervals": intervals,
        "usable_height": round(usable, 2),
    }


def all_columns_flow_intervals(grid, obstacles, usable_y1=None,
                               min_interval=MIN_INTERVAL_HEIGHT):
    out = []
    for column in (grid or {}).get("columns") or []:
        out.append(column_flow_intervals(column, grid, obstacles,
                                         usable_y1, min_interval))
    return out


def _x_overlap(a, b):
    return min(a[2], b[2]) - max(a[0], b[0])
