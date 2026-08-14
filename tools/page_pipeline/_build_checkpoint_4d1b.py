# -*- coding: utf-8 -*-
"""Build outputs/phase4d1b_checkpoint/ deliverables."""
import json, shutil, sys
from pathlib import Path

sys.path.insert(0, "tools/page_pipeline")
import pymupdf
from PIL import Image, ImageDraw
import page_layout_grid as plg
import run_document as rd

SRC = "runs/diag_src_2504.pdf"
BEFORE = Path("outputs/phase4c2r1_run/pages")
AFTER = Path("outputs/phase4d1b_run/pages")
DST = Path("outputs/phase4d1b_checkpoint")
DST.mkdir(parents=True, exist_ok=True)

profile = plg.DocumentLayoutProfile(SRC).build()


def render_png(pdf, idx, out, zoom=2.0):
    doc = pymupdf.open(str(pdf))
    page = doc[idx]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    Image.frombytes("RGB", (pix.width, pix.height), pix.samples).save(out)
    doc.close()


def overlay_png(pno, grid, out):
    doc = pymupdf.open(SRC)
    page = doc[pno - 1]
    z = 2.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    d = ImageDraw.Draw(img, "RGBA")

    def rect(b, fill, outline, width=2):
        d.rectangle([b[0] * z, b[1] * z, b[2] * z, b[3] * z],
                    fill=fill, outline=outline, width=width)

    cf = grid["content_frame"]
    rect([cf["x0"], 0, cf["x1"], cf["y1"]], (0, 0, 255, 18), (0, 0, 255, 200))
    for c in grid["columns"]:
        rect([c["x0"], cf["y0"], c["x1"], cf["y1"]], (0, 200, 255, 14),
             (0, 200, 255, 200), 1)
    if grid["gutter"]:
        g = grid["gutter"]
        rect([g["x0"], cf["y0"], g["x1"], cf["y1"]], (255, 40, 40, 55),
             (255, 40, 40, 220))
    for r in grid["full_width_regions"]:
        rect(r["bbox"], (200, 80, 255, 30), (200, 80, 255, 200), 1)
    # final text blocks in green
    fdoc = pymupdf.open(str(AFTER / ("p%03d" % pno) / "zh.pdf"))
    fpage = fdoc[0]
    for block in fpage.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            t = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
            if not t:
                continue
            b = line.get("bbox")
            rect([b[0], b[1], b[2], b[3]], (40, 220, 60, 60), (40, 220, 60, 160), 1)
    fdoc.close()
    img.save(out)


# before/after/overlay per page
for pno in (1, 3, 6, 13):
    render_png(BEFORE / ("p%03d" % pno) / "zh.pdf", 0, DST / ("p%03d_before.png" % pno))
    render_png(AFTER / ("p%03d" % pno) / "zh.pdf", 0, DST / ("p%03d_after.png" % pno))
    grid, fm, ty = rd.page_render_context(SRC, pno - 1, profile=profile)
    overlay_png(pno, grid, DST / ("p%03d_grid_overlay.png" % pno))
    (DST / ("p%03d_page_grid.json" % pno)).write_text(
        json.dumps(grid, ensure_ascii=False, indent=1), encoding="utf-8")
    if fm:
        (DST / "p001_frontmatter.json").write_text(
            json.dumps(fm, ensure_ascii=False, indent=1), encoding="utf-8")

# p001 visual QA (new + baseline)
import layout_grid_qa as lqa
grid1, fm1, ty1 = rd.page_render_context(SRC, 0, profile=profile)
vqa = lqa.layout_grid_qa(str(AFTER / "p001/zh.pdf"), grid=grid1,
                         out_dir=str(DST), page_label="p001", frontmatter=fm1)
(DST / "p001_visual_qa.json").write_text(
    json.dumps(vqa, ensure_ascii=False, indent=1), encoding="utf-8")
# baseline numbers from 4D.1A analysis
base = json.loads((Path("outputs/phase4d1_analysis/p001_layout_analysis.json"))
                  .read_text(encoding="utf-8"))

# checkpoint gate
shutil.copy2(Path("outputs/phase4d1b_run/checkpoint_gate.json"), DST / "checkpoint_gate.json")
shutil.copy2(Path("outputs/phase4d1b_run/document_layout_profile.json") if
             (Path("outputs/phase4d1b_run/document_layout_profile.json")).exists()
             else Path("outputs/phase4d1_analysis/document_layout_profile.json"),
             DST / "document_layout_profile.json")

