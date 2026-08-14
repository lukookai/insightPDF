# -*- coding: utf-8 -*-
"""AdaptiveCapacityPlanner (Phase 4E.1B-C1).

Runs CapacitySimulator through the adaptation ladder L0..L6 and reports the
minimum necessary adaptation, or declares capacity_unresolved (BLOCK).
Everything is document-general: params are derived from the page geometry,
obstacles, intervals, semantic roles and the readability guard -- never from a
page number.
"""
from __future__ import annotations

from capacity_simulator import CapacitySimulator

# readability guard (typography-safe lower bounds)
MIN_BODY_FONT_RATIO = 0.90
MIN_LINE_HEIGHT_RATIO = 1.15
MIN_REFERENCE_FONT_RATIO = 0.85

LADDER = [
    # (level, font_scale, line_height_ratio, gap_scale, note)
    (0, 1.00, 1.30, 1.00, "source-faithful"),
    (1, 1.00, 1.30, 0.82, "paragraph-gap tightening"),
    (2, 1.00, 1.21, 0.82, "line-height tightening"),
    (3, 1.00, 1.21, 0.82, "column flow redistribution (L3 active always)"),
    (4, 0.97, 1.21, 0.82, "local font scaling (min-necessary, binary)"),
    (5, 0.94, 1.18, 0.70, "dense reference mode (reference-only)"),
]


def _base_gap(font_size, line_height, gap_em):
    return max(float(font_size) * float(line_height) * 0.0, 0.0) + 3.0


def plan_page(paragraphs, intervals, formula_width_map=None,
              semantic_roles=None, base_font_size=11.0, gap_pt=4.0):
    """Return the planner decision for one page.

    ``paragraphs``: list of flow items in reading order (paragraph_id,
    render_text, style_role, column, base_font_size).
    ``intervals``: per-column usable intervals from flow_intervals.
    """
    sim = CapacitySimulator(intervals, formula_width_map, semantic_roles)
    trace = []
    for level, font_scale, lh, gap_scale, note in LADDER:
        gap = gap_pt * gap_scale
        placements, feasible, infeasible = sim.simulate(
            paragraphs, font_scale=font_scale, line_height_ratio=lh,
            base_font_size=base_font_size, gap=gap)
        redistributed = sum(1 for p in placements if p.get("redistributed"))
        trace.append({
            "level": level, "note": note,
            "font_scale": font_scale, "line_height_ratio": lh,
            "gap_scale": gap_scale, "feasible": feasible,
            "infeasible_count": len(infeasible),
            "redistributed_count": redistributed,
        })
        if feasible:
            # L4/L5 exact minimum not needed if a lower level already fits
            return _result(paragraphs, placements, level, font_scale, lh,
                           gap_scale, "feasible", trace, intervals)
    # L6: unresolved
    placements, feasible, infeasible = sim.simulate(
        paragraphs, font_scale=MIN_BODY_FONT_RATIO,
        line_height_ratio=MIN_LINE_HEIGHT_RATIO, base_font_size=base_font_size,
        gap=gap_pt * 0.7)
    return _result(paragraphs, placements, 6, MIN_BODY_FONT_RATIO,
                   MIN_LINE_HEIGHT_RATIO, 0.7, "infeasible", trace, intervals,
                   capacity_unresolved=True, infeasible_list=infeasible)


def _result(paragraphs, placements, level, font_scale, lh, gap_scale, status,
            trace, intervals, capacity_unresolved=False, infeasible_list=None):
    usable = sum(c["usable_height"] for c in intervals)
    placed = sum(p["estimated_height"] for p in placements if p.get("fits"))
    remaining_ratio = max(0.0, (usable - placed) / max(usable, 1.0))
    if status == "feasible" and remaining_ratio < 0.06:
        status = "estimated_tight"
    return {
        "capacity_status": status,
        "adaptation_level": level,
        "font_scale": round(font_scale, 3),
        "line_height_ratio": round(lh, 3),
        "gap_scale": round(gap_scale, 3),
        "estimated_capacity_deficit": round(max(0.0, placed - usable), 2),
        "usable_height": round(usable, 2),
        "estimated_text_height": round(placed, 2),
        "remaining_ratio": round(remaining_ratio, 3),
        "capacity_unresolved": capacity_unresolved,
        "infeasible_count": len(infeasible_list or []),
        "infeasible_details": (infeasible_list or [])[:6],
        "capacity_resolution_trace": trace,
        "placements": placements,
    }
