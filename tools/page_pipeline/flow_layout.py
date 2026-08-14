# -*- coding: utf-8 -*-
"""ColumnFlowModel for Phase 4B - paragraph flow reconstruction.

Every column gets a local flow:

    ColumnFlowModel {
      column, col_x0, col_x1,
      items: [
        { kind: "paragraph", paragraph_id, style_role, anchor_y,
          flow_y, est_height, rendered_bbox },
        { kind: "formula", formula_id, bbox (reserved box) },
        ...
      ]
    }

Rules (Phase 4B):
* geometry_locked regions (table / image / figure / display formula reserved
  boxes) are OBSTACLES - paragraph flow never enters their bbox.
* the first flow item keeps its original anchor_y; every following paragraph
  uses  y_next = max(original_anchor_y, previous_rendered_bottom + gap).
* paragraph height is ESTIMATED (CJK ~1.0em, ASCII ~0.5em per char) so the
  flow can be laid out in one pass; the real rendered bbox is measured later
  by QA against this estimate.
* Chinese reflows inside the paragraph box (white-space:normal) - source
  lines are never the layout unit.
"""
from __future__ import annotations

import math
import re

PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_0-9]+\}\}")


def _is_cjk(ch):
    return ord(ch) > 0x2E7F


def _est_text_width_em(text, formula_width_map=None, font_size=10.0):
    """Approximate rendered width of a translated paragraph in em units.

    CJK chars are full-width (1.0em), ASCII ~0.5em.  Inline formula
    placeholders occupy the width of their reserved boxes (pt -> em via
    ``width_pt / font_size``)."""
    formula_width_map = formula_width_map or {}
    w = 0.0
    fsize = max(float(font_size or 10.0), 1.0)
    for tok in PLACEHOLDER_RE.findall(text):
        w += formula_width_map.get(tok, 0.0) / fsize
    stripped = PLACEHOLDER_RE.sub("", text)
    for ch in stripped:
        if ch == " ":
            w += 0.25
        elif _is_cjk(ch):
            w += 1.0
        else:
            w += 0.55
    return w


def estimate_paragraph_height(text, font_size, col_width,
                              formula_width_map=None, line_height=1.3):
    """Estimated rendered height (pt) of a paragraph translation.

    Adds one line of slack when the paragraph contains inline formula
    placeholders: an inline-block box can force a line break next to the
    formula, so the estimate must not under-predict (the flow box bottom is
    also the QA extraction boundary)."""
    if font_size <= 0 or col_width <= 0:
        return font_size * 1.3
    em = _est_text_width_em(text, formula_width_map, font_size)
    chars_per_line = max(col_width / font_size, 1.0)
    lines = max(1, math.ceil(em / chars_per_line))
    atomic_tokens = [tok for tok in PLACEHOLDER_RE.findall(text)
                     if formula_width_map.get(tok, 0.0) > 0]
    if atomic_tokens:
        lines += 1
    elif lines >= 4:
        # Chromium's CJK serif metrics and punctuation are slightly wider
        # than the coarse em estimate on dense technical paragraphs.
        lines += 1
    return lines * font_size * line_height


def _intersect(a, b, margin=0.0):
    return not (a[2] < b[0] - margin or a[0] > b[2] + margin
                or a[3] < b[1] - margin or a[1] > b[3] + margin)


