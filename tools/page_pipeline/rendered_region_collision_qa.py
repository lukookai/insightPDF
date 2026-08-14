"""RenderedRegionCollisionQA (Phase 4C.2R).

Input is the FINAL rendered zh.pdf -- not the PageModel, not the HTML DOM,
not source bboxes.  This layer answers one question: in the physical PDF,
does any translated ParagraphBlock text actually overlap a Formula / Table /
Figure region?

Why a separate layer from FormulaExclusivityQA:
- FormulaExclusivityQA verifies *ownership* (which source components belong
  to which FormulaGroup, whether the same glyph appears twice).
- This layer verifies *rendered placement*: the display formula is
  geometry-locked at its source y while the translated paragraph grows and
  flows downwards, so the paragraph can physically enter the formula's
  enclosure.  That is exactly the p11/p14/p15/p16 failure.

Formula physical placement source of truth:
  The formula-seg <img> tags in zh.html carry data-layout="x0,y0,x1,y1"
  and are absolutely positioned at left:0 top:0 with a clip-path -- i.e.
  the layout coordinates ARE the physical PDF coordinates.  (The full-page
  SVG may be embedded either as an image or as vector drawings; relying on
  get_images() misses the vector case, so the HTML layout is the robust
  coordinate source.)

Threshold (Section 2):
  intersection_area > max(2 pt^2, 5% * min(text_area, formula_area))
  counts as a rendered collision.  Tuned so p011/p14/p15/p16 pre-fix are
  caught and normal inline-formula adjacency is not.

Outputs:
  rendered_region_collision_qa.json
  page_0NN_collision_overlay.png   (green=text bbox, orange=formula
                                    enclosure, red=intersection)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf

COLLISION_MIN_AREA = 2.0        # pt^2
COLLISION_RATIO = 0.05          # 5% of the smaller box
CLIP_CLUSTER_MIN = 15           # glyphs per 10pt y-band => candidate leak
CLIP_VISIBLE_INK = 1.0          # % ink density to confirm a visible leak


def _overlap_area(a, b):
    x = min(a[2], b[2]) - max(a[0], b[0])
    y = min(a[3], b[3]) - max(a[1], b[1])
    if x <= 0 or y <= 0:
        return 0.0
    return x * y


def _box_area(b):
    return max(b[2] - b[0], 0.0) * max(b[3] - b[1], 0.0)


def _collides(text_bbox, region_bbox):
    """Section 2 threshold: intersection > max(2pt^2, 5%*min(area))."""
    ia = _overlap_area(text_bbox, region_bbox)
    if ia <= 0:
        return False, 0.0
    ta = _box_area(text_bbox)
    ra = _box_area(region_bbox)
    if ta <= 0 or ra <= 0:
        return False, 0.0
    return ia > max(COLLISION_MIN_AREA, COLLISION_RATIO * min(ta, ra)), ia


# --------------------------------------------------------------------------
# formula physical enclosures from zh.html (data-layout = physical coords)
# --------------------------------------------------------------------------
_LAYOUT_RE = re.compile(
    r'data-formula="([^"]+)"[^>]*data-layout="([\d.,-]+)"')
_EQNUM_RE = re.compile(r"\((\d{1,2})\)")


def formula_physical_enclosures(html_path):
    """{formula_id: [x0,y0,x1,y1]} -- union of all its segment layouts."""
    html = Path(html_path).read_text(encoding="utf-8")
    boxes = {}
    for fid, lay in _LAYOUT_RE.findall(html):
        try:
            x0, y0, x1, y1 = [float(v) for v in lay.split(",")]
        except ValueError:
            continue
        b = boxes.get(fid)
        if b is None:
            boxes[fid] = [x0, y0, x1, y1]
        else:
            boxes[fid] = [min(b[0], x0), min(b[1], y0),
                          max(b[2], x1), max(b[3], y1)]
    return boxes


def _pdf_text_spans(pdf_path):
    doc = pymupdf.open(str(pdf_path))
    page = doc[0]
    spans = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "").strip()
                if not t:
                    continue
                spans.append({
                    "text": t,
                    "bbox": [float(v) for v in span["bbox"]],
                    "font": span.get("font") or "",
                    "size": span.get("size", 0.0),
                })
    doc.close()
    return spans


def rendered_region_collision_qa(pdf_path, html_path, page_model=None,
                                 out_dir=None, page_label="page", flows=None):
    """Run the final-PDF collision audit.

    Returns a dict with counts, per-formula records and a hard gate flag.
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir) if out_dir else pdf_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    formula_boxes = formula_physical_enclosures(html_path)

    # table / figure physical boxes come from the (geometry locked) model --
    # tables and figures ARE absolute geometry_locked, so source bbox ==
    # physical bbox for them.  Table CELL boxes are collected so that a
    # table's own cell content is not counted as a collision (a cell span
    # is the table rendering itself, not a translated paragraph intruding).
    table_boxes = []
    table_cell_boxes = []
    figure_boxes = []
    if page_model:
        for region in page_model.get("regions", []):
            rtype = region.get("type")
            b = region.get("bbox") or (region.get("payload") or {}).get(
                "layout_bbox")
            if rtype == "table" and b:
                table_boxes.append([float(v) for v in b])
                pl = region.get("payload") or {}
                for cell in pl.get("cells", []) or []:
                    cb = cell if isinstance(cell, list) else cell.get("bbox")
                    if cb and len(cb) == 4:
                        table_cell_boxes.append([float(v) for v in cb])
            elif rtype == "figure" and b:
                figure_boxes.append([float(v) for v in b])

    spans = _pdf_text_spans(pdf_path)

    def _fully_inside(tb, bb, margin=0.5):
        return (tb[0] >= bb[0] - margin and tb[2] <= bb[2] + margin
                and tb[1] >= bb[1] - margin and tb[3] <= bb[3] + margin)

    def in_table_cell(tb):
        # A span fully inside a table box is the table's own rendering
        # (TableModel cell content) -- NOT a translated paragraph intruding.
        for tb2 in table_boxes:
            if _fully_inside(tb, tb2):
                return True
        cx = (tb[0] + tb[2]) / 2.0
        cy = (tb[1] + tb[3]) / 2.0
        for cb in table_cell_boxes:
            if cb[0] - 1 <= cx <= cb[2] + 1 and cb[1] - 1 <= cy <= cb[3] + 1:
                return True
        return False

    text_formula = []   # (formula_id, span_text, overlap_area)
    text_table = []
    text_figure = []
    formula_formula = []
    eqnum_orphans = []

    for s in spans:
        tb = s["bbox"]
        for fid, fb in formula_boxes.items():
            hit, ia = _collides(tb, fb)
            if hit:
                text_formula.append({"formula_id": fid, "text": s["text"],
                                     "bbox": [round(v, 2) for v in tb],
                                     "overlap_area": round(ia, 2)})
        if not in_table_cell(tb):
            for tb2 in table_boxes:
                hit, ia = _collides(tb, tb2)
                if hit:
                    text_table.append({"table_bbox": [round(v, 2) for v in tb2],
                                       "text": s["text"],
                                       "overlap_area": round(ia, 2)})
            for fb2 in figure_boxes:
                hit, ia = _collides(tb, fb2)
                if hit:
                    text_figure.append({"figure_bbox": [round(v, 2) for v in fb2],
                                        "text": s["text"],
                                        "overlap_area": round(ia, 2)})

    # formula vs formula overlap (excluding members of the SAME display
    # formula row -- those sit adjacently by design, e.g. B1 + B2 subscript)
    same_row = set()
    if flows:
        for flow in flows:
            for item in flow.get("items", []):
                if item.get("kind") == "formula":
                    members = item.get("row_members") or []
                    for i in range(len(members)):
                        for j in range(i + 1, len(members)):
                            same_row.add((members[i]["formula_id"],
                                          members[j]["formula_id"]))
    fids = sorted(formula_boxes)
    for i in range(len(fids)):
        for j in range(i + 1, len(fids)):
            if (fids[i], fids[j]) in same_row or (fids[j], fids[i]) in same_row:
                continue
            ia = _overlap_area(formula_boxes[fids[i]], formula_boxes[fids[j]])
            if ia > COLLISION_MIN_AREA:
                formula_formula.append({"formula_a": fids[i],
                                        "formula_b": fids[j],
                                        "overlap_area": round(ia, 2)})

    # ---- formula clip integrity (SVG leak detection) ---------------------
    # The formula-seg / formula-inline <img> crops a whole-page SVG.  If
    # Chromium print loses the clip (overflow:hidden / clip-path), glyphs
    # from OTHER parts of that SVG leak onto the page (p015: the English
    # source body of the page renders inside the translated region).  A
    # leak = a filled vector glyph whose centre sits OUTSIDE every formula
    # enclosure and every table/figure box.  Isolated small glyphs are
    # normal (footnotes/symbols) -- a CLUSTER (>= CLIP_CLUSTER_MIN glyphs
    # within one 10pt y-band) is a candidate leak.
    # NOTE: get_drawings() returns paths that ARE clipped away in the
    # rendered page too (whole-page SVG content streams keep every glyph).
    # So a candidate cluster is only a leak when it is VISIBLE: the raster
    # ink in the cluster bbox must be substantially higher than a fully
    # clipped-out region would be.  We raster the cluster band and require
    # a non-trivial ink density.
    # Phase 4C.2R: clusters sitting inside a paragraph's flowed bbox are the
    # translated body rendered as vector glyphs (Chromium embeds some CJK
    # fonts as paths) -- they are the paragraph itself, not a leak.
    para_flow_boxes = []
    if flows:
        for flow in flows:
            for item in flow.get("items", []):
                if item.get("kind") == "paragraph":
                    x0 = flow.get("col_x0", 0.0)
                    x1 = flow.get("col_x1", 1.0)
                    fy = item.get("flow_y", 0.0)
                    para_flow_boxes.append(
                        [x0, fy, x1, fy + item.get("est_height", 0.0)])
    clip_leaks = []   # list of (y_band, count, sample_bbox)
    doc = pymupdf.open(str(pdf_path))
    page = doc[0]
    draw_glyphs = []
    for d in page.get_drawings():
        if d.get("fill") is None:
            continue
        r = d["rect"]
        cx, cy = (r.x0 + r.x1) / 2.0, (r.y0 + r.y1) / 2.0
        in_enc = any(e[0] - 2 <= cx <= e[2] + 2 and e[1] - 2 <= cy <= e[3] + 2
                     for e in formula_boxes.values())
        in_tbl = any(t[0] - 2 <= cx <= t[2] + 2 and t[1] - 2 <= cy <= t[3] + 2
                     for t in table_boxes)
        in_fig = any(f[0] - 2 <= cx <= f[2] + 2 and f[1] - 2 <= cy <= f[3] + 2
                     for f in figure_boxes)
        in_para_flow = any(pb[0] - 2 <= cx <= pb[2] + 2
                           and pb[1] - 2 <= cy <= pb[3] + 2
                           for pb in para_flow_boxes)
        if not in_enc and not in_tbl and not in_fig and not in_para_flow:
            draw_glyphs.append((cy, [r.x0, r.y0, r.x1, r.y1]))
    doc.close()
    bands = {}
    for cy, bb in draw_glyphs:
        band = int(cy // 10) * 10
        bands.setdefault(band, []).append(bb)
    _INK_DOC = None
    for band in sorted(bands):
        if len(bands[band]) < CLIP_CLUSTER_MIN:
            continue
        sample = bands[band][0]
        # EXCLUDE clusters that sit on top of the PDF TEXT layer: Chromium
        # sometimes embeds the same glyphs both as text AND as vector paths
        # (p015 '其计算公式如下：' rendered as both).  Such clusters are the
        # normal body text, not an SVG leak.  A true leak sits in a region
        # that has NO text of its own.
        text_overlap = False
        sb = sample
        for ts in spans:
            x = min(sb[2], ts["bbox"][2]) - max(sb[0], ts["bbox"][0])
            y = min(sb[3], ts["bbox"][3]) - max(sb[1], ts["bbox"][1])
            if x > 2 and y > 2:
                text_overlap = True
                break
        if text_overlap:
            continue
        # visible check: raster a wide horizontal strip of this y-band and
        # require ink density >= CLIP_VISIBLE_INK (a fully clipped cluster
        # renders as white).
        if _INK_DOC is None:
            _INK_DOC = pymupdf.open(str(pdf_path))
        spage = _INK_DOC[0]
        ph = spage.rect.height
        pw = spage.rect.width
        y0 = max(0.0, band - 2.0)
        y1 = min(ph, band + 12.0)
        clip_r = pymupdf.Rect(0, y0, pw, y1)
        pix = spage.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), clip=clip_r)
        s = pix.samples
        dark = sum(1 for i in range(0, len(s), pix.n)
                   if s[i] < 120 and s[i + 1] < 120 and s[i + 2] < 120)
        total = pix.width * pix.height
        density = 100.0 * dark / total if total else 0.0
        if density >= CLIP_VISIBLE_INK:
            clip_leaks.append({"y_band": band, "glyph_count": len(bands[band]),
                               "sample": [round(v, 1) for v in sample],
                               "ink_density": round(density, 2)})
    if _INK_DOC is not None:
        _INK_DOC.close()
    rendered_formula_clip_count = len(clip_leaks)

    # orphan equation numbers: "(15)" style spans that are NOT inside any
    # formula enclosure AND NOT part of a formula row (a number sitting on
    # the same row as a formula, to its right, IS the formula's equation
    # number - a legitimate FormulaBlock child, not an orphan).  Only a
    # number that floats alone (no formula on its row, no enclosure
    # overlap) is an orphan.
    cjk_span = re.compile(r"[\u3400-\u9fff]")
    for s in spans:
        t = s["text"].strip()
        if not _EQNUM_RE.fullmatch(t.replace(" ", "")):
            continue
        tb = s["bbox"]
        inside_any = any(_overlap_area(tb, fb) > 1.0
                         for fb in formula_boxes.values())
        if inside_any:
            continue  # inside a formula enclosure: its own child
        # same row as a formula, x beyond it (left or right): equation
        # number of that formula -> NOT an orphan
        belongs_to_formula = any(
            (min(tb[3], fb[3]) - max(tb[1], fb[1])) > 4
            and (tb[2] < fb[0] or tb[0] > fb[2])
            for fb in formula_boxes.values())
        if belongs_to_formula:
            continue
        # inline equation reference inside Chinese prose ("将(10)和(12)相加"):
        # with the balanced Latin font the "(10)" is a separate span adjacent
        # to CJK text on the same line -- a legitimate inline reference, not
        # an orphaned equation number.
        adjacent_cjk = any(
            s2 is not s
            and (min(tb[3], s2["bbox"][3]) - max(tb[1], s2["bbox"][1])) > 4
            and (abs(s2["bbox"][0] - tb[2]) < 30 or abs(s2["bbox"][2] - tb[0]) < 30)
            and cjk_span.search(s2["text"])
            for s2 in spans)
        if adjacent_cjk:
            continue
        eqnum_orphans.append({"text": t, "bbox": [round(v, 2) for v in tb]})

    records = []
    for fid in sorted(formula_boxes):
        hits = [r for r in text_formula if r["formula_id"] == fid]
        records.append({
            "formula_id": fid,
            "enclosure_bbox": [round(v, 2) for v in formula_boxes[fid]],
            "text_collision_count": len(hits),
            "text_collision_details": hits[:8],
        })

    result = {
        "page": page_label,
        "rendered_text_formula_collision_count": len(text_formula),
        "rendered_text_table_collision_count": len(text_table),
        "rendered_text_figure_collision_count": len(text_figure),
        "rendered_formula_formula_collision_count": len(formula_formula),
        "rendered_formula_clip_count": rendered_formula_clip_count,
        "orphan_equation_number_count": len(eqnum_orphans),
        "formula_enclosure_count": len(formula_boxes),
        "text_span_count": len(spans),
        "formula_records": records,
        "text_formula_collision_details": text_formula[:12],
        "text_table_collision_details": text_table[:12],
        "text_figure_collision_details": text_figure[:12],
        "formula_formula_collision_details": formula_formula[:12],
        "formula_clip_leak_details": clip_leaks[:12],
        "orphan_equation_number_details": eqnum_orphans[:12],
        "collision_gate_passed": (
            len(text_formula) == 0 and len(text_table) == 0
            and len(text_figure) == 0 and len(formula_formula) == 0
            and rendered_formula_clip_count == 0
            and len(eqnum_orphans) == 0),
    }
    _dump(out_dir / "rendered_region_collision_qa.json", result)
    _render_overlay(pdf_path, formula_boxes, spans,
                    out_dir / f"{page_label}_collision_overlay.png")
    return result


