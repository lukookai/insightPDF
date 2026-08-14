# -*- coding: utf-8 -*-
"""Hard QA for final resolved page grids.

Raw page-local candidates are allowed to be invalid: the fallback resolver
exists to repair them.  This gate audits only the final grid consumed by
layout/capacity code and contains no document- or page-specific policy.
"""
from __future__ import annotations

from typing import Any


def grid_sanity_qa(resolutions: list[dict[str, Any]],
                   document_profile: dict[str, Any] | None = None
                   ) -> dict[str, Any]:
    profile = document_profile or {}
    counts = {
        "invalid_resolved_track_count": 0,
        "implausibly_narrow_resolved_track_count": 0,
        "extreme_resolved_asymmetry_count": 0,
        "unresolved_profile_outlier_count": 0,
    }
    details = []
    expected = min(float(profile.get("left_width_median") or 0),
                   float(profile.get("right_width_median") or 0))
    symmetry_support = float(profile.get("width_ratio_p10") or .7)
    for resolution in resolutions:
        resolved = resolution.get("resolved") or {}
        grid = resolved.get("grid") or resolution.get("resolved_grid")
        if not grid:
            if (resolution.get("page_local") or {}).get("status") == "invalid":
                counts["unresolved_profile_outlier_count"] += 1
                details.append({
                    "page": resolution.get("page"),
                    "reason": "unresolved_invalid_local",
                })
            continue
        columns = grid.get("columns") or grid.get("column_tracks") or []
        invalid = (not columns or any(
            float(column.get("x1", 0)) - float(column.get("x0", 0)) <= 0
            for column in columns))
        counts["invalid_resolved_track_count"] += int(invalid)
        if len(columns) != 2:
            continue
        widths = [float(column["x1"]) - float(column["x0"])
                  for column in columns]
        narrow = min(widths) < expected * .55 if expected else False
        asymmetric = (min(widths) / max(widths)
                      < max(.45, symmetry_support * .65)) if max(widths) else True
        counts["implausibly_narrow_resolved_track_count"] += int(narrow)
        counts["extreme_resolved_asymmetry_count"] += int(asymmetric)
        if narrow or asymmetric:
            details.append({
                "page": resolution.get("page"),
                "widths": [round(value, 3) for value in widths],
                "narrow": narrow,
                "asymmetric": asymmetric,
            })
    return {
        **counts,
        "details": details,
        "grid_sanity_passed": all(value == 0 for value in counts.values()),
    }

