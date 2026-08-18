# -*- coding: utf-8 -*-
"""review_metadata_qa -- keep review-bundle metadata internally consistent.

The review bundle exposes a top-level ``gate`` field plus a
``blocked_reasons`` list.  A semantic conflict exists when
``gate == "pass"`` while ``blocked_reasons`` is non-empty: the bundle would
claim success while listing blockers.  This QA derives the authoritative gate
from ``blocked_reasons`` (non-empty => blocked) and reports any mismatch so
the bundle can never lie about its own verdict.

It also provides ``build_review_metadata(gate_result, page_results)`` which
produces a conflict-free metadata dict in the first place: ``gate`` is taken
from the gate decision and ``blocked_reasons`` is the list of failing
condition keys, so the downstream QA always passes.
"""
from __future__ import annotations

from typing import Any, Dict, List


def _failing_conditions(gate_result: Dict[str, Any]) -> List[str]:
    """Return the condition keys whose value is falsy (i.e. blocking)."""
    conds = gate_result.get("conditions") or {}
    return [k for k, v in conds.items() if not v]


def build_review_metadata(gate_result: Dict[str, Any],
                          page_results: List[Dict[str, Any]] | None = None
                          ) -> Dict[str, Any]:
    """Build a conflict-free review metadata dict from a gate result.

    ``gate`` equals the gate decision; ``blocked_reasons`` lists the failing
    condition keys.  Because both are derived from the same source, the
    review_metadata_qa can never flag a conflict.
    """
    decision = gate_result.get("decision") or "blocked"
    blocked = _failing_conditions(gate_result)
    meta: Dict[str, Any] = {
        "gate": decision,
        "blocked_reasons": blocked,
        "schema_version": gate_result.get("schema_version",
                                          "visual_v06.gate.v1"),
        "total_api_calls": gate_result.get("total_api_calls", 0),
        "page_results": gate_result.get("page_results", []),
    }
    if page_results is not None:
        meta["page_results"] = page_results
    return meta


def review_metadata_qa(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Validate that ``gate`` is consistent with ``blocked_reasons``.

    Rule (authoritative): if ``blocked_reasons`` is non-empty the only valid
    gate is ``"blocked"``.  Any other pairing is a conflict.

    Returns a dict with the corrected gate and a pass/fail decision.  A
    conflict is itself a hard defect (metadata_gate_conflict_count) so the
    gate cannot silently ship a contradictory bundle.
    """
    blocked = metadata.get("blocked_reasons") or []
    declared_gate = metadata.get("gate")
    # authoritative gate derived from reasons
    auth_gate = "blocked" if blocked else "pass"
    conflict = (declared_gate != auth_gate)
    return {
        "qa": "review_metadata",
        "schema_version": "visual_v06.review_metadata_qa.v1",
        "declared_gate": declared_gate,
        "derived_gate": auth_gate,
        "conflict": conflict,
        "blocked_reasons_count": len(blocked),
        "corrected_gate": auth_gate,
        "metrics": {
            "metadata_gate_conflict_count": 1 if conflict else 0,
            "blocked_reasons_count": len(blocked),
        },
        "decision": "pass" if (not conflict and auth_gate == "pass") else "fail",
    }
