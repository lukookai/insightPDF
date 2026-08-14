"""FixedCanvasAnchorLayout -- fixed-canvas visual page layout (visual-v01).

Produces a ColumnFlow-model-compatible ``flows`` structure (same schema as
``flow_layout.build_column_flow``) but with the OPPOSITE layout philosophy:

  * display formulas / tables / figures / images are HARD ANCHORS: their
    flow_y == source anchor_y (never displaced by prose growth);
  * soft-text paragraphs are grouped into *soft sequences* between hard
    separators; each sequence may only adapt INSIDE its source-derived
    region via the LocalFitStrategy gradient (L0..L5);
  * if a sequence still overflows its region -> capacity_unresolved and the
    visual gate must BLOCK (no silent page spill, no pushing anchors).

The output is consumed by the EXISTING ``build_unified_html`` renderer
(no renderer rewrite): formula row_members with flow_y == anchor_y produce
dy == 0, i.e. in-place rendering; paragraph boxes carry per-paragraph
font_scale / line_height_scale from the fit strategy.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from flow_layout import estimate_paragraph_height, _split_translation  # noqa: E402
from html_render import _display_formula_boxes, _obstacle_boxes  # noqa: E402
from fixed_canvas_fit import LocalFitStrategy, BASE_LINE_HEIGHT  # noqa: E402

_SAFE_GAP = 4.0


def _column_gap_source(paras: List[Dict[str, Any]]) -> float:
    """Median original inter-paragraph gap in the column (clamped)."""
    gaps = []
    for i in range(1, len(paras)):
        pbox = paras[i - 1].get("bbox") or []
        cbox = paras[i].get("bbox") or []
        if len(pbox) == 4 and len(cbox) == 4:
            g = cbox[1] - pbox[3]
        else:
            g = paras[i].get("anchor_y", 0) - paras[i - 1].get("anchor_y", 0)
        if 2.0 < g < 40.0:
            gaps.append(g)
    if not gaps:
        return _SAFE_GAP
    gaps.sort()
    med = gaps[len(gaps) // 2]
    return max(3.0, min(14.0, med))


def _rows_from_formulas(display_boxes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Cluster display formula boxes into source rows (y-overlap)."""
    boxes = sorted(display_boxes, key=lambda b: (b["anchor_y"], b["bbox"][0]))
    rows = []
    for box in boxes:
        placed = False
        for row in rows:
            overlap = (min(row["bbox"][3], box["bbox"][3])
                       - max(row["bbox"][1], box["bbox"][1]))
            if overlap > 2.0:
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
            rows.append({"anchor_y": box["anchor_y"], "bbox": list(box["bbox"]),
                         "members": [box]})
    return rows


