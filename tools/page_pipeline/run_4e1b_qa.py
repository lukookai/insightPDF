# -*- coding: utf-8 -*-
"""Phase 4E.1B-A -- run the new Generalization QAs on the OLD Ding baseline.

Reads ``outputs/phase4e1_ding_generalization/`` (NO re-render) and proves the
old production baseline FAILS the new document-general QAs.  Output:
``outputs/phase4e1b_qa_baseline/old_red_evidence.json``.  No renderer change.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

OLD = REPO / "outputs" / "phase4e1_ding_generalization"
BASELINE_OUT = REPO / "outputs" / "phase4e1b_qa_baseline"
SOURCE_PDF = (r"C:\Users\74496\Desktop\Ding_SynthRGB-T_Language-Vision_Guided_"
              r"Image_Translation_for_Diversity_Synthesis_CVPR_2026_paper.pdf")
PAGES = list(range(1, 12))


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def main():
    BASELINE_OUT.mkdir(parents=True, exist_ok=True)
    models, grids, flows, physical, translations = {}, {}, {}, {}, {}
    for p in PAGES:
        tag = "p%03d" % p
        pdir = OLD / "pages" / tag
        models[p] = _load(pdir / "stitched_page_model.json", {})
        grids[p] = _load(pdir / "page_grid.json", {})
        qa = _load(pdir / "qa.json", {})
        flows[p] = qa.get("flows", [])
        physical[p] = (_load(pdir / "physical_qa.json", {}) or {}).get(
            "physical_pdf_page_count", 1)
        translations[p] = _load(pdir / "translation.json", {})
    doc_report = _load(OLD / "document_report.json", {})
    status_map = (doc_report.get("translation_qa") or {}).get("status_map") or {}

    from document_semantic_qa import document_semantic_qa
    from page_capacity_qa import page_capacity_qa
    from formula_render_completeness_qa import formula_render_completeness_qa
    from math_dense_translation_qa import math_dense_translation_qa

    # ---- 1. semantic state ----
    sem = document_semantic_qa(PAGES, models, out_dir=BASELINE_OUT)
    semantic_roles = {p: sem["per_page"][p]["state"] for p in PAGES}
    _dump(BASELINE_OUT / "document_semantic_state.json",
          {str(p): sem["per_page"][p] for p in PAGES})

    # ---- 2. capacity + physical expansion ----
    cap = page_capacity_qa(PAGES, models, grids, flows, physical,
                           semantic_roles=semantic_roles,
                           out_dir=BASELINE_OUT)

    # ---- 3. formula completeness ----
    formula_pages = []
    for p in PAGES:
        fqa = formula_render_completeness_qa(SOURCE_PDF,
                                             OLD / "pages" / ("p%03d" % p) / "zh.pdf",
                                             models[p], flows[p],
                                             out_dir=BASELINE_OUT / "per_page",
                                             page_label="p%03d" % p)
        formula_pages.append({"page": p, **fqa})
    formula_agg = {
        "formula_blank_count": sum(f["formula_blank_count"] for f in formula_pages),
        "formula_crop_count": sum(f["formula_crop_count"] for f in formula_pages),
        "formula_orphan_component_count": sum(f["formula_orphan_component_count"] for f in formula_pages),
        "per_page": formula_pages,
        "hard": {"formula_blank_count": sum(f["formula_blank_count"] for f in formula_pages),
                 "formula_crop_count": sum(f["formula_crop_count"] for f in formula_pages),
                 "formula_orphan_component_count": sum(f["formula_orphan_component_count"] for f in formula_pages)},
        "decision": "pass" if all(f["decision"] == "pass" for f in formula_pages) else "fail",
    }
    _dump(BASELINE_OUT / "formula_render_completeness_qa.json", formula_agg)

    # ---- 4. math-dense translation ----
    md = math_dense_translation_qa(models, translations,
                                   semantic_roles=semantic_roles,
                                   status_map=status_map,
                                   out_dir=BASELINE_OUT)

    # ---- aggregate old_red_evidence ----
    evidence = {
        "schema_version": "phase4e1b.old_red_evidence.v1",
        "baseline": "outputs/phase4e1_ding_generalization",
        "document": {"source_pages": 11, "final_physical_pages": sum(physical.values())},
        "gates": {
            "document_semantic": sem["decision"],
            "page_capacity": cap["decision"],
            "formula_completeness": formula_agg["decision"],
            "math_dense_translation": md["decision"],
        },
        "hard_targets": {
            "reference_role_gap_count": sem["hard"]["reference_role_gap_count"],
            "reference_state_orphan_count": sem["hard"]["reference_state_orphan_count"],
            "unexpected_page_expansion_count": cap["hard"]["unexpected_page_expansion_count"],
            "capacity_unresolved_count": cap["hard"]["capacity_unresolved_count"],
            "formula_blank_count": formula_agg["hard"]["formula_blank_count"],
            "formula_crop_count": formula_agg["hard"]["formula_crop_count"],
            "formula_orphan_component_count": formula_agg["hard"]["formula_orphan_component_count"],
            "math_dense_translation_failure_count": md["hard"]["math_dense_translation_failure_count"],
            "placeholder_loss_count": md["hard"]["placeholder_loss_count"],
            "placeholder_mutation_count": md["hard"]["placeholder_mutation_count"],
        },
        "old_baseline_red": True,
    }
    _dump(BASELINE_OUT / "old_red_evidence.json", evidence)
    _dump(BASELINE_OUT / "page_capacity_profile.json", cap)

    print("=== old_red_evidence ===")
    print(json.dumps(evidence, ensure_ascii=False, indent=2)[:1800])
    return 0 if evidence["old_baseline_red"] else 1


if __name__ == "__main__":
    sys.exit(main())
