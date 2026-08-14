# -*- coding: utf-8 -*-
"""Phase 4E.2A QA-FIRST: generate crash + fail-open evidence from existing
Phase 4E.2 artifacts (no re-translation, no production change)."""
import json
from pathlib import Path

OUT = Path("outputs/phase4e2a_qa_recovery")
OUT.mkdir(parents=True, exist_ok=True)
P4E2 = Path("outputs/phase4e2_two_pdf_smoke")

DOWNSTREAM = ["footnote_collision_qa", "residual_prose_qa", "render_closure_qa",
              "rendered_collision_qa", "heading_duplicate_qa", "formula_crop_qa",
              "formula_adopted_prose_qa", "formula_render_trace",
              "formula_render_completeness_qa"]

# ---- QA crash evidence from existing failure.json ----
crash_rows = []
for d, npg in (("doc1", 19), ("doc2", 12)):
    for pg in range(1, npg + 1):
        f = P4E2 / d / "pages" / ("p%03d" % pg) / "failure.json"
        if f.exists():
            j = json.loads(f.read_text(encoding="utf-8"))
            err = j.get("error", "")
            crash_rows.append({
                "document": d, "page": pg,
                "qa_stage": "residual_source_language_qa",
                "exception_type": "NameError",
                "exception_message": err,
                "downstream_skipped_stages": list(DOWNSTREAM),
            })

crash = {
    "schema_version": "phase4e2a.qa_crash_old_evidence.v1",
    "source": "Phase 4E.2 existing artifacts (failure.json per page)",
    "crash_pages": crash_rows,
    "total_crashed_pages": len(crash_rows),
    "root_cause": ("residual_source_language_qa.py:486 references semantic_roles "
                   "(local to _pdf_spans_by_role:263, not returned)"),
    "confirmed": (len(crash_rows) > 0
                  and all("semantic_roles" in r["exception_message"] for r in crash_rows)),
}
(OUT / "qa_crash_old_evidence.json").write_text(
    json.dumps(crash, ensure_ascii=False, indent=2), encoding="utf-8")
print("crash pages:", len(crash_rows), "confirmed:", crash["confirmed"])

# ---- gate fail-open evidence ----
gate_rows = []
CRASHED_LAYERS = ["residual_language_qa", "footnote_collision_qa",
                  "residual_prose_qa", "render_closure_qa",
                  "rendered_collision_qa", "heading_duplicate_qa",
                  "formula_crop_qa", "formula_exclusivity_qa"]
for d in ("doc1", "doc2"):
    dg = json.loads((P4E2 / d / "delivery_gate.json").read_text(encoding="utf-8"))
    layers = dg.get("layers", {})
    for layer in CRASHED_LAYERS:
        gate_rows.append({
            "document": d, "layer": layer,
            "gate_reported": layers.get(layer),
            "actual": "NOT_RUN (page QA loop crashed before this stage)",
            "fail_open": layers.get(layer) == "pass",
        })

fail_open = {
    "schema_version": "phase4e2a.gate_false_pass_old_evidence.v1",
    "assertion": "NOT_RUN != PASS (hard)",
    "layers": gate_rows,
    "fail_open_count": sum(1 for r in gate_rows if r["fail_open"]),
    "confirmed": any(r["fail_open"] for r in gate_rows),
}
(OUT / "gate_false_pass_old_evidence.json").write_text(
    json.dumps(fail_open, ensure_ascii=False, indent=2), encoding="utf-8")
print("fail_open layers:", fail_open["fail_open_count"], "confirmed:", fail_open["confirmed"])
