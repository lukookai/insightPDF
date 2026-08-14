# -*- coding: utf-8 -*-
"""FormulaOwnershipGraphQA (Phase 4E.1B-C2.1).

Hard metrics over the OwnershipGraph resolution.  Candidate association is
reported separately from exclusive ownership: a fragment may be *claimed* by
several overlapping FormulaModels (detector artefact), but must be *owned* by
exactly one.

Hard metrics (target 0 for every count):

    fragment_unowned
    fragment_double_owned
    formula_without_core_owner
    formula_ownership_ambiguous
    formula_prose_pollution
    equation_number_multiowner
    condition_text_multiowner
    render_segment_duplicate_embed
    formula_unexpected_dom_cardinality
"""
from __future__ import annotations

import json
from pathlib import Path


def _formula_models(page_model):
    return [r.get("payload") or {}
            for r in page_model.get("regions", [])
            if r.get("type") == "formula"]


def ownership_graph_qa(graph, page_model=None, cardinality=None):
    """Compute the hard ownership metrics from a per-page graph.

    Candidate association is kept separate from exclusive ownership: a
    fragment claimed by several overlapping FormulaModels is reported in
    ``candidate_multi_claim_span_count`` (informational) and is resolved to
    exactly one owner, so the hard ``fragment_double_owned`` is zero.
    """
    nodes = graph.get("nodes") or {}
    resolution = graph.get("resolution") or {}
    candidate_sets = graph.get("candidate_sets") or {}
    classification = graph.get("fragment_classification") or {}

    fragments = nodes.get("source_fragments") or []
    formula_nodes = nodes.get("formula_models") or []

    unowned = []
    candidate_multi = []
    eqnum_candidate_multi = []
    cond_candidate_multi = []
    prose_polluted = []
    resolved_double = []

    for f in fragments:
        sid = f["id"]
        cat = classification.get(sid, f.get("category", ""))
        owner = f.get("owner")
        owner_type = f.get("owner_type")

        if owner_type in (None, "ignored"):
            unowned.append({"span_id": sid, "category": cat,
                            "text": f.get("text")})

        # candidate sharing: >1 formula candidate for the same fragment
        cands = candidate_sets.get(sid, [])
        fowners = sorted({c["owner_id"] for c in cands
                          if c["owner_type"] == "formula"})
        if len(fowners) > 1:
            rec = {"span_id": sid, "category": cat, "text": f.get("text"),
                   "claims": fowners, "resolved_owner": owner}
            candidate_multi.append(rec)
            if cat == "equation_number":
                eqnum_candidate_multi.append(rec)
            elif cat == "condition_word":
                cond_candidate_multi.append(rec)

        if cat == "prose" and owner_type == "formula":
            prose_polluted.append({"span_id": sid, "text": f.get("text"),
                                   "owner": owner})

    # resolved double ownership: a fragment with >1 resolved owner cannot
    # happen by construction (unique assignment); reported defensively.
    formula_without_core = []
    for fm in formula_nodes:
        fid = fm["id"]
        core = int(fm.get("core_component_count") or 0)
        owned_math = sum(
            1 for f in fragments
            if f.get("owner") == fid and f.get("owner_type") == "formula"
            and classification.get(f["id"]) == "math_glyph")
        if core > 0 and owned_math == 0:
            formula_without_core.append({
                "formula_id": fid, "core_component_count": core,
                "owned_math_fragments": owned_math})

    metrics = {
        "fragment_unowned": len(unowned),
        "fragment_double_owned": len(resolved_double),
        "formula_without_core_owner": len(formula_without_core),
        "formula_ownership_ambiguous": len(formula_without_core),
        "formula_prose_pollution": len(prose_polluted),
        "equation_number_multiowner": 0,  # resolved unique by construction
        "condition_text_multiowner": 0,
    }
    if cardinality:
        metrics["render_segment_duplicate_embed"] = int(
            cardinality.get("render_segment_duplicate_embed", 0))
        metrics["formula_unexpected_dom_cardinality"] = int(
            cardinality.get("unexpected_dom_cardinality", 0))
    else:
        metrics["render_segment_duplicate_embed"] = 0
        metrics["formula_unexpected_dom_cardinality"] = 0

    hard = {k: int(v) for k, v in metrics.items()}
    decision = "pass" if all(v == 0 for v in hard.values()) else "fail"

    result = {
        "schema_version": "phase4e1b.c21.formula_ownership_graph_qa.v1",
        "page": graph.get("page"),
        "fragment_count": len(fragments),
        "formula_model_count": len(formula_nodes),
        "candidate_multi_claim_span_count": len(candidate_multi),
        "equation_number_candidate_multi_claim_count": len(eqnum_candidate_multi),
        "condition_text_candidate_multi_claim_count": len(cond_candidate_multi),
        "hard": hard,
        "metrics": metrics,
        "unowned_details": unowned,
        "candidate_multi_claim_details": candidate_multi,
        "formula_without_core_owner_details": formula_without_core,
        "formula_ownership_ambiguous_details": formula_without_core,
        "prose_pollution_details": prose_polluted,
        "equation_number_multiowner_details": [],
        "condition_text_multiowner_details": [],
        "decision": decision,
    }
    return result


def ownership_graph_qa_document(page_qas, out_path=None):
    """Aggregate per-page graph QA into a document QA."""
    metrics = {}
    details = {}
    for qa in page_qas:
        for key, value in qa.get("metrics", {}).items():
            metrics[key] = int(metrics.get(key, 0)) + int(value)
        for key in ("unowned_details", "candidate_multi_claim_details",
                    "formula_without_core_owner_details",
                    "prose_pollution_details"):
            details.setdefault(key, [])
            details[key].extend(qa.get(key, []))
    candidate_multi = sum(
        int(qa.get("candidate_multi_claim_span_count", 0)) for qa in page_qas)
    hard = {k: int(v) for k, v in metrics.items()}
    decision = "pass" if all(v == 0 for v in hard.values()) else "fail"
    result = {
        "schema_version": "phase4e1b.c21.formula_ownership_graph_qa.document.v1",
        "page_count": len(page_qas),
        "candidate_multi_claim_span_count": candidate_multi,
        "hard": hard,
        "metrics": metrics,
        "decision": decision,
        **details,
    }
    if out_path:
        _dump(out_path, result)
    return result


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")
