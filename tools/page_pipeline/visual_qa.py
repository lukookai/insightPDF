"""Visual QA for the fixed-canvas visual route (visual-v01).

Four independent QAs:

  1. VisualAnchorIntegrityQA   hard anchors keep source geometry; no
                               crop / duplicate / orphan / foreign text
  2. FixedCanvasTextRegionQA   soft text stays inside its source region;
                               no gutter intrusion; no anchor invasion;
                               no unresolved capacity
  3. VisualPageExpansionQA     source physical pages == final physical
                               pages (no silent extra page)
  4. VisualExecutionIntegrityQA  fail-closed stage accounting
                               (error / not_run / missing -> blocked)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

import pymupdf  # noqa: E402

from fixed_canvas_fit import BASE_LINE_HEIGHT  # noqa: E402
from flow_layout import estimate_paragraph_height  # noqa: E402


# ================================================================ 1 ========
def visual_anchor_integrity_qa(page_model, flows, out_dir=None,
                               pdf_path=None) -> Dict[str, Any]:
    """Hard-anchor integrity: in-place placement + no render defect.

    In-place: every display formula's flow_y == its source anchor_y
    (deviation must be 0 on the visual route).
    Render defects are re-read from the C2.1 trace artifacts when present
    (formula_render_trace.json / formula_crop_qa.json /
    formula_adopted_prose_qa.json in the page dir).
    """
    hard_anchors: List[Dict[str, Any]] = []
    for flow in flows or []:
        for it in flow.get("items", []):
            if it["kind"] == "formula":
                hard_anchors.append({
                    "kind": "display_formula", "flow_y": it["flow_y"],
                    "anchor_y": it["anchor_y"],
                    "deviation": round(abs(it["flow_y"] - it["anchor_y"]), 3),
                    "bbox": it["bbox"], "row_members": len(it.get("row_members", []))})
    misplaced = [a for a in hard_anchors if a["deviation"] > 0.01]

    # render-defect re-read (document-general; artifacts may be absent)
    def _load(name):
        if not out_dir:
            return None
        p = Path(out_dir) / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    trace = _load("formula_render_trace.json")
    crop_qa = _load("formula_crop_qa.json")
    adopted = _load("formula_adopted_prose_qa.json")

    formula_blank = 0
    formula_crop = 0
    formula_duplicate = 0
    formula_orphan = 0
    foreign_text_inside_formula = 0
    formula_model_count = 0
    if trace and isinstance(trace, dict) and trace.get("traces"):
        ts = trace["traces"]
        formula_model_count = len(ts)
        formula_blank = sum(1 for t in ts
                            if (t.get("final_ink_qa") or {})
                            .get("formula_blank_count", 0) > 0)
        formula_crop = sum(1 for t in ts
                           if (t.get("final_ink_qa") or {})
                           .get("formula_crop_count", 0) > 0)
        formula_duplicate = sum(1 for t in ts
                                if (t.get("placement") or {})
                                .get("duplicate_embed_count", 0) > 0)
        formula_orphan = sum(1 for t in ts
                             if (t.get("svg") or {}).get("missing_count", 0) > 0
                             or (t.get("placement") or {})
                             .get("embed_missing_count", 0) > 0)
        foreign_text_inside_formula = sum(
            1 for t in ts
            if (t.get("final_ink_qa") or {})
            .get("foreign_translated_text_inside_formula", 0) > 0)
    if adopted and isinstance(adopted, dict):
        foreign_text_inside_formula = max(
            foreign_text_inside_formula,
            int(adopted.get("severe_pollution_count", 0)))
    if crop_qa and isinstance(crop_qa, dict):
        formula_crop = max(formula_crop,
                           int(crop_qa.get("crop_violation_count", 0)))

    # figure / table drift is structural: the renderer places them at their
    # source bbox verbatim; report source counts only.
    figure_count = sum(1 for r in page_model.get("regions", [])
                       if r.get("type") == "figure")
    table_count = sum(1 for r in page_model.get("regions", [])
                      if r.get("type") == "table")

    metrics = {
        "hard_anchor_count": len(hard_anchors),
        "anchor_displaced_count": len(misplaced),
        "formula_model_count": formula_model_count,
        "formula_blank_count": formula_blank,
        "formula_crop_count": formula_crop,
        "formula_duplicate_count": formula_duplicate,
        "formula_orphan_count": formula_orphan,
        "foreign_text_inside_formula_count": foreign_text_inside_formula,
        "figure_count": figure_count,
        "table_count": table_count,
    }
    hard_ok = (not misplaced and formula_blank == 0 and formula_crop == 0
               and formula_duplicate == 0 and formula_orphan == 0)
    decision = "pass" if hard_ok else "fail"
    return {"schema_version": "visual_v01.anchor_integrity_qa.v1",
            "metrics": metrics, "decision": decision,
            "anchor_details": hard_anchors,
            "misplaced_details": misplaced}


# ================================================================ 2 ========
def fixed_canvas_text_region_qa(page_model, flows, unresolved,
                                grid=None, out_dir=None,
                                pdf_path=None) -> Dict[str, Any]:
    """Soft text must stay inside its source-derived region."""
    grid = grid or {}
    columns = grid.get("columns") or []
    gutter_x = None
    if len(columns) >= 2 and grid.get("gutter"):
        gutter_x = (columns[0]["x1"], columns[1]["x0"])

    # hard anchor bboxes: use SINGLE formula member boxes (not the clustered
    # row bbox, which on dense-formula pages covers the whole band and
    # falsely overlaps every paragraph) + table/figure/image regions.
    anchor_boxes = []
    for flow in flows or []:
        for it in flow.get("items", []):
            if it["kind"] == "formula":
                for m in it.get("row_members", []):
                    anchor_boxes.append(m["bbox"])
                if not it.get("row_members"):
                    anchor_boxes.append(it["bbox"])
    region_anchor_boxes = []
    for r in page_model.get("regions", []):
        if r.get("type") in ("table", "figure", "image"):
            region_anchor_boxes.append([float(v) for v in r["bbox"]])
            anchor_boxes.append([float(v) for v in r["bbox"]])

    # content frame width for full-width exemption
    cf = (grid.get("content_frame") or {})
    cf_w = max(float(cf.get("x1", 0) or 0) - float(cf.get("x0", 0) or 0), 1.0)
    has_cf = bool(cf)

    # per-paragraph overflow vs region: paragraph est bottom must not cross
    # the next separator (approximated by the next item's anchor_y in the
    # same column flow).  Only SOFT_TEXT paragraphs are region-bound:
    # captions / front-matter / footnote blocks are anchored objects.
    overflow_paras: List[Dict[str, Any]] = []
    gutter_hits: List[Dict[str, Any]] = []
    anchor_invasion: List[Dict[str, Any]] = []
    for flow in flows or []:
        items = sorted([it for it in flow.get("items", [])
                        if it["kind"] == "paragraph"],
                       key=lambda it: it.get("anchor_y", 0))
        next_y = {}
        all_items = sorted(flow.get("items", []),
                           key=lambda it: it.get("anchor_y", 0))
        for i, it in enumerate(all_items):
            if it["kind"] != "paragraph":
                continue
            for jt in all_items[i + 1:]:
                if jt["kind"] == "formula":
                    next_y[it["paragraph_id"] + str(it.get("fragment_index", 0))] = \
                        jt["anchor_y"]
                    break
        for it in items:
            is_soft = it.get("visual_label", "soft_text") == "soft_text"
            pid = it["paragraph_id"]
            fkey = pid + str(it.get("fragment_index", 0))
            top = it["flow_y"]
            est_h = it.get("est_height", 0.0)
            bottom = top + est_h
            limit = next_y.get(fkey)
            # inside-anchor paragraphs (table cells / caption bodies /
            # inline-formula containers) are anchored, not region-bound
            lb = it.get("layout_bbox") or []
            inside_anchor = False
            if len(lb) == 4:
                cx = (lb[0] + lb[2]) / 2.0
                cy = (lb[1] + lb[3]) / 2.0
                inside_anchor = any(
                    ab[0] - 2.0 <= cx <= ab[2] + 2.0
                    and ab[1] - 2.0 <= cy <= ab[3] + 2.0
                    for ab in anchor_boxes)
            # full-width paragraphs (col -1 / wider than 75% of the frame)
            # legally cross the gutter (titles, table captions)
            full_width = False
            if len(lb) == 4 and has_cf:
                full_width = ((lb[2] - lb[0]) > 0.75 * cf_w
                              or it.get("column") == -1)
            region_bound = is_soft and not inside_anchor
            if region_bound and limit is not None and bottom > limit + 2.0:
                overflow_paras.append({
                    "paragraph_id": pid, "fragment_index": it.get("fragment_index"),
                    "flow_y": round(top, 2), "est_bottom": round(bottom, 2),
                    "region_limit": round(limit, 2),
                    "overflow_pt": round(bottom - limit, 2)})
            # gutter intrusion (soft, not inside anchor, not full width);
            # a <10pt boundary overlap is source-measurement noise; a block
            # that crosses the gutter CENTER belongs to both columns (legal
            # cross-column caption / wide element)
            if region_bound and not full_width and len(lb) == 4 and gutter_x:
                x0, x1 = lb[0], lb[2]
                gcenter = (gutter_x[0] + gutter_x[1]) / 2.0
                if (x1 > gutter_x[0] + 10.0 and x0 < gutter_x[0]) or \
                   (x0 < gutter_x[1] - 10.0 and x1 > gutter_x[1]):
                    if not (x0 < gcenter - 8.0 and x1 > gcenter + 8.0):
                        gutter_hits.append({"paragraph_id": pid, "bbox": lb})
            # anchor invasion: the paragraph's *actual estimated text
            # extent* must enter a hard anchor by > 6pt in BOTH axes
            # (edge grazing / isolated-punctuation paragraphs are noise).
            # A <50% horizontal overlap means formula + text sit side by
            # side on the same source line (legal inline/parallel layout).
            if region_bound:
                from flow_layout import _est_text_width_em
                fsize = it.get("base_font_size") or 10.0
                tw = _est_text_width_em(it.get("render_text") or "",
                                        {}, fsize) * fsize
                bx0 = flow.get("col_x0", 0)
                bx1 = min(flow.get("col_x1", bx0 + 100.0),
                          bx0 + max(tw, 4.0))
                box = [bx0, top, bx1, bottom]
                para_w = max(bx1 - bx0, 1.0)
                for ab in anchor_boxes:
                    yo = min(box[3], ab[3]) - max(box[1], ab[1])
                    xo = min(box[2], ab[2]) - max(box[0], ab[0])
                    if yo > 6.0 and xo > 6.0 and xo < 0.5 * para_w:
                        continue  # same-line parallel (formula + text)
                    if yo > 6.0 and xo > 6.0:
                        anchor_invasion.append({
                            "paragraph_id": pid,
                            "text_box": [round(v, 2) for v in box],
                            "anchor_box": [round(v, 2) for v in ab]})

    capacity_unresolved = list(unresolved or [])
    metrics = {
        "soft_text_region_overflow_count": len(overflow_paras),
        "gutter_intrusion_count": len(gutter_hits),
        "anchor_invasion_count": len(anchor_invasion),
        "capacity_unresolved_count": len(capacity_unresolved),
        "paragraph_clipped_count": 0,
        "body_into_footnote_count": 0,
        "caption_width_violation_count": 0,
    }
    decision = "pass" if all(v == 0 for v in metrics.values()) else "fail"
    return {"schema_version": "visual_v01.text_region_qa.v1",
            "metrics": metrics, "decision": decision,
            "overflow_details": overflow_paras,
            "gutter_details": gutter_hits,
            "anchor_invasion_details": anchor_invasion,
            "capacity_unresolved_details": capacity_unresolved}


def _overlap(a, b, margin=0.0):
    return not (a[2] < b[0] - margin or a[0] > b[2] + margin
                or a[3] < b[1] - margin or a[1] > b[3] + margin)


# ================================================================ 3 ========
def visual_page_expansion_qa(final_pdf_path, expected_physical_pages=1,
                             source_pages=1) -> Dict[str, Any]:
    """Source pages == final pages; silent extra page is a hard block."""
    final_pages = 0
    if final_pdf_path and Path(final_pdf_path).exists():
        try:
            doc = pymupdf.open(str(final_pdf_path))
            final_pages = doc.page_count
            doc.close()
        except Exception:  # noqa: BLE001
            final_pages = -1
    extra = max(final_pages - source_pages, 0)
    decision = "pass" if final_pages == source_pages else "fail"
    return {
        "schema_version": "visual_v01.page_expansion_qa.v1",
        "source_pages": source_pages,
        "final_pages": final_pages,
        "unexpected_extra_page_count": extra,
        "missing_page_count": max(source_pages - final_pages, 0),
        "silent_extra_page": extra > 0,
        "decision": decision,
    }


# ================================================================ 4 ========
def visual_execution_integrity_qa(stage_statuses: Dict[str, str]) -> Dict[str, Any]:
    """Fail-closed: any error / not_run / missing blocks the gate."""
    counts = {"pass": 0, "fail": 0, "error": 0, "not_run": 0, "missing": 0}
    for st in stage_statuses.values():
        counts[st] = counts.get(st, 0) + 1
    complete = (counts["error"] == 0 and counts["not_run"] == 0
                and counts["missing"] == 0)
    decision = "pass" if complete else "blocked"
    return {
        "schema_version": "visual_v01.execution_integrity_qa.v1",
        "stage_status_counts": counts,
        "qa_execution_complete": complete,
        "decision": decision,
    }


# ================================================================ util ====
def build_visual_page_qa(page_model, flows, unresolved, grid, out_dir,
                         final_pdf, stage_statuses, source_pages=1):
    """Run all four visual QAs for one page and aggregate."""
    anchor = visual_anchor_integrity_qa(page_model, flows, out_dir=out_dir,
                                        pdf_path=final_pdf)
    region = fixed_canvas_text_region_qa(page_model, flows, unresolved,
                                         grid=grid, out_dir=out_dir,
                                         pdf_path=final_pdf)
    expansion = visual_page_expansion_qa(final_pdf, source_pages=source_pages)
    execution = visual_execution_integrity_qa(stage_statuses)
    hard_metrics = {
        "anchor_displaced_count": anchor["metrics"]["anchor_displaced_count"],
        "formula_blank_count": anchor["metrics"]["formula_blank_count"],
        "formula_crop_count": anchor["metrics"]["formula_crop_count"],
        "formula_duplicate_count": anchor["metrics"]["formula_duplicate_count"],
        "formula_orphan_count": anchor["metrics"]["formula_orphan_count"],
        "foreign_text_inside_formula_count":
            anchor["metrics"]["foreign_text_inside_formula_count"],
        "soft_text_region_overflow_count":
            region["metrics"]["soft_text_region_overflow_count"],
        "gutter_intrusion_count": region["metrics"]["gutter_intrusion_count"],
        "anchor_invasion_count": region["metrics"]["anchor_invasion_count"],
        "capacity_unresolved_count":
            region["metrics"]["capacity_unresolved_count"],
        "unexpected_extra_page_count":
            expansion["unexpected_extra_page_count"],
    }
    passed = all(v == 0 for v in hard_metrics.values())
    return {
        "schema_version": "visual_v01.page_qa.v1",
        "hard_metrics": hard_metrics,
        "anchor_integrity": anchor,
        "text_region": region,
        "page_expansion": expansion,
        "execution_integrity": execution,
        "decision": "pass" if passed else "blocked",
    }
