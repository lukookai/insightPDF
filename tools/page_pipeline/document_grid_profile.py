# -*- coding: utf-8 -*-
"""Robust document-level layout profile for grid fallback."""
from __future__ import annotations

import statistics
from typing import Any

from layout_grid_reliability import infer_semantic_layout_mode, mad, median


def _trimmed(values: list[float], fraction: float = .1) -> list[float]:
    rows = sorted(float(value) for value in values)
    trim = int(len(rows) * fraction) if len(rows) >= 8 else 0
    return rows[trim:len(rows)-trim] if trim else rows


def _provisional_two_column_candidate(grid: dict[str, Any],
                                      page_model: dict[str, Any] | None) -> bool:
    columns = grid.get("columns") or []
    if len(columns) != 2 or not grid.get("gutter"):
        return False
    widths = [float(col["x1"]) - float(col["x0"]) for col in columns]
    if min(widths) <= 0 or min(widths) / max(widths) < .55:
        return False
    # Require ordinary body evidence on both columns when a model is known.
    if page_model:
        seen = {int((region.get("payload") or {}).get("column"))
                for region in page_model.get("regions", [])
                if region.get("type") == "text"
                and (region.get("payload") or {}).get("column") in (0, 1)
                and ((region.get("payload") or {}).get("style_role") or "body")
                in {"body", "body_bold_lead", "list_item", "reference",
                    "reference_item", "reference_continuation", "appendix"}}
        if seen != {0, 1}:
            return False
    return True


def build_document_grid_profile(page_grids: list[dict[str, Any]],
                                page_models: dict[int, dict[str, Any]] | None = None
                                ) -> dict[str, Any]:
    page_models = page_models or {}
    input_pages = [int(grid.get("page") or 0) for grid in page_grids]
    candidates = [grid for grid in page_grids
                  if _provisional_two_column_candidate(
                      grid, page_models.get(int(grid.get("page") or 0)))]
    if not candidates:
        return {
            "layout_mode": "unresolved", "support_pages": [],
            "excluded_low_confidence_pages": [g.get("page") for g in page_grids],
            "confidence": 0.0, "resolved": False,
        }

    left_x0 = [float(g["columns"][0]["x0"]) for g in candidates]
    left_x1 = [float(g["columns"][0]["x1"]) for g in candidates]
    right_x0 = [float(g["columns"][1]["x0"]) for g in candidates]
    right_x1 = [float(g["columns"][1]["x1"]) for g in candidates]
    left_w = [b - a for a, b in zip(left_x0, left_x1)]
    right_w = [b - a for a, b in zip(right_x0, right_x1)]
    gutters = [float(g["gutter"]["width"]) for g in candidates]

    centers = {
        "left_x0": median(left_x0), "left_x1": median(left_x1),
        "right_x0": median(right_x0), "right_x1": median(right_x1),
        "left_w": median(left_w), "right_w": median(right_w),
        "gutter": median(gutters),
    }
    spreads = {
        "left_x0": mad(left_x0), "left_x1": mad(left_x1),
        "right_x0": mad(right_x0), "right_x1": mad(right_x1),
        "left_w": mad(left_w), "right_w": mad(right_w),
        "gutter": mad(gutters),
    }

    def inlier(grid: dict[str, Any]) -> bool:
        values = {
            "left_x0": float(grid["columns"][0]["x0"]),
            "left_x1": float(grid["columns"][0]["x1"]),
            "right_x0": float(grid["columns"][1]["x0"]),
            "right_x1": float(grid["columns"][1]["x1"]),
            "left_w": float(grid["columns"][0]["x1"])-float(grid["columns"][0]["x0"]),
            "right_w": float(grid["columns"][1]["x1"])-float(grid["columns"][1]["x0"]),
            "gutter": float(grid["gutter"]["width"]),
        }
        for key, value in values.items():
            scale = max(1.4826 * float(spreads[key] or 0),
                        2.0 if "x" in key or "w" in key else 1.5)
            if abs(value - float(centers[key])) > 4.5 * scale:
                return False
        return True

    supports = [grid for grid in candidates if inlier(grid)]
    excluded = sorted(set(input_pages)
                      - {int(g.get("page") or 0) for g in supports})
    # Recompute from the robust inlier set.
    def vals(fn):
        return _trimmed([fn(grid) for grid in supports])

    lx0 = vals(lambda g: float(g["columns"][0]["x0"]))
    lx1 = vals(lambda g: float(g["columns"][0]["x1"]))
    rx0 = vals(lambda g: float(g["columns"][1]["x0"]))
    rx1 = vals(lambda g: float(g["columns"][1]["x1"]))
    lw = vals(lambda g: float(g["columns"][0]["x1"])-float(g["columns"][0]["x0"]))
    rw = vals(lambda g: float(g["columns"][1]["x1"])-float(g["columns"][1]["x0"]))
    gs = vals(lambda g: float(g["gutter"]["width"]))
    frame_x0 = vals(lambda g: float((g.get("content_frame") or {})["x0"]))
    frame_x1 = vals(lambda g: float((g.get("content_frame") or {})["x1"]))
    width_ratios = [min(a, b)/max(a, b) for a, b in zip(lw, rw) if max(a, b)]
    confidence = min(1.0, len(supports) / max(4.0, len(page_grids) * .55))
    if len(supports) < 2:
        confidence *= .4
    profile = {
        "layout_mode": "two_column",
        "support_pages": [int(g["page"]) for g in supports],
        "excluded_low_confidence_pages": excluded,
        "content_frame": {"x0": round(float(median(frame_x0)), 3),
                          "x1": round(float(median(frame_x1)), 3)},
        "content_frame_mad": {"x0": round(float(mad(frame_x0) or 0), 3),
                              "x1": round(float(mad(frame_x1) or 0), 3)},
        "left_track": [round(float(median(lx0)), 3), round(float(median(lx1)), 3)],
        "right_track": [round(float(median(rx0)), 3), round(float(median(rx1)), 3)],
        "left_width_median": round(float(median(lw)), 3),
        "right_width_median": round(float(median(rw)), 3),
        "left_width_mad": round(float(mad(lw) or 0), 3),
        "right_width_mad": round(float(mad(rw) or 0), 3),
        "width_ratio_p10": round(sorted(width_ratios)[max(0, int(len(width_ratios)*.1)-1)], 4)
        if width_ratios else None,
        "gutter_median": round(float(median(gs)), 3),
        "gutter_mad": round(float(mad(gs) or 0), 3),
        "confidence": round(confidence, 3),
        "statistics": "median + MAD + 10% trimmed distributions when n>=8",
        "fallback_prior_only": True,
        "resolved": confidence >= .68 and len(supports) >= 2,
    }
    return profile