class FixedCanvasAnchorLayout:
    """Fixed-canvas visual layout for one page."""

    def __init__(self, page_model: Dict[str, Any],
                 translations: Dict[str, str],
                 grid: Dict[str, Any] | None = None,
                 bottom_reserved_regions: List[Dict[str, Any]] | None = None,
                 width_map: Dict[str, float] | None = None,
                 frontmatter: Dict[str, Any] | None = None):
        self.page_model = page_model
        self.translations = translations
        self.grid = grid or {}
        self.bottom_reserved = bottom_reserved_regions or []
        self.width_map = width_map or {}
        self.frontmatter = frontmatter or {}
        self.fit = LocalFitStrategy(self.width_map)
        self.unresolved: List[Dict[str, Any]] = []

    # ------------------------------------------------------------- public
    def build_visual_flows(self) -> List[Dict[str, Any]]:
        pm = self.page_model
        from visual_region_policy import (VisualRegionPolicy, SOFT_TEXT,
                                          FRONT_MATTER, RESERVED_CAPTION,
                                          RESERVED_BOTTOM)
        policy = VisualRegionPolicy(
            pm, grid=self.grid, frontmatter=self.frontmatter,
            bottom_reserved_regions=self.bottom_reserved)
        reserved_owner_ids = {
            pid for region in self.bottom_reserved
            for pid in region.get("owner_paragraph_ids", [])}
        body_flow_bottom = min(
            (float(region["body_flow_bottom"]) for region in self.bottom_reserved
             if region.get("body_flow_bottom") is not None),
            default=None)
        paragraphs = [r["payload"] for r in pm.get("regions", [])
                      if r.get("type") == "text"]
        paragraphs = [p for p in paragraphs
                      if (p.get("paragraph_id")
                          not in reserved_owner_ids)]
        display_boxes = _display_formula_boxes(pm)
        obstacles = _obstacle_boxes(pm)

        # column x ranges from the grid ColumnTracks (never paragraph bboxes)
        tracks = {}
        if self.grid.get("columns") and len(self.grid["columns"]) >= 2 \
                and self.grid.get("gutter"):
            tracks[0] = [self.grid["columns"][0]["x0"],
                         self.grid["columns"][0]["x1"]]
            tracks[1] = [self.grid["columns"][1]["x0"],
                         self.grid["columns"][1]["x1"]]
            cf = self.grid.get("content_frame") or {}
            if cf:
                tracks[-1] = [cf["x0"], cf["x1"]]

        def _col_range(column, dx0, dx1):
            if column in tracks:
                return tracks[column]
            return dx0, dx1

        # group paragraphs by column fragment (cross-column splits stay)
        columns: Dict[int, List[Dict[str, Any]]] = {}
        para_label = {}
        for r in pm.get("regions", []):
            if r.get("type") == "text" and r.get("region_id"):
                pid = (r.get("payload") or {}).get("paragraph_id")
                if pid:
                    para_label[pid] = policy.label_of(r.get("region_id"))
        for p in paragraphs:
            pid = p.get("logical_paragraph_id") or p["paragraph_id"]
            zh = self.translations.get(
                pid, p.get("translation_source_text") or p.get("source_text", ""))
            fragments = p.get("source_fragments") or [{
                "flow_fragment_id": pid + "-F0", "column": p["column"],
                "anchor_y": p["anchor_y"], "bbox": p.get("bbox", []),
                "col_x0": p["col_x0"], "col_x1": p["col_x1"],
                "col_width": p["col_width"],
                "base_font_size": p.get("base_font_size"),
                "source_text": p.get("source_text", ""), "continuation": False,
            }]
            rendered = p.get("fragment_translation_texts") or _split_translation(
                zh, fragments)
            for index, (frag, frag_text) in enumerate(zip(fragments, rendered)):
                flow_para = dict(frag)
                flow_para.update({
                    "paragraph_id": pid,
                    "logical_paragraph_id": pid,
                    "style_role": p.get("style_role", "body"),
                    "render_text": frag_text,
                    "fragment_index": index,
                    "is_vertical": frag.get("column") == -2,
                    "visual_label": para_label.get(pid, SOFT_TEXT),
                })
                columns.setdefault(frag["column"], []).append(flow_para)

        flows: List[Dict[str, Any]] = []
        # hard-anchor member bboxes (single formulas + table/figure/image)
        # for "inside-anchor" containment detection
        anchor_member_boxes: List[List[float]] = []
        for b in display_boxes:
            anchor_member_boxes.append([float(v) for v in b["bbox"]])
        anchor_member_boxes.extend(obstacles)
        for ci in sorted(columns):
            col_paras = columns[ci]
            if ci == -1:
                col_x0 = min(p["col_x0"] for p in col_paras)
                col_x1 = max(p["col_x1"] for p in col_paras)
            else:
                col_x0, col_x1 = _col_range(
                    ci, min(p["col_x0"] for p in col_paras),
                    max(p["col_x1"] for p in col_paras))
            col_w = max(col_x1 - col_x0, 1.0)

            col_formulas = [b for b in display_boxes
                            if b["bbox"][2] > col_x0 - 2
                            and b["bbox"][0] < col_x1 + 2]
            rows = _rows_from_formulas(col_formulas)
            col_obstacles = [b for b in obstacles
                             if b[2] > col_x0 - 2 and b[0] < col_x1 + 2]

            # ---- hard separators: formula rows + obstacle bands ----------
            separators: List[Dict[str, Any]] = []
            for row in rows:
                separators.append({"kind": "formula_row", "y0": row["anchor_y"],
                                   "y1": row["bbox"][3], "box": row,
                                   "top": row["anchor_y"]})
            for ob in col_obstacles:
                separators.append({"kind": "obstacle", "y0": ob[1],
                                   "y1": ob[3], "box": ob, "top": ob[1]})
            if body_flow_bottom is not None and ci in (0, 1):
                separators.append({"kind": "page_bottom",
                                   "y0": body_flow_bottom,
                                   "y1": body_flow_bottom, "box": None,
                                   "top": body_flow_bottom})
            else:
                pm_h = float(pm.get("height") or 0.0)
                separators.append({"kind": "page_bottom", "y0": pm_h - 40.0,
                                   "y1": pm_h - 40.0, "box": None,
                                   "top": pm_h - 40.0})
            separators.sort(key=lambda s: (s["top"], s["y0"]))

            # ---- soft sequences between separators -----------------------
            col_paras_sorted = sorted(col_paras,
                                      key=lambda p: p.get("anchor_y", 0))
            items: List[Dict[str, Any]] = []
            seq: List[Dict[str, Any]] = []
            seq_region_top = None
            sep_idx = 0
            para_idx = 0

            def _emit_para(s, fscale, lscale, fit_level):
                fsize = (s.get("base_font_size") or 10.0) * fscale
                est_h = estimate_paragraph_height(
                    s.get("render_text") or "", fsize, col_w,
                    self.width_map,
                    line_height=BASE_LINE_HEIGHT * lscale)
                items.append({
                    "kind": "paragraph",
                    "paragraph_id": s.get("paragraph_id"),
                    "logical_paragraph_id": s.get("logical_paragraph_id"),
                    "flow_fragment_id": s.get("flow_fragment_id"),
                    "fragment_index": s.get("fragment_index", 0),
                    "continuation": bool(s.get("continuation")),
                    "render_text": s.get("render_text") or "",
                    "style_role": s.get("style_role", "body"),
                    "column": ci,
                    "layout_bbox": [round(v, 3)
                                    for v in (s.get("bbox") or [])],
                    "flow_y": round(s.get("anchor_y", 0.0), 3),
                    "est_height": round(est_h, 3),
                    "anchor_y": round(s.get("anchor_y", 0.0), 3),
                    "base_font_size": round(fsize, 4),
                    "font_scale": round(fscale, 4),
                    "line_height_scale": round(lscale, 4),
                    "is_reference": False,
                    "is_vertical": bool(s.get("is_vertical")),
                    "visual_fit_level": fit_level,
                    "visual_label": s.get("visual_label", SOFT_TEXT),
                })

            def _flush_sequence():
                nonlocal seq, seq_region_top
                if not seq:
                    return
                region_bottom = separators[sep_idx]["top"] - 2.0 \
                    if sep_idx < len(separators) else (seq[-1]["anchor_y"]
                                                       + 200.0)
                region_top = seq_region_top if seq_region_top is not None \
                    else seq[0]["anchor_y"]
                region_h = max(region_bottom - region_top, 1.0)
                fit = self.fit.fit_sequence(
                    [{"paragraph_id": s.get("paragraph_id"),
                      "text": s.get("render_text") or "",
                      "font_size": s.get("base_font_size") or 10.0,
                      "anchor_y": s.get("anchor_y", 0.0),
                      "est_h0": 0.0} for s in seq],
                    region_top, region_h, col_w)
                fscale = float(fit["font_scale"])
                lscale = float(fit["line_height_scale"])
                if fit["unresolved"]:
                    self.unresolved.append({
                        "column": ci, "region_top": round(region_top, 3),
                        "region_bottom": round(region_bottom, 3),
                        "est_height": round(fit["est_height"], 3),
                        "paragraph_ids": [s.get("paragraph_id")
                                          for s in seq],
                        "fit_level": fit["level"],
                        "reason": "soft sequence does not fit source region",
                    })
                for s in seq:
                    _emit_para(s, fscale, lscale, fit["level"])
                seq = []
                seq_region_top = None

            # walk paragraphs + separators in y order
            def _inside_anchor(p):
                lb = p.get("bbox") or []
                if len(lb) != 4:
                    return False
                cx = (lb[0] + lb[2]) / 2.0
                cy = (lb[1] + lb[3]) / 2.0
                for ab in anchor_member_boxes:
                    if (ab[0] - 2.0 <= cx <= ab[2] + 2.0
                            and ab[1] - 2.0 <= cy <= ab[3] + 2.0):
                        return True
                return False

            def _placeholder_only(p):
                import re as _re
                text = p.get("render_text") or ""
                stripped = _re.sub(r"\{\{[A-Z_0-9]+(?:_[A-Z0-9]+)?\}\}",
                                   "", text)
                return not stripped.strip()

            while para_idx < len(col_paras_sorted):
                p = col_paras_sorted[para_idx]
                label = p.get("visual_label", SOFT_TEXT)
                p_top = p.get("anchor_y", 0.0)
                # inside-anchor text (table cell / figure caption body /
                # inline-formula container) is anchored to its owner:
                # render in place, never fit-checked against the region.
                if label != SOFT_TEXT or _inside_anchor(p) \
                        or _placeholder_only(p):
                    while (sep_idx < len(separators)
                           and separators[sep_idx]["top"] <= p_top + 0.5):
                        _flush_sequence()
                        sep_idx += 1
                    _emit_para(p, 1.0, 1.0, 0)
                    para_idx += 1
                    continue
                # flush sequence before hitting a separator whose top is
                # above / at this paragraph (soft text belongs to the region
                # below that separator)
                while (sep_idx < len(separators)
                       and separators[sep_idx]["top"] <= p_top + 0.5):
                    _flush_sequence()
                    sep_idx += 1
                if seq_region_top is None:
                    seq_region_top = p_top
                seq.append(p)
                para_idx += 1
            _flush_sequence()

            # ---- hard anchors in place (flow_y == anchor_y) ---------------
            for row in rows:
                fbox = row["bbox"]
                est_h = fbox[3] - fbox[1]
                items.append({
                    "kind": "formula", "formula_id": None,
                    "bbox": [round(v, 3) for v in fbox],
                    "flow_y": round(row["anchor_y"], 3),
                    "est_height": round(est_h, 3),
                    "anchor_y": round(row["anchor_y"], 3),
                    "row_members": [
                        {"formula_id": m["formula_id"],
                         "bbox": [round(v, 3) for v in m["bbox"]],
                         "anchor_y": round(m["anchor_y"], 3),
                         "segments": [[round(v, 3) for v in s]
                                      for s in m["segments"]]}
                        for m in row["members"]],
                    "visual_anchor": True,
                })

            model = {"column": ci, "col_x0": round(col_x0, 3),
                     "col_x1": round(col_x1, 3), "items": items}
            if body_flow_bottom is not None and ci in (0, 1):
                model["max_y"] = round(body_flow_bottom, 3)
            flows.append(model)
        return flows

    def build(self) -> Dict[str, Any]:
        flows = self.build_visual_flows()
        return {"flows": flows, "unresolved": list(self.unresolved),
                "hard_anchor_count": sum(
                    1 for f in flows
                    for it in f.get("items", [])
                    if it["kind"] == "formula"),
                "soft_sequence_count": len(self.fit.records)}


def build_visual_layout(page_model, translations, grid=None,
                        bottom_reserved_regions=None, width_map=None):
    """One-shot: returns {flows, unresolved, hard_anchor_count, ...}."""
    layout = FixedCanvasAnchorLayout(
        page_model, translations, grid=grid,
        bottom_reserved_regions=bottom_reserved_regions,
        width_map=width_map)
    return layout.build()
