# -*- coding: utf-8 -*-
"""Conservative page-capacity resolver for document batch rendering."""
from __future__ import annotations

from flow_layout import build_column_flow


class PageCapacityResolver:
    """Try bounded spacing/line-height/font adjustments before spill."""

    LEVELS = [
        {"level": 0, "gap_scale": 1.00, "line_height_scale": 1.00, "font_scale": 1.00},
        {"level": 1, "gap_scale": 0.75, "line_height_scale": 1.00, "font_scale": 1.00},
        {"level": 2, "gap_scale": 0.50, "line_height_scale": 1.00, "font_scale": 1.00},
        {"level": 3, "gap_scale": 0.45, "line_height_scale": 0.96, "font_scale": 1.00},
        {"level": 4, "gap_scale": 0.40, "line_height_scale": 0.92, "font_scale": 0.97},
        {"level": 4, "gap_scale": 0.35, "line_height_scale": 0.92, "font_scale": 0.94},
        # L5: bibliography/reference-only compact.  Tighter gap / line-height
        # / font size apply ONLY to reference/bibliography paragraphs
        # (applied inside build_column_flow via reference_compact).  Formula
        # / table / figure are geometry-locked and never scaled.  If a page
        # still overflows after L5 the run reports capacity_unresolved and
        # the document delivery gate stays BLOCKED (no silent pagination).
        {"level": 5, "gap_scale": 0.35, "line_height_scale": 0.92,
         "font_scale": 0.94,
         "reference_compact": {"gap_scale": 0.22,
                               "line_height_scale": 0.84,
                               "font_scale": 0.88}},
    ]

    def __init__(self, page_height, bottom_margin=10.0):
        self.page_height = float(page_height)
        self.bottom_limit = self.page_height - float(bottom_margin)

    def _overflow(self, flows):
        bottoms = []
        reserved_overflows = []
        for flow in flows:
            flow_bottoms = []
            for item in flow.get("items", []):
                if item.get("kind") == "paragraph":
                    bottoms.append(item["flow_y"] + item["est_height"])
                    flow_bottoms.append(item["flow_y"] + item["est_height"])
                elif item.get("kind") == "formula":
                    # flow_locked formulas participate in the page budget
                    # (Phase 4C.2R): their container y is flow-driven, so a
                    # pushed-down formula can itself overflow the page.
                    bottoms.append(item["flow_y"] + item["est_height"])
                    flow_bottoms.append(item["flow_y"] + item["est_height"])
            if flow.get("max_y") is not None and flow_bottoms:
                reserved_overflows.append(max(0.0, max(flow_bottoms)
                                              - float(flow["max_y"])))
        bottom = max(bottoms) if bottoms else 0.0
        return max([max(0.0, bottom - self.bottom_limit)]
                   + reserved_overflows), bottom

    def resolve(self, paragraphs, display_boxes, obstacles, translations,
                formula_width_map, grid=None, bottom_reserved_regions=None):
        attempts = []
        selected = None
        for capacity in self.LEVELS:
            flows = build_column_flow(
                paragraphs, display_boxes, obstacles, translations,
                formula_width_map, capacity=capacity, grid=grid,
                bottom_reserved_regions=bottom_reserved_regions)
            overflow, bottom = self._overflow(flows)
            attempts.append({**capacity, "projected_bottom": round(bottom, 3),
                             "projected_overflow": round(overflow, 3)})
            selected = (flows, dict(capacity), overflow)
            if overflow <= 0.01:
                break
        flows, capacity, overflow = selected
        return flows, {
            "selected_level": capacity["level"],
            "gap_scale": capacity["gap_scale"],
            "line_height_scale": capacity["line_height_scale"],
            "font_scale": capacity["font_scale"],
            "projected_overflow": round(overflow, 3),
            "spill_required": overflow > 0.01,
            "attempts": attempts,
        }
