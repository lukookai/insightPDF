# -*- coding: utf-8 -*-
"""visual_v05_gate -- aggregate all v05 QA layers + existing hard metrics.

Hard gate conditions (all must hold):

  v05 new:
    translatable_source_residual_fragment_count == 0
    translatable_source_residual_char_count == 0
    translatable_source_residual_sentence_count == 0
    source_head_residual_count == 0
    source_tail_residual_count == 0
    formula_adjacent_source_residual_count == 0
    untranslated_required_fragment_count == 0
    source_fallback_for_required_translation_count == 0

    semantic_role_loss_count == 0
    heading_boundary_loss_count == 0
    heading_body_merge_count == 0
    body_heading_merge_count == 0
    heading_level_mismatch_count == 0
    source_block_target_merge_count == 0
    illegal_target_block_split_count == 0
    reading_order_structure_violation_count == 0

    formula_adjacent_target_missing_count == 0
    formula_context_drop_count == 0
    formula_context_duplicate_count == 0
    formula_context_reorder_count == 0
    protected_math_token_loss_count == 0
    protected_math_token_mutation_count == 0
    orphan_formula_punctuation_count == 0
    formula_context_wrong_owner_count == 0

    replacement_character_count == 0
    unexpected_square_glyph_count == 0
    missing_unicode_mapping_count == 0
    protected_token_glyph_loss_count == 0
    unresolved_font_fallback_count == 0

    source_prose_vector_still_rendered_count == 0

  v04/v03/v02 regression (unchanged):
    final_visible_translation_coverage == 1.0
    target missing/duplicate/double-render == 0
    severe collision / duplicate baseline cluster == 0
    missing/duplicate/wrong-region render == 0
    anchor / formula / region / group / fragment metrics == 0

    production_special_case_count == 0
"""

from __future__ import annotations

from typing import Any, Dict, List

V05_NEW_KEYS = [
    "translatable_source_residual_fragment_count",
    "translatable_source_residual_char_count",
    "translatable_source_residual_sentence_count",
    "source_head_residual_count",
    "source_tail_residual_count",
    "formula_adjacent_source_residual_count",
    "untranslated_required_fragment_count",
    "source_fallback_for_required_translation_count",
    "semantic_role_loss_count",
    "heading_boundary_loss_count",
    "heading_body_merge_count",
    "body_heading_merge_count",
    "heading_level_mismatch_count",
    "source_block_target_merge_count",
    "illegal_target_block_split_count",
    "reading_order_structure_violation_count",
    "formula_adjacent_target_missing_count",
    "formula_context_drop_count",
    "formula_context_duplicate_count",
    "formula_context_reorder_count",
    "protected_math_token_loss_count",
    "protected_math_token_mutation_count",
    "orphan_formula_punctuation_count",
    "formula_context_wrong_owner_count",
    "replacement_character_count",
    "unexpected_square_glyph_count",
    "missing_unicode_mapping_count",
    "protected_token_glyph_loss_count",
    "unresolved_font_fallback_count",
    "source_prose_vector_still_rendered_count",
]

# keys that are COVERAGE / counts (>= 0, but 0 required for hard) vs
# keys whose "0" check must skip coverage-style metrics
_COVERAGE_KEYS = {
    "final_visible_translation_coverage",
    "translation_pipeline_coverage",
}


def merge_v05_qa(qa: Dict[str, Any], hard: Dict[str, Any]) -> Dict[str, Any]:
    """Merge one v05 QA's metrics into the per-page hard dict."""
    for k, v in (qa.get("metrics") or {}).items():
        hard[k] = int(v)
    return hard


def build_v05_gate(page_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-page bundles into the final v05 gate."""
    hard_keys: List[str] = []
    for r in page_results:
        for k in (r.get("hard_metrics") or {}):
            if k not in hard_keys:
                hard_keys.append(k)
    totals = {k: sum(int((r.get("hard_metrics") or {}).get(k, 0))
                     for r in page_results) for k in hard_keys}
    conditions = {}
    for k in hard_keys:
        if k in _COVERAGE_KEYS:
            continue
        conditions[k] = totals[k] == 0
    # coverage: average across pages must be 1.0
    cov_vals = [(r.get("hard_metrics") or {}).get(
        "final_visible_translation_coverage") for r in page_results]
    cov_vals = [v for v in cov_vals if v is not None]
    conditions["final_visible_translation_coverage"] = bool(
        cov_vals) and abs(sum(cov_vals) / len(cov_vals) - 1.0) < 1e-6
    conditions["qa_execution_complete"] = all(
        r.get("execution_integrity", {}).get("qa_execution_complete", False)
        for r in page_results)
    conditions["all_pages_rendered"] = all(
        r.get("outputs") for r in page_results)
    conditions["production_special_case_count"] = all(
        (r.get("hard_metrics") or {}).get(
            "production_special_case_count", 0) == 0
        for r in page_results)
    decision = "pass" if all(conditions.values()) else "blocked"
    return {
        "schema_version": "visual_v05.gate.v1",
        "decision": decision,
        "conditions": conditions,
        "totals": totals,
        "total_api_calls": sum(int(r.get("api_calls", 0))
                               for r in page_results),
        "page_results": [{
            "doc": r.get("doc"), "page": r.get("page"),
            "passed": r.get("passed"), "decision": r.get("decision"),
            "hard_metrics": r.get("hard_metrics", {}),
        } for r in page_results],
    }