def build_document_grid_profile_from_candidates(
        page_candidates: list[dict[str, Any]],
        page_models: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a robust profile from both projection and persisted candidates.

    Earlier stages may already have restored a reliable page with a previous
    document prior.  Such a candidate is still admitted only when semantic
    body evidence independently says the page is two-column and its geometry
    joins the dominant robust cluster.  This avoids a bootstrap failure where
    several obstacle-heavy pages all have weak raw projections.
    """
    page_models = page_models or {}
    usable = []
    input_pages = [int(record.get("page") or 0) for record in page_candidates]
    for record in page_candidates:
        page = int(record.get("page") or 0)
        if infer_semantic_layout_mode(page_models.get(page)) not in {
                "two_column", "two_column_sparse"}:
            continue
        candidates = record.get("candidates") or [record.get("grid") or record]
        best = None
        for grid in candidates:
            if not grid:
                continue
            columns = grid.get("columns") or grid.get("column_tracks") or []
            if len(columns) != 2 or not grid.get("gutter"):
                continue
            widths = [float(c["x1"])-float(c["x0"]) for c in columns]
            ratio = min(widths)/max(widths) if max(widths) else 0.0
            score = ratio * min(widths)
            if best is None or score > best[0]:
                clone = dict(grid)
                clone["page"] = page
                clone["columns"] = [dict(c) for c in columns]
                best = (score, clone)
        if best:
            usable.append(best[1])
    # Include all pages as exclusion context, but only the best semantic
    # candidates can become profile support.
    profile = build_document_grid_profile(usable, page_models)
    profile["excluded_low_confidence_pages"] = sorted(
        set(input_pages) - set(profile.get("support_pages") or []))
    profile["profile_scan_page_count"] = len(input_pages)
    return profile


def profile_grid(profile: dict[str, Any], page_grid: dict[str, Any]) -> dict[str, Any] | None:
    if profile.get("layout_mode") != "two_column" or not profile.get("resolved"):
        return None
    left, right = profile["left_track"], profile["right_track"]
    frame = dict(page_grid.get("content_frame") or {})
    frame["x0"], frame["x1"] = profile["content_frame"]["x0"], profile["content_frame"]["x1"]
    return {
        "page": page_grid.get("page"),
        "page_width": page_grid.get("page_width"),
        "page_height": page_grid.get("page_height"),
        "body_size_estimated": page_grid.get("body_size_estimated"),
        "content_frame": frame,
        "columns": [
            {"column_id": 0, "x0": left[0], "x1": left[1],
             "width": round(left[1]-left[0], 3)},
            {"column_id": 1, "x0": right[0], "x1": right[1],
             "width": round(right[1]-right[0], 3)},
        ],
        "gutter": {"x0": left[1], "x1": right[0],
                   "width": round(right[0]-left[1], 3)},
        "confidence": profile.get("confidence"),
        "document_profile_fallback": True,
    }
