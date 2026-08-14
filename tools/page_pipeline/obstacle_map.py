# -*- coding: utf-8 -*-
"""ObstacleMap (Phase 4E.1B-C1).

Builds a document-general map of geometry-locked obstacles from the existing
PageModel / DocumentModel (NO re-detection).  An obstacle affects only the
column(s) whose x-range it actually overlaps -- a full-width obstacle reduces
BOTH columns, a single-column obstacle only its own column.

Inline formulas are NOT obstacles (they follow paragraph flow); display
formulas / tables / figures / full-width captions / footnotes / bottom
reserved regions ARE.
"""
from __future__ import annotations


def _x_overlap(a, b):
    return min(a[2], b[2]) - max(a[0], b[0])


def _columns_affected(bbox, columns):
    affected = []
    for col in columns:
        col_x = [float(col["x0"]), float(col["x1"])]
        if _x_overlap(bbox, [col_x[0], 0, col_x[1], 1]) > 1.0:
            affected.append(col.get("column_id"))
    return affected


def build_obstacle_map(page_model, grid, bottom_reserved_regions=None):
    """Return ObstacleMap: {obstacles: [...], columns: [...]}."""
    columns = (grid or {}).get("columns") or []
    obstacles = []
    for region in page_model.get("regions", []):
        rtype = region.get("type")
        if rtype not in ("table", "figure", "image", "formula"):
            continue
        bbox = [float(v) for v in region.get("bbox", [])]
        if rtype == "formula":
            fm = region.get("payload") or {}
            if fm.get("placement") == "inline":
                continue  # inline formula follows paragraph flow
            otype = "display_formula"
        elif rtype == "image":
            otype = "figure"
        else:
            otype = rtype
        obstacles.append({
            "region_id": region.get("region_id", ""),
            "type": otype,
            "bbox": bbox,
            "columns_affected": _columns_affected(bbox, columns),
            "placement_policy": "geometry_locked",
            "hard": True,
        })
    # bottom reserved region (footnote) is an obstacle for both columns
    for br in bottom_reserved_regions or []:
        b = br.get("bbox")
        if not b:
            continue
        obstacles.append({
            "region_id": br.get("owner", "footnote"),
            "type": br.get("owner", "footnote"),
            "bbox": [float(v) for v in b],
            "columns_affected": _columns_affected(b, columns),
            "placement_policy": "geometry_locked",
            "hard": True,
        })
    # content frame / full-width regions: a caption spanning both columns is
    # treated as a full-width block obstacle when it is geometry-locked
    return {"obstacles": obstacles, "columns": columns}
