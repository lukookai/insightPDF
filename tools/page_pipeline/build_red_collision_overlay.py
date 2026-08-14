"""Build collision overlays for the visual-v04 RED evidence.

Renders the final PDF page (raster) and draws the detected soft-soft
collision boxes (text-layer blocks in red, prose-adopted-formula vector
ink blocks in orange) on top, writing:

    outputs/visual_v04_checkpoint/old_red_evidence/
        ppat_p004_collision_overlay.png
        ppat_p005_collision_overlay.png
        ppat_p006_collision_overlay.png
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))

RED_OUT = REPO / "outputs" / "visual_v04_checkpoint" / "old_red_evidence"
V03 = REPO / "outputs" / "visual_v03_checkpoint"

PAGES = [("ppat", 4), ("ppat", 5), ("ppat", 6)]


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def build_overlay(doc, page):
    evidence = _load(RED_OUT / ("%s_p%03d_final_truth_red.json" % (doc, page)))
    if not evidence:
        print("missing evidence", doc, page)
        return
    pdf_path = V03 / ("%s_p%03d" % (doc, page)) / "zh_visual.pdf"
    doc_pdf = pymupdf.open(str(pdf_path))
    pg = doc_pdf[0]
    # 2x raster for readability
    mat = pymupdf.Matrix(2, 2)
    pix = pg.get_pixmap(matrix=mat)
    shape = pg.new_shape()
    draw = pg.get_drawings()
    # text-layer blocks: red boxes around text-like glyph paths
    def _rect(bbox, color, width=1.0):
        r = pymupdf.Rect(*bbox)
        shape.draw_rect(r)
        shape.finish(color=color, width=width, fill_opacity=0.0)

    # severe collisions from QA3 -> bright red boxes (the two blocks)
    qa3 = evidence.get("soft_text_collision_qa") or {}
    seen = set()
    for c in qa3.get("collision_details", []):
        if c.get("kind") != "severe_soft_soft_collision":
            continue
        for side in ("block_a", "block_b"):
            bb = c.get(side, {}).get("bbox")
            if bb and tuple(bb) not in seen:
                seen.add(tuple(bb))
                _rect(bb, (1, 0, 0), width=1.4)
    # prose-adopted formula ink regions -> orange
    for r in (evidence.get("final_source_residual_qa") or {}).get(
            "residual_details", []):
        if r.get("kind") == "vector_ink_source_residual":
            bb = r.get("bbox")
            if bb:
                _rect(bb, (1.0, 0.55, 0.0), width=1.2)
    shape.commit()
    out = RED_OUT / ("%s_p%03d_collision_overlay.png" % (doc, page))
    pix.save(str(out))
    doc_pdf.close()
    print("wrote", out)


def main():
    for doc, page in PAGES:
        build_overlay(doc, page)


if __name__ == "__main__":
    main()
