# -*- coding: utf-8 -*-
import json
from pathlib import Path

out = Path("outputs/phase4c2r1_run/pages")
print("=== per-page hard counts (final) ===")
tot = {"heading_duplicate_count": 0, "formula_crop_violation_count": 0,
       "short_residual_token_count": 0}
for pno in range(1, 20):
    d = json.loads((out / ("p%03d" % pno) / "qa.json").read_text(encoding="utf-8"))
    hd = (d.get("heading_duplicate_qa") or {}).get("heading_duplicate_count", 0)
    fc = (d.get("formula_crop_qa") or {}).get("formula_crop_violation_count", 0)
    sr = (d.get("residual_language_qa") or {}).get("short_residual_token_count", 0)
    tot["heading_duplicate_count"] += hd
    tot["formula_crop_violation_count"] += fc
    tot["short_residual_token_count"] += sr
    if hd or fc or sr:
        print("p%03d: hd=%d fc=%d sr=%d" % (pno, hd, fc, sr))
print("TOTAL:", tot)

print("\n=== formula crop details (any) ===")
for pno in range(1, 20):
    p = out / ("p%03d" % pno) / "formula_crop_qa.json"
    r = json.loads(p.read_text(encoding="utf-8"))
    if r.get("formula_crop_details"):
        print("p%03d:" % pno, json.dumps(r["formula_crop_details"][:2], ensure_ascii=False)[:300])

print("\n=== p014 formula crop evidence (B11/B12) ===")
r = json.loads((out / "p014/formula_crop_qa.json").read_text(encoding="utf-8"))
print("clean:", r["formula_crop_clean"], "violations:", r["formula_crop_violation_count"])
model = json.loads((out / "p014/raw_page_model.json").read_text(encoding="utf-8"))
for rg in model.get("regions", []):
    if rg.get("type") == "formula" and rg["payload"].get("formula_id") in ("B11", "B12"):
        fm = rg["payload"]
        for seg in fm.get("render_segments", []):
            rv = seg.get("render_viewbox")
            lb = seg.get("layout_bbox")
            if rv and rv != lb:
                print("%s %s rv=%s (was lb=%s)" % (fm["formula_id"], seg["segment_id"],
                      [round(v,2) for v in rv], [round(v,2) for v in lb]))
