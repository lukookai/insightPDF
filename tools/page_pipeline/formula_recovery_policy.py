# -*- coding: utf-8 -*-
"""Stage-specific, minimal FormulaRecoveryPolicy.

The policy is intentionally conservative.  It chooses a reusable downstream
asset before an upstream rerun, and blocks when ownership or model semantics
are ambiguous.  It never performs page- or document-specific recovery.
"""
from __future__ import annotations

import json
from pathlib import Path


NO_ACTION = "NO_ACTION"
REBUILD_VIEWBOX = "REBUILD_VIEWBOX"
READOPT_ORPHAN_FRAGMENT = "READOPT_ORPHAN_FRAGMENT"
REEMBED_EXISTING_SVG = "REEMBED_EXISTING_SVG"
REGENERATE_SVG_FROM_EXISTING_MODEL = "REGENERATE_SVG_FROM_EXISTING_MODEL"
BLOCK_UNSAFE = "BLOCK_UNSAFE"


def choose_recovery(trace: dict) -> dict:
    """Choose the smallest safe recovery from a complete formula trace."""
    status = trace.get("status") or "complete"
    stage = trace.get("failure_stage")
    reason = trace.get("failure_reason")
    svg = trace.get("svg") or {}
    model = trace.get("model") or {}
    ownership = trace.get("ownership") or {}
    placement = trace.get("placement") or {}
    final_pdf = trace.get("final_pdf") or {}
    policy = NO_ACTION
    safe = True
    prerequisites = []
    rationale = "formula lifecycle is complete"

    if status == "complete":
        pass
    elif ownership.get("double_owned_fragment_count", 0) > 0:
        policy = BLOCK_UNSAFE
        safe = False
        rationale = ("a source fragment is claimed by multiple FormulaModels; "
                     "automatic nearest-owner repair can change semantics")
    elif status in ("ownership_error", "orphan"):
        if ownership.get("reattachment_evidence_complete"):
            policy = READOPT_ORPHAN_FRAGMENT
            prerequisites = ["baseline proximity", "reading order",
                             "vertical overlap", "semantic owner"]
            rationale = "one orphan fragment has convergent attachment evidence"
        else:
            policy = BLOCK_UNSAFE
            safe = False
            rationale = "orphan attachment evidence is incomplete or conflicting"
    elif placement.get("duplicate_embed_count", 0) > 0:
        policy = BLOCK_UNSAFE
        safe = False
        rationale = ("duplicate embed cannot be repaired by re-embedding; "
                     "the unique semantic placeholder must be proven first")
    elif (placement.get("embed_missing_count", 0) > 0
          and svg.get("missing_count", 0) > 0):
        policy = BLOCK_UNSAFE
        safe = False
        rationale = ("both the inline SVG asset and its DOM placement are "
                     "missing; regenerating only the SVG would leave an "
                     "orphan and placeholder ownership is not proven")
    elif stage == "svg_generation" and svg.get("viewbox_crop_count", 0):
        policy = REBUILD_VIEWBOX
        prerequisites = ["owned component union", "measured ink proximity"]
        rationale = "component ink exceeds the current render viewBox"
    elif stage == "svg_generation" and model.get("exists"):
        policy = REGENERATE_SVG_FROM_EXISTING_MODEL
        prerequisites = ["valid existing FormulaModel components"]
        rationale = "model exists but SVG is missing, invalid, or zero-ink"
    elif stage in ("embedding", "final_pdf") and svg.get("all_segments_have_ink"):
        policy = REEMBED_EXISTING_SVG
        prerequisites = ["existing SVG SHA unchanged", "target bbox unchanged"]
        rationale = "upstream SVG is valid; only embed/final placement failed"
    elif stage in ("detection", "model"):
        policy = BLOCK_UNSAFE
        safe = False
        rationale = ("no safe downstream asset exists; detector/model recovery "
                     "is outside the authorized C2 automatic path")
    elif final_pdf.get("renderer_disagreement"):
        policy = BLOCK_UNSAFE
        safe = False
        rationale = "independent final-PDF renderers disagree"
    else:
        policy = BLOCK_UNSAFE
        safe = False
        rationale = "failure does not have a proven stage-specific safe recovery"

    return {
        "formula_trace_id": trace.get("formula_trace_id"),
        "formula_id": trace.get("source_formula_id"),
        "page": trace.get("page"),
        "status": status,
        "failure_stage": stage,
        "failure_reason": reason,
        "policy": policy,
        "safe_to_apply_automatically": safe,
        "prerequisites": prerequisites,
        "rationale": rationale,
        "redetection_requested": False,
    }


def build_formula_recovery_report(traces, out_path=None) -> dict:
    rows = [choose_recovery(trace) for trace in traces or []]
    counts = {}
    for row in rows:
        counts[row["policy"]] = counts.get(row["policy"], 0) + 1
    blocked = [row for row in rows if row["policy"] == BLOCK_UNSAFE]
    result = {
        "schema_version": "phase4e1b.c2.formula_recovery_report.v1",
        "formula_trace_count": len(rows),
        "policy_counts": counts,
        "recoveries": rows,
        "automatic_recovery_count": sum(
            row["policy"] != NO_ACTION and row["safe_to_apply_automatically"]
            for row in rows),
        "blocked_unsafe_count": len(blocked),
        "redetection_count": 0,
        "svg_regeneration_count": sum(
            row["policy"] == REGENERATE_SVG_FROM_EXISTING_MODEL for row in rows),
        "svg_reembed_count": sum(
            row["policy"] == REEMBED_EXISTING_SVG for row in rows),
        "decision": "blocked" if blocked else "pass",
    }
    if out_path:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return result
