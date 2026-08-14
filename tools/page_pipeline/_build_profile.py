# -*- coding: utf-8 -*-
"""4D.1A: build document layout profile for the whole PDF."""
import json, sys
from pathlib import Path
sys.path.insert(0, "tools/page_pipeline")
import page_layout_grid as plg

prof = plg.DocumentLayoutProfile("runs/diag_src_2504.pdf").build()
print(json.dumps(prof, ensure_ascii=False, indent=1))
out = Path("outputs/phase4d1_analysis")
out.mkdir(parents=True, exist_ok=True)
(out / "document_layout_profile.json").write_text(
    json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
# per-page grids
for i in range(prof["page_count"]):
    g = plg.infer_page_grid("runs/diag_src_2504.pdf", i)
    (out / ("page_%03d_grid.json" % (i + 1))).write_text(
        json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
print("wrote profile + page grids")
