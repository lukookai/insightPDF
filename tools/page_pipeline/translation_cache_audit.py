# -*- coding: utf-8 -*-
"""Detailed, side-effect-free translation-cache audit.

The audit mirrors ``run_document.DocumentTranslationCache`` key semantics but
never mutates the cache.  It distinguishes an initial cache miss from a cached
entry that must be invalidated, avoiding the old double subtraction where an
invalidated entry was counted as both a hit adjustment and a final miss.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    from translation_status import (TRANSLATABLE_STYLE_ROLES, has_cjk,
                                    is_reference_item, is_unchanged_allowed)
except ImportError:  # package import during focused tests
    from .translation_status import (TRANSLATABLE_STYLE_ROLES, has_cjk,
                                     is_reference_item, is_unchanged_allowed)


DEFAULT_TARGET_LANGUAGE = "zh-CN"
DEFAULT_PROMPT_VERSION = "phase4b1-deepseek-prompt-v1"


def _tokens(text):
    import re
    return re.findall(r"\{\{[A-Z_0-9]+\}\}", text or "")


def cache_key(item, *, target_language=DEFAULT_TARGET_LANGUAGE,
              prompt_version=DEFAULT_PROMPT_VERSION):
    """Compute the frozen cache key used by ``run_document``."""
    payload = {
        "source_logical_text": item.get("source_text") or "",
        "protected_placeholder_structure": _tokens(
            item.get("source_text") or ""),
        "target_language": target_language,
        "translation_prompt_version": prompt_version,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def invalidation_reason(item, translation):
    """Return the same narrow invalidation reason as ``run_document``."""
    if item.get("type") != "paragraph":
        return None
    source = item.get("source_text") or ""
    if (not source.strip() or has_cjk(source) or is_reference_item(item)
            or is_unchanged_allowed(item)):
        return None
    role = item.get("style_role") or "body"
    if role not in TRANSLATABLE_STYLE_ROLES:
        return None
    if (translation is not None and translation.strip() == source.strip()
            and len(source.strip()) >= 8):
        return "verbatim_source_for_translatable_prose"
    return None


def _entries(cache):
    if cache is None:
        return {}
    if isinstance(cache, (str, Path)):
        cache = json.loads(Path(cache).read_text(encoding="utf-8"))
    if hasattr(cache, "data"):
        cache = cache.data
    if not isinstance(cache, dict):
        return {}
    return cache.get("entries", cache)


def build_translation_cache_audit(
        items, cache, *, translations=None, status_map=None,
        invalidated_reasons=None, target_language=DEFAULT_TARGET_LANGUAGE,
        prompt_version=DEFAULT_PROMPT_VERSION, out_path=None):
    """Build a detailed audit without changing translation/cache semantics.

    ``translations`` may be the final translation mapping.  It is used only
    to report final availability/status; initial hit/miss and invalidation are
    always derived from the supplied pre-mutation cache snapshot.

    ``invalidated_reasons`` optionally maps item_id to an explicit reason
    captured by the caller.  When omitted, the frozen verbatim-source rule is
    evaluated against the cached value.
    """
    items = list(items or [])
    entries = _entries(cache)
    translations = translations or {}
    status_map = status_map or {}
    invalidated_reasons = invalidated_reasons or {}
    records = []
    reason_counts = {}
    for item in items:
        item_id = item.get("item_id")
        key = cache_key(item, target_language=target_language,
                        prompt_version=prompt_version)
        entry = entries.get(key)
        cached_translation = (entry.get("translation") if isinstance(entry, dict)
                              else None)
        initial_hit = cached_translation is not None
        reason = invalidated_reasons.get(item_id)
        if reason is None and initial_hit:
            reason = invalidation_reason(item, cached_translation)
        invalidated = bool(reason)
        usable_hit = initial_hit and not invalidated
        cache_miss = not initial_hit
        translation_call_required = cache_miss or invalidated
        if reason:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        records.append({
            "item_id": item_id,
            "type": item.get("type"),
            "style_role": item.get("style_role"),
            "cache_key": key,
            "cache_hit": initial_hit,
            "cache_miss": cache_miss,
            "cache_invalidated": invalidated,
            "cache_invalidated_reason": reason,
            "usable_cache_hit": usable_hit,
            "translation_call_required": translation_call_required,
            "final_translation_present": bool(
                (translations.get(item_id) or "").strip()),
            "translation_status": status_map.get(item_id),
        })

    hit = sum(r["cache_hit"] for r in records)
    miss = sum(r["cache_miss"] for r in records)
    invalidated = sum(r["cache_invalidated"] for r in records)
    usable = sum(r["usable_cache_hit"] for r in records)
    calls = sum(r["translation_call_required"] for r in records)
    result = {
        "item_count": len(records),
        # Required public fields: initial lookup truth, before invalidation.
        "cache_hit": hit,
        "cache_miss": miss,
        "cache_invalidated_count": invalidated,
        "cache_invalidated_reason": reason_counts,
        "cache_invalidated_reason_details": [
            {"item_id": r["item_id"],
             "reason": r["cache_invalidated_reason"]}
            for r in records if r["cache_invalidated"]
        ],
        # Explicit reconciliation prevents double-counting invalidations.
        "usable_cache_hit": usable,
        "translation_call_required_count": calls,
        "cache_accounting_closed": hit + miss == len(records),
        "translation_call_accounting_closed": usable + calls == len(records),
        "cache_hit_ratio": round(hit / len(records), 4) if records else 1.0,
        "usable_cache_hit_ratio": round(
            usable / len(records), 4) if records else 1.0,
        "large_miss_warning": bool(records and calls / len(records) > 0.2),
        "records": records,
    }
    if out_path:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return result


__all__ = [
    "build_translation_cache_audit", "cache_key", "invalidation_reason",
]
