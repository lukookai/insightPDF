# -*- coding: utf-8 -*-
"""Document-general reliability audit for page-local layout grids.

The page-local x-projection remains evidence, not truth.  This module scores
its geometry against robust document statistics and against semantic body
support.  No page number, document name, or fixed track is encoded here.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Iterable


BODY_ROLES = {
    "body", "body_bold_lead", "list_item", "reference",
    "reference_item", "reference_continuation", "appendix",
    "acknowledgement",
}
EXCLUDED_ROLES = {
    "caption", "table_caption", "figure_caption", "table_cell", "formula",
    "display_formula", "page_number", "header", "footer", "vertical_text",
    "figure_label", "figure_text",
}


def median(values: Iterable[float]) -> float | None:
    rows = sorted(float(value) for value in values if value is not None)
    return statistics.median(rows) if rows else None


def mad(values: Iterable[float], center: float | None = None) -> float | None:
    rows = [float(value) for value in values if value is not None]
    if not rows:
        return None
    center = statistics.median(rows) if center is None else float(center)
    return statistics.median(abs(value - center) for value in rows)


def robust_z(value: float | None, center: float | None,
             spread: float | None, *, floor: float = 1.0) -> float | None:
    if value is None or center is None:
        return None
    scale = max(1.4826 * float(spread or 0.0), float(floor))
    return abs(float(value) - float(center)) / scale


def _track_widths(grid: dict[str, Any]) -> list[float]:
    return [max(0.0, float(col.get("x1", 0)) - float(col.get("x0", 0)))
            for col in (grid.get("columns") or grid.get("column_tracks") or [])]


def _layout_mode(grid: dict[str, Any], page_model: dict[str, Any] | None) -> str:
    columns = grid.get("columns") or grid.get("column_tracks") or []
    if len(columns) == 2 and grid.get("gutter"):
        return "two_column"
    if len(columns) == 1:
        return "single_column"
    traits = (grid.get("layout_traits") or {})
    if traits.get("substantive_two_column_evidence"):
        return "two_column"
    if page_model:
        seen = {int((region.get("payload") or {}).get("column"))
                for region in page_model.get("regions", [])
                if region.get("type") == "text"
                and (region.get("payload") or {}).get("column") in (0, 1)
                and ((region.get("payload") or {}).get("style_role") or "body")
                in BODY_ROLES}
        if seen == {0, 1}:
            return "two_column"
    return "unknown"


def _front_matter_semantic_evidence(page_model: dict[str, Any] | None) -> bool:
    """Detect title-page grammar from role/size/placement, without page id."""
    model = page_model or {}
    height = float(model.get("height") or 0.0)
    candidates = []
    bodies = []
    for region in model.get("regions", []):
        if region.get("type") != "text":
            continue
        payload = region.get("payload") or {}
        bbox = region.get("bbox") or payload.get("bbox")
        if not bbox:
            continue
        role = payload.get("style_role") or "body"
        size = float(payload.get("base_font_size") or 0.0)
        text = (payload.get("source_text") or "").strip()
        if role in BODY_ROLES and payload.get("column") in (0, 1) and size:
            bodies.append(size)
        if payload.get("column") == -1 and bbox[1] < (height * .36 if height else 300):
            candidates.append((role, size, text))
    body_size = median(bodies) or 0.0
    if not candidates or body_size <= 0:
        return False
    title_like = any(role == "heading" and size >= body_size * 1.2
                     and len(text) >= 20 for role, size, text in candidates)
    metadata_like = sum(1 for _role, size, text in candidates
                        if size >= body_size * .65 and len(text) >= 8) >= 2
    return bool(title_like and metadata_like)


def infer_semantic_layout_mode(page_model: dict[str, Any] | None,
                               fallback: str = "unknown") -> str:
    """Infer the intended mode independently from the projection candidate."""
    page_model = page_model or {}
    text_regions = [region for region in page_model.get("regions", [])
                    if region.get("type") == "text"]
    non_text_regions = [region for region in page_model.get("regions", [])
                        if region.get("type") in {"table", "figure", "image"}]
    meaningful = []
    for region in text_regions:
        payload = region.get("payload") or {}
        role = payload.get("semantic_role") or payload.get("style_role") or "body"
        text = (payload.get("source_text") or "").strip()
        if role in {"page_number", "footer", "header_footer"}:
            continue
        if role == "heading" and text.isdigit():
            continue
        meaningful.append(payload)
    # Figure/table plate pages with only a short caption are a genuine
    # non-dominant page grammar even when the legacy paragraph splitter has
    # assigned the caption fragments to opposite source columns.
    if (non_text_regions and len(meaningful) <= max(4, len(non_text_regions) * 2)
            and not any(bool(payload.get("cross_column_continuation"))
                        for payload in meaningful)):
        ordinary_text = [payload for payload in meaningful
                         if (payload.get("style_role") or "body")
                         not in {"caption", "figure_caption", "table_caption"}]
        short_caption_grammar = all(
            len((payload.get("source_text") or "").strip()) < 120
            for payload in ordinary_text)
        if short_caption_grammar:
            return "mixed_non_dominant"
    seen = {int((region.get("payload") or {}).get("column"))
            for region in text_regions
            if region.get("type") == "text"
            and (region.get("payload") or {}).get("column") in (0, 1)
            and ((region.get("payload") or {}).get("style_role") or "body")
            in BODY_ROLES}
    if seen == {0, 1}:
        return "two_column"
    if len(seen) == 1:
        payloads = [region.get("payload") or {} for region in text_regions
                    if (region.get("payload") or {}).get("column") in seen
                    and ((region.get("payload") or {}).get("style_role") or "body")
                    in BODY_ROLES]
        # A paragraph reconstructed across both source columns is direct
        # two-column continuation evidence even though it owns one logical
        # column id.  Without that evidence, a sparse one-sided page remains a
        # genuine non-dominant mode unless its stored paragraph track itself
        # is close to a normal half-page column.
        if any(bool(payload.get("cross_column_continuation"))
               for payload in payloads):
            return "two_column_sparse"
        widths = [float(payload.get("col_width") or 0) for payload in payloads
                  if float(payload.get("col_width") or 0) > 0]
        page_width = float(page_model.get("width") or 0)
        if widths and page_width and median(widths) <= page_width * .46:
            return "two_column_sparse"
        return "single_column"
    return fallback


def semantic_grid_evidence(page_model: dict[str, Any] | None,
                           page_width: float,
                           page_height: float) -> dict[str, Any]:
    """Summarize body support and obstacle dominance from semantic regions."""
    page_model = page_model or {}
    body_records, excluded_records = [], []
    hard_area = 0.0
    for region in page_model.get("regions", []):
        rtype = region.get("type")
        payload = region.get("payload") or {}
        bbox = region.get("bbox") or payload.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        box = [float(v) for v in bbox]
        area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
        if rtype in {"table", "figure", "image"} or (
                rtype == "formula" and payload.get("placement") != "inline"):
            hard_area += area
        if rtype != "text":
            continue
        role = payload.get("semantic_role") or payload.get("style_role") or "body"
        record = {
            "paragraph_id": payload.get("paragraph_id"),
            "role": role,
            "column": payload.get("column"),
            "bbox": box,
            "text_length": len((payload.get("source_text") or "").strip()),
        }
        if role in BODY_ROLES and payload.get("column") in (0, 1):
            body_records.append(record)
        else:
            excluded_records.append(record)
    body_columns = sorted({record["column"] for record in body_records})
    page_area = max(float(page_width) * float(page_height), 1.0)
    dominance = min(1.0, hard_area / page_area)
    body_characters = sum(record["text_length"] for record in body_records)
    return {
        "body_record_count": len(body_records),
        "body_character_count": body_characters,
        "body_columns_supported": body_columns,
        "excluded_text_record_count": len(excluded_records),
        "hard_obstacle_area_ratio": round(dominance, 4),
        "body_support_score": round(min(1.0, len(body_records) / 6.0)
                                    * min(1.0, body_characters / 500.0), 4),
        "body_records": body_records,
        "excluded_records": excluded_records,
    }


def assess_grid_reliability(page_local_grid: dict[str, Any],
                            document_profile: dict[str, Any] | None = None,
                            page_model: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = document_profile or {}
    columns = page_local_grid.get("columns") or page_local_grid.get(
        "column_tracks") or []
    widths = _track_widths(page_local_grid)
    projection_mode = _layout_mode(page_local_grid, page_model)
    layout_mode = infer_semantic_layout_mode(page_model, projection_mode)
    if _front_matter_semantic_evidence(page_model):
        layout_mode = "front_matter"
    width_ratio = (min(widths) / max(widths)) if len(widths) == 2 and max(widths) else None
    gutter = page_local_grid.get("gutter") or {}
    gutter_width = float(gutter["width"]) if gutter.get("width") is not None else None
    frame = page_local_grid.get("content_frame") or {}
    model_width = (page_model or {}).get("width") or 0
    model_height = (page_model or {}).get("height") or 0
    evidence = semantic_grid_evidence(
        page_model,
        float(page_local_grid.get("page_width") or model_width),
        float(page_local_grid.get("page_height") or model_height),
    )

    expected_width = median([
        profile.get("left_width_median"), profile.get("right_width_median")])
    width_mad = max(float(profile.get("left_width_mad") or 0),
                    float(profile.get("right_width_mad") or 0))
    track_z = [robust_z(width, expected_width, width_mad,
                        floor=max((expected_width or 100) * .035, 2.0))
               for width in widths]
    gutter_z = robust_z(gutter_width, profile.get("gutter_median"),
                        profile.get("gutter_mad"), floor=1.5)
    frame_z = {
        "left_x0": robust_z(frame.get("x0"),
                             (profile.get("content_frame") or {}).get("x0"),
                             (profile.get("content_frame_mad") or {}).get("x0"), floor=2.0),
        "right_x1": robust_z(frame.get("x1"),
                              (profile.get("content_frame") or {}).get("x1"),
                              (profile.get("content_frame_mad") or {}).get("x1"), floor=2.0),
    }

    reasons, hard_invalid = [], False
    scores = []
    if layout_mode in {"two_column", "two_column_sparse"}:
        if len(columns) != 2 or gutter_width is None:
            hard_invalid = True
            reasons.append("two_column_semantics_without_two_resolved_local_tracks")
        if width_ratio is not None:
            symmetry_floor = float(profile.get("width_ratio_p10") or .72)
            score = min(1.0, width_ratio / max(symmetry_floor, .35))
            scores.append(score)
            if width_ratio < symmetry_floor:
                reasons.append("column_width_asymmetry_below_document_support")
            if width_ratio < max(.35, symmetry_floor * .55):
                hard_invalid = True
                reasons.append("extreme_column_width_asymmetry")
        available_track_z = [value for value in track_z if value is not None]
        if available_track_z:
            max_z = max(available_track_z)
            scores.append(max(0.0, 1.0 - max_z / 7.0))
            if max_z > 4.5:
                reasons.append("column_width_implausible_against_document_median")
            if max_z > 7.0:
                hard_invalid = True
                reasons.append("column_track_severely_narrower_than_document_distribution")
        if gutter_z is not None:
            scores.append(max(0.0, 1.0 - gutter_z / 6.0))
            if gutter_z > 4.5:
                reasons.append("gutter_outlier_against_document_median_mad")
        if evidence["body_columns_supported"] != [0, 1]:
            reasons.append("insufficient_two_column_body_span_support")
    else:
        # A real single-column/front-matter page is not invalid merely because
        # the dominant document profile is two-column.
        scores.append(float(page_local_grid.get("confidence") or .6))

    frame_max_z = max((value for value in frame_z.values() if value is not None),
                      default=0.0)
    if layout_mode in {"two_column", "two_column_sparse"}:
        scores.append(max(0.0, 1.0 - frame_max_z / 7.0))
        if frame_max_z > 5.0:
            reasons.append("content_frame_outlier")
    support = evidence["body_support_score"]
    scores.append(support)
    if support < .25:
        reasons.append("sparse_body_evidence")
    dominance = evidence["hard_obstacle_area_ratio"]
    obstacle_score = max(.15, 1.0 - dominance * 1.7)
    scores.append(obstacle_score)
    if dominance > .32:
        reasons.append("hard_obstacle_dominates_page_evidence")

    local_signal = float(page_local_grid.get("grid_confidence")
                         or page_local_grid.get("confidence") or .5)
    confidence = .15 * local_signal + .85 * (sum(scores) / max(len(scores), 1))
    if hard_invalid:
        confidence = min(confidence, .24)
    elif len(reasons) >= 3:
        confidence = min(confidence, .58)
    if layout_mode in {"single_column", "front_matter",
                       "mixed_non_dominant"}:
        # Dominant-profile disagreement is not a defect for a protected
        # non-dominant page grammar.
        confidence = max(confidence,
                         float(page_local_grid.get("grid_confidence")
                               or page_local_grid.get("confidence") or .68),
                         .68)
    confidence = round(max(0.0, min(1.0, confidence)), 3)
    status = "invalid" if hard_invalid else ("suspect" if confidence < .68 else "valid")
    return {
        "page": page_local_grid.get("page") or (page_model or {}).get("page"),
        "layout_mode": layout_mode,
        "page_local_grid": page_local_grid,
        "confidence": confidence,
        "status": status,
        "reasons": sorted(set(reasons)),
        "metrics": {
            "column_widths": [round(value, 3) for value in widths],
            "column_width_ratio": round(width_ratio, 4) if width_ratio is not None else None,
            "column_width_robust_z": [round(value, 3) if value is not None else None
                                      for value in track_z],
            "gutter_width": round(gutter_width, 3) if gutter_width is not None else None,
            "gutter_robust_z": round(gutter_z, 3) if gutter_z is not None else None,
            "content_frame_robust_z": {key: (round(value, 3) if value is not None else None)
                                       for key, value in frame_z.items()},
            "semantic_evidence": evidence,
        },
    }
