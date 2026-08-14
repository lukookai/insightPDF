# -*- coding: utf-8 -*-
"""FormulaModelCompletenessQA (Phase 4E.1B-C2.1).

Classifies every FormulaModel against its six lifecycle counters:

    core_component_count
    owned_fragment_count
    math_ink_component_count
    render_segment_count
    svg_asset_count
    dom_placement_count

and assigns one terminal classification:

    complete
    ownership_ambiguous
    component_missing
    render_segment_missing
    asset_missing
    placement_missing
"""
from __future__ import annotations

import json
from pathlib import Path

from formula_ownership_graph import _is_math_font, _bbox


def _formula_models(page_model):
    return [r.get("payload") or {}
            for r in page_model.get("regions", [])
            if r.get("type") == "formula"]


def _svg_assets_for(trace, formula_id):
    for t in (trace or {}).get("traces", []):
        if t.get("source_formula_id") != formula_id:
            continue
        segs = (t.get("svg") or {}).get("segments", [])
        existing = [s for s in segs if s.get("svg_exists")]
        return len(segs), len(existing)
    return 0, 0


def _dom_nodes_for(trace, formula_id):
    for t in (trace or {}).get("traces", []):
        if t.get("source_formula_id") != formula_id:
            continue
        return len((t.get("placement") or {}).get("placements", []))
    return 0


def classify_model(fm, owned_fragments, trace):
    """Classify one FormulaModel's completeness."""
    fid = fm.get("formula_id")
    comps = fm.get("components") or []
    math_comps = [c for c in comps if _is_math_font(c.get("font"))]
    ink_comps = [c for c in comps if _bbox(c.get("bbox")) is not None]
    nseg = len(fm.get("render_segments") or [])
    seg_total, seg_existing = _svg_assets_for(trace, fid)
    dom = _dom_nodes_for(trace, fid)

    core = len(math_comps)
    owned = len(owned_fragments.get(fid, []))
    ink = len(ink_comps)

    classification = "complete"
    reasons = []
    if core == 0:
        classification = "component_missing"
        reasons.append("no math core component")
    elif owned == 0:
        classification = "ownership_ambiguous"
        reasons.append("no source fragment uniquely owned")
    elif nseg == 0:
        classification = "render_segment_missing"
        reasons.append("no render segment")
    elif seg_existing == 0:
        classification = "asset_missing"
        reasons.append("no SVG asset generated")
    elif dom == 0:
        classification = "placement_missing"
        reasons.append("no DOM placement")

    return {
        "formula_id": fid,
        "placement": fm.get("placement"),
        "type": fm.get("type"),
        "core_component_count": core,
        "owned_fragment_count": owned,
        "math_ink_component_count": ink,
        "render_segment_count": nseg,
        "svg_asset_count": seg_existing,
        "svg_segment_count": seg_total,
        "dom_placement_count": dom,
        "classification": classification,
        "reasons": reasons,
    }


def formula_model_completeness_qa(page_model, graph=None, trace=None,
                                  out_path=None):
    """Classify every FormulaModel on one page."""
    # owned fragments resolved to each formula (from the graph resolution)
    owned_fragments = {}
    if graph:
        for f in graph.get("nodes", {}).get("source_fragments", []):
            if f.get("owner_type") == "formula" and f.get("owner"):
                owned_fragments.setdefault(f["owner"], []).append(f["id"])

    records = [classify_model(fm, owned_fragments, trace)
               for fm in _formula_models(page_model)]

    counts = {}
    for r in records:
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1

    result = {
        "schema_version": "phase4e1b.c21.formula_model_completeness_qa.v1",
        "page": int(page_model.get("page") or 0),
        "formula_model_count": len(records),
        "counts": counts,
        "records": records,
        "complete_count": counts.get("complete", 0),
        "orphan_formula_count": counts.get("asset_missing", 0)
        + counts.get("placement_missing", 0),
        "owned_unrendered_count": counts.get("asset_missing", 0)
        + counts.get("placement_missing", 0),
        "decision": "pass" if all(r["classification"] == "complete"
                                  for r in records) else "fail",
    }
    if out_path:
        _dump(out_path, result)
    return result


def formula_model_completeness_document(page_results, out_path=None):
    """Aggregate per-page completeness into a document report."""
    records = [r for page in page_results
               for r in page.get("records", [])]
    counts = {}
    for r in records:
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1
    result = {
        "schema_version": "phase4e1b.c21.formula_model_completeness_qa.document.v1",
        "page_count": len(page_results),
        "formula_model_count": len(records),
        "counts": counts,
        "complete_count": counts.get("complete", 0),
        "orphan_formula_count": counts.get("asset_missing", 0)
        + counts.get("placement_missing", 0),
        "owned_unrendered_count": counts.get("asset_missing", 0)
        + counts.get("placement_missing", 0),
        "records": records,
        "decision": "pass" if all(r["classification"] == "complete"
                                  for r in records) else "fail",
    }
    if out_path:
        _dump(out_path, result)
    return result


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")
