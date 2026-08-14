# -*- coding: utf-8 -*-
"""Closure audit for subtracting hard obstacles from column flow regions."""
from __future__ import annotations

import re
from typing import Any


FLOW_TEXT_ROLES = {
    "body", "body_bold_lead", "list_item", "reference",
    "reference_item", "reference_continuation", "appendix",
    "appendix_body", "heading", "appendix_heading",
}


def semantic_content_bounds(page_model: dict[str, Any] | None,
                            grid: dict[str, Any]) -> tuple[float, float]:
    """Resolve the vertical flow envelope from page semantics.

    The x-projection grid is useful for column tracks, but obstacle-heavy
    pages can give it a false ``content_frame.y1`` (for example, the bottom
    of a table rather than the bottom of the page's text area).  This helper
    derives only the *vertical* envelope from ordinary flow text, locked
    geometry, and a semantic footer/page-number boundary.  It contains no
    document or page-number policy.
    """
    model = page_model or {}
    frame = grid.get("content_frame") or {}
    page_height = float(grid.get("page_height") or model.get("height") or 0.0)
    starts: list[float] = []
    bottoms: list[float] = []
    footer_starts: list[float] = []

    for region in model.get("regions", []):
        payload = region.get("payload") or {}
        bbox = region.get("bbox") or payload.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        box = [float(value) for value in bbox]
        rtype = region.get("type")
        if rtype in {"table", "figure", "image"} or (
                rtype == "formula" and payload.get("placement") != "inline"):
            starts.append(box[1])
            bottoms.append(box[3])
            continue
        if rtype != "text":
            continue

        role = (payload.get("semantic_role")
                or payload.get("style_role") or "body")
        text = (payload.get("source_text") or "").strip()
        explicit_footer = role in {"page_number", "footer", "header_footer"}
        numeric_footer = bool(re.fullmatch(
            r"(?:page\s*)?\d+(?:\s*/\s*\d+)?", text, re.I))
        near_bottom = bool(page_height and box[1] >= page_height * .86)
        if explicit_footer or (numeric_footer and near_bottom):
            footer_starts.append(box[1])
            continue
        # Running headers/watermarks do not define the body start.  A normal
        # column paragraph at the top is retained; only very-top, non-column
        # material is ignored.
        if page_height and box[3] <= page_height * .07 \
                and payload.get("column") not in (0, 1):
            continue
        if payload.get("column") in (0, 1) and role in FLOW_TEXT_ROLES:
            starts.append(box[1])
            bottoms.append(box[3])

    default_y0 = float(frame.get("y0", 0.0))
    default_y1 = float(frame.get("y1", page_height))
    y0 = min(starts) if starts else default_y0
    y1 = max([default_y1, *bottoms]) if bottoms else default_y1
    if footer_starts:
        # A detected footer/page number is the usable bottom boundary itself;
        # whitespace between the last source paragraph and that boundary is
        # available capacity, not evidence that the page ends early.
        y1 = min(footer_starts)
    if page_height:
        y0 = max(0.0, min(y0, page_height))
        y1 = max(y0, min(y1, page_height))
    return round(y0, 3), round(y1, 3)


def _x_overlap(a: list[float], b: list[float]) -> float:
    return min(a[2], b[2]) - max(a[0], b[0])


def _merge(rows: list[list[float]]) -> list[list[float]]:
    result = []
    for start, stop in sorted(rows):
        if stop <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], stop)
        else:
            result.append([start, stop])
    return result


def _subtract(base: list[float], blocked: list[list[float]]) -> list[list[float]]:
    cursor, result = base[0], []
    for start, stop in blocked:
        start, stop = max(base[0], start), min(base[1], stop)
        if stop <= base[0] or start >= base[1] or stop <= start:
            continue
        if start > cursor:
            result.append([cursor, start])
        cursor = max(cursor, stop)
    if cursor < base[1]:
        result.append([cursor, base[1]])
    return result


