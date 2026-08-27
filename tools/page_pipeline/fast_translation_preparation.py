"""Prepare prose-recovery translation before the FAST renderer starts.

Candidate discovery delegates to the established recovery implementation so
its geometry/semantic rules remain unchanged.  This module only separates
discovery, translation-item preparation, target application, and the renderer
API guard into explicit lifecycle stages.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Iterable


class TranslationPreparationIncompleteError(RuntimeError):
    """A required target was absent at the renderer boundary."""

    error_code = "translation_preparation_incomplete"

    def __init__(
        self, missing_items: Iterable[dict[str, Any] | str], *,
        owner: str,
    ) -> None:
        normalized = []
        for item in missing_items:
            if isinstance(item, dict):
                normalized.append({
                    "item_id": str(item.get("item_id") or ""),
                    "type": str(item.get("type") or owner),
                    "source_text": str(item.get("source_text") or ""),
                })
            else:
                normalized.append({
                    "item_id": str(item), "type": owner,
                    "source_text": "",
                })
        self.owner = owner
        self.missing_items = normalized
        super().__init__(
            "%s: owner=%s missing=%s"
            % (self.error_code, owner,
               ", ".join(row["item_id"] for row in normalized)))


def discover_page_prose_recovery(
    page_model: dict[str, Any],
    pdf_path: str | Path,
    page_idx: int,
    *,
    existing_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Discover candidates using the unchanged production recovery rules."""
    from prose_adopted_formula_recovery import recover_prose_adopted_formulas

    recovery = recover_prose_adopted_formulas(
        page_model,
        {},
        str(pdf_path),
        page_idx,
        translator_fn=None,
        existing_ids=set(existing_ids or set()))
    recovery = copy.deepcopy(recovery)
    for paragraph in recovery.get("recovered") or []:
        paragraph["target_text"] = None
        paragraph["target_text_with_paragraph_markers"] = None
        paragraph["render_source"] = "BLOCK"
        paragraph["render_source_reason"] = "translation_pending"
    trace = recovery.setdefault("trace", {})
    trace.update({
        "lifecycle_stage": "translation_preparation.discovery",
        "discovery_only": True,
        "api_calls": 0,
    })
    recovery["api_calls"] = 0
    return recovery


def prose_recovery_translation_items(
    recovery: dict[str, Any],
) -> list[dict[str, str]]:
    """Return recovery candidates in the normal translation item schema."""
    return [{
        "item_id": str(paragraph["paragraph_id"]),
        "type": "paragraph",
        "source_text": str(
            paragraph.get("translation_source_text")
            or paragraph.get("source_text") or ""),
        "style_role": str(paragraph.get("style_role") or "body"),
        "semantic_role": str(paragraph.get("semantic_role") or "body"),
        "translation_origin": "prose_recovery_candidate",
    } for paragraph in recovery.get("recovered") or []]


def apply_prepared_recovery_translations(
    recovery: dict[str, Any],
    translations: dict[str, str],
) -> dict[str, Any]:
    """Attach unified-batch targets without performing any provider call."""
    from source_paragraph_style import target_paragraph_segments

    prepared = copy.deepcopy(recovery)
    applied = 0
    missing = []
    for paragraph in prepared.get("recovered") or []:
        paragraph_id = str(paragraph.get("paragraph_id") or "")
        target = str(translations.get(paragraph_id) or "").strip()
        if target and not re.search(r"[\u4e00-\u9fff]", target) \
                and re.search(r"[A-Za-z]{6,}", target):
            target = ""
            paragraph["render_source_reason"] = "translation_not_chinese"
        paragraph["target_text_with_paragraph_markers"] = target or None
        clean_target = target_paragraph_segments(
            target,
            str(paragraph.get("source_text") or ""),
            paragraph.get("source_paragraph_styles") or [],
        )[0] if target else None
        paragraph["target_text"] = clean_target or None
        if paragraph["target_text"]:
            paragraph["render_source"] = "canonical_target"
            paragraph["render_source_reason"] = (
                "translation_preparation_unified_batch")
            applied += 1
        else:
            paragraph["render_source"] = "BLOCK"
            if paragraph.get("render_source_reason") != \
                    "translation_not_chinese":
                paragraph["render_source_reason"] = "target_missing_block"
            missing.append(paragraph_id)

    trace = prepared.setdefault("trace", {})
    trace.update({
        "lifecycle_stage": "translation_preparation.complete",
        "discovery_only": False,
        "prepared_target_count": applied,
        "required_target_missing_count": len(missing),
        "api_calls": 0,
    })
    prepared["api_calls"] = 0
    prepared["translation_preparation"] = {
        "candidate_count": len(prepared.get("recovered") or []),
        "prepared_target_count": applied,
        "required_target_missing_count": len(missing),
        "missing_target_ids": missing,
        "provider_called_here": False,
    }
    return prepared


def prepared_recovery_missing_targets(
    recovery: dict[str, Any],
) -> list[dict[str, str]]:
    return [{
        "item_id": str(paragraph.get("paragraph_id") or ""),
        "type": "prose_recovery",
        "source_text": str(paragraph.get("source_text") or ""),
    } for paragraph in recovery.get("recovered") or []
      if not str(paragraph.get("target_text") or "").strip()
      or paragraph.get("render_source") != "canonical_target"]


def require_prepared_prose_recovery(
    recovery: dict[str, Any],
) -> dict[str, int]:
    missing = prepared_recovery_missing_targets(recovery)
    if missing:
        raise TranslationPreparationIncompleteError(
            missing, owner="prose_recovery")
    return {
        "recovery_candidate_count": len(recovery.get("recovered") or []),
        "required_target_missing_before_render_count": 0,
        "prose_recovery_translation_api_call_count": 0,
    }


def renderer_translation_provider_guard(
    items: list[dict[str, Any]], **_kwargs: Any,
) -> dict[str, str]:
    """Provider-shaped guard used by FAST renderer-side closures."""
    raise TranslationPreparationIncompleteError(
        items, owner="renderer_translation_provider")


__all__ = [
    "TranslationPreparationIncompleteError",
    "apply_prepared_recovery_translations",
    "discover_page_prose_recovery",
    "prepared_recovery_missing_targets",
    "prose_recovery_translation_items",
    "renderer_translation_provider_guard",
    "require_prepared_prose_recovery",
]
