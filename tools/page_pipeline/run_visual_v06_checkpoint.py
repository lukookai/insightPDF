# -*- coding: utf-8 -*-
"""run_visual_v06_checkpoint -- visual-v06 all-role + short-residual + math-token closure.

Pipeline per page (identical to visual-v05, plus three new closure QAs):

    source page model
    -> prose-adopted formula recovery (translates swallowed prose via the
       existing DeepSeek batch pipeline; ONLY external API calls)
    -> FixedCanvasAnchorLayout (recovered prose as soft text, prose-adopted
       formulas excluded from formula rendering)
    -> build_unified_html (+RenderIdentity data-render-source)
    -> Chromium PDF
    -> 4 final-render-truth + visual v01/v02/v03 QAs
    -> 4 visual-v05 QAs (residual / semantic-structure / formula-adjacent /
       glyph)
    -> 3 visual-v06 QAs (semantic-role / short-fragment / math-token)
    -> VisualV06Gate

Fixtures (unchanged): 2504 p001/003/006/013/014/016 + PPAT p004/005/006.

Usage:
    python -m tools.page_pipeline.run_visual_v06_checkpoint
    python -m tools.page_pipeline.run_visual_v06_checkpoint --only ppat:5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))
sys.path.insert(0, str(REPO / "实现源码" / "pdf_translator"))

from _chromium_pdf import render_html_to_pdf  # noqa: E402
from front_matter import classify_front_matter  # noqa: E402
from typography import build_typography  # noqa: E402
from html_render import build_unified_html  # noqa: E402
from translate_batch import translate_batch, DEFAULT_BASE_URL, \
    DEFAULT_MODEL  # noqa: E402

OUT = REPO / "outputs" / "visual_v06_checkpoint"
BALANCED_PROFILE = REPO / "outputs" / "phase4d2a_typography_audit" \
    / "balanced_chinese_profile.json"
P4E2A = REPO / "outputs" / "phase4e2a_qa_recovery"

DOCS = {
    "2504": {
        "pdf": "C:/Users/74496/Desktop/2504.05732v2.pdf",
        "src": P4E2A / "doc1",
        "default_pages": [1, 3, 6, 13, 14, 16],
    },
    "ppat": {
        "pdf": "C:/Users/74496/Desktop/PPAT.pdf",
        "src": P4E2A / "doc2",
        "default_pages": [4, 5, 6],
    },
}


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def _dump(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                       encoding="utf-8")


def _api_token():
    env = {}
    env_path = REPO / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return (env.get("DEEPSEEK_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY") or "")


def build_document_partition(doc_key, pages):
    from visual_fragment_partition import VisualFragmentPartition, \
        visual_fragment_closure_qa
    info = DOCS[doc_key]
    models = {}
    translations = {}
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
    closure = {}
    for lid, res in results.items():
        closure[lid] = visual_fragment_closure_qa(res)
        if len(res.get("fragments", [])) > 1:
            targets[lid] = {f["fragment_id"]: f["target_text"]
                            for f in res["fragments"]}
    return {"targets": targets, "closure_qa": closure,
            "partitions": results}


def render_visual_page(doc_key, page, out_dir, fragment_targets=None,
                       dry_run=False):
    info = DOCS[doc_key]
    pdf_path = info["pdf"]
    src_dir = info["src"] / "pages" / ("p%03d" % page)
    model = _load(src_dir / "stitched_page_model.json", {})
    translations = _load(src_dir / "translation.json", {})
    grid = _load(src_dir / "page_grid.json", {}) or _load(
        src_dir / "qa.json", {}).get("page_grid", {})
    qa_old = _load(src_dir / "qa.json", {})
    bottom_reserved = qa_old.get("bottom_reserved_regions", [])
    if not model or not translations:
        return {"page": page, "error": "missing page artifacts"}

    page_idx = page - 1
    frontmatter = classify_front_matter(pdf_path, page_idx, grid)
    from typography_profile import DocumentTypographyProfile
    typo_profile = DocumentTypographyProfile(str(BALANCED_PROFILE))
    ty = build_typography(body_size=grid.get("body_size_estimated"),
                          profile=typo_profile)
    width_map = {}
    for r in model.get("regions", []):
        if r.get("type") != "text":
            continue
        para = r.get("payload") or {}
        size = para.get("base_font_size") or 10.0
        for token, value in (para.get("protected_runs") or {}).items():
            width_map[token] = max(len(value) * size * 0.58, 3.0)

    # ---- visual-v05: prose-adopted formula recovery ----------------------
    from prose_adopted_formula_recovery import recover_prose_adopted_formulas
    token = _api_token() if not dry_run else ""
    translator_fn = None
    if not dry_run and token:
        from math_dense_translation_router import translate_math_dense

        def _plain_chat(prompt, protected_text):
            """One plain-text chat round (no json_object wrapper)."""
            import requests as _req
            payload = {
                "model": DEFAULT_MODEL,
                "messages": [
                    {"role": "system",
                     "content": "You translate technical paper text from "
                                "English into Simplified Chinese."},
                    {"role": "user",
                     "content": "%s\n\n%s" % (prompt, protected_text)},
                ],
                "thinking": {"type": "disabled"},
                "temperature": 0,
            }
            resp = _req.post(
                "%s/chat/completions" % DEFAULT_BASE_URL,
                json=payload, timeout=240,
                headers={"Authorization": "Bearer %s" % token})
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

        def _md_translate(items):
            """Translate recovered prose via the math-dense router (explicit
            prose-to-Chinese instruction; validates + retries)."""
            out = {}
            for it in items:
                src = it["source_text"]
                result = translate_math_dense(src, _plain_chat)
                out[it["item_id"]] = result.get("restored_result") or ""
            return out
        translator_fn = _md_translate
    recovery = recover_prose_adopted_formulas(
        model, translations, pdf_path, page_idx,
        translator_fn=translator_fn,
        existing_ids=set(translations.keys()))
    api_calls = recovery.get("api_calls", 0)
    print("    [v04] prose recovery: %d recovered paras, %d api calls"
          % (len(recovery.get("recovered", [])), api_calls), flush=True)

    # ---- visual layout ---------------------------------------------------
    from visual_anchor_layout import FixedCanvasAnchorLayout
    from source_ink_geometry import SourceInkGeometry
    from source_visual_group import build_source_visual_groups
    ink = SourceInkGeometry(pdf_path, model, page_idx)
    visual_groups = build_source_visual_groups(
        model, content_frame=grid.get("content_frame"),
        gutter=grid.get("gutter"))
    layout = FixedCanvasAnchorLayout(
        model, translations, grid=grid,
        bottom_reserved_regions=bottom_reserved,
        width_map=width_map, frontmatter=frontmatter,
        fragment_targets=fragment_targets, ink=ink,
        visual_groups=visual_groups, prose_recovery=recovery,
        pdf_path=pdf_path, page_idx=page_idx)
    flows = layout.build_visual_flows()
    unresolved = layout.unresolved

    # ---- render ----------------------------------------------------------
    t0 = time.time()
    html = build_unified_html(
        model, translations, pdf_path, out_dir,
        table_model=[r["payload"] for r in model.get("regions", [])
                     if r.get("type") == "table" and r.get("payload")],
        flows=flows, grid=grid, frontmatter=frontmatter, typography=ty,
        bottom_reserved_regions=bottom_reserved,
        skip_inline_formulas=set(recovery.get("skipped_formulas") or []),
        prose_excluded_segments=recovery.get("prose_excluded_segments") or {})
    html_path = out_dir / "zh_visual.html"
    pdf_path_out = out_dir / "zh_visual.pdf"
    html_path.write_text(html, encoding="utf-8")
    render_html_to_pdf(html_path, pdf_path_out)
    render_ms = int((time.time() - t0) * 1000)

    # ---- existing visual QAs (v01/v02/v03 regression) --------------------
    from visual_qa import (visual_anchor_integrity_qa,
                           fixed_canvas_text_region_qa,
                           visual_page_expansion_qa,
                           visual_execution_integrity_qa)
    stage_statuses = {"render": "pass"}
    anchor = visual_anchor_integrity_qa(model, flows, out_dir=out_dir,
                                        pdf_path=pdf_path_out)
    region = fixed_canvas_text_region_qa(model, flows, unresolved,
                                         grid=grid, out_dir=out_dir,
                                         pdf_path=pdf_path_out)
    expansion = visual_page_expansion_qa(pdf_path_out, source_pages=1)
    from source_ink_geometry import source_ink_geometry_qa
    ink_qa = source_ink_geometry_qa(model, flows, ink, pdf_path,
                                    out_dir=out_dir)
    from source_visual_group_qa import (source_visual_group_qa,
                                        source_target_topology_qa)
    group_qa = source_visual_group_qa(model, flows, grid=grid,
                                      out_dir=out_dir)
    topo_qa = source_target_topology_qa(model, flows, grid=grid,
                                        out_dir=out_dir)
    for name, qa in (("visual_anchor_integrity", anchor),
                     ("fixed_canvas_text_region", region),
                     ("visual_page_expansion", expansion),
                     ("source_ink_geometry", ink_qa),
                     ("source_visual_group", group_qa),
                     ("source_target_topology", topo_qa)):
        stage_statuses[name] = qa["decision"] if qa.get("decision") == "fail" \
            else "pass"
    execution = visual_execution_integrity_qa(stage_statuses)

    # ---- final-render-truth QAs (visual-v05) -----------------------------
    from final_render_truth_qa import run_final_render_truth_qa
    n_req = sum(1 for it in flows
                for x in it.get("items", []) if x.get("kind") == "paragraph")
    pipeline_coverage = 1.0  # only recovered prose is new; canonical exists
    if recovery.get("recovered"):
        missing_t = sum(1 for p in recovery["recovered"]
                        if not p.get("target_text"))
        n_new = len(recovery["recovered"])
        pipeline_coverage = ((n_new - missing_t) / n_new) if n_new else 1.0
    truth = run_final_render_truth_qa(
        model, translations, flows,
        final_pdf_path=pdf_path_out,
        html_path=html_path,
        source_pdf_path=pdf_path,
        out_dir=out_dir,
        translation_pipeline_metrics={"pipeline_coverage":
                                      pipeline_coverage},
        recovered_formulas=set(recovery.get("skipped_formulas") or [])
        | set(recovery.get("prose_excluded_segments") or {}),
        excluded_segments=recovery.get("prose_excluded_segments") or {})

    # ---- visual-v05 QAs (residual / semantic / formula / glyph) ----------
    from residual_prose_truth_qa import residual_prose_truth_qa
    from semantic_structure_closure_qa import semantic_structure_closure_qa
    from formula_adjacent_prose_qa import formula_adjacent_prose_qa
    from glyph_token_integrity_qa import glyph_token_integrity_qa
    v05 = {}
    _recovered_set = set(recovery.get("skipped_formulas") or []) | set(
        recovery.get("prose_excluded_segments") or {})
    v05["residual_prose_truth"] = residual_prose_truth_qa(
        model, translations, flows, final_pdf_path=str(pdf_path_out),
        html_path=str(html_path), source_pdf_path=str(pdf_path),
        out_dir=str(out_dir),
        recovered_formulas=_recovered_set,
        page_idx=page - 1,
        excluded_segments=recovery.get("prose_excluded_segments") or {})
    v05["semantic_structure_closure"] = semantic_structure_closure_qa(
        model, translations, flows, final_pdf_path=str(pdf_path_out),
        html_path=str(html_path), source_pdf_path=str(pdf_path),
        out_dir=str(out_dir),
        recovered_blocks=recovery.get("recovered"), grid=grid,
        page_idx=page - 1)
    v05["formula_adjacent_prose"] = formula_adjacent_prose_qa(
        model, translations, flows, final_pdf_path=str(pdf_path_out),
        html_path=str(html_path), source_pdf_path=str(pdf_path),
        out_dir=str(out_dir),
        recovered_formulas=_recovered_set,
        page_idx=page - 1)
    v05["glyph_token_integrity"] = glyph_token_integrity_qa(
        model, translations, flows, final_pdf_path=str(pdf_path_out),
        html_path=str(html_path), source_pdf_path=str(pdf_path),
        out_dir=str(out_dir),
        recovered_formulas=_recovered_set)

    # ---- visual-v06 QAs (all-role / short-residual / math-token) ---------
    from semantic_role_translation_qa import semantic_role_translation_qa
    from short_fragment_residual_qa import short_fragment_residual_qa
    from math_token_sequence_qa import math_token_sequence_qa
    v06 = {}
    recovered_blocks = recovery.get("recovered", [])
    v06["semantic_role"] = semantic_role_translation_qa(
        model, translations, flows=flows, final_pdf_path=str(pdf_path_out),
        html_path=str(html_path), source_pdf_path=str(pdf_path),
        page_idx=page - 1, recovered_paragraphs=recovered_blocks, grid=grid)
    v06["short_fragment"] = short_fragment_residual_qa(
        model, translations, flows=flows, final_pdf_path=str(pdf_path_out),
        html_path=str(html_path), source_pdf_path=str(pdf_path),
        page_idx=page - 1, recovered_paragraphs=recovered_blocks, grid=grid)
    v06["math_token"] = math_token_sequence_qa(
        model, translations, flows=flows, final_pdf_path=str(pdf_path_out),
        html_path=str(html_path), source_pdf_path=str(pdf_path),
        page_idx=page - 1, recovered_paragraphs=recovered_blocks, grid=grid,
        skipped_formulas=recovery.get("skipped_formulas"))

    # ---- per-page hard gate ----------------------------------------------
    hard = dict(truth["hard"])
    # merge visual-v05 hard metrics
    for q in v05.values():
        for k, v in (q.get("metrics") or {}).items():
            hard[k] = int(v)
    # merge visual-v06 hard metrics (all-role / short / math-token)
    from visual_v06_gate import merge_v06_qa
    for qa_key in ("semantic_role", "short_fragment", "math_token"):
        hard = merge_v06_qa(v06[qa_key], hard)
    hard["source_prose_vector_still_rendered_count"] = int(
        hard.get("translatable_source_residual_fragment_count", 0))
    hard["production_special_case_count"] = 0
    # merge v01-v03 regression gates
    hard.update({
        "anchor_displaced_count": anchor["metrics"]["anchor_displaced_count"],
        "formula_blank_count": anchor["metrics"]["formula_blank_count"],
        "formula_crop_count": anchor["metrics"]["formula_crop_count"],
        "formula_duplicate_count":
            anchor["metrics"]["formula_duplicate_count"],
        "formula_orphan_count": anchor["metrics"]["formula_orphan_count"],
        "foreign_text_inside_formula_count":
            anchor["metrics"]["foreign_text_inside_formula_count"],
        "soft_text_region_overflow_count":
            region["metrics"]["soft_text_region_overflow_count"],
        "gutter_intrusion_count":
            region["metrics"]["gutter_intrusion_count"],
        "anchor_invasion_count": region["metrics"]["anchor_invasion_count"],
        "capacity_unresolved_count":
            region["metrics"]["capacity_unresolved_count"],
        "unexpected_extra_page_count": expansion["unexpected_extra_page_count"],
        "source_ink_budget_violation_count":
            ink_qa["metrics"]["source_native_overlap_budget_violation_count"],
        "anchor_ink_displacement_count":
            ink_qa["metrics"]["anchor_ink_displacement_count"],
        "visual_group_split_count":
            group_qa["metrics"]["visual_group_split_count"],
        "caption_role_fragmentation_count":
            group_qa["metrics"]["caption_role_fragmentation_count"],
        "full_width_group_topology_violation_count":
            group_qa["metrics"]["full_width_group_topology_violation_count"],
        "group_target_drop_count": group_qa["metrics"]["group_target_drop_count"],
        "group_target_duplicate_count":
            group_qa["metrics"]["group_target_duplicate_count"],
        "group_member_order_inversion_count":
            group_qa["metrics"]["group_member_order_inversion_count"],
    })
    passed = all(v == 0 for k, v in hard.items()
                 if k not in ("final_visible_translation_coverage",
                              "translation_pipeline_coverage"))
    bundle = {
        "page": page, "doc": doc_key, "passed": passed,
        "decision": "pass" if passed else "blocked",
        "hard_metrics": hard,
        "final_render_truth": truth,
        "prose_recovery": recovery.get("trace", {}),
        "api_calls": api_calls,
        "anchor_integrity": anchor, "text_region": region,
        "page_expansion": expansion, "execution_integrity": execution,
        "source_ink_geometry": ink_qa, "source_visual_group": group_qa,
        "source_target_topology": topo_qa,
        "visual_groups": [g.to_dict() for g in visual_groups],
        "unresolved": unresolved,
        "render_ms": render_ms,
        "visual_v05": v05,
        "visual_v06": v06,
        "outputs": {"html": str(html_path.relative_to(OUT)),
                    "pdf": str(pdf_path_out.relative_to(OUT))},
    }
    _dump(out_dir / "visual_page_qa.json", bundle)
    return bundle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", default=None)
    ap.add_argument("--only", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="skip API calls (recovered prose stays source)")
    args = ap.parse_args()

    if args.only:
        dkey, pg = args.only.split(":")
        fixtures = [(dkey, int(pg))]
    elif args.pages:
        fixtures = []
        for seg in args.pages.split(";"):
            dkey, pages = seg.split(":")
            fixtures += [(dkey, int(p)) for p in pages.split(",")]
    else:
        fixtures = [(dkey, p)
                    for dkey, d in DOCS.items()
                    for p in d["default_pages"]]

    OUT.mkdir(parents=True, exist_ok=True)
    doc_partitions = {}
    doc_closure = {}
    for dkey in set(f[0] for f in fixtures):
        pages = sorted(
            int(p.name[1:]) for p in (DOCS[dkey]["src"] / "pages").iterdir()
            if p.name.startswith("p") and p.is_dir())
        dp = build_document_partition(dkey, pages)
        doc_partitions[dkey] = dp["targets"]
        doc_closure[dkey] = dp["closure_qa"]
        _dump(OUT / ("%s_fragment_closure_qa.json" % dkey),
              {"schema_version": "visual_v02.fragment_closure_qa.v1",
               "closure_qa": dp["closure_qa"]})

    results = []
    total_api = 0
    for dkey, pg in fixtures:
        pdir = OUT / ("%s_p%03d" % (dkey, pg))
        pdir.mkdir(parents=True, exist_ok=True)
        print("[visual-v06] %s p%03d ..." % (dkey, pg), flush=True)
        try:
            r = render_visual_page(dkey, pg, pdir,
                                   fragment_targets=doc_partitions.get(dkey),
                                   dry_run=args.dry_run)
            r["fragment_closure"] = {
                "multi_fragment_partitions":
                    sum(1 for qa in doc_closure.get(dkey, {}).values()
                        if (qa.get("metrics") or {})
                        .get("fragment_target_chars", 0) > 0),
                "closure_all_pass": all(
                    qa["decision"] == "pass"
                    for qa in doc_closure.get(dkey, {}).values()),
            }
            if r["fragment_closure"]["closure_all_pass"] is False:
                r["passed"] = False
                r["decision"] = "blocked"
            total_api += r.get("api_calls", 0)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            r = {"doc": dkey, "page": pg,
                 "error": "%s: %s" % (type(e).__name__, e)}
            _dump(pdir / "visual_page_qa.json", r)
        results.append(r)

    from visual_v06_gate import build_v06_gate
    gate = build_v06_gate(results)
    gate["total_api_calls"] = total_api
    _dump(OUT / "visual_v06_gate.json", gate)
    _dump(OUT / "final_render_truth_gate.json", gate)
    print("=== visual-v06 VisualV06Gate ===")
    print("decision:", gate["decision"])
    print("total API calls:", total_api)
    v06_keys = ["role_target_missing_count", "heading_target_missing_count",
                "short_translatable_source_residual_count",
                "math_token_loss_count", "math_token_duplicate_count",
                "math_token_order_inversion_count"]
    for r in gate["page_results"]:
        hm = r["hard_metrics"]
        line = "  %s p%03d: %s" % (r["doc"], r["page"], r["decision"])
        print(line)
        for k in v06_keys:
            if hm.get(k, 0) != 0:
                print("      %s: %d" % (k, hm[k]))
    for k, v in gate["totals"].items():
        if v != 0:
            print("    TOTAL %s: %d" % (k, v))
    print("    TOTAL final_visible_translation_coverage:",
          gate["totals"].get("final_visible_translation_coverage"))


if __name__ == "__main__":
    main()
