"""run_visual_checkpoint -- visual-v01 fixed-canvas checkpoint runner.

Renders the checkpoint fixture pages through the visual route
(FixedCanvasAnchorLayout + build_unified_html + Chromium) and runs the four
visual QAs, then writes per-page evidence + a fail-closed checkpoint gate.

Fixtures are chosen by (document, page) from the task spec; every page is
loaded from the frozen Phase 4E.2A artifacts (page model / translation /
grid), never re-translated.

Usage:
    python -m tools.page_pipeline.run_visual_checkpoint [--pages "2504:1,3,6,13,14,16;ppat:4,5,6"]
"""

from __future__ import annotations

import argparse
import json
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

OUT = REPO / "outputs" / "visual_v03_checkpoint"
BALANCED_PROFILE = REPO / "outputs" / "phase4d2a_typography_audit" / "balanced_chinese_profile.json"
P4E2A = REPO / "outputs" / "phase4e2a_qa_recovery"

DOCS = {
    "2504": {
        "pdf": REPO.parent / "Users" / "74496" / "Desktop" / "2504.05732v2.pdf",
        "src": P4E2A / "doc1",
        "default_pages": [1, 3, 6, 13, 14, 16],
    },
    "ppat": {
        "pdf": REPO.parent / "Users" / "74496" / "Desktop" / "PPAT.pdf",
        "src": P4E2A / "doc2",
        "default_pages": [4, 5, 6],
    },
}


def _table_models(page_model):
    return [r["payload"] for r in page_model.get("regions", [])
            if r.get("type") == "table" and r.get("payload")]


def _formula_width_map(page_model):
    out = {}
    for r in page_model.get("regions", []):
        if r.get("type") != "text":
            continue
        para = r.get("payload") or {}
        size = para.get("base_font_size") or 10.0
        for token, value in (para.get("protected_runs") or {}).items():
            out[token] = max(len(value) * size * 0.58, 3.0)
    return out


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def _dump(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                       encoding="utf-8")


def build_document_partition(doc_key, pages):
    """Document-level VisualFragmentPartition for the whole source PDF.

    Collects every page model + merged canonical translations, then
    partitions each multi-fragment logical paragraph.  Returns
    {logical_paragraph_id: {fragment_id: target_text}} + closure QA.
    """
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
    from visual_fragment_partition import VisualFragmentPartition, \
        visual_fragment_closure_qa
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