def _dump(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1),
                          encoding="utf-8")


def _render_overlay(pdf_path, formula_boxes, spans, out_png):
    """green = translated text bbox, orange = formula enclosure,
    red = intersection."""
    doc = pymupdf.open(str(pdf_path))
    page = doc[0]
    mat = pymupdf.Matrix(2, 2)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    doc.close()
    pix.save(str(out_png))
    # draw overlays on top of the rendered page
    from PIL import Image, ImageDraw
    img = Image.open(str(out_png)).convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")
    scale = 2.0
    for b in formula_boxes.values():
        d.rectangle([b[0] * scale, b[1] * scale, b[2] * scale, b[3] * scale],
                    outline=(255, 140, 0, 255), width=2)
    for s in spans:
        b = s["bbox"]
        d.rectangle([b[0] * scale, b[1] * scale, b[2] * scale, b[3] * scale],
                    outline=(0, 200, 0, 255), width=1)
    for s in spans:
        tb = s["bbox"]
        for fb in formula_boxes.values():
            x0 = max(tb[0], fb[0]); y0 = max(tb[1], fb[1])
            x1 = min(tb[2], fb[2]); y1 = min(tb[3], fb[3])
            if x1 > x0 and y1 > y0:
                d.rectangle([x0 * scale, y0 * scale, x1 * scale, y1 * scale],
                            fill=(255, 0, 0, 120))
    img.save(str(out_png))
