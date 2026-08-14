# -*- coding: utf-8 -*-
"""Build outputs/deliverables_phase4d1a/ -- standalone 4D.1A evidence for
external (GPT) review: analysis JSONs + source/final/overlay PNGs."""
import json, shutil, sys
from pathlib import Path

sys.path.insert(0, "tools/page_pipeline")
import page_layout_grid as plg
import front_matter as fm
import layout_grid_qa as lqa
import pymupdf
from PIL import Image

SRC = "runs/diag_src_2504.pdf"
FINAL = Path("outputs/phase4c2r1_run/pages")
ANALYSIS = Path("outputs/phase4d1_analysis")
DST = Path("outputs/deliverables_phase4d1a")
DST.mkdir(parents=True, exist_ok=True)

GOLDEN = [1, 3, 6, 13]

# 1. analysis JSONs
for name in ("PHASE4D1A_ANALYSIS.md", "p001_layout_analysis.json",
             "p001_page_grid.json", "p001_frontmatter.json",
             "document_layout_profile.json"):
    shutil.copy2(ANALYSIS / name, DST / name)

# 2. overlays + QA JSONs
ov = DST / "overlays"
ov.mkdir(exist_ok=True)
for name in ANALYSIS.glob("*layout_grid_overlay.png"):
    shutil.copy2(name, ov / name.name)
for name in ANALYSIS.glob("visual_layout_qa_page_*.json"):
    shutil.copy2(name, DST / name.name)

# 3. raw page renders (source + final) for side-by-side review
raw = DST / "pages_raw"
raw.mkdir(exist_ok=True)

def render_png(pdf, page_idx, out_path, zoom=2.0):
    doc = pymupdf.open(str(pdf))
    page = doc[page_idx]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    Image.frombytes("RGB", (pix.width, pix.height), pix.samples).save(out_path)
    doc.close()

for pno in GOLDEN:
    render_png(SRC, pno - 1, raw / ("source_p%03d.png" % pno))
    final_pdf = FINAL / ("p%03d" % pno) / "zh.pdf"
    if final_pdf.exists():
        render_png(final_pdf, 0, raw / ("final_p%03d.png" % pno))

# 4. README index for external review
readme = """# Phase 4D.1A 交付物 — 布局分析证据（供外部 GPT 独立判断）

**状态：Analysis Only（未修改任何 renderer / 4C gate 代码）。**
目标：验证 4D.1 的 grid 推断与 front-matter 分类是否正确，再决定是否进入 4D.1B 接入 renderer。

## 怎么看（建议顺序）
1. `pages_raw/source_p001.png` ↔ `pages_raw/final_p001.png` — source 原版 vs 当前中文版 p001 对比（看 front matter 碎裂 / 栏位 / 摘要缩进）。
2. `overlays/p001_layout_overlay.png` — source 页上叠加 grid（蓝=content frame、青=栏、红=gutter 禁区、紫=全宽行、绿=当前 final 文本块、橙=公式、粉=表/图）。**重点：绿块是否压进红色 gutter 带。**
3. `PHASE4D1A_ANALYSIS.md` — 完整测量结论（42 问的 1–5 + front matter 碎裂明细 + baseline 表）。
4. `p001_layout_analysis.json` / `p001_frontmatter.json` / `p001_page_grid.json` / `document_layout_profile.json` — 原始数据。
5. `visual_layout_qa_page_00X.json` — 当前 final 版 4 个 golden 页的 grid QA 数字。

## 关键数据（速览）
| 指标 | 值 |
|---|---|
| source content frame (p001) | [70.87, 126.03, 526.23, 775.50] |
| source 左/右栏 | [87.9, 289.1] w201.2 / [306.1, 524.4] w218.3 |
| source gutter | [289.1, 306.1] 宽 **17.0 pt** |
| 文档中位（13 双栏页） | gutter 17.0 / L 218.2 / R 218.3 / 边距 70.5·69.0 |
| 当前 final 的 grid | p001/p003/p006 推断为**单栏**（无稳定 gutter） |
| gutter 侵入 span 数（当前 final） | p001=34 p003=32 p006=38 p013=36 |
| 栏宽差（当前 final） | p001=17.1pt p006=21.9pt（source 0.1pt） |
| front matter | title 全宽居中；authors 2 行；aff 3 行；abstract 左栏缩进块 [87.5,273.8]；figure 右栏矢量 [316.5,229,512.1,329]；caption；3 条 footnotes |

## 判断要点（给 GPT）
- grid 推断（x-projection：字号众数过滤 front matter + 相对阈值 gutter + 对齐主导边 snap）是否可信？
- p001 左栏 x0=87.9（abstract 缩进拉偏）应否接受，正文栏是否应回归文档级 70.9？
- 当前 final 的碎裂证据（右栏英文作者、机构分栏）是否与 4D.1B 的修复目标一致？
- 4D.1B 是否可按此 grid（L[70.9,289.1] / gutter 17 / R[306.1,524.4]）接入？
"""
(DST / "README.md").write_text(readme, encoding="utf-8")

print("deliverables ->", DST)
for f in sorted(DST.rglob("*")):
    if f.is_file():
        print("  ", f.relative_to(DST), f.stat().st_size)
