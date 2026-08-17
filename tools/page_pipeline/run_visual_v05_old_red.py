# -*- coding: utf-8 -*-
"""run_visual_v05_old_red -- QA-FIRST old-red baseline on FROZEN v04 outputs.

Runs the four new visual-v05 QAs against the frozen visual-v04 final
artifacts (zh_visual.html / zh_visual.pdf) WITHOUT any production change.

Expectation:
    PPAT p004 / p005 / p006  -> RED (residual prose / semantic structure /
                                formula-adjacent / glyph)
    2504 six pages           -> 6/6 PASS (zero false positives)

Outputs: outputs/visual_v05_checkpoint/old_red_evidence/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))

from residual_prose_truth_qa import residual_prose_truth_qa  # noqa: E402
from semantic_structure_closure_qa import semantic_structure_closure_qa  # noqa: E402
from formula_adjacent_prose_qa import formula_adjacent_prose_qa  # noqa: E402
from glyph_token_integrity_qa import glyph_token_integrity_qa  # noqa: E402
from prose_adopted_formula_recovery import recover_prose_adopted_formulas  # noqa: E402

OUT = REPO / "outputs" / "visual_v05_checkpoint"
RED = OUT / "old_red_evidence"
# frozen visual-v04 final artifacts (the QA-FIRST baseline)
V04_OUT = REPO / "outputs" / "visual_v04_checkpoint"

DOCS = {
    "2504": {"src": REPO / "outputs" / "phase4e2a_qa_recovery" / "doc1",
             "pdf": REPO / "C:/Users/74496/Desktop/2504.05732v2.pdf",
             "pages": [1, 3, 6, 13, 14, 16]},
    "ppat": {"src": REPO / "outputs" / "phase4e2a_qa_recovery" / "doc2",
             "pdf": REPO / "C:/Users/74496/Desktop/PPAT.pdf",
             "pages": [4, 5, 6]},
}


def _load(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default if default is not None else {}


def build_flows(doc_key, page, recovery):
    """Rebuild visual flows (dry-run: no API) to feed QAs."""
    from visual_anchor_layout import FixedCanvasAnchorLayout
    from source_ink_geometry import SourceInkGeometry
    from source_visual_group import build_source_visual_groups
    info = DOCS[doc_key]
    src_dir = info["src"] / "pages" / ("p%03d" % page)
    model = _load(src_dir / "stitched_page_model.json")
    translations = _load(src_dir / "translation.json")
    grid = _load(src_dir / "page_grid.json") or _load(
        src_dir / "qa.json", {}).get("page_grid", {})
    pdf = info["pdf"]
    ink = SourceInkGeometry(str(pdf), model, page - 1)
    vg = build_source_visual_groups(
        model, content_frame=grid.get("content_frame"),
        gutter=grid.get("gutter"))
    width_map = {}
    for r in model.get("regions", []):
        if r.get("type") != "text":
            continue
        para = r.get("payload") or {}
        size = para.get("base_font_size") or 10.0
        for token, value in (para.get("protected_runs") or {}).items():
            width_map[token] = max(len(value) * size * 0.58, 3.0)
    layout = FixedCanvasAnchorLayout(
        model, translations, grid=grid, width_map=width_map,
        ink=ink, visual_groups=vg, prose_recovery=recovery,
        pdf_path=str(pdf), page_idx=page - 1)
    return layout.build_visual_flows()


def run_page(doc_key, page):
    info = DOCS[doc_key]
    src_dir = info["src"] / "pages" / ("p%03d" % page)
    v04_dir = V04_OUT / ("%s_p%03d" % (doc_key, page))
    model = _load(src_dir / "stitched_page_model.json")
    translations = _load(src_dir / "translation.json")
    grid = _load(src_dir / "page_grid.json") or _load(
        src_dir / "qa.json", {}).get("page_grid", {})
    pdf = info["pdf"]
    html = v04_dir / "zh_visual.html"
    pdf_out = v04_dir / "zh_visual.pdf"

    recovery = recover_prose_adopted_formulas(
        model, translations, str(pdf), page - 1,
        translator_fn=None,
        existing_ids=set(translations.keys()))
    flows = build_flows(doc_key, page, recovery)

    q1 = residual_prose_truth_qa(
        model, translations, flows, final_pdf_path=str(pdf_out),
        html_path=str(html), source_pdf_path=str(pdf),
        recovered_formulas=set(recovery.get("skipped_formulas") or []),
        page_idx=page - 1)
    q2 = semantic_structure_closure_qa(
        model, translations, flows, final_pdf_path=str(pdf_out),
        html_path=str(html), source_pdf_path=str(pdf),
        recovered_blocks=recovery.get("recovered"),
        grid=grid, page_idx=page - 1)
    q3 = formula_adjacent_prose_qa(
        model, translations, flows, final_pdf_path=str(pdf_out),
        html_path=str(html), source_pdf_path=str(pdf),
        recovered_formulas=set(recovery.get("skipped_formulas") or []),
        page_idx=page - 1)
    q4 = glyph_token_integrity_qa(
        model, translations, flows, final_pdf_path=str(pdf_out),
        html_path=str(html), source_pdf_path=str(pdf))

    qas = {"residual_prose_truth": q1, "semantic_structure_closure": q2,
           "formula_adjacent_prose": q3, "glyph_token_integrity": q4}
    failed = [name for name, q in qas.items() if q["decision"] == "fail"]
    hard = {}
    for q in qas.values():
        for k, v in (q.get("metrics") or {}).items():
            hard[k] = int(v)
    passed = all(v == 0 for v in hard.values())
    return {
        "doc": doc_key, "page": page, "passed": passed,
        "decision": "pass" if passed else "blocked",
        "hard_metrics": hard,
        "failed_qa": failed,
        "qas": {k: {"metrics": v.get("metrics"),
                    "details": (v.get("residual_details")
                                or v.get("structure_details")
                                or v.get("adjacent_details")
                                or v.get("glyph_details") or [])}
                for k, v in qas.items()},
        "recovery": recovery.get("trace", {}),
        "skipped_formulas": recovery.get("skipped_formulas"),
    }


def main():
    RED.mkdir(parents=True, exist_ok=True)
    results = []
    for dkey, doc in DOCS.items():
        for pg in doc["pages"]:
            print("== %s p%03d" % (dkey, pg), flush=True)
            r = run_page(dkey, pg)
            results.append(r)
            out = RED / ("%s_p%03d_v04_red.json" % (dkey, pg))
            out.write_text(json.dumps(r, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            print("   decision=%s failed_qa=%s" % (r["decision"],
                                                   r["failed_qa"]),
                  flush=True)
    summary = {
        "schema_version": "visual_v05.old_red.v1",
        "expectation": "ppat p004/p005/p006 RED; 2504 6/6 PASS",
        "pages": [{"doc": r["doc"], "page": r["page"],
                   "decision": r["decision"], "failed_qa": r["failed_qa"],
                   "hard_metrics": r["hard_metrics"]} for r in results],
        "all_ppat_red": all(r["decision"] == "blocked"
                            for r in results if r["doc"] == "ppat"),
        "all_2504_pass": all(r["decision"] == "pass"
                             for r in results if r["doc"] == "2504"),
    }
    (RED / "v04_red_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== SUMMARY ===")
    for r in results:
        print("  %s p%03d: %s %s" % (r["doc"], r["page"], r["decision"],
                                     r["failed_qa"]))
    print("ppat all RED:", summary["all_ppat_red"],
          "| 2504 all PASS:", summary["all_2504_pass"])


if __name__ == "__main__":
    main()
