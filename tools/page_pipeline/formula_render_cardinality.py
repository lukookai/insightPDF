# -*- coding: utf-8 -*-
"""FormulaRenderCardinality (Phase 4E.1B-C2.1).

For every FormulaModel, compute the *expected* render count (RenderSegment
cardinality) and the *actual* DOM placement count, then attribute every
duplicate to a root cause.

Expected cardinality is derived from RenderSegments, display/inline mode,
line fragmentation and continuation semantics -- NOT a hard "one render per
formula" rule: a multi-segment formula legitimately produces one DOM
placement per segment.

Duplicate root causes (A-F):
    A  same RenderSegment embedded twice
    B  one FormulaModel wrongly split into multiple RenderSegments
    C  paragraph renderer + formula renderer dual path
    D  SVG DOM node clone
    E  inline + display double consumption
    F  ownership duplication -> downstream duplication
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def _formula_models(page_model):
    return [r.get("payload") or {}
            for r in page_model.get("regions", [])
            if r.get("type") == "formula"]


def _paragraphs(page_model):
    return [r.get("payload") or {}
            for r in page_model.get("regions", [])
            if r.get("type") == "text"]


def _dom_nodes_for(trace, formula_id):
    """Return DOM placement nodes for a formula (from the C2 trace)."""
    out = []
    for t in (trace or {}).get("traces", []):
        if t.get("source_formula_id") != formula_id:
            continue
        for pl in (t.get("placement") or {}).get("placements", []):
            out.append(pl)
    return out


def _expected_render_count(fm):
    """RenderSegment cardinality (>= 1)."""
    return max(1, len(fm.get("render_segments") or []))


def _placeholder_occurrences(page_model, formula_id):
    """Count how many paragraph fragments carry this formula's placeholder."""
    token = "{{FORMULA_%s}}" % formula_id
    count = 0
    locations = []
    for para in _paragraphs(page_model):
        src = para.get("source_text") or ""
        occurrences = src.count(token)
        if occurrences:
            count += occurrences
            locations.append({
                "paragraph_id": para.get("paragraph_id"),
                "occurrences": occurrences,
            })
    return count, locations


def _attribute_duplicate(fm, expected, actual, placements, page_model):
    """Attribute a duplicate to a root cause (A-F)."""
    fid = fm.get("formula_id")
    placement = fm.get("placement")
    segs = fm.get("render_segments") or []
    nseg = len(segs)
    causes = []

    # unique segment ids present in the DOM
    dom_segment_ids = [pl.get("segment_id") for pl in placements if pl]
    seg_ids = [s.get("segment_id") for s in segs]

    occ_count, occ_locations = _placeholder_occurrences(page_model, fid)

    if placement == "inline":
        # an inline formula is duplicated when its placeholder appears in
        # more than one paragraph fragment (per line/paragraph).
        if occ_count > 1:
            causes.append({
                "code": "F",
                "reason": ("ownership duplication: inline placeholder inserted "
                           "into %d paragraph fragments, each rendering the "
                           "formula" % occ_count),
                "placeholder_occurrences": occ_count,
                "locations": occ_locations,
            })
        else:
            causes.append({
                "code": "A",
                "reason": "same RenderSegment embedded more than once",
            })
    else:
        # display formula: duplicated when the same segment appears twice
        dup_seg = [sid for sid in seg_ids if dom_segment_ids.count(sid) > 1]
        if dup_seg:
            causes.append({
                "code": "A",
                "reason": "RenderSegment embedded twice: %s" % dup_seg,
            })
        else:
            causes.append({
                "code": "B",
                "reason": "FormulaModel produced more RenderSegments than expected",
            })

    return causes


def formula_render_cardinality(page_model, trace=None, out_path=None):
    """Compute expected/actual render cardinality + duplicate attribution."""
    records = []
    duplicates = []
    for fm in _formula_models(page_model):
        fid = fm.get("formula_id")
        expected = _expected_render_count(fm)
        placements = _dom_nodes_for(trace, fid)
        actual = len(placements)
        placement = fm.get("placement")
        nseg = len(fm.get("render_segments") or [])
        rec = {
            "formula_id": fid,
            "placement": placement,
            "type": fm.get("type"),
            "render_segment_count": nseg,
            "expected_render_count": expected,
            "actual_dom_placement_count": actual,
            "cardinality_ok": actual == expected,
            "duplicate": max(0, actual - expected),
            "under_rendered": max(0, expected - actual),
            "expected_basis": ("render_segment_cardinality; display formula "
                               "renders one <img> per segment, inline formula "
                               "renders one <span> per segment from a single "
                               "canonical placeholder"),
        }
        if rec["duplicate"] > 0:
            rec["duplicate_root_causes"] = _attribute_duplicate(
                fm, expected, actual, placements, page_model)
            duplicates.append(rec)
        records.append(rec)

    duplicate_models = [r for r in records if r["duplicate"] > 0]
    under_rendered = [r for r in records if r["under_rendered"] > 0]
    result = {
        "schema_version": "phase4e1b.c21.formula_render_cardinality.v1",
        "formula_model_count": len(records),
        "expected_total_placements": sum(r["expected_render_count"]
                                         for r in records),
        "actual_total_placements": sum(r["actual_dom_placement_count"]
                                       for r in records),
        "duplicate_formula_count": len(duplicate_models),
        "duplicate_render_count": sum(r["duplicate"] for r in records),
        "render_segment_duplicate_embed": sum(
            r["duplicate"] for r in records),
        "unexpected_dom_cardinality": len(duplicate_models) + len(under_rendered),
        "under_rendered_formula_count": len(under_rendered),
        "records": records,
        "duplicate_details": duplicate_models,
        "decision": "pass" if not duplicate_models and not under_rendered
                    else "fail",
    }
    if out_path:
        _dump(out_path, result)
    return result


def build_duplicate_root_causes(cardinality, out_path=None):
    """Extract the per-duplicate root-cause report."""
    details = []
    for rec in (cardinality or {}).get("duplicate_details", []):
        details.append({
            "formula_id": rec["formula_id"],
            "placement": rec["placement"],
            "render_segment_count": rec["render_segment_count"],
            "expected_render_count": rec["expected_render_count"],
            "actual_dom_placement_count": rec["actual_dom_placement_count"],
            "duplicate_count": rec["duplicate"],
            "root_causes": rec.get("duplicate_root_causes", []),
        })
    cause_counts = {}
    for d in details:
        for c in d.get("root_causes", []):
            code = c.get("code")
            cause_counts[code] = cause_counts.get(code, 0) + 1
    result = {
        "schema_version": "phase4e1b.c21.duplicate_formula_root_causes.v1",
        "duplicate_formula_count": len(details),
        "cause_counts": cause_counts,
        "details": details,
        "summary": {
            "A_same_segment_embedded_twice": cause_counts.get("A", 0),
            "B_model_split_into_multiple_segments": cause_counts.get("B", 0),
            "C_paragraph_formula_dual_path": cause_counts.get("C", 0),
            "D_svg_dom_clone": cause_counts.get("D", 0),
            "E_inline_display_double": cause_counts.get("E", 0),
            "F_ownership_duplication_downstream": cause_counts.get("F", 0),
        },
    }
    if out_path:
        _dump(out_path, result)
    return result


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")
