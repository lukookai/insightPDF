# -*- coding: utf-8 -*-
import json
from pathlib import Path

out = Path("outputs/phase4c2r1_run")
g = json.loads((out / "delivery_gate.json").read_text(encoding="utf-8"))
print("gate:", g["decision"], "blocked:", g["blocked_layers"], "warning:", g["warning_layers"])
print("hard counts:", {k: g[k] for k in ("heading_duplicate_count", "formula_crop_violation_count", "short_residual_token_count")})
r = json.loads((out / "document_report.json").read_text(encoding="utf-8"))
print("preview pages:", r.get("preview_physical_page_count"), "generated:", r.get("delivery_preview_generated"),
      "coverage:", r.get("qa", {}).get("body_translation_coverage_ratio"))

pages = out / "pages"
print("\n== 9 page regression ==")
reg = [3, 6, 7, 11, 12, 13, 14, 15, 16]
for pno in reg:
    d = json.loads((pages / ("p%03d" % pno) / "qa.json").read_text(encoding="utf-8"))
    checks = []
    for key, val in (("heading_duplicate_qa", "heading_qa_clean"),
                     ("formula_crop_qa", "formula_crop_clean"),
                     ("residual_language_qa", "residual_clean"),
                     ("rendered_collision_qa", "collision_gate_passed"),
                     ("formula_exclusivity_qa", "formula_exclusivity_passed")):
        v = (d.get(key) or {}).get(val)
        checks.append("%s=%s" % (key.split("_qa")[0][:11], v))
    print("p%03d: %s" % (pno, " ".join(checks)))

model = json.loads((pages / "p011/raw_page_model.json").read_text(encoding="utf-8"))
for rg in model.get("regions", []):
    if rg.get("type") == "formula" and rg["payload"].get("formula_id") == "B1":
        fm = rg["payload"]
        for seg in fm.get("render_segments", []):
            if seg.get("segment_id") == "B1-S6":
                print("\nB1-S6 rv:", [round(v, 2) for v in (seg.get("render_viewbox") or [])])
        for c in fm.get("components", []):
            if (c.get("text") or "").strip() == "(15)":
                print("(15) comp:", [round(v, 1) for v in c.get("bbox", [])])
