# -*- coding: utf-8 -*-
"""Resolve a page-local grid against a robust document fallback prior."""
from __future__ import annotations

from typing import Any

from document_grid_profile import profile_grid
from flow_interval_closure_qa import semantic_content_bounds
from layout_grid_reliability import assess_grid_reliability


def resolve_grid(page_local_grid: dict[str, Any],
                 document_profile: dict[str, Any] | None,
                 page_model: dict[str, Any] | None = None) -> dict[str, Any]:
    reliability = assess_grid_reliability(page_local_grid, document_profile,
                                          page_model)
    local_mode = reliability["layout_mode"]
    document_candidate = profile_grid(document_profile or {}, page_local_grid)
    protected_semantic_mode = local_mode in {
        "single_column", "front_matter", "mixed_non_dominant"}
    # A genuine non-dominant mode is protected regardless of disagreement
    # with the dominant two-column prior.  Reliability still records the
    # warning, but the prior must never overwrite the page grammar.
    if protected_semantic_mode:
        source, resolved = "page_local", page_local_grid
        reason = "non_dominant_layout_mode_preserved"
    # Valid local two-column geometry stays local.
    elif reliability["status"] == "valid":
        source, resolved = "page_local", page_local_grid
        reason = "page_local_reliability_valid"
    elif (local_mode in {"two_column", "two_column_sparse"}
          and (document_profile or {}).get("layout_mode") == "two_column"
          and float((document_profile or {}).get("confidence") or 0) >= .68):
        if document_candidate:
            source, resolved = "document_fallback", document_candidate
            reason = "low_confidence_two_column_page_local_replaced_by_document_prior"
        else:
            source, resolved = "unresolved", None
            reason = "document_profile_not_resolved"
    else:
        # Preserve genuine non-dominant modes unless local evidence is
        # explicitly invalid.  A document two-column prior is never forced
        # onto a true single-column/front-matter page.
        traits = page_local_grid.get("layout_traits") or {}
        local_class = page_local_grid.get("layout_class") or ""
        protected_mode = (bool(traits.get("frontmatter_detected"))
                          or "front_matter" in local_class)
        if protected_mode:
            source, resolved = "page_local", page_local_grid
            reason = "non_dominant_layout_mode_preserved"
        else:
            source, resolved = "unresolved", None
            reason = "no_compatible_high_confidence_grid"
    if resolved is not None:
        resolved = dict(resolved)
        resolved["content_frame"] = dict(resolved.get("content_frame") or {})
        if source == "document_fallback" and page_model:
            old_y = [resolved["content_frame"].get("y0"),
                     resolved["content_frame"].get("y1")]
            y0, y1 = semantic_content_bounds(page_model, resolved)
            resolved["content_frame"]["y0"] = y0
            resolved["content_frame"]["y1"] = y1
            resolved["content_frame_y_resolution"] = {
                "source": "semantic_page_model",
                "page_local_y": old_y,
                "resolved_y": [y0, y1],
            }
    return {
        "page": page_local_grid.get("page") or (page_model or {}).get("page"),
        "page_local": reliability,
        "document_profile": document_profile,
        "page_local_grid": page_local_grid,
        "document_profile_grid": document_candidate,
        "resolved_grid": resolved,
        "resolved": {
            "source": source,
            "tracks": (resolved or {}).get("columns") if resolved else None,
            "grid": resolved,
            "reason": reason,
        },
    }
