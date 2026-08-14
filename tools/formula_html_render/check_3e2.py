# -*- coding: utf-8 -*-
"""Phase 3E.2 acceptance assertions."""
import json
import sys
sys.path.insert(0, "tools/formula_html_render")
from run_p3e import raw_geometry
from formula_compose import compose_blocks

spans, drawings, images = raw_geometry("runs/diag_src_2504.pdf", 2)
models = compose_blocks(spans, drawings, images, 595.276, 841.89)
all_src = " ".join(m["source_text"] for m in models)
ok = lambda name, cond: print("%s %s" % ("PASS" if cond else "FAIL", name))

# 1. B10 = 1 FormulaGroup but 2 RenderSegments
b10 = next(m for m in models if "C(R)" in m["source_text"])
ok("1. B10 1 group + 2 segments", len(b10["render_segments"]) == 2)

# 2. two compact segment boxes (no big union)
s1, s2 = b10["render_segments"]
ok("2a. S1 (C(R)=) compact box", s1["layout_bbox"][2] - s1["layout_bbox"][0] < 50)
ok("2b. S2 ({C1..}) compact box", s2["layout_bbox"][2] - s2["layout_bbox"][0] < 100)
ok("2c. group layout kept (semantic only)", "layout_bbox" in b10)

# 3. B11 independent group (Sigma Cj = R: contains j=1 limit, not C(R))
b11 = next(m for m in models
           if "j=1" in m["source_text"] and "C(R)" not in m["source_text"]
           and "C1" not in m["source_text"]
           and "XX" not in m["source_text"])
ok("3. B11 independent group", len(b11["render_segments"]) == 1
   and b11["formula_id"] != b10["formula_id"])

# 4. B10 segments vs B11: no substantial overlap
def area(a, b):
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, ix) * max(0.0, iy)

ovs = [area(s["layout_bbox"], b11["render_segments"][0]["layout_bbox"])
       for s in b10["render_segments"]]
ok("4. B10 segments vs B11 no substantial overlap", all(o <= 4.0 for o in ovs))

# 5-6. foreign QA from report
r = json.load(open("runs/fyresults/formula_svg/page_003_formula_blocks_report.json",
                   encoding="utf-8"))
fq = r["foreign_content_qa"]
ok("5. foreign_text_overlap_count=0", fq["foreign_text_overlap_count"] == 0)
ok("6. foreign_formula_overlap_count=0", fq["foreign_formula_overlap_count"] == 0)
ok("6b. render_segment_overlap_count=0", fq["render_segment_overlap_count"] == 0)

# 7. no regression on eq(2)/eq(3)/S(0)
b2 = next(m for m in models if "(2)" in m["source_text"])
b4 = next(m for m in models if "(3)" in m["source_text"])
b14 = next(m for m in models if "XX" in m["source_text"] and "(0)" in m["source_text"])
ok("7a. eq(2) display single segment",
   b2["type"] == "display" and len(b2["render_segments"]) == 1)
ok("7b. eq(3) complex_display single segment",
   b4["type"] == "complex_display" and len(b4["render_segments"]) == 1)
ok("7c. S(0) complex_display single segment",
   b14["type"] == "complex_display" and len(b14["render_segments"]) == 1)

# 8. title x absent
ok("8. no multiplication-sign formula block", "\u00d7" not in all_src)

# 9-10. rendered QA from the report (run_p3e fills per-segment qa)
seg_qa = [q for f in r["formulas"] for q in f["qa"]["per_segment"]]
ok("9. ink_clipped=0 (%d segments)" % len(seg_qa),
   all(not q["ink_clipped"] for q in seg_qa))
ok("9b. render_success all", all(q["render_success"] for q in seg_qa))
ok("10. all vector, zero raster",
   all(q["vector_drawings"] > 0 and q["raster_images"] == 0 for q in seg_qa))

print()
print("groups:", len(models), "| segments:", len(seg_qa))
print("B10 raw_component_text:", b10["raw_component_text"][:40],
      "| order_reliable:", b10["source_text_order_reliable"])
