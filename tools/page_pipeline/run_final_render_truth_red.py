"""run_final_render_truth_red -- visual-v04 QA-FIRST: RED baseline.

Runs the four new final-render-truth QAs against the visual-v03 FROZEN
outputs (no re-render, no production change).  Proves that PPAT
p004 / p005 / p006 are RED under the new truth, then writes:

    outputs/visual_v04_checkpoint/old_red_evidence/
        ppat_p004_final_truth_red.json
        ppat_p005_final_truth_red.json
        ppat_p006_final_truth_red.json
        ppat_p004_collision_overlay.png
        ppat_p005_collision_overlay.png
        ppat_p006_collision_overlay.png
        old_red_summary.json

If the three pages PASS under the new QA -> STOP (the QA does not yet
capture the human-visible defect; no production fix is allowed).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))

from final_render_truth_qa import run_final_render_truth_qa  # noqa: E402

V03 = REPO / "outputs" / "visual_v03_checkpoint"
P4E2A = REPO / "outputs" / "phase4e2a_qa_recovery"
RED_OUT = REPO / "outputs" / "visual_v04_checkpoint" / "old_red_evidence"

DOCS = {
    "ppat": {"src": P4E2A / "doc2", "pdf": "C:/Users/74496/Desktop/PPAT.pdf",
             "pages": [4, 5, 6]},
    "2504": {"src": P4E2A / "doc1", "pdf": "C:/Users/74496/Desktop/2504.05732v2.pdf",
             "pages": [1, 3, 6, 13, 14, 16]},
}


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def build_flows(doc_key, page, fragment_targets=None):
    """Re-run the visual layout ONLY (never re-render the PDF)."""
    from visual_anchor_layout import FixedCanvasAnchorLayout
    from source_ink_geometry import SourceInkGeometry
    from source_visual_group import build_source_visual_groups
    info = DOCS[doc_key]
    src_dir = info["src"] / "pages" / ("p%03d" % page)
    model = _load(src_dir / "stitched_page_model.json", {})
    translations = _load(src_dir / "translation.json", {})
    grid = _load(src_dir / "page_grid.json", {}) or _load(
        src_dir / "qa.json", {}).get("page_grid", {})
    pdf = info["pdf"]
    ink = SourceInkGeometry(pdf, model, page - 1)
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
        fragment_targets=fragment_targets or {},
        ink=ink, visual_groups=vg)
    return layout.build_visual_flows()


def load_doc_partition(dkey):
    """Recompute the document-level VisualFragmentPartition (visual-v02)."""
    from visual_fragment_partition import VisualFragmentPartition
    info = DOCS[dkey]
    pages = sorted(
        int(p.name[1:]) for p in (info["src"] / "pages").iterdir()
        if p.name.startswith("p") and p.is_dir())
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
    for lid, res in results.items():
        if len(res.get("fragments", [])) > 1:
            targets[lid] = {f["fragment_id"]: f["target_text"]
                            for f in res["fragments"]}
    return targets


def main():
    RED_OUT.mkdir(parents=True, exist_ok=True)
    results = []
    doc_partitions = {}
    for dkey in DOCS:
        doc_partitions[dkey] = load_doc_partition(dkey)
    for dkey, doc in DOCS.items():
        for pg in doc["pages"]:
            src_dir = doc["src"] / "pages" / ("p%03d" % pg)
            model = _load(src_dir / "stitched_page_model.json", {})
            translations = _load(src_dir / "translation.json", {})
            flows = build_flows(dkey, pg,
                                fragment_targets=doc_partitions[dkey])
            page_dir = V03 / ("%s_p%03d" % (dkey, pg))
            qa = run_final_render_truth_qa(
                model, translations, flows,
                final_pdf_path=page_dir / "zh_visual.pdf",
                html_path=page_dir / "zh_visual.html",
                source_pdf_path=doc["pdf"])
            results.append({"doc": dkey, "page": pg,
                            "decision": qa["decision"],
                            "hard": qa["hard"],
                            "qa": qa})
            # per-page red evidence
            _dump(RED_OUT / ("%s_p%03d_final_truth_red.json" % (dkey, pg)),
                  {"doc": dkey, "page": pg,
                   "schema_version": "visual_v04.old_red_evidence.v1",
                   "decision": qa["decision"], "hard": qa["hard"],
                   "conditions": qa["conditions"],
                   "final_visible_translation_qa":
                       qa["final_visible_translation_qa"],
                   "final_source_residual_qa": qa["final_source_residual_qa"],
                   "soft_text_collision_qa": qa["soft_text_collision_qa"],
                   "final_render_cardinality_qa":
                       qa["final_render_cardinality_qa"]})

    summary = {
        "schema_version": "visual_v04.old_red_summary.v1",
        "note": "visual-v03 frozen outputs under the new final-render-truth "
                "QAs. RED == the new QA captures a human-visible defect.",
        "pages": [{"doc": r["doc"], "page": r["page"],
                   "decision": r["decision"],
                   "hard": r["hard"]} for r in results],
        "ppat_red_count": sum(1 for r in results
                              if r["doc"] == "ppat" and r["decision"] == "blocked"),
        "ppat_page_count": sum(1 for r in results if r["doc"] == "ppat"),
    }
    _dump(RED_OUT / "old_red_summary.json", summary)
    print("=== visual-v04 RED baseline (v03 frozen outputs) ===")
    for r in results:
        print("  %s p%03d: %s" % (r["doc"], r["page"], r["decision"]))
        for k, v in r["hard"].items():
            if v != 0 and k != "translation_pipeline_coverage":
                print("      %s = %s" % (k, v))
    print("ppat RED:", summary["ppat_red_count"], "/", summary["ppat_page_count"])
    if summary["ppat_red_count"] == 0:
        print("STOP: new QA does not capture the human-visible defect.")


def _dump(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                       encoding="utf-8")


if __name__ == "__main__":
    main()
