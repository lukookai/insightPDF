# -*- coding: utf-8 -*-
"""CapacitySimulator (Phase 4E.1B-C1).

Pure-geometry placement simulation -- no Chromium, no PDF.  Places paragraphs
in document reading order into the per-column usable y-intervals, using the
production height estimate.  When the preferred column is exhausted it can
continue into another source-valid column's free intervals (mixed-content
redistribution) -- for movable roles only.  Reading order is never reordered.
"""
from __future__ import annotations

from flow_layout import estimate_paragraph_height

MOVABLE_ROLES = {"body", "list_item", "reference", "reference_continuation",
                 "reference_item", "appendix", "acknowledgement"}


class CapacitySimulator:
    def __init__(self, intervals, formula_width_map=None,
                 semantic_roles=None):
        self.intervals = intervals            # list of per-column interval dicts
        self.formula_width_map = formula_width_map or {}
        self.semantic_roles = semantic_roles or {}
        self._cursor = {}                     # (column_id, interval_index) -> y
        self.reset()

    def reset(self):
        self._cursor = {}
        for col in self.intervals:
            cid = col["column"]
            for i, iv in enumerate(col["usable_y_intervals"]):
                self._cursor[(cid, i)] = iv[0]

    def _try_place(self, cid, i, est_h, gap=0.0):
        ivs = self.intervals[cid]["usable_y_intervals"]
        if i >= len(ivs):
            return None
        y0, y1 = ivs[i]
        cursor = max(self._cursor[(cid, i)], y0)
        if cursor + est_h <= y1 + 0.01:
            self._cursor[(cid, i)] = cursor + est_h + gap
            return (cid, i, round(cursor, 2), round(cursor + est_h, 2))
        return None

    def simulate(self, paragraphs, font_scale=1.0, line_height_ratio=1.3,
                 base_font_size=11.0, gap=0.0):
        """Place paragraphs in reading order.  Returns (placements, feasible)."""
        self.reset()
        placements = []
        infeasible = []
        col_ids = [c["column"] for c in self.intervals]
        for para in paragraphs:
            role = para.get("style_role") or "body"
            sem_role = self.semantic_roles.get(para.get("paragraph_id")) or ""
            movable = (role in MOVABLE_ROLES
                       or (sem_role.startswith("reference")
                           and role in ("body", "list_item")))
            fsize = (para.get("base_font_size") or base_font_size) * font_scale
            preferred = para.get("column", col_ids[0] if col_ids else None)
            if preferred is None or preferred not in col_ids:
                preferred = col_ids[0] if col_ids else None
            width = (self.intervals[preferred]["track"][1]
                     - self.intervals[preferred]["track"][0]) if preferred is not None else 218.0
            est_h = estimate_paragraph_height(
                para.get("render_text") or "", fsize, max(width, 1.0),
                self.formula_width_map, line_height=line_height_ratio)
            placed = None
            if preferred is not None:
                for i in range(len(self.intervals[preferred]["usable_y_intervals"])):
                    placed = self._try_place(preferred, i, est_h, gap)
                    if placed:
                        break
            if placed is None and movable:
                for other in col_ids:
                    if other == preferred:
                        continue
                    for i in range(len(self.intervals[other]["usable_y_intervals"])):
                        placed = self._try_place(other, i, est_h, gap)
                        if placed:
                            break
                    if placed:
                        break
            if placed is None:
                largest = 0.0
                for c in col_ids:
                    for i, iv in enumerate(self.intervals[c]["usable_y_intervals"]):
                        rem = iv[1] - max(self._cursor[(c, i)], iv[0])
                        largest = max(largest, rem)
                infeasible.append({
                    "paragraph_id": para.get("paragraph_id"),
                    "required_height": round(est_h, 2),
                    "largest_remaining_interval": round(largest, 2),
                    "reason": "no_contiguous_interval",
                })
                placements.append({
                    "paragraph_id": para.get("paragraph_id"),
                    "estimated_height": round(est_h, 2),
                    "column": None, "interval_index": None,
                    "placed_y0": None, "placed_y1": None,
                    "fits": False, "reason": "no_contiguous_interval",
                })
            else:
                cid, i, y0, y1 = placed
                placements.append({
                    "paragraph_id": para.get("paragraph_id"),
                    "estimated_height": round(est_h, 2),
                    "column": cid, "interval_index": i,
                    "placed_y0": y0, "placed_y1": y1,
                    "fits": True, "reason": None,
                    "redistributed": cid != preferred,
                })
        return placements, (not infeasible), infeasible
