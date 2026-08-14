# -*- coding: utf-8 -*-
"""4D.1B dev harness: render p001 (and optionally other pages) with the
new PageLayoutGrid + FrontMatter renderer into a scratch output dir."""
import json, shutil, sys
from pathlib import Path

sys.path.insert(0, "tools/page_pipeline")

import run_document as rd
from _chromium_pdf import render_html_to_pdf
from page_layout_grid import DocumentLayoutProfile
from page_model import build_page_model
from document_model import build_document_model

SRC = "runs/diag_src_2504.pdf"
PAGES = [int(x) for x in sys.argv[1:]] or [1]
OUT = Path("outputs/phase4d1b_dev")
OUT.mkdir(parents=True, exist_ok=True)

# reuse translations from the 4C.2R.1 run (same translation units)
SRC_RUN = Path("outputs/phase4c2r1_run")
translations = {}
cache = json.loads((SRC_RUN / "translation_cache.json").read_text(encoding="utf-8"))
document = None
for page_num in PAGES:
    model = json.loads((SRC_RUN / "pages" / ("p%03d" % page_num) / "stitched_page_model.json")
                       .read_text(encoding="utf-8"))
    t = json.loads((SRC_RUN / "pages" / ("p%03d" % page_num) / "translation.json")
                   .read_text(encoding="utf-8"))
    translations.update(t)

profile = DocumentLayoutProfile(SRC).build()
for page_num in PAGES:
    model = json.loads((SRC_RUN / "pages" / ("p%03d" % page_num) / "stitched_page_model.json")
                       .read_text(encoding="utf-8"))
    t = json.loads((SRC_RUN / "pages" / ("p%03d" % page_num) / "translation.json")
                   .read_text(encoding="utf-8"))
    grid, fm, ty = rd.page_render_context(SRC, page_num - 1, profile=profile)
    page_dir = OUT / ("p%03d" % page_num)
    page_dir.mkdir(parents=True, exist_ok=True)
    flows = rd.build_column_flow(
        rd._paragraphs(model), rd._display_formula_boxes(model),
        rd._obstacle_boxes(model), t,
        rd._formula_width_map(model), capacity=None, grid=grid)
    html = rd.build_unified_html(model, t, SRC, page_dir,
                                 table_model=rd._table_models(model),
                                 flows=flows, grid=grid, frontmatter=fm,
                                 typography=ty)
    (page_dir / "zh.html").write_text(html, encoding="utf-8")
    render_html_to_pdf(page_dir / "zh.html", page_dir / "zh.pdf")
    print("p%03d rendered -> %s" % (page_num, page_dir / "zh.pdf"))
    # visual QA on the new output
    import layout_grid_qa as lqa
    qa = lqa.layout_grid_qa(str(page_dir / "zh.pdf"), grid=grid,
                            out_dir=str(page_dir), page_label="page_%03d" % page_num,
                            frontmatter=fm)
    print("   visual_qa hard=%s intrusion=%d fw_cross=%d overlap=%d frag=%d "
          "width_diff=%.1f ratio=%.3f title_center=%s" % (
        qa["hard_gate_passed"], qa["column_scoped_gutter_intrusion_count"],
        qa["full_width_gutter_crossing_count"], qa["block_overlap_count"],
        qa["frontmatter_fragment_count"], qa["column_width_difference"],
        qa["column_width_ratio"], qa["title_center_error"]))
    json.dump({"grid": grid, "frontmatter": fm,
               "visual_qa": qa},
              open(page_dir / "context.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
