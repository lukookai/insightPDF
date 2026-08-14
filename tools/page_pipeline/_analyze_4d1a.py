# -*- coding: utf-8 -*-
"""4D.1A: p001 layout analysis — source grid + front matter + current
final-PDF baseline + overlays."""
import json, sys
from pathlib import Path
sys.path.insert(0, "tools/page_pipeline")

import pymupdf
from PIL import Image, ImageDraw

import page_layout_grid as plg
import front_matter as fm
import layout_grid_qa as lqa

SRC = "runs/diag_src_2504.pdf"
FINAL = Path("outputs/phase4c2r1_run/pages")
OUT = Path("outputs/phase4d1_analysis")
OUT.mkdir(parents=True, exist_ok=True)

GOLDEN = [1, 3, 6, 13]

# 1. document profile
profile = plg.DocumentLayoutProfile(SRC).build()
(OUT / "document_layout_profile.json").write_text(
    json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8")

# 2. p001 source grid + front matter
grid1 = plg.infer_page_grid(SRC, 0)
fm1 = fm.classify_front_matter(SRC, 0, grid1)
(OUT / "p001_page_grid.json").write_text(
    json.dumps(grid1, ensure_ascii=False, indent=1), encoding="utf-8")
(OUT / "p001_frontmatter.json").write_text(
    json.dumps(fm1, ensure_ascii=False, indent=1), encoding="utf-8")

# 3. current final-PDF baseline for golden pages
baseline = {}
for pno in GOLDEN:
    final_pdf = FINAL / ("p%03d" % pno) / "zh.pdf"
    src_grid = plg.infer_page_grid(SRC, pno - 1)
    # final pdf's OWN grid (what the old renderer actually produced)
    final_grid = plg.infer_page_grid(str(final_pdf), 0)
    qa = lqa.layout_grid_qa(str(final_pdf), grid=src_grid,
                            out_dir=str(OUT), page_label="page_%03d" % pno,
                            frontmatter=(fm1 if pno == 1 else None))
    baseline["p%03d" % pno] = {
        "source_grid": src_grid,
        "final_grid": final_grid,
        "visual_qa": qa,
    }
(OUT / "p001_layout_analysis.json").write_text(json.dumps({
    "page": 1,
    "source_grid": grid1,
    "frontmatter": fm1,
    "document_profile": profile,
    "baseline": baseline,
}, ensure_ascii=False, indent=1), encoding="utf-8")


def draw_overlay(source_pdf, page_idx, grid, frontmatter, final_pdf,
                 out_path, label="source"):
    doc = pymupdf.open(source_pdf)
    page = doc[page_idx]
    zoom = 2.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    d = ImageDraw.Draw(img, "RGBA")
    z = zoom

    def rect(b, fill, outline, width=2):
        d.rectangle([b[0] * z, b[1] * z, b[2] * z, b[3] * z],
                    fill=fill, outline=outline, width=width)

    cf = grid["content_frame"]
    # blue: content frame
    rect([cf["x0"], 0, cf["x1"], cf["y1"]], (0, 0, 255, 18), (0, 0, 255, 200))
    # cyan: column boundaries
    for c in grid["columns"]:
        rect([c["x0"], cf["y0"], c["x1"], cf["y1"]], (0, 200, 255, 14),
             (0, 200, 255, 200), 1)
    # red: gutter forbidden region
    if grid["gutter"]:
        g = grid["gutter"]
        rect([g["x0"], cf["y0"], g["x1"], cf["y1"]], (255, 40, 40, 55),
             (255, 40, 40, 220))
    # purple: full-width regions
    for r in grid["full_width_regions"]:
        rect(r["bbox"], (200, 80, 255, 30), (200, 80, 255, 200), 1)
    # orange: formula regions from source model
    model_path = Path("outputs/phase4c2r1_run/pages/p%03d/stitched_page_model.json" % (page_idx + 1))
    if model_path.exists():
        model = json.loads(model_path.read_text(encoding="utf-8"))
        for rg in model.get("regions", []):
            if rg["type"] == "formula":
                rect(rg["bbox"], (255, 150, 20, 40), (255, 150, 20, 220), 1)
            elif rg["type"] in ("table", "figure", "image"):
                rect(rg["bbox"], (255, 80, 200, 40), (255, 80, 200, 220), 1)
    # pink: final-PDF text blocks (green overlay)
    if final_pdf and Path(final_pdf).exists():
        fdoc = pymupdf.open(final_pdf)
        fpage = fdoc[0]
        for block in fpage.get_text("dict").get("blocks", []):
            if block.get("type") == 1:
                continue
            for line in block.get("lines", []):
                t = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                if not t:
                    continue
                b = line.get("bbox")
                rect([b[0], b[1], b[2], b[3]], (40, 220, 60, 60),
                     (40, 220, 60, 160), 1)
        fdoc.close()
    img.save(out_path)


# overlays
draw_overlay(SRC, 0, grid1, fm1, str(FINAL / "p001/zh.pdf"),
             OUT / "p001_layout_overlay.png", "source")
for pno in GOLDEN:
    g = plg.infer_page_grid(SRC, pno - 1)
    draw_overlay(SRC, pno - 1, g, None, str(FINAL / ("p%03d" % pno) / "zh.pdf"),
                 OUT / ("page_%03d_layout_grid_overlay.png" % pno), "source")

print("4D.1A analysis written to", OUT)
print(json.dumps(baseline, ensure_ascii=False, indent=1)[:1500])
