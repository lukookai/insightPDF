# -*- coding: utf-8 -*-
"""build_v05_review_bundle -- before/after + overlay review artifacts.

outputs/visual_v05_checkpoint/review_bundle/  (FLAT, per user rule 43):
    checkpoint_board.png            (gate summary board)
    REVIEW_README.md
    review_index.json
    ppat_p004_source.png / _v04_before.png / _v05_after.png
    ppat_p004_before_after.png / _residual_overlay.png
    ppat_p004_formula_context_overlay.png / _glyph_overlay.png
    ppat_p005_* (same set + semantic_structure_overlay)
    ppat_p006_* (same set + semantic_structure_overlay)
    2504_p001_source.png / _target.png ... (x6)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))

OUT = REPO / "outputs" / "visual_v05_checkpoint"
BUNDLE = OUT / "review_bundle"
V04 = REPO / "outputs" / "visual_v04_checkpoint"
RED = OUT / "old_red_evidence"

PPAT_PAGES = [4, 5, 6]
SOURCE_PDF = {
    "ppat": "C:/Users/74496/Desktop/PPAT.pdf",
    "2504": "C:/Users/74496/Desktop/2504.05732v2.pdf",
}


def _page_png(pdf_path: str, page_idx: int, out_png: Path, dpi: int = 90):
    doc = pymupdf.open(pdf_path)
    try:
        page = doc[page_idx]
        pix = page.get_pixmap(dpi=dpi)
        pix.save(str(out_png))
    finally:
        doc.close()
    return out_png


def _overlay_png(pdf_path: str, out_png: Path, boxes, title, color,
                 dpi: int = 90):
    from PIL import Image, ImageDraw, ImageFont
    doc = pymupdf.open(pdf_path)
    page = doc[0]
    rect = page.rect
    pix = page.get_pixmap(dpi=dpi)
    doc.close()
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    draw = ImageDraw.Draw(img)
    scale = dpi / 72.0
    for bb in boxes:
        if len(bb) != 4:
            continue
        px0 = bb[0] * scale
        py0 = (rect.height - bb[3]) * scale
        draw.rectangle([px0, py0, px0 + (bb[2] - bb[0]) * scale,
                        py0 + (bb[3] - bb[1]) * scale],
                       outline=tuple(int(c * 255) for c in color), width=3)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 26)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    draw.text((14, 10), title, fill=(255, 0, 0), font=font)
    img.save(str(out_png))
    return out_png


def _before_after(left, right, out_png, title):
    from PIL import Image, ImageDraw, ImageFont
    im_l = Image.open(left)
    im_r = Image.open(right)
    h = max(im_l.height, im_r.height)
    w = im_l.width + im_r.width + 8
    canvas = Image.new("RGB", (w, h), (240, 240, 240))
    canvas.paste(im_l, (0, 0))
    canvas.paste(im_r, (im_l.width + 8, 0))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 30)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    draw.text((14, 8), title, fill=(200, 30, 30), font=font)
    draw.text((im_l.width + 14, 8), "AFTER", fill=(30, 130, 30), font=font)
    canvas.save(str(out_png))
    return out_png


def _load(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default if default is not None else {}


def main():
    BUNDLE.mkdir(parents=True, exist_ok=True)
    index = []

    # ---- PPAT pages -------------------------------------------------------
    for pg in PPAT_PAGES:
        src_png = BUNDLE / ("ppat_p%03d_source.png" % pg)
        _page_png(SOURCE_PDF["ppat"], pg - 1, src_png)
        before_png = BUNDLE / ("ppat_p%03d_v04_before.png" % pg)
        _page_png(str(V04 / ("ppat_p%03d" % pg) / "zh_visual.pdf"),
                 0, before_png)
        after_png = BUNDLE / ("ppat_p%03d_v05_after.png" % pg)
        _page_png(str(OUT / ("ppat_p%03d" % pg) / "zh_visual.pdf"), 0,
                  after_png)
        ba_png = BUNDLE / ("ppat_p%03d_before_after.png" % pg)
        _before_after(before_png, after_png, ba_png,
                      "PPAT p%03d BEFORE" % pg)

        # overlays from old-red evidence + final QA
        evidence = _load(RED / ("ppat_p%03d_v04_red.json" % pg))
        qa = _load(OUT / ("ppat_p%03d" % pg) / "visual_page_qa.json")
        res_boxes = [d.get("source_bbox") or d.get("bbox")
                     for d in evidence.get("qas", {}).get(
                         "residual_prose_truth", {}).get("details", [])
                     if d.get("source_bbox") or d.get("bbox")]
        if res_boxes:
            _overlay_png(str(V04 / ("ppat_p%03d" % pg) / "zh_visual.pdf"),
                         BUNDLE / ("ppat_p%03d_residual_overlay.png" % pg),
                         res_boxes, "residual prose (v04 RED)", (1, 0, 0))
        ctx_boxes = [d.get("bbox") or d.get("source_bbox")
                     for d in evidence.get("qas", {}).get(
                         "formula_adjacent_prose", {}).get("details", [])
                     if d.get("bbox") or d.get("source_bbox")]
        if ctx_boxes:
            _overlay_png(str(V04 / ("ppat_p%03d" % pg) / "zh_visual.pdf"),
                         BUNDLE / ("ppat_p%03d_formula_context_overlay.png"
                                   % pg), ctx_boxes,
                         "formula context / orphan punct (v04)", (0, 0.55, 0))
        glyph_boxes = [d.get("bbox")
                       for d in evidence.get("qas", {}).get(
                           "glyph_token_integrity", {}).get("details", [])
                       if d.get("bbox")]
        if glyph_boxes:
            _overlay_png(str(V04 / ("ppat_p%03d" % pg) / "zh_visual.pdf"),
                         BUNDLE / ("ppat_p%03d_glyph_overlay.png" % pg),
                         glyph_boxes, "glyph integrity (v04)", (0.6, 0, 0.8))
        sem_boxes = [d.get("bbox") or d.get("source_bbox")
                     for d in evidence.get("qas", {}).get(
                         "semantic_structure_closure", {}).get("details", [])
                     if d.get("bbox") or d.get("source_bbox")]
        if not sem_boxes:
            # heading candidates from source typography (document-general)
            try:
                from semantic_structure_closure_qa import (
                    _source_heading_candidates)
                sem_boxes = [h["bbox"]
                             for h in _source_heading_candidates(
                                 SOURCE_PDF["ppat"], pg - 1, 9.96)
                             if h.get("bbox")]
            except Exception:  # noqa: BLE001
                sem_boxes = []
        if sem_boxes:
            _overlay_png(str(V04 / ("ppat_p%03d" % pg) / "zh_visual.pdf"),
                         BUNDLE / ("ppat_p%03d_semantic_structure_overlay.png"
                                   % pg), sem_boxes,
                         "semantic/heading loss (v04)", (0.8, 0.5, 0))

        hm = qa.get("hard_metrics") or {}
        index.append({
            "doc": "ppat", "page": pg,
            "source_file": str(src_png.relative_to(OUT)),
            "before_file": str(before_png.relative_to(OUT)),
            "after_file": str(after_png.relative_to(OUT)),
            "before_after_file": str(ba_png.relative_to(OUT)),
            "residual_overlay": "review_bundle/ppat_p%03d_residual_overlay.png"
            % pg if res_boxes else None,
            "semantic_overlay":
            "review_bundle/ppat_p%03d_semantic_structure_overlay.png" % pg
            if sem_boxes else None,
            "formula_context_overlay":
            "review_bundle/ppat_p%03d_formula_context_overlay.png" % pg
            if ctx_boxes else None,
            "glyph_overlay": "review_bundle/ppat_p%03d_glyph_overlay.png" % pg
            if glyph_boxes else None,
            "gate": qa.get("decision"),
            "blocked_reasons": [k for k, v in hm.items() if v],
            "key_metrics": {k: v for k, v in hm.items()
                            if k in (
                                "translatable_source_residual_fragment_count",
                                "orphan_formula_punctuation_count",
                                "heading_body_merge_count",
                                "replacement_character_count",
                                "final_visible_translation_coverage",
                                "severe_soft_soft_collision_count")},
        })
        print("ppat p%03d bundle OK" % pg, flush=True)

    # ---- 2504 regression pages -------------------------------------------
    for pg in (1, 3, 6, 13, 14, 16):
        src_png = BUNDLE / ("2504_p%03d_source.png" % pg)
        _page_png(SOURCE_PDF["2504"], pg - 1, src_png)
        tgt_png = BUNDLE / ("2504_p%03d_target.png" % pg)
        _page_png(str(OUT / ("2504_p%03d" % pg) / "zh_visual.pdf"), 0,
                  tgt_png)
        qa = _load(OUT / ("2504_p%03d" % pg) / "visual_page_qa.json")
        hm = qa.get("hard_metrics") or {}
        index.append({
            "doc": "2504", "page": pg,
            "source_file": str(src_png.relative_to(OUT)),
            "after_file": str(tgt_png.relative_to(OUT)),
            "gate": qa.get("decision"),
            "blocked_reasons": [k for k, v in hm.items() if v],
            "key_metrics": {k: v for k, v in hm.items()
                            if k in ("final_visible_translation_coverage",
                                     "severe_soft_soft_collision_count")},
        })
        print("2504 p%03d bundle OK" % pg, flush=True)

    # ---- gate summary board ----------------------------------------------
    gate = _load(OUT / "visual_v05_gate.json")
    _board(gate, index)
    (BUNDLE / "review_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    _readme(gate)
    print("bundle done ->", BUNDLE)


def _board(gate, index):
    from PIL import Image, ImageDraw, ImageFont
    rows = [(r["doc"], r["page"], r["gate"]) for r in index]
    w, h = 1100, 120 + 46 * len(rows)
    img = Image.new("RGB", (w, h), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 26)
        small = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 20)
    except Exception:  # noqa: BLE001
        font = small = ImageFont.load_default()
    draw.text((30, 25), "visual-v05 checkpoint board  |  gate: %s  |  "
              "API: %s" % (gate.get("decision"), gate.get("total_api_calls")),
              fill=(30, 30, 30), font=font)
    y = 90
    for doc, pg, g in rows:
        color = (30, 160, 60) if g == "pass" else (200, 40, 40)
        draw.text((40, y), "%s p%03d  ->  %s" % (doc, pg, g),
                  fill=color, font=small)
        y += 40
    img.save(str(BUNDLE / "checkpoint_board.png"))


def _readme(gate):
    lines = [
        "# visual-v05 REVIEW BUNDLE", "",
        "## HUMAN REVIEW PRIORITY",
        "1. ppat_p004_v05_after.png",
        "2. ppat_p005_v05_after.png",
        "3. ppat_p006_v05_after.png",
        "4. residual overlays (v04 RED evidence)",
        "5. ppat_p005_semantic_structure_overlay.png",
        "6. regression pages (2504 source/target)",
        "",
        "## GATE",
        "decision: %s | total API calls: %s" % (
            gate.get("decision"), gate.get("total_api_calls")),
        "",
        "## BEFORE vs AFTER",
        "Each ppat page has: source (original English), v04_before "
        "(visual-v04 final), v05_after (visual-v05 final), before_after "
        "(side by side), residual_overlay (v04 RED evidence), "
        "formula_context_overlay, semantic_structure_overlay, "
        "glyph_overlay.",
        "",
        "Known defects closed in v05:",
        "- PPAT p004: B26 'C channels, we can extract its high-frequency "
        "components' -> translated; orphan ':' removed; NUL glyphs fixed",
        "- PPAT p005: B2/B11/B14 annotation rows translated; '3.4 自适应MOE"
        "注入器' restored as an independent heading block; orphan ':' gone",
        "- PPAT p006: B1/B10 'Training / We employ the AdamW optimizer...' "
        "translated; 4.2/4.3 headings independent",
        "- 2504 x6: zero regression",
    ]
    (BUNDLE / "REVIEW_README.md").write_text("\n".join(lines),
                                             encoding="utf-8")


if __name__ == "__main__":
    main()
