# -*- coding: utf-8 -*-
"""FinalRenderTruthGate (visual-v04).

Aggregates the four final-render-truth QAs into one fail-closed gate:

    1. FinalVisibleTranslationQA  (canonical target actually in final PDF)
    2. FinalSourceResidualQA      (source prose still visible? role-aware)
    3. SoftTextCollisionQA        (soft<->soft overlap in final PDF)
    4. FinalRenderCardinalityQA   (exactly-once, correct payload/region)

Hard (all zero):
    translatable_target_missing_count
    translatable_target_duplicate_count
    translatable_source_residual_count
    severe_soft_soft_collision_count
    duplicate_baseline_cluster_count
    missing_render_count
    duplicate_render_count
    wrong_region_render_count
    source_and_target_double_render_count

Plus the two translation-coverage metrics, computed SEPARATELY:

    translation_pipeline_coverage      (internal state: translated targets /
                                        required-translatable targets)
    final_visible_translation_coverage (FINAL PDF text layer: visible /
                                        required-translatable targets)

Hard: final_visible_translation_coverage == 1.0
"""

from __future__ import annotations

from typing import Any, Dict, List

from final_visible_translation_qa import final_visible_translation_qa  # noqa: E402
from final_source_residual_qa import final_source_residual_qa  # noqa: E402
from soft_text_collision_qa import soft_text_collision_qa  # noqa: E402
from final_render_cardinality_qa import final_render_cardinality_qa  # noqa: E402

HARD_KEYS = [
    "translatable_target_missing_count",
    "translatable_target_duplicate_count",
    "translatable_source_residual_count",
    "severe_soft_soft_collision_count",
    "duplicate_baseline_cluster_count",
    "missing_render_count",
    "duplicate_render_count",
    "wrong_region_render_count",
    "source_and_target_double_render_count",
]


def run_final_render_truth_qa(
    page_model: Dict[str, Any],
    translations: Dict[str, str],
    flows: List[Dict[str, Any]],
    final_pdf_path=None,
    html_path=None,
    source_pdf_path=None,
    out_dir=None,
    translation_pipeline_metrics: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run all four QAs for one page and build the fail-closed gate."""
    qa1 = final_visible_translation_qa(
        page_model, translations, flows, final_pdf_path=final_pdf_path,
        out_dir=out_dir)
    qa2 = final_source_residual_qa(
        page_model, translations, flows, final_pdf_path=final_pdf_path,
        source_pdf_path=source_pdf_path, out_dir=out_dir)
    qa3 = soft_text_collision_qa(
        page_model, flows, final_pdf_path=final_pdf_path,
        html_path=html_path, out_dir=out_dir)
    qa4 = final_render_cardinality_qa(
        page_model, translations, flows, final_pdf_path=final_pdf_path,
        html_path=html_path, out_dir=out_dir)

    hard = {
        "translatable_target_missing_count":
            qa1["metrics"]["translatable_target_missing_count"],
        "translatable_target_duplicate_count":
            qa1["metrics"]["translatable_target_duplicate_count"],
        "translatable_source_residual_count":
            qa2["metrics"]["translatable_source_residual_count"],
        "severe_soft_soft_collision_count":
            qa3["metrics"]["severe_soft_soft_collision_count"],
        "duplicate_baseline_cluster_count":
            qa3["metrics"]["duplicate_baseline_cluster_count"],
        "missing_render_count": qa4["metrics"]["missing_render_count"],
        "duplicate_render_count": qa4["metrics"]["duplicate_render_count"],
        "wrong_region_render_count":
            qa4["metrics"]["wrong_region_render_count"],
        "source_and_target_double_render_count":
            qa4["metrics"]["source_and_target_double_render_count"],
    }
    conditions = {k: (hard[k] == 0) for k in HARD_KEYS}

    # ---- two translation coverages, computed separately ----------------
    pipeline = translation_pipeline_metrics or {}
    pipeline_coverage = float(pipeline.get("pipeline_coverage", 1.0))
    n_req = qa1["metrics"]["translatable_target_count"]
    n_vis = qa1["metrics"]["target_visible_count"]
    final_coverage = (n_vis / n_req) if n_req else 1.0

    conditions["final_visible_translation_coverage"] = final_coverage >= 1.0
    hard["final_visible_translation_coverage"] = round(final_coverage, 6)
    hard["translation_pipeline_coverage"] = round(pipeline_coverage, 6)

    decision = "pass" if all(conditions.values()) else "blocked"
    return {
        "schema_version": "visual_v04.final_render_truth_gate.v1",
        "decision": decision,
        "conditions": conditions,
        "hard": hard,
        "final_visible_translation_qa": qa1,
        "final_source_residual_qa": qa2,
        "soft_text_collision_qa": qa3,
        "final_render_cardinality_qa": qa4,
    }
