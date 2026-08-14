# -*- coding: utf-8 -*-
"""QA execution integrity (Phase 4E.2A).

Tracks per-stage QA execution status and enforces a FAIL-CLOSED delivery gate:
only ``status == "pass"`` counts as PASS.  ``error`` / ``not_run`` / ``missing``
all BLOCK.

This is the architectural fix for the 4E.2 CRITICAL regression where a
NameError in ``residual_source_language_qa`` crashed the whole page QA loop,
silently skipped 8 downstream QA stages, and the delivery gate reported those
never-run stages as "pass" (fail-open false negative).

DocumentSemanticState stays the single semantic truth: ``semantic_role`` is
threaded through the paragraph model into residual QA, never re-derived.
"""
from __future__ import annotations

import time
import traceback

# QA stages that MUST run per page (render is the prerequisite; the rest are
# independent audit stages that must each actually execute and pass).
REQUIRED_STAGES = [
    "render",
    "formula_exclusivity_qa",
    "residual_language_qa",
    "footnote_collision_qa",
    "residual_prose_qa",
    "render_closure_qa",
    "rendered_collision_qa",
    "heading_duplicate_qa",
    "formula_crop_qa",
    "formula_render_trace",
    "formula_render_completeness_qa",
]

# stage -> the boolean key in the stage result dict that means "this stage
# found no defect".  ``None`` means the stage is a diagnostic trace whose
# execution (no exception) is its only pass criterion.
STAGE_PASS_KEYS = {
    "formula_exclusivity_qa": "formula_exclusivity_passed",
    "residual_language_qa": "residual_clean",
    "residual_prose_qa": "residual_prose_clean",
    "render_closure_qa": "logical_paragraph_render_closure_clean",
    "rendered_collision_qa": "collision_gate_passed",
    "heading_duplicate_qa": "heading_qa_clean",
    "formula_crop_qa": "formula_crop_clean",
    "formula_render_trace": None,
    "formula_render_completeness_qa": "completeness_passed",
}

# stages whose "no defect" condition is NOT a single boolean key.
STAGE_PASS_FN = {
    # footnote QA reports footnote_collision_clean=False when NO footnote was
    # detected (footnote_bbox is None) -- that is "no defect", not "collision".
    "footnote_collision_qa": lambda r: (
        r.get("footnote_bbox") is None
        or r.get("footnote_collision_clean", True)),
}

# stage -> delivery-gate layer it feeds (for the fail-closed gate check).
STAGE_TO_LAYER = {
    "formula_exclusivity_qa": "formula_exclusivity_qa",
    "residual_language_qa": "residual_language_qa",
    "footnote_collision_qa": "footnote_collision_qa",
    "residual_prose_qa": "residual_prose_qa",
    "render_closure_qa": "render_closure_qa",
    "rendered_collision_qa": "rendered_collision_qa",
    "heading_duplicate_qa": "heading_duplicate_qa",
    "formula_crop_qa": "formula_crop_qa",
    "formula_render_trace": "formula_render_completeness_qa",
    "formula_render_completeness_qa": "formula_render_completeness_qa",
}


def run_stage(execution, stage, fn, *args, **kwargs):
    """Run one QA stage in isolation; NEVER raises.

    On success records status pass/fail (derived from the stage's pass key);
    on exception records status error with exception_type/message/traceback and
    returns None.  Independent stages continue to run so a single full run
    surfaces every real defect instead of truncating at the first error.
    """
    entry = {"stage": stage, "status": "not_run", "started_at": time.time()}
    try:
        result = fn(*args, **kwargs)
        pass_fn = STAGE_PASS_FN.get(stage)
        key = STAGE_PASS_KEYS.get(stage)
        if pass_fn is not None:
            entry["status"] = "pass" if pass_fn(result) else "fail"
        elif key is None:
            entry["status"] = "pass"
        elif result is None:
            entry["status"] = "error"
            entry["exception_type"] = "NoResult"
            entry["exception_message"] = "stage returned None"
        else:
            entry["status"] = "pass" if result.get(key, True) else "fail"
        entry["finished_at"] = time.time()
        entry["result"] = result
        execution[stage] = entry
        return result
    except Exception as exc:  # noqa: BLE001
        entry["status"] = "error"
        entry["finished_at"] = time.time()
        entry["exception_type"] = type(exc).__name__
        entry["exception_message"] = str(exc)
        entry["traceback"] = traceback.format_exc()
        execution[stage] = entry
        return None


def mark_render_failed(execution):
    """Mark every required QA stage not_run after a render failure (no PDF)."""
    for stage in REQUIRED_STAGES:
        if stage == "render":
            execution[stage] = {"stage": stage, "status": "error",
                                "reason": "render failed"}
        else:
            execution[stage] = {"stage": stage, "status": "not_run",
                                "reason": "render failed (no final PDF)"}