def audit_flow_interval_closure(grid: dict[str, Any],
                                obstacles: list[dict[str, Any]],
                                flow_intervals: list[dict[str, Any]],
                                *, base_y0: float | None = None,
                                base_y1: float | None = None,
                                min_interval: float = 12.0,
                                tolerance: float = 1.0) -> dict[str, Any]:
    frame = grid.get("content_frame") or {}
    y0 = float(frame.get("y0", 0.0) if base_y0 is None else base_y0)
    y1 = float(frame.get("y1", grid.get("page_height", 0.0))
               if base_y1 is None else base_y1)
    actual_by_col = {row.get("column"): row for row in flow_intervals}
    records = []
    counts = {"lost_interval_count": 0,
              "phantom_blocked_interval_count": 0,
              "interval_overlap_count": 0,
              "negative_interval_count": 0}
    for column in grid.get("columns") or []:
        cid = column.get("column_id")
        track = [float(column["x0"]), y0, float(column["x1"]), y1]
        trace = []
        blocked_raw = []
        for obstacle in obstacles or []:
            bbox = [float(v) for v in obstacle.get("bbox", [])]
            if len(bbox) < 4 or not obstacle.get("hard", True):
                continue
            if _x_overlap(bbox, track) <= 1.0:
                continue
            clipped = [max(y0, bbox[1]), min(y1, bbox[3])]
            if clipped[1] > clipped[0]:
                blocked_raw.append(clipped)
                trace.append({"operation": "subtract",
                              "region_id": obstacle.get("region_id"),
                              "type": obstacle.get("type"),
                              "bbox": bbox, "clipped_y": clipped})
        blocked = _merge(blocked_raw)
        expected_all = _subtract([y0, y1], blocked)
        expected_kept = [row for row in expected_all if row[1]-row[0] >= min_interval]
        expected_discarded = [row for row in expected_all if row[1]-row[0] < min_interval]
        actual = [[float(v) for v in row] for row in
                  (actual_by_col.get(cid, {}).get("usable_y_intervals") or [])]
        for first, second in zip(sorted(actual), sorted(actual)[1:]):
            if second[0] < first[1] - tolerance:
                counts["interval_overlap_count"] += 1
        counts["negative_interval_count"] += sum(1 for a, b in actual if b < a)

        def matches(row: list[float], rows: list[list[float]]) -> bool:
            return any(abs(row[0]-candidate[0]) <= tolerance
                       and abs(row[1]-candidate[1]) <= tolerance
                       for candidate in rows)

        lost = [row for row in expected_kept if not matches(row, actual)]
        phantom = [row for row in actual if not matches(row, expected_kept)]
        counts["lost_interval_count"] += len(lost)
        counts["phantom_blocked_interval_count"] += len(phantom)
        base_height = y1-y0
        blocked_height = sum(stop-start for start, stop in blocked)
        free_height = sum(stop-start for start, stop in expected_all)
        records.append({
            "column": cid,
            "track": [track[0], track[2]],
            "base_interval": [round(y0, 3), round(y1, 3)],
            "subtraction_trace": trace,
            "merged_blocked_intervals": [[round(a, 3), round(b, 3)] for a, b in blocked],
            "expected_free_intervals_before_min_filter": [[round(a, 3), round(b, 3)] for a, b in expected_all],
            "discarded_below_min_interval": [[round(a, 3), round(b, 3)] for a, b in expected_discarded],
            "expected_final_intervals": [[round(a, 3), round(b, 3)] for a, b in expected_kept],
            "actual_final_intervals": [[round(a, 3), round(b, 3)] for a, b in actual],
            "lost_intervals": lost,
            "phantom_intervals": phantom,
            "height_closure": {
                "base_height": round(base_height, 3),
                "blocked_height": round(blocked_height, 3),
                "free_height_before_min_filter": round(free_height, 3),
                "difference": round(base_height-blocked_height-free_height, 6),
                "within_tolerance": abs(base_height-blocked_height-free_height) <= tolerance,
            },
        })
    clean = all(value == 0 for value in counts.values()) and all(
        record["height_closure"]["within_tolerance"] for record in records)
    return {
        "base_region_policy": "content top to semantic content bottom/page footer boundary",
        **counts,
        "flow_interval_closure_clean": clean,
        "columns": records,
    }