def render_visual_page(doc_key, page, out_dir, fragment_targets=None):
    """Render one page via the visual route; returns the page QA bundle.

    ``fragment_targets``: optional {logical_id: {fragment_id: text}} from
    the document-level VisualFragmentPartition (visual-v02)."""
    info = DOCS[doc_key]
    pdf_path = _resolve_desktop_pdf(doc_key)
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
    frontmatter = classify_front_matter(str(pdf_path), page_idx, grid)
    from typography_profile import DocumentTypographyProfile
    typo_profile = DocumentTypographyProfile(str(BALANCED_PROFILE))
    ty = build_typography(body_size=grid.get("body_size_estimated"),
                          profile=typo_profile)
    width_map = _formula_width_map(model)

    # ---- visual layout (fixed canvas) -----------------------------------
    from visual_anchor_layout import FixedCanvasAnchorLayout
    from source_ink_geometry import SourceInkGeometry
    from source_visual_group import build_source_visual_groups
    ink = SourceInkGeometry(pdf_path, model, page_idx)
    visual_groups = build_source_visual_groups(
        model, content_frame=grid.get("content_frame"),
        gutter=grid.get("gutter"))
    layout = FixedCanvasAnchorLayout(model, translations, grid=grid,
                                     bottom_reserved_regions=bottom_reserved,
                                     width_map=width_map,
                                     frontmatter=frontmatter,
                                     fragment_targets=fragment_targets,
                                     ink=ink, visual_groups=visual_groups)
    flows = layout.build_visual_flows()
    unresolved = layout.unresolved

    # ---- render (reuse the existing build_unified_html) -----------------
    t0 = time.time()
    html = build_unified_html(
        model, translations, pdf_path, out_dir,
        table_model=_table_models(model), flows=flows, grid=grid,
        frontmatter=frontmatter, typography=ty,
        bottom_reserved_regions=bottom_reserved)
    html_path = out_dir / "zh_visual.html"
    pdf_path_out = out_dir / "zh_visual.pdf"
    html_path.write_text(html, encoding="utf-8")
    render_html_to_pdf(html_path, pdf_path_out)
    render_ms = int((time.time() - t0) * 1000)

    # ---- visual QAs ------------------------------------------------------
    from visual_qa import (visual_anchor_integrity_qa,
                           fixed_canvas_text_region_qa,
                           visual_page_expansion_qa,
                           visual_execution_integrity_qa)
    stage_statuses = {
        "visual_anchor_integrity": "pass",
        "fixed_canvas_text_region": "pass",
        "visual_page_expansion": "pass",
        "render": "pass",
    }
    anchor = visual_anchor_integrity_qa(model, flows, out_dir=out_dir,
                                        pdf_path=pdf_path_out)
    region = fixed_canvas_text_region_qa(model, flows, unresolved,
                                         grid=grid, out_dir=out_dir,
                                         pdf_path=pdf_path_out)
    expansion = visual_page_expansion_qa(pdf_path_out, source_pages=1)
    execution = visual_execution_integrity_qa(stage_statuses)
    # visual-v02: SourceInkGeometryQA (ink-aware isolation)
    from source_ink_geometry import source_ink_geometry_qa
    ink_qa = source_ink_geometry_qa(model, flows, ink, pdf_path,
                                    out_dir=out_dir)
    # visual-v03: SourceVisualGroupQA + SourceTargetTopologyQA
    from source_visual_group_qa import (source_visual_group_qa,
                                        source_target_topology_qa)
    group_qa = source_visual_group_qa(model, flows, grid=grid,
                                      out_dir=out_dir)
    topo_qa = source_target_topology_qa(model, flows, grid=grid,
                                        out_dir=out_dir)
    if anchor["decision"] == "fail":
        stage_statuses["visual_anchor_integrity"] = "fail"
    if region["decision"] == "fail":
        stage_statuses["fixed_canvas_text_region"] = "fail"
    if expansion["decision"] == "fail":
        stage_statuses["visual_page_expansion"] = "fail"
    if ink_qa["decision"] == "fail":
        stage_statuses["source_ink_geometry"] = "fail"
    if group_qa["decision"] == "fail":
        stage_statuses["source_visual_group"] = "fail"
    if topo_qa["decision"] == "fail":
        stage_statuses["source_target_topology"] = "fail"
    execution = visual_execution_integrity_qa(stage_statuses)

    hard = {
        "anchor_displaced_count": anchor["metrics"]["anchor_displaced_count"],
        "formula_blank_count": anchor["metrics"]["formula_blank_count"],
        "formula_crop_count": anchor["metrics"]["formula_crop_count"],
        "formula_duplicate_count": anchor["metrics"]["formula_duplicate_count"],
        "formula_orphan_count": anchor["metrics"]["formula_orphan_count"],
        "foreign_text_inside_formula_count":
            anchor["metrics"]["foreign_text_inside_formula_count"],
        "soft_text_region_overflow_count":
            region["metrics"]["soft_text_region_overflow_count"],
        "gutter_intrusion_count": region["metrics"]["gutter_intrusion_count"],
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
    }
    passed = all(v == 0 for v in hard.values())
    bundle = {
        "page": page, "doc": doc_key, "passed": passed,
        "hard_metrics": hard,
        "anchor_integrity": anchor, "text_region": region,
        "page_expansion": expansion, "execution_integrity": execution,
        "source_ink_geometry": ink_qa,
        "source_visual_group": group_qa,
        "source_target_topology": topo_qa,
        "visual_groups": [g.to_dict() for g in visual_groups],
        "unresolved": unresolved,
        "render_ms": render_ms,
        "outputs": {"html": str(html_path.relative_to(OUT)),
                    "pdf": str(pdf_path_out.relative_to(OUT))},
    }
    _dump(out_dir / "visual_page_qa.json", bundle)
    return bundle


