# -*- coding: utf-8 -*-
"""Collect 4D.1B 4-page checkpoint stats."""
import json, sys
from pathlib import Path

sys.path.insert(0, "tools/page_pipeline")
import page_layout_grid as plg
import layout_grid_qa as lqa

RUN = Path("outputs/phase4d1b_run")
SRC = "runs/diag_src_2504.pdf"
pages_dir = RUN / "pages"

profile = plg.DocumentLayoutProfile(SRC).build()
print("=== 4D.1B checkpoint pages ===")
for pno in (1, 3, 6, 13):
    d = json.loads((pages_dir / ("p%03d" % pno) / "qa.json").read_text(encoding="utf-8"))
    qa = d["qa"]
    checks = {
        "physical": d["physical_qa"].get("all_assertions_passed"),
        "collision": d["rendered_collision_qa"].get("collision_gate_passed"),
        "exclusivity": d["formula_exclusivity_qa"].get("formula_exclusivity_passed"),
        "residual": d["residual_language_qa"].get("residual_clean"),
        "heading_dup": d["heading_duplicate_qa"].get("heading_qa_clean"),
        "crop": d["formula_crop_qa"].get("formula_crop_clean"),
        "assertions": qa.get("all_assertions_passed"),
        "recovery": qa["typography"].get("paragraph_text_recovery_ratio"),
        "overflow": qa["typography"].get("column_bottom_overflow_count"),
        "splits": qa.get("latin_token_split_count"),
    }
    print("p%03d: %s" % (pno, checks))

    # visual QA against the SAME grid the renderer used (profile-snapped)
    import run_document as rd
    grid, fm, ty = rd.page_render_context(SRC, pno - 1, profile=profile)
    vqa = lqa.layout_grid_qa(str(pages_dir / ("p%03d" % pno) / "zh.pdf"),
                             grid=grid, out_dir=str(RUN),
                             page_label="4d1b_page_%03d" % pno,
                             frontmatter=fm)
    print("   visual: hard=%s intrusion=%d fw_cross=%d overlap=%d width_diff=%.1f ratio=%.3f" % (
        vqa["hard_gate_passed"], vqa["column_scoped_gutter_intrusion_count"],
        vqa["full_width_gutter_crossing_count"], vqa["block_overlap_count"],
        vqa["column_width_difference"], vqa["column_width_ratio"]))
