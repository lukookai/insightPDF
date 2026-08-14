# -*- coding: utf-8 -*-
import json, pymupdf
from pathlib import Path

out = Path("outputs/phase4c2r1_run/pages")

def ink(pdf, idx, rect, z=4.0):
    doc = pymupdf.open(pdf)
    pix = doc[idx].get_pixmap(matrix=pymupdf.Matrix(z, z), clip=pymupdf.Rect(*rect), alpha=False)
    doc.close()
    return sum(1 for i in range(0, len(pix.samples), pix.n) if pix.samples[i] < 200)

# p011 (15): find final enclosure of B1 from collision QA
r = json.loads((out / "p011/rendered_region_collision_qa.json").read_text(encoding="utf-8"))
for rec in r.get("formula_records", []):
    if rec["formula_id"] == "B1":
        enc = rec.get("enclosure_bbox", [])
        print("B1 final enclosure:", [round(v, 1) for v in enc])
# (15) at source [507,180.6,525.1,191.5]; find its final y via dy of B1 enclosure vs layout
model = json.loads((out / "p011/raw_page_model.json").read_text(encoding="utf-8"))
for rg in model.get("regions", []):
    if rg.get("type") == "formula" and rg["payload"].get("formula_id") == "B1":
        dy = enc[1] - rg["payload"]["layout_bbox"][1]
        print("B1 dy = %.1f" % dy)
        rect15 = (507.0, 180.6 + dy, 525.1, 191.5 + dy)
        print("(15) @final rect:", [round(v, 1) for v in rect15])
        print("(15) ink: src=%d final=%d" % (
            ink("runs/diag_src_2504.pdf", 10, (507.0, 180.6, 525.1, 191.5)),
            ink(str(out / "p011/zh.pdf"), 0, rect15)))

# p014 otherwise at final position (dy from B12 enclosure)
r14 = json.loads((out / "p014/rendered_region_collision_qa.json").read_text(encoding="utf-8"))
for rec in r14.get("formula_records", []):
    if rec["formula_id"] == "B12":
        enc12 = rec.get("enclosure_bbox", [])
        break
model14 = json.loads((out / "p014/raw_page_model.json").read_text(encoding="utf-8"))
for rg in model14.get("regions", []):
    if rg.get("type") == "formula" and rg["payload"].get("formula_id") == "B12":
        dy14 = enc12[1] - rg["payload"]["layout_bbox"][1]
        print("\np014 B12 dy = %.1f" % dy14)
        rect_o = (424.32, 594.24 + dy14, 466.73, 605.15 + dy14)
        print("otherwise @final ink: src=%d final=%d" % (
            ink("runs/diag_src_2504.pdf", 13, (424.32, 594.24, 466.73, 605.15)),
            ink(str(out / "p014/zh.pdf"), 0, rect_o)))