def _resolve_desktop_pdf(doc_key):
    home = Path.home()
    name = {"2504": "2504.05732v2.pdf", "ppat": "PPAT.pdf"}[doc_key]
    cand = home / "Desktop" / name
    if cand.exists():
        return cand
    return Path("C:/Users/74496/Desktop") / name


def build_gate(results):
    conditions = {}
    totals = {}
    for key in ("anchor_displaced_count", "formula_blank_count",
                "formula_crop_count", "formula_duplicate_count",
                "formula_orphan_count", "foreign_text_inside_formula_count",
                "soft_text_region_overflow_count", "gutter_intrusion_count",
                "anchor_invasion_count", "capacity_unresolved_count",
                "unexpected_extra_page_count",
                "source_ink_budget_violation_count",
                "anchor_ink_displacement_count",
                "visual_group_split_count",
                "caption_role_fragmentation_count",
                "full_width_group_topology_violation_count",
                "group_target_drop_count", "group_target_duplicate_count",
                "group_member_order_inversion_count"):
        totals[key] = sum(r["hard_metrics"].get(key, 0) for r in results)
        conditions[key] = totals[key] == 0
    conditions["all_pages_rendered"] = all(
        "error" not in r and r.get("outputs") for r in results)
    conditions["qa_execution_complete"] = all(
        r["execution_integrity"]["qa_execution_complete"] for r in results)
    decision = "pass" if all(conditions.values()) else "blocked"
    return {"schema_version": "visual_v01.checkpoint_gate.v1",
            "decision": decision, "conditions": conditions,
            "totals": totals,
            "page_results": [{"doc": r["doc"], "page": r["page"],
                              "passed": r["passed"],
                              "decision": "pass" if r["passed"] else "blocked",
                              "hard_metrics": r["hard_metrics"]}
                             for r in results]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", default=None,
                    help='e.g. "2504:1,3,6,13,14,16;ppat:4,5,6"')
    ap.add_argument("--only", default=None, help="doc:page e.g. 2504:1")
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
    # document-level fragment partition (visual-v02) per source document
    doc_partitions = {}
    doc_closure = {}
    doc_all_pages = {}
    for dkey in set(f[0] for f in fixtures):
        # read the full source page range from the frozen artifacts
        pages = sorted(
            int(p.name[1:]) for p in (DOCS[dkey]["src"] / "pages").iterdir()
            if p.name.startswith("p") and p.is_dir())
        doc_all_pages[dkey] = pages
        dp = build_document_partition(dkey, pages)
        doc_partitions[dkey] = dp["targets"]
        doc_closure[dkey] = dp["closure_qa"]
        _dump(OUT / ("%s_fragment_closure_qa.json" % dkey),
              {"schema_version": "visual_v02.fragment_closure_qa.v1",
               "closure_qa": dp["closure_qa"]})

    results = []
    for dkey, pg in fixtures:
        pdir = OUT / ("%s_p%03d" % (dkey, pg))
        pdir.mkdir(parents=True, exist_ok=True)
        print("[visual-v02] %s p%03d ..." % (dkey, pg), flush=True)
        try:
            r = render_visual_page(dkey, pg, pdir,
                                   fragment_targets=doc_partitions.get(dkey))
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
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            r = {"doc": dkey, "page": pg,
                 "error": "%s: %s" % (type(e).__name__, e)}
            _dump(pdir / "visual_page_qa.json", r)
        results.append(r)

    gate = build_gate(results)
    _dump(OUT / "checkpoint_gate.json", gate)
    print("=== visual-v02 checkpoint gate ===")
    print("decision:", gate["decision"])
    for k, v in gate["totals"].items():
        print("  %s: %d" % (k, v))
    for r in gate["page_results"]:
        print("  %s p%03d: %s" % (r["doc"], r["page"], r["decision"]))


if __name__ == "__main__":
    main()
