"""_visual_v03_finalize -- visual-v03 deliverables.

Per-page boards, p014 source/target group overlays, aggregated QA JSONs,
visual-group probes inventory.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pymupdf  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))
sys.path.insert(0, str(REPO / "实现源码" / "pdf_translator"))

OUT = REPO / "outputs" / "visual_v03_checkpoint"
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
    d.text((src_img.width + 10, 8), "VISUAL-v03 (visual grouping)", fill=(0, 0, 0))
    canvas.paste(src_img, (0, 40))
    canvas.paste(vis_img, (src_img.width, 40))
    canvas.save(str(board))
    return board


def make_p014_group_overlays(pdir):
    """p014 Figure 6 caption group: source member bboxes + group union +
    target caption block + figure bbox."""
    from PIL import Image, ImageDraw
    m = _load(P4E2A / "doc1" / "pages" / "p014" / "stitched_page_model.json", {})
    src_png = pdir / "source.png"
    _render_png(DOCS["2504"]["pdf"], 13, src_png)
    scale = Image.open(src_png).width / float(m["width"])
    img = Image.open(src_png).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    for r in m["regions"]:
        if r["type"] == "text" and r["payload"].get("paragraph_id") in (
                "DLP00171", "DLP00178"):
            b = [v * scale for v in r["bbox"]]
            draw.rectangle(b, outline=(0, 200, 0, 255), width=3)
            draw.text((b[0], b[1] - 10),
                      r["payload"]["paragraph_id"], fill=(0, 200, 0, 255))
        if r["type"] == "figure":
            b = [v * scale for v in r["bbox"]]
            draw.rectangle(b, outline=(255, 120, 0, 255), width=3)
            draw.text((b[0], b[1] - 10), "FIG1", fill=(255, 120, 0, 255))
    # group union bbox
    ub = [150.0, 370.3, 445.3, 380.2]
    b = [v * scale for v in ub]
    draw.rectangle(b, outline=(0, 0, 255, 255), width=2)
    draw.text((b[0], b[3] + 2), "caption group union (full-width)",
              fill=(0, 0, 255, 255))
    img.save(str(pdir / "source_visual_group_overlay.png"))
    # target overlay: merged caption block position
    img2 = Image.open(src_png).convert("RGB")
    draw2 = ImageDraw.Draw(img2, "RGBA")
    # target caption renders on union bbox as one full-width block
    b = [v * scale for v in ub]
    draw2.rectangle(b, outline=(0, 0, 255, 255), width=3)
    draw2.text((b[0], b[1] - 10), "TARGET caption block (merged)",
               fill=(0, 0, 255, 255))
    draw2.rectangle([v * scale for v in [81.0, 72.9, 514.0, 359.9]],
                    outline=(255, 120, 0, 255), width=2)
    img2.save(str(pdir / "target_visual_group_overlay.png"))
    return pdir


def collect_probes():
    """Auto-retrieve visual-group probes across both PDFs."""
    from source_visual_group import build_source_visual_groups
    fig, tbl, fw = [], [], []
    for dkey, npages, dd in (("2504", 19, "doc1"), ("ppat", 12, "doc2")):
        for pg in range(1, npages + 1):
            p = P4E2A / dd / "pages" / ("p%03d" % pg) / "stitched_page_model.json"
            if not p.exists():
                continue
            m = _load(p, {})
            g = _load(P4E2A / dd / "pages" / ("p%03d" % pg)
                      / "page_grid.json", {})
            groups = build_source_visual_groups(
                m, content_frame=g.get("content_frame"),
                gutter=g.get("gutter"))
            for gr in groups:
                if gr.confidence < 0.55:
                    continue
                d = gr.to_dict()
                entry = {"doc": dkey, "page": pg, "group_id": d["group_id"],
                         "type": d["group_type"],
                         "topology": d["expected_topology"],
                         "members": d["member_paragraph_ids"],
                         "confidence": d["confidence"]}
                if "figure" in d["group_type"]:
                    fig.append(entry)
                elif "table" in d["group_type"]:
                    tbl.append(entry)
                if d["expected_topology"] == "full_width":
                    fw.append(entry)
    # dedupe by (page, frozenset members)
    def _dedupe(items):
        seen, out = set(), []
        for e in items:
            k = (e["page"], e["type"], frozenset(e["members"]))
            if k in seen:
                continue
            seen.add(k)
            out.append(e)
        return out
    return {"figure_caption_groups": _dedupe(fig),
            "table_caption_groups": _dedupe(tbl),
            "full_width_groups": _dedupe(fw)}


def main():
    results = []
    group_qa = {}
    topo_qa = {}
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
            group_qa[key] = b.get("source_visual_group", {})
            topo_qa[key] = b.get("source_target_topology", {})
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
        make_p014_group_overlays(OUT / "2504_p014")
    except Exception as e:  # noqa: BLE001
        print("p014 overlay fail", e)

    probes = collect_probes()
    _dump(OUT / "visual_group_probes.json", probes)
    _dump(OUT / "source_visual_groups.json", {
        "schema_version": "visual_v03.source_visual_groups.v1",
        "per_page": {k: v.get("groups", []) for k, v in group_qa.items()},
    })
    _dump(OUT / "source_visual_group_qa.json", group_qa)
    _dump(OUT / "source_target_topology_qa.json", topo_qa)
    _dump(OUT / "visual_anchor_integrity.json", anchor)
    _dump(OUT / "fixed_canvas_text_region_qa.json", region)
    _dump(OUT / "visual_page_expansion_qa.json", expansion)
    _dump(OUT / "execution_integrity.json", execution)
    _dump(OUT / "source_ink_geometry_qa.json", ink)

    totals = {}
    keys = ("anchor_displaced_count", "formula_blank_count",
            "formula_crop_count", "formula_duplicate_count",
            "formula_orphan_count", "foreign_text_inside_formula_count",
            "soft_text_region_overflow_count", "gutter_intrusion_count",
            "anchor_invasion_count", "capacity_unresolved_count",
            "unexpected_extra_page_count", "source_ink_budget_violation_count",
            "anchor_ink_displacement_count", "visual_group_split_count",
            "caption_role_fragmentation_count",
            "full_width_group_topology_violation_count",
            "group_target_drop_count", "group_target_duplicate_count",
            "group_member_order_inversion_count")
    for k in keys:
        totals[k] = sum(r.get("hard_metrics", {}).get(k, 0) for r in results)
    passed = [r for r in results if r["passed"]]
    summary = {
        "schema_version": "visual_v03.checkpoint_summary.v1",
        "fixture_pages": len(results), "passed_pages": len(passed),
        "blocked_pages": len(results) - len(passed), "totals": totals,
        "gate_decision": "pass" if (len(passed) == len(results)
                                    and all(v == 0 for v in totals.values()))
                         else "blocked",
    }
    _dump(OUT / "checkpoint_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
