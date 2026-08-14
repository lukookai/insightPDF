# -*- coding: utf-8 -*-
"""p014 / p016 formula ownership debug visualization (4C.2A).

Four colors on the original page raster:
  red    - math-font formula components (CMMI/CMSY/CMEX/...)
  orange - normal Roman spans that ARE owned by a FormulaGroup
  green  - Roman spans that fell through to a paragraph (the bug: "if /
           "otherwise" / "correctly supports" being translated)
  blue   - final FormulaGroup / RenderSegment bbox
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for path in (HERE, REPO / "tools" / "table_html_render",
             REPO / "tools" / "formula_html_render",
             REPO / "实现源码" / "pdf_translator"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from formula_exclusivity_qa import _is_math_font  # noqa: E402

MATH_FONT_HINTS = ("cmmi", "cmsy", "cmex", "msam", "msbm", "eufm", "mtsy",
                   "stix", "xits", "math", "latinmodern")

RED = (230, 60, 60)
ORANGE = (240, 140, 30)
GREEN = (60, 200, 90)
BLUE = (60, 120, 240)


def draw_debug(source_pdf, page_index, page_model, out_png):
    doc = pymupdf.open(source_pdf)
    pix = doc[page_index].get_pixmap(matrix=pymupdf.Matrix(2, 2))
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    draw = ImageDraw.Draw(image)
    scale = 2.0

    def box(bbox, color, width=3):
        draw.rectangle([v * scale for v in bbox], outline=color, width=width)

    # 1. paragraph-owned spans (green): the bug
    para_span_boxes = []
    for region in page_model.get("regions", []):
        if region.get("type") != "text":
            continue
        para = region["payload"]
        for line in para.get("lines", []):
            for s in line.get("spans", []):
                t = (s.get("text") or "").strip()
                if not t:
                    continue
                para_span_boxes.append((s["bbox"], s.get("font", ""), t))

    # 2. formula components (red math / orange roman) + bboxes (blue)
    for region in page_model.get("regions", []):
        if region.get("type") != "formula":
            continue
        fm = region["payload"]
        b = fm.get("layout_bbox")
        if b:
            box(b, BLUE, 4)
        for seg in fm.get("render_segments") or []:
            rb = seg.get("render_viewbox") or seg.get("layout_bbox")
            if rb:
                box(rb, BLUE, 2)
        for comp in fm.get("components", []):
            cb = comp.get("bbox")
            if not cb:
                continue
            is_math = _is_math_font(comp.get("font"))
            color = RED if is_math else ORANGE
            draw.rectangle([v * scale for v in cb],
                           outline=color, width=2)

    # 3. green for paragraph spans (draw AFTER so they are visible)
    for bbox, font, text in para_span_boxes:
        draw.rectangle([v * scale for v in bbox], outline=GREEN, width=2)

    image.save(out_png)
    print("saved", out_png)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))
    draw_debug(args.pdf, args.page - 1, model, args.out)


if __name__ == "__main__":
    main()
