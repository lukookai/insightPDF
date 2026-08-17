# -*- coding: utf-8 -*-
"""build_v05_old_red_overlays -- visualize v05 QA-FIRST RED evidence.

Draws the residual formula bboxes / orphan punctuation / heading loss onto
a rendered page snapshot for human review:
    outputs/visual_v05_checkpoint/old_red_evidence/
        ppat_p004_residual_overlay.png
        ppat_p005_residual_overlay.png
        ppat_p006_residual_overlay.png
        ppat_p005_semantic_structure_overlay.png
        ppat_p004_formula_context_overlay.png
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))

RED = REPO / "outputs" / "visual_v05_checkpoint" / "old_red_evidence"
V04 = REPO / "outputs" / "visual_v04_checkpoint"


def _page_render(pdf_path: str, out_png: Path, dpi: int = 100):
    doc = pymupdf.open(pdf_path)
    page = doc[0]
    pix = page.get_pixmap(dpi=dpi)
    pix.save(str(out_png))
    doc.close()
    return out_png


def _bbox_to_px(bb, page, dpi):
    """Convert PDF pt bbox to pixel coords for the overlay drawing."""
    scale = dpi / 72.0
    r = page.rect
    return (bb[0] * scale, (r.height - bb[3]) * scale,
            (bb[2] - bb[0]) * scale, (bb[3] - bb[1]) * scale)


def draw_overlay(pdf_path: str, out_png: Path, boxes, title,
                 color=(1, 0, 0), dpi: int = 100):
    """Draw red rectangles around evidence boxes on a rendered page."""
    from PIL import Image, ImageDraw, ImageFont
    doc = pymupdf.open(pdf_path)
    page = doc[0]
    page_rect = page.rect
    pix = page.get_pixmap(dpi=dpi)
    doc.close()
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    draw = ImageDraw.Draw(img)
    scale = dpi / 72.0
    for bb in boxes:
        x0, y0, x1, y1 = bb
        # PIL: top-left origin
        px0 = x0 * scale
        py0 = (page_rect.height - y1) * scale
        pw = (x1 - x0) * scale
        ph = (y1 - y0) * scale
        draw.rectangle([px0, py0, px0 + pw, py0 + ph],
                       outline=tuple(int(c * 255) for c in color), width=3)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 28)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    draw.text((16, 12), title, fill=(255, 0, 0), font=font)
    img.save(str(out_png))
    return out_png


def main():
    RED.mkdir(parents=True, exist_ok=True)
    for pg, doc_key in [(4, "ppat"), (5, "ppat"), (6, "ppat")]:
        evidence = json.load(open(
            RED / ("%s_p00%d_v04_red.json" % (doc_key, pg)), encoding="utf-8"))
        v04_pdf = V04 / ("%s_p%03d" % (doc_key, pg)) / "zh_visual.pdf"

        # residual formula boxes
        res_boxes = []
        for det in evidence["qas"]["residual_prose_truth"]["details"]:
            bb = det.get("source_bbox") or det.get("bbox")
            if bb and len(bb) == 4:
                res_boxes.append(bb)
        draw_overlay(str(v04_pdf),
                     RED / ("%s_p00%d_residual_overlay.png" % (doc_key, pg)),
                     res_boxes, "residual prose (vector ink) - %s p%03d" % (
                         doc_key, pg))

        # formula-context boxes (orphan punct + missing target bands)
        ctx_boxes = []
        for det in evidence["qas"]["formula_adjacent_prose"]["details"]:
            bb = det.get("bbox") or det.get("source_bbox")
            if bb and len(bb) == 4:
                ctx_boxes.append(bb)
        if ctx_boxes:
            draw_overlay(
                str(v04_pdf),
                RED / ("%s_p00%d_formula_context_overlay.png" % (doc_key, pg)),
                ctx_boxes, "formula context / orphan punct - %s p%03d" % (
                    doc_key, pg),
                color=(0, 0.55, 0))

        # semantic structure overlay (heading loss)
        sem_boxes = []
        for det in evidence["qas"]["semantic_structure_closure"]["details"]:
            bb = det.get("bbox") or det.get("source_bbox")
            if bb and len(bb) == 4:
                sem_boxes.append(bb)
        if sem_boxes:
            draw_overlay(
                str(v04_pdf),
                RED / ("%s_p00%d_semantic_structure_overlay.png" % (doc_key, pg)),
                sem_boxes, "semantic/heading loss - %s p%03d" % (doc_key, pg),
                color=(0.8, 0.5, 0))
    print("overlays written to", RED)


if __name__ == "__main__":
    main()
