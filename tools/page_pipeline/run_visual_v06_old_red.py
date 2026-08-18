# -*- coding: utf-8 -*-
"""run_visual_v06_old_red -- QA-FIRST on the frozen visual-v05 artifacts.

Runs the new v06 QAs against the v05 final PDFs and asserts PPAT p005/p006
are RED (heading_target_missing / short residual) while 2504 stays 6/6 PASS.

Zero API calls: recovery runs in dry-run (no translation), we only need the
recovered paragraph BBOXES to know which source lines v05 already covered.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path("E:/Code/fanyi")
P4 = REPO / "outputs/phase4e2a_qa_recovery"
V05 = REPO / "outputs/visual_v05_checkpoint"
OUT = REPO / "outputs/visual_v06_checkpoint"
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))
sys.path.insert(0, str(REPO / "实现源码" / "pdf_translator"))

DOCS = {
    "2504": {"src": P4 / "doc1", "default_pages": [1, 3, 6, 13, 14, 16]},
    "ppat": {"src": P4 / "doc2", "default_pages": [4, 5, 6]},
}
PDF = {
    "2504": "C:/Users/74496/Desktop/2504.05732v2.pdf",
    "ppat": "C:/Users/74496/Desktop/PPAT.pdf",
}


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def _dump(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                       encoding="utf-8")


def build_document_partition(doc_key, pages):
    """Zero-API replica of v05's fragment partition: produces the page-local
    target slice for every multi-fragment logical paragraph so the visual
    flows carry the EXACT fragment v05 rendered (cross-page continuation
    paragraphs match their page-local slice, not the whole-doc target)."""
    from visual_fragment_partition import (VisualFragmentPartition,
                                           visual_fragment_closure_qa)
    info = DOCS[doc_key]
    models, translations = {}, {}
    for pg in pages:
        src_dir = info["src"] / "pages" / ("p%03d" % pg)
        m = _load(src_dir / "stitched_page_model.json", {})
        if m:
            models[pg] = m
        t = _load(src_dir / "translation.json", {})
        if t:
            translations.update(t)
    part = VisualFragmentPartition(models, translations)
    results = part.partition_all()
    targets = {}
    for lid, res in results.items():
        visual_fragment_closure_qa(res)
        if len(res.get("fragments", [])) > 1:
            targets[lid] = {f["fragment_id"]: f["target_text"]
                            for f in res["fragments"]}
    return targets


# fragment_targets cache per doc (built once over all doc pages)
_FRAG_TARGETS = {}


def _frag_targets(doc_key):
    if doc_key not in _FRAG_TARGETS:
        pages = sorted(
            int(p.name[1:]) for p in (DOCS[doc_key]["src"] / "pages").iterdir()
            if p.name.startswith("p") and p.is_dir())
        _FRAG_TARGETS[doc_key] = build_document_partition(doc_key, pages)
    return _FRAG_TARGETS[doc_key]


def run_page(doc, pg):
    from prose_adopted_formula_recovery import recover_prose_adopted_formulas
    from semantic_role_translation_qa import semantic_role_translation_qa
    from short_fragment_residual_qa import short_fragment_residual_qa
    from math_token_sequence_qa import math_token_sequence_qa
    from visual_anchor_layout import FixedCanvasAnchorLayout

    src = DOCS[doc]["src"]
    sdir = src / "pages" / ("p%03d" % pg)
    model = _load(sdir / "stitched_page_model.json", {})
    trans = _load(sdir / "translation.json", {})
    grid = _load(sdir / "page_grid.json", {}) or (_load(sdir / "qa.json", {})
                                                or {}).get("page_grid", {})
    final_pdf = V05 / ("%s_p%03d" % (doc, pg)) / "zh_visual.pdf"
    final_html = V05 / ("%s_p%03d" % (doc, pg)) / "zh_visual.html"
    pdf_path = str(PDF[doc])
    page_idx = pg - 1
    rec = recover_prose_adopted_formulas(model, trans, pdf_path, page_idx,
                                         translator_fn=None,
                                         existing_ids=set(trans.keys()))
    recovered = rec["recovered"]
    # build real visual flows (zero API -- pure layout) with the SAME
    # fragment_targets v05 used, so each text-region paragraph's
    # render_text is the EXACT page-local slice v05 rendered.  Cross-page
    # continuation paragraphs then match their page-local fragment, not the
    # whole-doc target.
    frag_targets = _frag_targets(doc)
    layout = FixedCanvasAnchorLayout(
        model, trans, grid=grid, prose_recovery=rec,
        fragment_targets=frag_targets,
        pdf_path=pdf_path, page_idx=page_idx)
    flows = layout.build_visual_flows()
    sr = semantic_role_translation_qa(
        model, trans, flows=flows, final_pdf_path=str(final_pdf),
        html_path=str(final_html), source_pdf_path=pdf_path,
        page_idx=page_idx, recovered_paragraphs=recovered, grid=grid)
    sf = short_fragment_residual_qa(
        model, trans, flows=flows, final_pdf_path=str(final_pdf),
        html_path=str(final_html), source_pdf_path=pdf_path,
        page_idx=page_idx, recovered_paragraphs=recovered, grid=grid)
    mt = math_token_sequence_qa(
        model, trans, flows=flows, final_pdf_path=str(final_pdf),
        html_path=str(final_html), source_pdf_path=pdf_path,
        page_idx=page_idx, recovered_paragraphs=recovered, grid=grid)
    return {"doc": doc, "page": pg, "semantic_role": sr,
            "short_fragment": sf, "math_token": mt,
            "recovered_count": len(recovered)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "old_red_evidence").mkdir(parents=True, exist_ok=True)
    results = {}
    for doc, info in DOCS.items():
        for pg in info["default_pages"]:
            r = run_page(doc, pg)
            results.setdefault(doc, {})[pg] = r
            m = r["semantic_role"]["metrics"]
            s = r["short_fragment"]["metrics"]
            mt = r["math_token"]["metrics"]
            print("%s p%03d: role_missing=%d heading_missing=%d "
                  "role_wrong_rs=%d | short=%d head=%d tail=%d "
                  "standalone=%d heading_short=%d fctx=%d | math:"
                  " loss=%d dup=%d inv=%d sup=%d sub=%d base=%d grp=%d"
                  % (doc, pg,
                     m["role_target_missing_count"],
                     m["heading_target_missing_count"],
                     m["role_wrong_render_source_count"],
                     s["short_translatable_source_residual_count"],
                     s["paragraph_head_residual_count"],
                     s["paragraph_tail_residual_count"],
                     s["standalone_short_residual_count"],
                     s["heading_short_residual_count"],
                     s["formula_context_short_residual_count"],
                     mt["math_token_loss_count"], mt["math_token_duplicate_count"],
                     mt["math_token_order_inversion_count"],
                     mt["math_superscript_loss_count"],
                     mt["math_subscript_loss_count"],
                     mt["math_base_symbol_loss_count"],
                     mt["math_group_structure_mismatch_count"]))
    for doc in ("2504", "ppat"):
        for pg in DOCS[doc]["default_pages"]:
            r = results[doc][pg]
            ev = {
                "doc": doc, "page": pg,
                "semantic_role_metrics": r["semantic_role"]["metrics"],
                "short_fragment_metrics": r["short_fragment"]["metrics"],
                "math_token_metrics": r["math_token"]["metrics"],
                "recovered_count": r["recovered_count"],
                "role_decision": r["semantic_role"]["decision"],
                "short_decision": r["short_fragment"]["decision"],
                "math_decision": r["math_token"]["decision"],
            }
            _dump(OUT / "old_red_evidence" /
                  ("%s_p%03d_v05_red.json" % (doc, pg)), ev)
    p5 = results["ppat"][5]
    p6 = results["ppat"][6]

    def _math_fail(page_res):
        return any(v > 0 for v in page_res["math_token"]["metrics"].values())

    # PPAT p005 blind spot: untranslated "4. Experiment" heading (short
    # heading dropped by the >=2-word prose-line filter) + short residuals +
    # math token sequence loss (v05 dropped standalone formulas B2/B4/... that
    # never reached the final render).
    red5 = (p5["semantic_role"]["metrics"]["heading_target_missing_count"] > 0
            or p5["short_fragment"]["metrics"]
            ["short_translatable_source_residual_count"] > 0
            or _math_fail(p5))
    # PPAT p006 blind spot: formula-swallowed body prose left as source SVG
    # vector ink (e.g. B28 "Results on LSOTB-TIR120. As shown in Table 1") +
    # math token loss (19 standalone formulas dropped by v05).
    # NOTE: p006's *headings* (4.1/4.2/4.3) ARE translated in v05's frozen
    # artifacts, so the residual here is prose, not heading -- the gate
    # therefore accepts any new-QA residual on p006 as the caught blind spot.
    red6 = (p6["semantic_role"]["metrics"]["heading_target_missing_count"] > 0
            or p6["semantic_role"]["metrics"]["role_target_missing_count"] > 0
            or p6["short_fragment"]["metrics"]
            ["short_translatable_source_residual_count"] > 0
            or _math_fail(p6))
    p2504 = all(results["2504"][pg]["semantic_role"]["decision"] == "pass"
                and results["2504"][pg]["short_fragment"]["decision"] == "pass"
                for pg in DOCS["2504"]["default_pages"])
    # Math findings on 2504 are reported separately: the frozen v05 is expected
    # clean on the two ORIGINAL QAs (role + short); the new math QA may surface
    # additional blind spots (e.g. a dropped standalone formula on p016) that
    # the production fix resolves.  So 2504_all_pass gates on role+short only.
    p2504_math = all(results["2504"][pg]["math_token"]["decision"] == "pass"
                     for pg in DOCS["2504"]["default_pages"])
    summary = {
        "ppat_p005_red": red5,
        "ppat_p006_red": red6,
        "ppat_p005_heading_missing":
            p5["semantic_role"]["metrics"]["heading_target_missing_count"],
        "ppat_p005_short":
            p5["short_fragment"]["metrics"]
            ["short_translatable_source_residual_count"],
        "ppat_p005_math_loss":
            p5["math_token"]["metrics"]["math_token_loss_count"],
        "ppat_p006_heading_missing":
            p6["semantic_role"]["metrics"]["heading_target_missing_count"],
        "ppat_p006_math_loss":
            p6["math_token"]["metrics"]["math_token_loss_count"],
        "2504_all_pass": p2504,
        "2504_math_pass": p2504_math,
    }
    _dump(OUT / "old_red_evidence" / "v05_red_summary.json", summary)
    print("=== OLD RED SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    ok = red5 and red6 and p2504
    print("RED GATE:", "PASS (p005/p006 RED, 2504 PASS)" if ok
          else "FAIL (check QA)")


if __name__ == "__main__":
    main()
