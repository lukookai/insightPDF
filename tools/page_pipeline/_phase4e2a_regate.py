# -*- coding: utf-8 -*-
"""Phase 4E.2A: re-compute the delivery gate + manifest + integrity gate from
already-produced qa.json files (no pipeline re-run).  Used to correct the gate
after a fail-closed logic fix without re-rendering."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "实现源码" / "pdf_translator"))

import qa_execution as qx
from delivery_gate import build_delivery_gate


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


# stage -> record key holding the stage's RESULT dict (to re-derive status
# with the corrected STAGE_PASS_FN / STAGE_PASS_KEYS).
STAGE_RESULT_KEY = {
    "formula_exclusivity_qa": "formula_exclusivity_qa",
    "residual_language_qa": "residual_language_qa",
    "footnote_collision_qa": "footnote_collision_qa",
    "residual_prose_qa": "residual_prose_qa",
    "render_closure_qa": "render_closure_qa",
    "rendered_collision_qa": "rendered_collision_qa",
    "heading_duplicate_qa": "heading_duplicate_qa",
    "formula_crop_qa": "formula_crop_qa",
    "formula_render_trace": "formula_render_trace",
    "formula_render_completeness_qa": "formula_render_completeness_qa",
}


def _rederive_status(stage, result):
    if result is None:
        return "error"
    fn = qx.STAGE_PASS_FN.get(stage)
    if fn is not None:
        return "pass" if fn(result) else "fail"
    key = qx.STAGE_PASS_KEYS.get(stage)
    if key is None:
        return "pass"
    return "pass" if result.get(key, True) else "fail"


def _rederive_execution(record):
    """Recompute qa_execution statuses from the QA RESULT dicts using the
    corrected pass rules (fixes the pre-fix footnote mislabeling)."""
    exec_map = dict(record.get("qa_execution") or {})
    for stage, rkey in STAGE_RESULT_KEY.items():
        result = record.get(rkey)
        # keep error/not_run statuses; re-derive pass/fail only for stages that
        # actually produced a result
        prev = exec_map.get(stage, {})
        if prev.get("status") in ("error", "not_run", "missing"):
            continue
        exec_map[stage] = {"stage": stage,
                           "status": _rederive_status(stage, result),
                           "result": result}
    return exec_map


def regate(doc_dir):
    doc_dir = Path(doc_dir)
    preflight = _load(doc_dir / "preflight.json", {})
    page_count = preflight.get("page_count", 0)
    report = _load(doc_dir / "document_report.json", {})
    translation_qa = report.get("translation_qa") or {}

    page_results = {}
    structural_qas = {}
    for pg in range(1, page_count + 1):
        q = _load(doc_dir / "pages" / ("p%03d" % pg) / "qa.json")
        if q:
            q["qa_execution"] = _rederive_execution(q)
            page_results[pg] = q
        s = _load(doc_dir / "pages" / ("p%03d" % pg)
                  / "paragraph_structural_qa.json")
        if s:
            structural_qas[pg] = s

    renderer_parity = {
        "renderer_b_available": bool(all(
            (r.get("physical_qa") or {}).get("renderer_b_available", False)
            for r in page_results.values())),
        "renderer_specific_failure_count": sum(
            (r.get("physical_qa") or {}).get("renderer_specific_failure_count", 0)
            for r in page_results.values()),
        "renderer_parity_diff_ratio": None,
        "changed_page_count": 0,
    }
    diff_ratios = [(r.get("physical_qa") or {}).get("renderer_parity_diff_ratio")
                   for r in page_results.values()]
    diff_ratios = [d for d in diff_ratios if d is not None]
    if diff_ratios:
        renderer_parity["renderer_parity_diff_ratio"] = round(
            sum(diff_ratios) / len(diff_ratios), 4)

    gate = build_delivery_gate(
        preflight=preflight, page_results=page_results,
        structural_qas=structural_qas, translation_qa=translation_qa,
        renderer_parity=renderer_parity, expected_page_count=page_count)

    manifest = gate.get("qa_execution_manifest") or \
        qx.document_manifest(page_results, page_count)
    integrity = qx.integrity_gate(manifest)

    _dump(doc_dir / "delivery_gate.json", gate)
    _dump(doc_dir / "qa_execution_manifest.json", manifest)
    _dump(doc_dir / "qa_integrity_gate.json", integrity)
    return gate, manifest, integrity


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


if __name__ == "__main__":
    gate, manifest, integrity = regate(sys.argv[1])
    print("decision:", gate["decision"])
    print("blocked_layers:", gate.get("blocked_layers"))
    print("qa_execution_complete:", gate.get("qa_execution_complete"))
    print("integrity:", integrity["decision"],
          integrity["hard"])
    print("counts:", manifest["counts"])
