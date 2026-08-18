# -*- coding: utf-8 -*-
"""visual_v06_gate -- aggregate all v06 QA layers + existing hard metrics.

v06 adds three new closure QAs on top of v05's hard gate:

  semantic_role_translation_qa (All-Role Translation Closure):
    role_target_missing_count, role_source_residual_count,
    heading_target_missing_count, heading_source_residual_count,
    caption_target_missing_count, list_target_missing_count,
    abstract_target_missing_count, role_wrong_render_source_count

  short_fragment_residual_qa (Short Residual Closure):
    short_translatable_source_residual_count, paragraph_head_residual_count,
    paragraph_tail_residual_count, standalone_short_residual_count,
    heading_short_residual_count, formula_context_short_residual_count,
    short_target_missing_count

  math_token_sequence_qa (Math Token Sequence Closure):
    math_token_loss_count, math_token_duplicate_count,
    math_token_order_inversion_count, math_base_symbol_loss_count,
    math_superscript_loss_count, math_subscript_loss_count,
    math_group_structure_mismatch_count

All v05/v04/v03/v02 hard metrics remain in the gate (auto-discovered from each
page result's ``hard_metrics``), so the gate is a strict superset of v05's.

Hard gate conditions (all must hold):

  * every discovered hard metric (except coverage keys) == 0
  * final_visible_translation_coverage average across pages == 1.0
  * qa_execution_complete == True on every page
  * all pages produced rendered output
  * production_special_case_count == 0
"""

from __future__ import annotations

from typing import Any, Dict, List

# v06 new hard keys, by QA (for documentation + merge helpers).
V06_NEW_KEYS = [
    "role_target_missing_count",
    "role_source_residual_count",
    "heading_target_missing_count",
    "heading_source_residual_count",
    "caption_target_missing_count",
    "list_target_missing_count",
    "abstract_target_missing_count",
    "role_wrong_render_source_count",
    "short_translatable_source_residual_count",
    "paragraph_head_residual_count",
    "paragraph_tail_residual_count",
    "standalone_short_residual_count",
    "heading_short_residual_count",
    "formula_context_short_residual_count",
    "short_target_missing_count",
    "math_token_loss_count",
    "math_token_duplicate_count",
    "math_token_order_inversion_count",
    "math_base_symbol_loss_count",
    "math_superscript_loss_count",
    "math_subscript_loss_count",
    "math_group_structure_mismatch_count",
]

# QA result key -> list of metric keys it contributes as HARD.
_V06_QA_KEYS = {
    "semantic_role": [
        "role_target_missing_count", "role_source_residual_count",
        "heading_target_missing_count", "heading_source_residual_count",
        "caption_target_missing_count", "list_target_missing_count",
        "abstract_target_missing_count", "role_wrong_render_source_count",
    ],
    "short_fragment": [
        "short_translatable_source_residual_count",
        "paragraph_head_residual_count", "paragraph_tail_residual_count",
        "standalone_short_residual_count", "heading_short_residual_count",
        "formula_context_short_residual_count", "short_target_missing_count",
    ],
    "math_token": [
        "math_token_loss_count", "math_token_duplicate_count",
        "math_token_order_inversion_count", "math_base_symbol_loss_count",
        "math_superscript_loss_count", "math_subscript_loss_count",
        "math_group_structure_mismatch_count",
    ],
}

# coverage-style keys: their value is a ratio (==1.0 required), not a count.
_COVERAGE_KEYS = {
    "final_visible_translation_coverage",
    "translation_pipeline_coverage",
}


def merge_v06_qa(qa: Dict[str, Any], hard: Dict[str, Any]) -> Dict[str, Any]:
    """Merge one v06 QA's metrics into the per-page hard dict.

    Only known hard keys are injected; informational counters (e.g.
    translation_required_role_count) are intentionally excluded so they never
    drive the gate.
    """
    for k, v in (qa.get("metrics") or {}).items():
        if k in V06_NEW_KEYS:
            hard[k] = int(v)
    return hard


def collect_v06_hard_metrics(page_qa_results: Dict[str, Any]) -> Dict[str, int]:
    """Build the v06 slice of hard_metrics from a per-page QA dict that
    contains semantic_role / short_fragment / math_token keys."""
    hard: Dict[str, int] = {}
    for qa_key in ("semantic_role", "short_fragment", "math_token"):
        qa = page_qa_results.get(qa_key)
        if qa:
            merge_v06_qa(qa, hard)
    return hard


def build_v06_gate(page_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-page bundles into the final v06 gate.

    Each page result must expose:
      hard_metrics: dict[str, num]   -- all hard metrics (v02..v06)
      execution_integrity: {qa_execution_complete: bool}
      outputs: truthy if the page produced rendered output
      api_calls: int
      doc, page, passed, decision
    """
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
        "schema_version": "visual_v06.gate.v1",
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
