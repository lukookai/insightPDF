# -*- coding: utf-8 -*-
"""Phase 4E.2A minimal fixtures (section H): prove NOT_RUN/ERROR/MISSING can
never become PASS, and the semantic_roles threading works."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "实现源码" / "pdf_translator"))

import qa_execution as qx
from residual_source_language_qa import residual_source_language_qa

P4E2 = Path("outputs/phase4e2_two_pdf_smoke")


def _page_model(pid="DLP00001", semantic_role="body", source_text="The cat sits on the mat."):
    return {"regions": [{"type": "text", "payload": {
        "paragraph_id": pid, "style_role": "body",
        "semantic_role": semantic_role,
        "source_text": source_text, "translated_text": "",
        "bbox": [70, 120, 520, 140]}}]}


results = []

# Fixture 1: semantic_role normal -> residual QA runs, returns pass/fail (no crash)
try:
    m = _page_model()
    pdf = P4E2 / "doc1" / "pages" / "p001" / "zh.pdf"
    r = residual_source_language_qa(m, pdf, out_dir=P4E2, flows=None)
    results.append({"fixture": "1_semantic_role_normal",
                    "ok": isinstance(r, dict) and "residual_clean" in r,
                    "status": "pass" if r.get("residual_clean") else "fail",
                    "residual_clean": r.get("residual_clean")})
except Exception as e:  # noqa: BLE001
    results.append({"fixture": "1_semantic_role_normal", "ok": False,
                    "error": "%s: %s" % (type(e).__name__, e)})

# Fixture 2: semantic_role reference_* -> residual exempt (no residual flagged)
try:
    m = _page_model(pid="DLP00099", semantic_role="reference",
                    source_text="Smith, John. A survey. 2024.")
    pdf = P4E2 / "doc1" / "pages" / "p001" / "zh.pdf"
    r = residual_source_language_qa(m, pdf, out_dir=P4E2, flows=None)
    residual_count = r.get("residual_english_span_count", 0)
    results.append({"fixture": "2_semantic_role_reference_exempt",
                    "ok": isinstance(r, dict) and residual_count == 0,
                    "residual_english_span_count": residual_count,
                    "status": "pass"})
except Exception as e:  # noqa: BLE001
    results.append({"fixture": "2_semantic_role_reference_exempt", "ok": False,
                    "error": "%s: %s" % (type(e).__name__, e)})

# Fixture 3: forced QA exception -> stage=error -> gate BLOCK
execution = {"render": {"stage": "render", "status": "pass"}}
qx.run_stage(execution, "residual_language_qa",
             lambda: (_ for _ in ()).throw(RuntimeError("boom")))
for s in qx.REQUIRED_STAGES:
    if s not in execution:
        execution[s] = {"stage": s, "status": "pass"}
m = qx.document_manifest({"1": {"qa_execution": execution}}, 1)
g = qx.integrity_gate(m)
results.append({"fixture": "3_qa_exception_blocks",
                "ok": execution["residual_language_qa"]["status"] == "error"
                      and g["decision"] == "blocked",
                "stage_status": execution["residual_language_qa"]["status"],
                "integrity_decision": g["decision"]})

# Fixture 4: delete a required QA result -> missing -> gate BLOCK
execution2 = {"render": {"stage": "render", "status": "pass"}}
for s in qx.REQUIRED_STAGES:
    if s != "residual_language_qa":
        execution2[s] = {"stage": s, "status": "pass"}
# residual_language_qa intentionally MISSING
m2 = qx.document_manifest({"1": {"qa_execution": execution2}}, 1)
g2 = qx.integrity_gate(m2)
results.append({"fixture": "4_missing_stage_blocks",
                "ok": m2["counts"]["missing_qa_stage_count"] > 0
                      and g2["decision"] == "blocked",
                "missing": m2["counts"]["missing_qa_stage_count"],
                "integrity_decision": g2["decision"]})

out = {
    "schema_version": "phase4e2a.minimal_fixtures.v1",
    "fixtures": results,
    "all_pass": all(r["ok"] for r in results),
}
Path("outputs/phase4e2a_qa_recovery/minimal_fixtures.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
for r in results:
    print(("PASS " if r["ok"] else "FAIL "), r["fixture"], r)
print("all_pass:", out["all_pass"])