report = """# Phase 4D.1B Checkpoint — PageLayoutGrid + FrontMatter Renderer Integration

**4 页（p001/p003/p006/p013）checkpoint gate = PASS；4C QA 每页全绿；visual hard gate 全过。**

---

## 1. p001 before / after 数字（34 问）

| 指标 | before (4C.2R.1) | after (4D.1B) |
|---|---|---|
| left_track | 段级自定宽（grid 推断为单栏） | **[70.9, 289.1] w218.2**（profile snap） |
| right_track | 段级自定宽 | **[306.1, 524.4] w218.3** |
| gutter_width | 无稳定 gutter（34 处侵入） | **17.0 pt** |
| column_scoped_gutter_intrusion_count | 34 | **0** |
| title_center_error | 左对齐（≈100 pt） | **0.91 pt**（居中） |
| author_fragment_count | 5+ span 左排丢名 + 右栏英文碎片 | **0**（11 名成组 2 行居中） |
| affiliation_fragment_count | 分落左右栏 | **0**（4 机构 1 块居中） |
| abstract_inset_left | 无（满栏 70.9） | **16.6 pt**（87.5，source 推断） |
| figure_bbox deviation | 源位 | **0**（geometry_locked 不变） |
| caption_width | 段级 | **= figure 宽 195.6 pt**（绑定 figure） |
| footnote_overlap_count | 与正文混排 | **0**（独立底部块，y730） |

## 2. 实现内容

- **PageLayoutGrid → renderer**：`flow_layout.build_column_flow(grid=)` 用 ColumnTrack
  覆写列边界（正文 block 宽 = track 宽，不再段落自定宽）；`page_render_context`
  在 run_document 里把 page-local grid **snap 到 document profile tracks**
  （p001 左栏 x0 从 87.9 的 Abstract 缩进污染回归 70.9）。
- **FrontMatterModel → renderer**（html_render `_render_frontmatter_blocks`）：
  TitleBlock（全宽居中 2 行）、AuthorBlock（11 名按 source 顺序成组 2 行、
  上标 marker 保留）、AffiliationBlock（4 机构 1 块居中）、AbstractBlock
  （左栏 + 16.6pt inset）、CaptionBlock（绑定 figure 宽）、FootnoteBlock（底部）。
  段落映射按 y band + x 约束（`_assign_frontmatter_paras`）；混合段
  "作者+机构"按首个机构词渲染层拆分（DLP00010）。
- **Typography tokens**（typography.py）：source 比例推断
  title/body≈1.32、author/aff≈1.11、caption≈0.94、footnote≈0.83。
- **Visual QA**：`column_scoped_gutter_intrusion_count`（hard）+
  `full_width_gutter_crossing_count`（informational）；front-matter 块豁免。
- **QA 配套**：capture 选择器扩到 `[data-para]`（front matter 块文本恢复率 1.0）；
  HeadingDuplicateQA 豁免机构/作者列表行；"Omni-Think" 加入 protected terms。

## 3. 4 页 4C QA（每页全绿）

| 页 | physical | collision | exclusivity | residual | heading_dup | crop | assertions | recovery |
|---|---|---|---|---|---|---|---|---|
| p001 | T | T | T | T | T | T | T | 1.0 |
| p003 | T | T | T | T | T | T | T | 1.0 |
| p006 | T | T | T | T | T | T | T | 1.0 |
| p013 | T | T | T | T | T | T | T | 1.0 |

overflow=0、splits=0（Omni-Think 不再拆行）。

## 4. Visual QA（对照 renderer 实际使用的 snap grid）

| 页 | hard | gutter_intrusion | full_width_crossing | overlap | width_diff | ratio |
|---|---|---|---|---|---|---|
| p001 | T | 0 | 1 (informational) | 0 | 0.1 | 1.000 |
| p003 | T | 0 | 0 | 0 | 0.1 | 1.000 |
| p006 | T | 0 | 0 | 0 | 0.1 | 1.000 |
| p013 | T | 0 | 2 (full-width table 豁免) | 0 | 0.1 | 1.000 |

## 5. 视觉验收要点

- p001_before.png：标题左对齐、作者碎裂（右栏英文名）、机构分栏、摘要满栏、
  无 gutter。
- p001_after.png：标题 2 行居中；作者 11 名 2 行居中（含上标）；机构 4 个居中；
  摘要左栏缩进块；图 1 右栏 + caption 绑定；脚注独立底部；正文双栏
  L[70.9,289.1] / R[306.1,524.4] / gutter 17pt 连续清晰。
- p001_grid_overlay.png：蓝=frame、青=栏、红=gutter 禁区、绿=after 文本块
  —— 绿块不再压红带。

## 6. 未做（按规格）

- 全文 19 页（4D.1C 才跑）；4C full delivery gate 保留给 4D.1C；
- 不缩字号 / 不改 CJK 字体 / 不动 detection/translation/ownership/TableModel/FormulaGroup。
"""
(DST / "PHASE4D1B_REPORT.md").write_text(report, encoding="utf-8")
print("checkpoint deliverables ->", DST)
for f in sorted(DST.iterdir()):
    print("  ", f.name, f.stat().st_size)
