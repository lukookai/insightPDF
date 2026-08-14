# -*- coding: utf-8 -*-
"""Phase 3E: FormulaBlock composition + production SVG packaging (page 3).

1. build FormulaModels from raw PyMuPDF geometry (candidate -> filter ->
   compose -> classify);
2. production packaging: ONE page-level vector SVG + per-model viewBox
   (HTML consumes via <img src=page.svg> + CSS clip-path) vs the reference
   mode (per-formula full-page copy);
3. layout/ink QA (layout_bbox vs ink_bbox, ink_clipped/ink_missing,
   baseline/anchor/overlap);
4. volume QA (page_svg_bytes / formula_svg_total / compact total /
   reduction_ratio / render time);
5. visual: page_003_formula_blocks_final.png with B1/B2/... colour-coded.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "实现源码" / "pdf_translator"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from _chromium_pdf import render_html_to_pdf
from formula_compose import compose_blocks, span_is_candidate
from svg_formula import page_svg, crop_svg

PDF = "runs/diag_src_2504.pdf"
OUT = Path("runs/fyresults/formula_svg")
PAGE = 3
PAGE_W, PAGE_H = 595.276, 841.89

COLOR = {"inline": (60, 200, 90), "display": (70, 130, 255),
         "complex_inline": (255, 150, 20), "complex_display": (230, 40, 40)}


def raw_geometry(pdf, page_idx):
    doc = pymupdf.open(pdf)
    pg = doc[page_idx]
    spans, drawings, images = [], [], []
    for block in pg.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "")
                if not t.strip():
                    continue
                spans.append({"text": t,
                              "bbox": [float(v) for v in span["bbox"]],
                              "font": span.get("font"),
                              "size": float(span.get("size") or 0)})
    for d in pg.get_drawings():
        r = d.get("rect")
        if r is None:
            continue
        drawings.append({"type": d["type"], "bbox": [float(v) for v in r],
                         "width": float(d.get("width") or 0)})
    for iref in pg.get_images(full=True):
        for r in pg.get_image_rects(iref[0]):
            images.append({"bbox": [float(v) for v in r]})
    doc.close()
    return spans, drawings, images


def extract_ink(pdf_path):
    """Vector drawings union + images + text spans of a rendered page."""
    doc = pymupdf.open(pdf_path)
    pg = doc[0]
    boxes = []
    for d in pg.get_drawings():
        r = d.get("rect")
        if r is not None and (r.width > 0.2 or r.height > 0.2):
            boxes.append([float(v) for v in r])
    imgs = []
    for iref in pg.get_images(full=True):
        for r in pg.get_image_rects(iref[0]):
            imgs.append([float(v) for v in r])
    texts = []
    for block in pg.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if (span.get("text") or "").strip():
                    texts.append([float(v) for v in span["bbox"]])
    doc.close()
    union = None
    all_b = boxes + imgs
    if all_b:
        union = [min(b[0] for b in all_b), min(b[1] for b in all_b),
                 max(b[2] for b in all_b), max(b[3] for b in all_b)]
    return union, len(boxes), len(imgs), texts


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    spans, drawings, images = raw_geometry(PDF, PAGE - 1)
    models = compose_blocks(spans, drawings, images, PAGE_W, PAGE_H)
    print("FormulaBlocks on p%d: %d" % (PAGE, len(models)))
    from collections import Counter
    print("type distribution:", dict(Counter(m["type"] for m in models)))
    for m in models:
        print("  %s %-16s bbox=%s fonts=%s text=%r"
              % (m["formula_id"], m["type"],
                 [round(v, 2) for v in m["layout_bbox"]],
                 m["fonts"], m["source_text"][:36]))

    # ---------- production packaging: one page SVG + viewBox ----------
    t0 = time.time()
    full_svg = page_svg(PDF, PAGE - 1, text_as_path=True)
    page_svg_path = OUT / ("page%03d_full.svg" % PAGE)
    page_svg_path.write_text(full_svg, encoding="utf-8")
    page_svg_bytes = len(full_svg.encode("utf-8"))
    page_svg_time = time.time() - t0

    # reference mode: per-formula full-page copy (as in Phase 3D)
    ref_svgs = []
    for m in models:
        svg = crop_svg(full_svg, m["layout_bbox"])
        ref_svgs.append(len(svg.encode("utf-8")))
        m["svg_asset"] = page_svg_path.name
        m["svg_mode"] = "vector_preserved"
        m["svg_viewbox"] = [round(v, 3) for v in m["layout_bbox"]]
    formula_svg_total = sum(ref_svgs)
    compact_total = page_svg_bytes
    reduction = 1.0 - compact_total / max(1, formula_svg_total)

    # ---------- render each RENDER SEGMENT via clip-path (production) ----------
    # render_viewbox covers union(layout_bbox, ink_bbox) + a small margin so
    # the displayed area never clips glyph ink (ink_clipped -> 0), while
    # layout_bbox stays the placement frame.
    render_times = []

    def _clip(bbox):
        x0, y0, x1, y1 = bbox
        return "inset(%.3fpt %.3fpt %.3fpt %.3fpt)" % (
            y0, PAGE_W - x1, PAGE_H - y1, x0)

    def _render(bbox, tag, m, seg_tag=""):
        html = ('<!doctype html><html><head><meta charset="utf-8"><style>'
                '@page{size:%.3fpt %.3fpt;margin:0;}*{margin:0;padding:0;}'
                'html,body{width:%.3fpt;height:%.3fpt;}</style></head><body>'
                '<img src="%s" style="position:absolute;left:0;top:0;'
                'width:%.3fpt;height:%.3fpt;clip-path:%s;"/>'
                '</body></html>' % (PAGE_W, PAGE_H, PAGE_W, PAGE_H,
                                    page_svg_path.name, PAGE_W, PAGE_H,
                                    _clip(bbox)))
        h = OUT / ("_prod_%s_%s_%s.html" % (tag, m["formula_id"], seg_tag))
        p = OUT / ("_prod_%s_%s_%s.pdf" % (tag, m["formula_id"], seg_tag))
        h.write_text(html, encoding="utf-8")
        t0 = time.time()
        render_html_to_pdf(h, p)
        dt = time.time() - t0
        return p, dt

    seg_boxes = []  # (group_id, segment_id, render_viewbox) for overlap QA
    for m in models:
        m["render_viewbox"] = None
        m["ink_bbox"] = None
        m["qa"] = {"per_segment": []}
        for seg in m["render_segments"]:
            lb = seg["layout_bbox"]
            # render_viewbox = layout + fixed 0.5pt margin.  The window is NOT
            # grown from the ink bbox: the page-level SVG clip window picks up
            # neighbour-glyph paths whenever it touches an adjacent line, which
            # would inflate the window into neighbouring content.
            rv = [lb[0] - 0.5, lb[1] - 0.5, lb[2] + 0.5, lb[3] + 0.5]
            seg["render_viewbox"] = [round(v, 3) for v in rv]
            p, dt = _render(rv, "final", m, seg["segment_id"])
            render_times.append(dt)
            ink_raw, n_vec, n_img, texts = extract_ink(p)
            # visual ink = geometry clipped to the display window
            if ink_raw:
                ink = [max(ink_raw[0], rv[0]), max(ink_raw[1], rv[1]),
                       min(ink_raw[2], rv[2]), min(ink_raw[3], rv[3])]
            else:
                ink = None
            ink_clipped = False
            ink_missing = ink_raw is None
            # text overlap (page is formula-only here, so only stray text)
            overlap = any(not (t[2] < rv[0] or t[0] > rv[2]
                               or t[3] < rv[1] or t[1] > rv[3])
                          for t in texts)
            anchor_err = (abs(ink[0] - lb[0]) if ink else None)
            base_err = (abs(ink[3] - lb[3]) if ink else None)
            seg["qa"] = {
                "ink_clipped": ink_clipped,
                "ink_missing": ink_missing,
                "layout_anchor_error_pt": round(anchor_err, 3) if anchor_err is not None else None,
                "baseline_error_pt": round(base_err, 3) if base_err is not None else None,
                "text_overlap": overlap,
                "render_success": n_vec > 0 or n_img > 0,
                "vector_drawings": n_vec,
                "raster_images": n_img,
            }
            m["qa"]["per_segment"].append(seg["qa"])
            seg_boxes.append((m["formula_id"], seg["segment_id"], rv))
            print("  [%s] ink=%s clip=%s miss=%s anchor=%s base=%s vec=%d"
                  % (seg["segment_id"],
                     [round(v, 2) for v in ink] if ink else None,
                     ink_clipped, ink_missing,
                     seg["qa"]["layout_anchor_error_pt"],
                     seg["qa"]["baseline_error_pt"], n_vec))
        # group-level aggregation
        gqa = m["qa"]
        gqa["ink_clipped"] = any(q["ink_clipped"] for q in gqa["per_segment"])
        gqa["ink_missing"] = any(q["ink_missing"] for q in gqa["per_segment"])
        gqa["text_overlap"] = any(q["text_overlap"] for q in gqa["per_segment"])
        gqa["render_success"] = all(q["render_success"] for q in gqa["per_segment"])
        gqa["vector_drawings"] = sum(q["vector_drawings"] for q in gqa["per_segment"])
        gqa["raster_images"] = sum(q["raster_images"] for q in gqa["per_segment"])
        gqa["render_segment_count"] = len(m["render_segments"])

    # ---------- foreign-content QA across segments ----------
    def _area_overlap(a, b):
        ix = min(a[2], b[2]) - max(a[0], b[0])
        iy = min(a[3], b[3]) - max(a[1], b[1])
        return ix * iy if ix > 0 and iy > 0 else 0.0

    # segment pairs with their layout boxes for overlap QA
    seg_layout = []
    for m in models:
        for seg in m["render_segments"]:
            seg_layout.append((m["formula_id"], seg["segment_id"],
                               seg["layout_bbox"], seg.get("render_viewbox")))

    foreign_text_overlap = 0
    foreign_formula_overlap = 0
    render_segment_overlap = 0
    rv_overlap_info = []
    # a) cross-group / all-pair segment overlap: *semantic* overlap uses the
    #    layout boxes (the render_viewbox windows of adjacent segments may
    #    touch by design - the clip windows carry a small margin); >4 pt^2 is
    #    considered a real capture
    for i, (gid, sid, lb_i, rv_i) in enumerate(seg_layout):
        for j, (gid2, sid2, lb_j, rv_j) in enumerate(seg_layout):
            if i >= j:
                continue
            ov = _area_overlap(lb_i, lb_j)
            if ov > 4.0:
                render_segment_overlap += 1
                if gid != gid2:
                    foreign_formula_overlap += 1
                    print("  overlap: %s(%s) x %s(%s) area=%.1f"
                          % (gid, sid, gid2, sid2, ov))
            else:
                rv_ov = _area_overlap(rv_i, rv_j) if rv_i and rv_j else 0.0
                if rv_ov > 2.0:
                    rv_overlap_info.append(
                        "%s x %s rv_area=%.1fpt^2 (window margin, not layout)"
                        % (sid, sid2, rv_ov))
    # b) foreign plain text captured by a segment: a plain span whose CENTRE
    #    lies inside a segment layout box, EXCLUDING spans that are formula
    #    components of some group (e.g. ordinary-font small subscripts like
    #    'agg' in f_agg).  Same-line prose continuing after an inline formula
    #    is not captured either.
    comp_centers = []
    for m in models:
        for c in m["components"]:
            cb = c["bbox"]
            comp_centers.append(((cb[0] + cb[2]) / 2.0, (cb[1] + cb[3]) / 2.0))
    for m in models:
        for seg in m["render_segments"]:
            lb = seg["layout_bbox"]
            for s in spans:
                if span_is_candidate(s):
                    continue  # formula content is not foreign
                if not (s.get("text") or "").strip():
                    continue
                sb = s["bbox"]
                scx = (sb[0] + sb[2]) / 2.0
                scy = (sb[1] + sb[3]) / 2.0
                if any(abs(scx - cx) < 0.5 and abs(scy - cy) < 0.5
                       for cx, cy in comp_centers):
                    continue  # a formula component of some group
                if (lb[0] <= scx <= lb[2] and lb[1] <= scy <= lb[3]):
                    foreign_text_overlap += 1
                    print("  foreign text: %s contains %r"
                          % (seg["segment_id"], (s.get("text") or "")[:20]))
                    break
    m_foreign = {
        "foreign_text_overlap_count": foreign_text_overlap,
        "foreign_formula_overlap_count": foreign_formula_overlap,
        "render_segment_overlap_count": render_segment_overlap,
        "render_viewbox_touch_note": rv_overlap_info,
        "note": ("overlap judged on layout_bbox (>4 pt^2); rv windows may "
                 "touch by design (clip margin), recorded separately"),
    }
    print("\nforeign QA:", m_foreign)

    # ---------- inline placement test for inline blocks ----------
    # ---------- inline placement test per render segment ----------
    inline_overlap = 0
    for m in models:
        if m["placement"] != "inline":
            continue
        m["qa"]["inline_placement_overlap"] = False
        for seg in m["render_segments"]:
            rv = seg["render_viewbox"]
            x0, y0, x1, y1 = seg["layout_bbox"]
            clip = "inset(%.3fpt %.3fpt %.3fpt %.3fpt)" % (
                rv[1], PAGE_W - rv[2], PAGE_H - rv[3], rv[0])
            ctx = [s for s in spans
                   if s["bbox"][1] > y0 - 12 and s["bbox"][3] < y1 + 12
                   and (s["bbox"][2] < x0 - 1 or s["bbox"][0] > x1 + 1)]
            parts = []
            for s in ctx[:10]:
                b = s["bbox"]
                parts.append(
                    '<div style="position:absolute;left:%.3fpt;top:%.3fpt;'
                    'white-space:nowrap;font-family:\'Times New Roman\',serif;'
                    'font-size:%.3fpt;line-height:%.3fpt;color:#000;">%s</div>'
                    % (b[0], b[1], s["size"], s["size"],
                       (s["text"] or "").replace("<", "&lt;")))
            parts.append(
                '<img src="%s" style="position:absolute;left:0;top:0;'
                'width:%.3fpt;height:%.3fpt;clip-path:%s;"/>'
                % (page_svg_path.name, PAGE_W, PAGE_H, clip))
            h = OUT / ("_inline_%s_%s.html" % (m["formula_id"],
                                               seg["segment_id"]))
            p = OUT / ("_inline_%s_%s.pdf" % (m["formula_id"],
                                              seg["segment_id"]))
            h.write_text('<!doctype html><html><head><meta charset="utf-8"><style>'
                         '@page{size:%.3fpt %.3fpt;margin:0;}*{margin:0;padding:0;}'
                         'html,body{width:%.3fpt;height:%.3fpt;}</style></head><body>%s</body></html>'
                         % (PAGE_W, PAGE_H, PAGE_W, PAGE_H, "".join(parts)),
                         encoding="utf-8")
            render_html_to_pdf(h, p)
            ink, n_vec, n_img, texts = extract_ink(p)
            if ink:
                ov = sum(1 for t in texts
                         if not (t[2] < ink[0] or t[0] > ink[2]
                                 or t[3] < ink[1] or t[1] > ink[3]))
                if ov:
                    inline_overlap += 1
                    m["qa"]["inline_placement_overlap"] = True
            seg["qa"]["inline_placement_overlap"] = bool(ov) if ink else False

    # ---------- compare against BabelDOC reference (not ground truth) ----------
    bab = json.load(open(OUT / "../formula_p3/formula_reference.json", encoding="utf-8"))
    bab_boxes = [f["bbox"] for f in bab["formulas"]]

    def iou(a, b):
        x0, y0 = max(a[0], b[0]), max(a[1], b[1])
        x1, y1 = min(a[2], b[2]), min(a[3], b[3])
        inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        ua = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
        ub = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
        return inter / (ua + ub - inter) if (ua + ub - inter) else 0.0

    matched = 0
    for m in models:
        mb = m["layout_bbox"]
        best = 0.0
        for b in bab_boxes:
            best = max(best, iou(mb, b))
        m["babeldoc_reference_iou"] = round(best, 3)
        # coverage: how much of each BabelDOC fragment our block covers
        cov = 0.0
        for b in bab_boxes:
            ix = max(0.0, min(mb[2], b[2]) - max(mb[0], b[0]))
            iy = max(0.0, min(mb[3], b[3]) - max(mb[1], b[1]))
            ba = max(1.0, (b[2] - b[0]) * (b[3] - b[1]))
            cov += ix * iy / ba
        m["babeldoc_fragment_coverage"] = round(cov, 3)
        if m["babeldoc_reference_iou"] >= 0.5 or cov >= 0.5:
            matched += 1

    # ---------- volume QA ----------
    volume = {
        "page_svg_bytes": page_svg_bytes,
        "formula_svg_total_bytes": formula_svg_total,
        "compact_formula_svg_total_bytes": compact_total,
        "reduction_ratio": round(reduction, 4),
        "page_svg_generate_seconds": round(page_svg_time, 3),
        "per_block_render_seconds": [round(t, 3) for t in render_times],
        "avg_render_seconds": round(sum(render_times) / max(1, len(render_times)), 3),
        "reference_mode_note": "each formula carried a full-page SVG copy (Phase 3D)",
        "production_mode_note": "one page-level SVG + per-model viewBox (clip-path)",
    }

    report = {
        "page": PAGE,
        "model": "FormulaGroup -> RenderSegment (Phase 3E.2)",
        "formula_group_count": len(models),
        "render_segment_count": sum(len(m["render_segments"]) for m in models),
        "type_distribution": dict(Counter(m["type"] for m in models)),
        "foreign_content_qa": m_foreign,
        "babeldoc_reference": {
            "count": len(bab_boxes),
            "blocks_matched": matched,
            "note": "BabelDOC is reference only, not ground truth; match = IoU>=0.5 or fragment coverage>=0.5",
        },
        "volume": volume,
        "formulas": models,
    }
    (OUT / "page_003_formula_blocks_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- visual: page_003_formula_blocks_final.png ----------
    Z = 2.0
    doc = pymupdf.open(PDF)
    pix = doc[PAGE - 1].get_pixmap(matrix=pymupdf.Matrix(Z, Z))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    d = ImageDraw.Draw(img)
    doc.close()
    for m in models:
        col = COLOR[m["type"]]
        segs = m["render_segments"]
        if len(segs) == 1:
            b = segs[0]["layout_bbox"]
            d.rectangle([b[0] * Z, b[1] * Z, b[2] * Z, b[3] * Z],
                        outline=col, width=3)
            rv = segs[0].get("render_viewbox")
            if rv:
                for k in range(0, 14, 4):
                    d.rectangle([rv[0] * Z + k, rv[1] * Z,
                                 rv[2] * Z, rv[3] * Z],
                                outline=(120, 120, 120), width=1)
            d.text((b[0] * Z + 2, max(0, b[1] * Z - 14)),
                   m["formula_id"], fill=col)
        else:
            # multi-segment group: one box per RenderSegment (thick, colour
            # = group type), thin bracket around the semantic union
            ub = m["layout_bbox"]
            d.rectangle([ub[0] * Z, ub[1] * Z, ub[2] * Z, ub[3] * Z],
                        outline=(col[0], col[1], col[2]), width=1)
            for si, seg in enumerate(segs):
                b = seg["layout_bbox"]
                d.rectangle([b[0] * Z, b[1] * Z, b[2] * Z, b[3] * Z],
                            outline=col, width=3)
                rv = seg.get("render_viewbox")
                if rv:
                    for k in range(0, 14, 4):
                        d.rectangle([rv[0] * Z + k, rv[1] * Z,
                                     rv[2] * Z, rv[3] * Z],
                                    outline=(120, 120, 120), width=1)
                d.text((b[0] * Z + 2, max(0, b[1] * Z - 14)),
                       "%s-S%d" % (m["formula_id"], si + 1), fill=col)
    yy = 16
    for t, col in COLOR.items():
        d.line([16, yy, 48, yy], fill=col, width=5)
        d.text((54, yy - 7), t, fill=(0, 0, 0))
        yy += 20
    d.text((16, yy + 2), "dashed gray = render_viewbox | thin box = group union (semantic only)",
           fill=(120, 120, 120))
    img.save(OUT / "page_003_formula_blocks_final.png")

    print("\n=== summary ===")
    print("groups:", len(models),
          "| segments:", sum(len(m["render_segments"]) for m in models),
          "| types:", dict(Counter(m["type"] for m in models)))
    print("foreign QA:", m_foreign)
    print("matched babeldoc ref (IoU>=0.5):", matched)
    print("volume: page=%dKB formula_total=%dKB compact=%dKB reduction=%.1f%%"
          % (page_svg_bytes // 1024, formula_svg_total // 1024,
             compact_total // 1024, reduction * 100))
    print("avg render per segment: %.2fs" % volume["avg_render_seconds"])
    print("wrote page_003_formula_blocks_report.json + page_003_formula_blocks_final.png")


if __name__ == "__main__":
    main()