def _column_gap(paragraphs):
    """Median original inter-paragraph gap in the column (clamped)."""
    gaps = []
    for i in range(1, len(paragraphs)):
        prev_box = paragraphs[i - 1].get("bbox") or []
        cur_box = paragraphs[i].get("bbox") or []
        if len(prev_box) == 4 and len(cur_box) == 4:
            g = cur_box[1] - prev_box[3]
        else:
            g = paragraphs[i]["anchor_y"] - paragraphs[i - 1]["anchor_y"]
        if 2.0 < g < 40.0:
            gaps.append(g)
    if not gaps:
        return 8.0
    gaps.sort()
    med = gaps[len(gaps) // 2]
    return max(3.0, min(14.0, med))


def _split_translation(text, source_fragments):
    """Split one translated LogicalParagraph into render fragments.

    This is a rendering split only; the translation dictionary retains one
    entry keyed by logical_paragraph_id.  Prefer punctuation/space boundaries
    near the source-length ratio and never cut inside a protected placeholder.
    """
    if len(source_fragments) <= 1:
        return [text]
    lengths = [max(len(f.get("source_text") or ""), 1) for f in source_fragments]
    total = sum(lengths)
    remaining = text
    out = []
    used_source = 0
    for length in lengths[:-1]:
        used_source += length
        target = max(1, round(len(text) * used_source / total) - sum(len(x) for x in out))
        lo = max(1, target - 24)
        hi = min(len(remaining) - 1, target + 24)
        candidates = [i + 1 for i in range(lo, hi)
                      if remaining[i] in "。！？；，,.!?; " ]
        cut = min(candidates, key=lambda i: abs(i - target)) if candidates else target
        # A placeholder is atomic, including style start/end and CodeRun.
        for match in PLACEHOLDER_RE.finditer(remaining):
            if match.start() < cut < match.end():
                cut = match.end()
                break
        out.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    out.append(remaining)
    return out


def build_column_flow(paragraphs, display_boxes, obstacles,
                      translations, formula_width_map=None, capacity=None,
                      grid=None, bottom_reserved_regions=None):
    """Return list of ColumnFlowModel dicts (one per column).

    ``paragraphs``: list of paragraph dicts (with column / col_x0 / col_x1 /
    col_width / anchor_y / style_role / base_font_size / paragraph_id).
    ``display_boxes``: [bbox, ...] of display-formula reserved boxes
    (geometry locked - flow must not enter them).
    ``obstacles``: [bbox, ...] of table / image / figure regions.
    ``translations``: {paragraph_id: zh}.
    ``formula_width_map``: {token: width_pt} for inline formula boxes.
    ``grid``: optional PageLayoutGrid (Phase 4D.1).  When provided, the
    column x ranges come from the grid ColumnTracks (snapped to the
    document profile), NOT from the per-paragraph source bbox extremes --
    a paragraph never decides its own final column width.  ``column -1``
    (full-width / table captions) uses the content frame.
    """
    formula_width_map = formula_width_map or {}
    bottom_reserved_regions = bottom_reserved_regions or []
    reserved_owner_ids = {
        pid for region in bottom_reserved_regions
        for pid in region.get("owner_paragraph_ids", [])}
    body_flow_bottom = min(
        (float(region["body_flow_bottom"]) for region in bottom_reserved_regions
         if region.get("body_flow_bottom") is not None), default=None)
    capacity = capacity or {}
    gap_scale = max(float(capacity.get("gap_scale", 1.0)), 0.0)
    font_scale = max(float(capacity.get("font_scale", 1.0)), 0.5)
    line_height_scale = max(float(capacity.get("line_height_scale", 1.0)), 0.5)
    ref_compact = capacity.get("reference_compact") or {}
    ref_gap_scale = max(float(ref_compact.get("gap_scale", 1.0)), 0.0)
    ref_font_scale = max(float(ref_compact.get("font_scale", 1.0)), 0.5)
    ref_line_height_scale = max(
        float(ref_compact.get("line_height_scale", 1.0)), 0.5)
    # reference/bibliography detection for L5 compact (author + year lines)
    from translation_status import is_reference_item
    ref_paragraphs = {p.get("logical_paragraph_id") or p["paragraph_id"]
                      for p in paragraphs
                      if is_reference_item({"type": "paragraph",
                                            "source_text": p.get("source_text") or ""})}

    # Phase 4D.1: resolve ColumnTracks from the PageLayoutGrid.  The track
    # is the final geometry constraint; source bboxes keep only y / style /
    # reading-order evidence.
    tracks = {}
    if grid and grid.get("columns") and len(grid["columns"]) >= 2 \
            and grid.get("gutter"):
        tracks[0] = [grid["columns"][0]["x0"], grid["columns"][0]["x1"]]
        tracks[1] = [grid["columns"][1]["x0"], grid["columns"][1]["x1"]]
        cf = grid.get("content_frame") or {}
        if cf:
            tracks[-1] = [cf["x0"], cf["x1"]]

    def _col_range(column, default_x0, default_x1):
        """Column x range: grid track when available, else paragraph range."""
        if column in tracks:
            return tracks[column]
        return default_x0, default_x1

    columns = {}
    for p in paragraphs:
        pid = p.get("logical_paragraph_id") or p["paragraph_id"]
        if pid in reserved_owner_ids:
            continue
        zh = translations.get(pid, p.get("translation_source_text")
                              or p.get("source_text", ""))
        fragments = p.get("source_fragments") or [{
            "flow_fragment_id": pid + "-F0", "column": p["column"],
            "anchor_y": p["anchor_y"], "bbox": p.get("bbox", []),
            "col_x0": p["col_x0"], "col_x1": p["col_x1"],
            "col_width": p["col_width"], "base_font_size": p.get("base_font_size"),
            "source_text": p.get("source_text", ""), "continuation": False,
        }]
        rendered = p.get("fragment_translation_texts") or _split_translation(zh, fragments)
        is_ref = pid in ref_paragraphs
        for index, (frag, frag_text) in enumerate(zip(fragments, rendered)):
            flow_para = dict(frag)
            flow_para.update({
                "paragraph_id": pid,
                "logical_paragraph_id": pid,
                "style_role": p.get("style_role", "body"),
                "render_text": frag_text,
                "fragment_index": index,
                "is_reference": is_ref,
                "is_vertical": frag.get("column") == -2,
            })
            columns.setdefault(frag["column"], []).append(flow_para)

    flows = []
    for ci in sorted(columns):
        col_paras = columns[ci]
        # column x range: grid ColumnTrack when available (Phase 4D.1) --
        # never the per-paragraph source bbox extremes
        if ci == -1:
            col_x0 = min(p["col_x0"] for p in col_paras)
            col_x1 = max(p["col_x1"] for p in col_paras)
        else:
            col_x0, col_x1 = _col_range(
                ci, min(p["col_x0"] for p in col_paras),
                max(p["col_x1"] for p in col_paras))
        col_w = max(col_x1 - col_x0, 1.0)
        gap = _column_gap(col_paras) * gap_scale
        # L5 reference compact: gaps BETWEEN two reference paragraphs are
        # tighter (gap_scale for ref-ref pairs)
        ref_gap = _column_gap(col_paras) * ref_gap_scale

        # display formula blocks belonging to this column: the box's x range
        # must overlap the column's x range (never use the box against
        # itself)
        col_formulas = [b for b in display_boxes
                        if b["bbox"][2] > col_x0 - 2 and b["bbox"][0] < col_x1 + 2]

        # merge flow items: paragraphs + display formulas, ordered by y
        items = []
        for p in col_paras:
            items.append({
                "kind": "paragraph", "y": p["anchor_y"],
                "para": p, "box": None})
        for b in col_formulas:
            items.append({
                "kind": "formula", "y": b["anchor_y"],
                "para": None, "box": b})
        items.sort(key=lambda it: (it["y"], 0 if it["kind"] == "paragraph" else 1))

        # column-local obstacles (table/image/figure) intersecting this column
        col_obstacles = [b for b in obstacles
                         if b[2] > col_x0 - 2 and b[0] < col_x1 + 2]

        # ---- DisplayFormulaRow clustering (Phase 4C.2R section 10) --------
        # Formulas that share the same source equation row (their enclosure
        # y-ranges overlap) must move TOGETHER as one flow_locked obstacle;
        # they keep their relative x/y so multi-formula rows stay intact.
        formula_items = [it for it in items if it["kind"] == "formula"]
        other_items = [it for it in items if it["kind"] != "formula"]
        formula_items.sort(key=lambda it: (it["y"], it["box"]["bbox"][0]))
        rows = []  # list of dicts: {"anchor_y", "bbox", "members": [box,...]}
        for it in formula_items:
            box = it["box"]
            placed = False
            for row in rows:
                y_overlap = (min(row["bbox"][3], box["bbox"][3])
                             - max(row["bbox"][1], box["bbox"][1]))
                if y_overlap > 2.0:
                    row["members"].append(box)
                    row["bbox"] = [
                        min(row["bbox"][0], box["bbox"][0]),
                        min(row["bbox"][1], box["bbox"][1]),
                        max(row["bbox"][2], box["bbox"][2]),
                        max(row["bbox"][3], box["bbox"][3])]
                    row["anchor_y"] = min(row["anchor_y"], box["anchor_y"])
                    placed = True
                    break
            if not placed:
                rows.append({"anchor_y": box["anchor_y"],
                             "bbox": list(box["bbox"]),
                             "members": [box]})
        for row in rows:
            other_items.append({"kind": "formula_row", "y": row["anchor_y"],
                                "para": None, "box": row})
        items = other_items
        items.sort(key=lambda it: (it["y"], 0 if it["kind"] == "paragraph" else 1))

        cursor_bottom = None
        model = {"column": ci, "col_x0": round(col_x0, 3),
                 "col_x1": round(col_x1, 3), "items": []}
        if body_flow_bottom is not None and ci in (0, 1):
            model["max_y"] = round(body_flow_bottom, 3)
        prev_is_ref = False
        for it in items:
            if it["kind"] == "formula_row":
                # flow_locked (Phase 4C.2R): the formula's internal geometry
                # (x, width, glyph layout, segment relative positions) is
                # unchanged -- only the container y joins the ColumnFlow.
                # preferred_anchor_y is a hint; the row must sit below the
                # previous rendered bottom + gap.
                row = it["box"]
                fbox = row["bbox"]
                anchor = row["anchor_y"]
                if cursor_bottom is None:
                    y = anchor
                else:
                    y = max(anchor, cursor_bottom + gap)
                # a formula row is itself a flow_locked obstacle: it must
                # also clear the table/figure obstacles below which it sits
                est_h = fbox[3] - fbox[1]
                for ob in col_obstacles:
                    if ob[1] > y - 0.5 and y + est_h > ob[1] + 0.5 \
                            and _intersect([col_x0, y, col_x1, y + est_h], ob,
                                           margin=1.0):
                        y = ob[3] + gap
                model["items"].append({
                    "kind": "formula", "formula_id": None,
                    "bbox": [round(v, 3) for v in fbox],
                    "flow_y": round(y, 3),
                    "est_height": round(est_h, 3),
                    "anchor_y": round(anchor, 3),
                    "row_members": [{"formula_id": m["formula_id"],
                                     "bbox": [round(v, 3) for v in m["bbox"]],
                                     "anchor_y": round(m["anchor_y"], 3),
                                     "segments": [[round(v, 3) for v in s]
                                                  for s in m["segments"]]}
                                    for m in row["members"]],
                })
                cursor_bottom = y + est_h
                prev_is_ref = False
                continue

            p = it["para"]
            zh = p.get("render_text") or translations.get(
                p["paragraph_id"], p.get("source_text", ""))
            is_ref = bool(p.get("is_reference"))
            # L5: reference paragraphs get tighter font + line-height
            p_font_scale = ref_font_scale if is_ref else font_scale
            p_lh_scale = ref_line_height_scale if is_ref else line_height_scale
            fsize = (p.get("base_font_size") or 10.0) * p_font_scale
            est_h = estimate_paragraph_height(zh, fsize, col_w,
                                              formula_width_map,
                                              line_height=1.3 * p_lh_scale)
            anchor = p["anchor_y"]
            if cursor_bottom is None:
                y = anchor
            else:
                gap_here = ref_gap if (is_ref and prev_is_ref) else gap
                y = max(anchor, cursor_bottom + gap_here)
            # avoid obstacles: if the estimated box overlaps an obstacle,
            # push below it
            for ob in col_obstacles:
                if ob[1] > y - 0.5 and y + est_h > ob[1] + 0.5 \
                        and _intersect([col_x0, y, col_x1, y + est_h], ob,
                                       margin=1.0):
                    y = ob[3] + gap
            model["items"].append({
                "kind": "paragraph", "paragraph_id": p["paragraph_id"],
                "logical_paragraph_id": p["logical_paragraph_id"],
                "flow_fragment_id": p["flow_fragment_id"],
                "fragment_index": p.get("fragment_index", 0),
                "continuation": bool(p.get("continuation")),
                "render_text": zh,
                "style_role": p.get("style_role", "body"),
                "column": ci,
                "layout_bbox": [round(v, 3) for v in p.get("bbox", [])],
                "flow_y": round(y, 3),
                "est_height": round(est_h, 3),
                "anchor_y": round(anchor, 3),
                "base_font_size": fsize,
                "font_scale": p_font_scale,
                "line_height_scale": p_lh_scale,
                "is_reference": is_ref,
                "is_vertical": bool(p.get("is_vertical")),
            })
            cursor_bottom = y + est_h
            prev_is_ref = is_ref
        flows.append(model)
    return flows
