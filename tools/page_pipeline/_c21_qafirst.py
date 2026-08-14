# -*- coding: utf-8 -*-
"""Phase 4E.1B-C2.1 QA-FIRST: reproduce old C2 defects from frozen artifacts.

Reads the frozen C2 output (formula_document_trace.json / taxonomy / gate) and
reproduces the five red numbers BEFORE any production ownership change.
"""
import json
from pathlib import Path

C2 = Path("outputs/phase4e1b_c2_formula_trace")
OUT = Path("outputs/phase4e1b_c21_formula_ownership")


def main():
    doc = json.loads((C2 / "formula_document_trace.json").read_text(encoding="utf-8"))
    tax = json.loads((C2 / "formula_failure_taxonomy.json").read_text(encoding="utf-8"))
    gate = json.loads((C2 / "checkpoint_gate.json").read_text(encoding="utf-8"))
    traces = doc["traces"]

    double_spans = {}
    affected_models = []
    for t in traces:
        for f in t["ownership"].get("double_owned_fragments", []):
            sid = f.get("source_span_id")
            if sid:
                double_spans.setdefault(sid, {
                    "text": f.get("text"),
                    "bbox": f.get("bbox"),
                    "claims": f.get("formula_claims"),
                    "owner_claims": f.get("owner_claims"),
                    "reported_by_formula": [],
                })
                double_spans[sid]["reported_by_formula"].append({
                    "page": t["page"], "formula_id": t["source_formula_id"],
                    "trace_id": t["formula_trace_id"]})
        if t["ownership"].get("double_owned_fragment_count", 0) > 0:
            affected_models.append({
                "page": t["page"], "formula_id": t["source_formula_id"],
                "trace_id": t["formula_trace_id"]})

    dups = []
    for t in traces:
        if t["placement"].get("duplicate_embed_count", 0) > 0:
            dups.append({
                "page": t["page"], "formula_id": t["source_formula_id"],
                "trace_id": t["formula_trace_id"],
                "expected_segment_count": t["placement"]["expected_segment_count"],
                "dom_placement_count": t["placement"]["dom_placement_count"],
                "duplicate_embed_count": t["placement"]["duplicate_embed_count"]})

    orphans = []
    for t in traces:
        if t["svg"].get("missing_count", 0) > 0 or t["placement"].get("embed_missing_count", 0) > 0:
            orphans.append({
                "page": t["page"], "formula_id": t["source_formula_id"],
                "trace_id": t["formula_trace_id"],
                "svg_missing": t["svg"].get("missing_count", 0),
                "embed_missing": t["placement"].get("embed_missing_count", 0),
                "components": [c["text"] for c in t["source"]["source_text_fragments"]]})

    owned_unrendered = []
    for t in traces:
        if t["placement"].get("embed_missing_count", 0) > 0 or \
                t["final_pdf"].get("blank_placement_count", 0) > 0:
            owned_unrendered.append({
                "page": t["page"], "formula_id": t["source_formula_id"],
                "trace_id": t["formula_trace_id"]})

    evidence = {
        "schema_version": "phase4e1b.c21.ownership_old_red_evidence.v1",
        "source": "frozen Phase 4E.1B-C2 artifacts",
        "reproduction": {
            "fragment_double_owned": len(double_spans),
            "affected_formula_models": len(affected_models),
            "duplicate_formula": len(dups),
            "orphan_formula": len(orphans),
            "owned_unrendered": len(owned_unrendered),
        },
        "expected": {"fragment_double_owned": 19, "affected_formula_models": 17,
                     "duplicate_formula": 7, "orphan_formula": 2,
                     "owned_unrendered": 2},
        "reproduced": (len(double_spans) == 19 and len(affected_models) == 17
                       and len(dups) == 7 and len(orphans) == 2
                       and len(owned_unrendered) == 2),
        "double_owned_spans": [
            {"span_id": sid, **d}
            for sid, d in sorted(double_spans.items(),
                                 key=lambda kv: int(kv[0][1:]))],
        "double_owned_affected_models": affected_models,
        "duplicate_formulas": dups,
        "orphan_formulas": orphans,
        "owned_unrendered_formulas": owned_unrendered,
        "c2_taxonomy_counts": tax["counts"],
        "c2_gate_hard_metrics": gate.get("hard_metrics"),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ownership_old_red_evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print("reproduction:", evidence["reproduction"])
    print("reproduced == expected:", evidence["reproduced"])


if __name__ == "__main__":
    main()
