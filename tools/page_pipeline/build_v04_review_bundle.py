# -*- coding: utf-8 -*-
"""Build the visual-v04 review bundle.

For PPAT p004/p005/p006 renders before/after comparison PNGs:
  * v03 (the broken frozen output: English prose visible, collisions)
  * v04 (the fixed output: Chinese target, no residual)
plus a side-by-side contact sheet per page.
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf  # noqa: E402

V03 = Path("outputs/visual_v03_checkpoint")
V04 = Path("outputs/visual_v04_checkpoint")
OUT = V04 / "review_bundle"

PAGES = [("ppat", 4), ("ppat", 5), ("ppat", 6)]


def render_page(src_pdf, out_png, zoom=1.2):
    doc = pymupdf.open(str(src_pdf))
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    pix.save(str(out_png))
    doc.close()
    return out_png


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for doc, pg in PAGES:
        v03_pdf = V03 / ("%s_p%03d" % (doc, pg)) / "zh_visual.pdf"
        v04_pdf = V04 / ("%s_p%03d" % (doc, pg)) / "zh_visual.pdf"
        if not v03_pdf.exists() or not v04_pdf.exists():
            print("missing", v03_pdf, v04_pdf)
            continue
        a = render_page(v03_pdf, OUT / ("%s_p%03d_before_v03.png" % (doc, pg)))
        b = render_page(v04_pdf, OUT / ("%s_p%03d_after_v04.png" % (doc, pg)))
        # side-by-side contact sheet
        import PIL.Image as Image
        im_a = Image.open(a)
        im_b = Image.open(b)
        W = im_a.width + im_b.width + 24
        H = max(im_a.height, im_b.height) + 40
        sheet = Image.new("RGB", (W, H), "white")
        from PIL import ImageDraw
        dr = ImageDraw.Draw(sheet)
        dr.text((10, 8), "BEFORE (visual-v03 frozen)", fill=(180, 0, 0))
        dr.text((im_a.width + 34, 8), "AFTER (visual-v04)", fill=(0, 120, 0))
        sheet.paste(im_a, (0, 40))
        sheet.paste(im_b, (im_a.width + 24, 40))
        sheet.save(OUT / ("%s_p%03d_before_after.png" % (doc, pg)))
        print("wrote", OUT / ("%s_p%03d_before_after.png" % (doc, pg)))


if __name__ == "__main__":
    main()
