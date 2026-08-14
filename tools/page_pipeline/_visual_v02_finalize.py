"""_visual_v02_finalize -- visual-v02 checkpoint deliverables.

Per-page before/after boards + p001 fragment-map overlay + p014 bbox-vs-ink
overlay + aggregated QA JSONs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))
sys.path.insert(0, str(REPO / "实现源码" / "pdf_translator"))

OUT = REPO / "outputs" / "visual_v02_checkpoint"
P4E2A = REPO / "outputs" / "phase4e2a_qa_recovery"
DOCS = {
    "2504": {"pdf": "C:/Users/74496/Desktop/2504.05732v2.pdf",
             "src": P4E2A / "doc1", "pages": [1, 3, 6, 13, 14, 16]},
    "ppat": {"pdf": "C:/Users/74496/Desktop/PPAT.pdf",
             "src": P4E2A / "doc2", "pages": [4, 5, 6]},
}


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def _dump(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                       encoding="utf-8")


def _render_png(pdf, page_idx, out_png, dpi=110):
    doc = pymupdf.open(pdf)
    pix = doc[page_idx].get_pixmap(dpi=dpi)
    pix.save(str(out_png))
    doc.close()


def make_board(dkey, page, pdir):
    info = DOCS[dkey]
    model = _load(info["src"] / "pages" / ("p%03d" % page)
                  / "stitched_page_model.json", {})
    src_png = pdir / "source.png"
    _render_png(info["pdf"], page - 1, src_png)
    vis_png = pdir / "zh_visual.png"
    if not vis_png.exists():
        _render_png(str(pdir / "zh_visual.pdf"), 0, vis_png)
    board = pdir / "before_after_board.png"
    from PIL import Image, ImageDraw
    src_img = Image.open(src_png).convert("RGB")
    vis_img = Image.open(vis_png).convert("RGB")
    w = max(src_img.width, vis_img.width) * 2
    h = max(src_img.height, vis_img.height) + 40
    canvas = Image.new("RGB", (w, h), (250, 250, 250))
    d = ImageDraw.Draw(canvas)
    d.text((10, 8), "SOURCE", fill=(0, 0, 0))
    d.text((src_img.width + 10, 8), "VISUAL-v02 (fixed canvas + fragment restore + ink)", fill=(0, 0, 0))
    canvas.paste(src_img, (0, 40))
    canvas.paste(vis_img, (src_img.width, 40))
    canvas.save(str(board))
    return board


def make_p001_fragment_overlay(pdir):
    """p001 DLP00012-F0 region + its target char range."""
    model = _load(P4E2A / "doc1" / "pages" / "p001" / "stitched_page_model.json", {})
    out_png = pdir / "p001_fragment_map_overlay.png"
    _render_png(DOCS["2504"]["pdf"], 0, out_png)
    from PIL import Image, ImageDraw
    img = Image.open(out_png).convert("RGB")
    scale = img.width / float(model["width"])
    draw = ImageDraw.Draw(img, "RGBA")
    for r in model.get("regions", []):
        if r["type"] == "text" and r["payload"].get("paragraph_id") == "DLP00012":
            b = [v * scale for v in r["bbox"]]
            draw.rectangle(b, outline=(0, 0, 255, 255), width=3)
            draw.text((b[0], b[1] - 12), "DLP00012-F0 (p001, 291/1227 tgt)",
                      fill=(0, 0, 255, 255))
    for r in model.get("regions", []):
        if r["type"] == "formula":
            b = [v * scale for v in r["bbox"]]
            draw.rectangle(b, outline=(255, 40, 40, 255), width=2)
    img.save(str(out_png))
    return out_png


def make_p014_ink_overlay(pdir):
    """p014 DLP00182 raw bbox vs formula B12 ink vs source ink."""
    from PIL import Image, ImageDraw
    src_png = pdir / "source.png"
    if not src_png.exists():
        _render_png(DOCS["2504"]["pdf"], 13, src_png)
    img = Image.open(src_png).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    m = _load(P4E2A / "doc1" / "pages" / "p014" / "stitched_page_model.json", {})
    scale = img.width / float(m["width"])
    for r in m["regions"]:
        if r["type"] == "text" and r["payload"].get("paragraph_id") == "DLP00182":
            b = [v * scale for v in r["bbox"]]
            draw.rectangle(b, outline=(0, 200, 0, 255), width=3)
            draw.text((b[0], b[1] - 10), "DLP00182 ', and' (raw bbox)",
                      fill=(0, 200, 0, 255))
        if r["type"] == "formula":
            fm = r["payload"]
            lb = fm.get("layout_bbox") or []
            if len(lb) == 4 and 540 < lb[1] < 630:
                b = [v * scale for v in lb]
                draw.rectangle(b, outline=(255, 0, 0, 255), width=3)
                draw.text((b[0], b[1] - 10), "formula B12 (raw bbox)",
                          fill=(255, 0, 0, 255))
    # formula ink bbox (source raster)
    from source_ink_geometry import _raster_ink
    _, inkb = _raster_ink(DOCS["2504"]["pdf"], 13,
                          [362.6, 574.5, 459.9, 604.9], dpi=288)
    if inkb:
        b = [v * scale for v in inkb]
        draw.rectangle(b, outline=(255, 180, 0, 255), width=2)
        draw.text((b[0], b[3] + 4), "B12 source ink", fill=(255, 180, 0, 255))
    img.save(str(pdir / "p014_bbox_vs_ink_overlay.png"))
    return pdir / "p014_bbox_vs_ink_overlay.png"


def main():
    results = []
    anchor = {}
    region = {}
    expansion = {}
    execution = {}
    ink = {}
    for dkey, d in DOCS.items():
        for pg in d["pages"]:
            pdir = OUT / ("%s_p%03d" % (dkey, pg))
            b = _load(pdir / "visual_page_qa.json", {})
            if not b or b.get("error"):
                results.append({"doc": dkey, "page": pg, "passed": False,
                                "error": b.get("error")})
                continue
            results.append({"doc": dkey, "page": pg, "passed": b["passed"],
                            "hard_metrics": b["hard_metrics"]})
            key = "%s_p%03d" % (dkey, pg)
            anchor[key] = b["anchor_integrity"]
            region[key] = b["text_region"]
            expansion[key] = b["page_expansion"]
            execution[key] = b["execution_integrity"]
            ink[key] = b.get("source_ink_geometry", {})
            try:
                make_board(dkey, pg, pdir)
            except Exception as e:  # noqa: BLE001
                print("board fail", dkey, pg, e)
    try:
        make_p001_fragment_overlay(OUT / "2504_p001")
        make_p014_ink_overlay(OUT / "2504_p014")
    except Exception as e:  # noqa: BLE001
        print("overlay fail", e)

    totals = {}
    for key in ("anchor_displaced_count", "formula_blank_count",
                "formula_crop_count", "formula_duplicate_count",
                "formula_orphan_count", "foreign_text_inside_formula_count",
                "soft_text_region_overflow_count", "gutter_intrusion_count",
                "anchor_invasion_count", "capacity_unresolved_count",
                "unexpected_extra_page_count",
                "source_ink_budget_violation_count",
                "anchor_ink_displacement_count"):
        totals[key] = sum(r.get("hard_metrics", {}).get(key, 0)
                          for r in results)
    passed = [r for r in results if r["passed"]]
    blocked = [r for r in results if not r["passed"]]
    summary = {
        "schema_version": "visual_v02.checkpoint_summary.v1",
        "fixture_pages": len(results), "passed_pages": len(passed),
        "blocked_pages": len(blocked), "totals": totals,
        "per_page": results,
        "gate_decision": "pass" if (not blocked and
                                    all(v == 0 for v in totals.values()))
                         else "blocked",
    }
    _dump(OUT / "visual_anchor_integrity.json", anchor)
    _dump(OUT / "fixed_canvas_text_region_qa.json", region)
    _dump(OUT / "visual_page_expansion_qa.json", expansion)
    _dump(OUT / "execution_integrity.json", execution)
    _dump(OUT / "source_ink_geometry_qa.json", ink)
    _dump(OUT / "checkpoint_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