def document_manifest(page_results, expected_page_count):
    """Aggregate per-page qa_execution into a document-level manifest."""
    required = [s for s in REQUIRED_STAGES]
    pages_seen = 0
    stage_statuses = {s: {"pass": 0, "fail": 0, "error": 0, "not_run": 0,
                          "missing": 0} for s in required}
    for page_number, record in page_results.items():
        pages_seen += 1
        exec_map = record.get("qa_execution") or {}
        for s in required:
            status = exec_map.get(s, {}).get("status", "missing")
            if status not in stage_statuses[s]:
                status = "missing"
            stage_statuses[s][status] += 1

    # a page is "complete" only if every required stage is present and passed
    complete_pages = 0
    incomplete = []
    for page_number, record in page_results.items():
        exec_map = record.get("qa_execution") or {}
        ok = all(exec_map.get(s, {}).get("status") == "pass" for s in required)
        if ok:
            complete_pages += 1
        else:
            incomplete.append({
                "page": page_number,
                "missing_or_not_pass": [
                    s for s in required
                    if exec_map.get(s, {}).get("status") != "pass"],
            })

    # document-level counts
    executed = sum(stage_statuses[s]["pass"] + stage_statuses[s]["fail"]
                   for s in required)
    counts = {"required_qa_stage_count": len(required),
              "executed_qa_stage_count": executed,
              "passed_qa_stage_count": sum(stage_statuses[s]["pass"]
                                           for s in required),
              "failed_qa_stage_count": sum(stage_statuses[s]["fail"]
                                           for s in required),
              "errored_qa_stage_count": sum(stage_statuses[s]["error"]
                                            for s in required),
              "not_run_qa_stage_count": sum(stage_statuses[s]["not_run"]
                                            for s in required),
              "missing_qa_stage_count": sum(stage_statuses[s]["missing"]
                                            for s in required)}

    # qa_execution_complete == "QA actually ran completely" (integrity), NOT
    # "all stages found no defect".  A "fail" status is a legitimate result
    # that the delivery gate blocks via its layer logic; only error/not_run/
    # missing (and un-rendered pages) violate execution completeness.
    qa_execution_complete = (
        counts["errored_qa_stage_count"] == 0
        and counts["not_run_qa_stage_count"] == 0
        and counts["missing_qa_stage_count"] == 0
        and pages_seen == expected_page_count)

    slots = len(required) * expected_page_count
    required_stage_coverage = round(executed / max(slots, 1), 4)

    return {
        "schema_version": "phase4e2a.qa_execution_manifest.v1",
        "expected_page_count": expected_page_count,
        "pages_seen": pages_seen,
        "required_stages": required,
        "per_stage_status_counts": stage_statuses,
        "counts": counts,
        "required_stage_coverage": required_stage_coverage,
        "complete_pages": complete_pages,
        "incomplete_pages": incomplete,
        "qa_execution_complete": qa_execution_complete,
    }


def integrity_gate(manifest):
    """QAIntegrityGate hard metrics (Phase 4E.2A acceptance criteria)."""
    counts = manifest.get("counts", {})
    hard = {
        "semantic_roles_scope_error": counts.get("errored_qa_stage_count", 0),
        "qa_stage_exception_count": counts.get("errored_qa_stage_count", 0),
        "qa_stage_not_run_count": counts.get("not_run_qa_stage_count", 0),
        "qa_stage_missing_count": counts.get("missing_qa_stage_count", 0),
        "required_stage_coverage": manifest.get("required_stage_coverage", 0.0),
        "delivery_gate_fail_open_count": 0,  # filled by caller post-check
    }
    decision = "pass" if (
        hard["semantic_roles_scope_error"] == 0
        and hard["qa_stage_exception_count"] == 0
        and hard["qa_stage_not_run_count"] == 0
        and hard["qa_stage_missing_count"] == 0
        and hard["required_stage_coverage"] >= 1.0
    ) else "blocked"
    return {
        "schema_version": "phase4e2a.qa_integrity_gate.v1",
        "hard": hard,
        "decision": decision,
        "qa_execution_complete": manifest.get("qa_execution_complete", False),
    }


def fail_closed_layer_statuses(page_results, layers):
    """Force every delivery-gate layer whose backing QA stage did NOT pass to
    "blocked".  This is the fail-closed rule: NOT_RUN / ERROR / MISSING can
    never be PASS."""
    from collections import defaultdict
    # per stage collect statuses across pages
    stage_status = defaultdict(list)
    for record in page_results.values():
        exec_map = record.get("qa_execution") or {}
        for stage, layer in STAGE_TO_LAYER.items():
            stage_status[layer].append(exec_map.get(stage, {}).get("status", "missing"))

    forced = []
    for layer, statuses in stage_status.items():
        # force blocked ONLY when the backing stage did NOT actually execute
        # (error / not_run / missing).  A "fail" status is a legitimate
        # "found a defect" result that the existing gate layer logic already
        # turns into blocked via the result's clean flag.
        if any(s in ("error", "not_run", "missing") for s in statuses):
            layers[layer] = "blocked"
            forced.append({"layer": layer, "statuses": {
                "pass": statuses.count("pass"),
                "fail": statuses.count("fail"),
                "error": statuses.count("error"),
                "not_run": statuses.count("not_run"),
                "missing": statuses.count("missing"),
            }})
    return forced
