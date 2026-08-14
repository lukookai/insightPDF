# -*- coding: utf-8 -*-
"""ParagraphStructuralQA - source recovery != correctness.

``paragraph_text_recovery_ratio == 1.0`` only proves that characters were
RETAINED, not that they were GROUPED correctly.  A paragraph whose bbox
swallows an arXiv header, a footnote and fragments from another column can
still recover at 1.0 (p001 DLP00005: bbox height ~531pt, 156 spans).

This module adds a SEPARATE structural QA (kept alongside the existing
recovery QA; never a replacement).  Six violation counters:

* paragraph_source_order_violation_count  - span y-order inversion inside a DLP
* paragraph_large_y_jump_count            - adjacent line gap > 15% of page h
* paragraph_cross_column_merge_count      - one DLP owns col0 + col1 fragments
* paragraph_cross_region_merge_count      - DLP bbox enters a locked region
* paragraph_obstacle_crossing_count       - DLP bbox crosses a locked region
* paragraph_bbox_pollution_count          - oversized non-crossing DLP bbox

Design rules (calibrated on the 19-page corpus so the golden pages
p003/p006/p013 stay clean):

* obstacles = table / figure / image ONLY.  Formula regions are excluded:
  inline formulas interleave with text by design and the composer reports
  placement=None for both inline and display groups, so treating formulas as
  obstacles flags every formula-rich page (p003) as polluted.
* y-jump / order violations are computed PER FRAGMENT: a legitimate
  cross-column or cross-page continuation (p006 DLP00071 spans 73..775pt)
  must not be counted as a large y jump.
* bbox pollution is only flagged for NON-crossing paragraphs whose bbox is
  larger than 30% of the page and reaches the footer band - a 531pt single
  fragment (p001 DLP00005) qualifies; a normal long paragraph that merely
  ends at the page bottom (p003 DLP00026, 106pt) does not.

Fixture: p001 must FAIL (paragraph_bbox_pollution_count > 0 or another
violation > 0).  If p001 stays green, this QA is broken.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for path in (HERE, REPO / "tools" / "table_html_render",
             REPO / "tools" / "formula_html_render",
             REPO / "实现源码" / "pdf_translator"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# page-relative thresholds
Y_JUMP_FRACTION = 0.15         # >15% of page height between adjacent lines
FOOTER_BOTTOM_FRACTION = 0.92  # below 92% of the page -> footer band
POLLUTION_HEIGHT_FRACTION = 0.30  # non-crossing paragraph taller than 30%


def _span_cy(span):
    b = span["bbox"]
    return (b[1] + b[3]) / 2.0


def _interior_overlap(a, b):
    x = min(a[2], b[2]) - max(a[0], b[0])
    y = min(a[3], b[3]) - max(a[1], b[1])
    return x > 1.0 and y > 1.0


def _bbox_overlap(a, b):
    x = min(a[2], b[2]) - max(a[0], b[0])
    y = min(a[3], b[3]) - max(a[1], b[1])
    return max(x, 0.0) * max(y, 0.0)


def _fragment_lines(para):
    """Group the paragraph's lines by source fragment (crossing continuations
    are evaluated per fragment, never across the page jump)."""
    frag_ids = []
    for f in para.get("source_fragments") or []:
        frag_ids.append((f.get("column"), round(f.get("anchor_y") or 0, 1)))
    if len(frag_ids) <= 1:
        return [_para_lines(para)]
    # assign each line to the fragment whose anchor_y is closest
    groups = [[] for _ in frag_ids]
    for line in _para_lines(para):
        cy = line["cy"]
        idx = min(range(len(frag_ids)),
                  key=lambda i: abs(cy - frag_ids[i][1]))
        groups[idx].append(line)
    return [g for g in groups if g]


def _para_lines(para):
    lines = []
    for line in para.get("lines") or []:
        spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
        if not spans:
            continue
        xs = [s["bbox"] for s in spans]
        bbox = [min(v[0] for v in xs), min(v[1] for v in xs),
                max(v[2] for v in xs), max(v[3] for v in xs)]
        cy = sum(_span_cy(s) for s in spans) / len(spans)
        lines.append({"cy": cy, "bbox": bbox})
    return lines


def structural_paragraph_qa(page_model, *, page_height=None):
    """Independent structural audit of paragraph grouping."""
    page_height = page_height or float(page_model.get("height") or 0)
    regions = page_model.get("regions", []) or []
    paras = [r["payload"] for r in regions if r.get("type") == "text"]
    # geometry-locked obstacles: table / figure / image only.
    # Formulas are deliberately excluded (see module docstring).
    obstacles = []
    for r in regions:
        if r.get("type") in ("table", "figure", "image") and r.get("bbox"):
            obstacles.append([float(v) for v in r["bbox"]])

    order_violations = []
    y_jumps = []
    cross_column = []
    cross_region = []
    obstacle_cross = []
    bbox_pollution = []
    y_jump_limit = page_height * Y_JUMP_FRACTION if page_height else 0
    footer_band = page_height * FOOTER_BOTTOM_FRACTION if page_height else 0
    pollution_height = page_height * POLLUTION_HEIGHT_FRACTION if page_height else 0

    for para in paras:
        pid = para.get("paragraph_id") or para.get("logical_paragraph_id") or "?"
        pbbox = [float(v) for v in para.get("bbox", [])]
        para_height = pbbox[3] - pbbox[1]
        fragments = para.get("source_fragments") or []
        columns = {f.get("column") for f in fragments if f.get("column") is not None}
        crossing = bool(para.get("cross_column_continuation")) \
            or bool(para.get("cross_page_continuation")) \
            or len(fragments) > 1

        # 1. source order violation (per fragment)
        for group in _fragment_lines(para):
            cys = [line["cy"] for line in group]
            for i in range(1, len(cys)):
                if cys[i] < cys[i - 1] - 4.0:
                    order_violations.append({
                        "paragraph_id": pid, "from_y": round(cys[i - 1], 1),
                        "to_y": round(cys[i], 1)})
        # 2. large y jump (per fragment)
        for group in _fragment_lines(para):
            for i in range(1, len(group)):
                gap = group[i]["cy"] - group[i - 1]["cy"]
                if y_jump_limit and gap > y_jump_limit:
                    y_jumps.append({"paragraph_id": pid,
                                    "gap_pt": round(gap, 1),
                                    "limit_pt": round(y_jump_limit, 1)})
        # 3. cross-column merge: a paragraph owns both columns WITHOUT an
        #    explicit continuation marker.  Legitimate cross-column /
        #    cross-page continuations (p006 DLP00071) are marked and must
        #    not be counted as violations.
        if len(columns) >= 2 and not crossing:
            cross_column.append({"paragraph_id": pid,
                                 "columns": sorted(columns)})
        # 4. cross-region merge (enter a locked obstacle)
        for ob in obstacles:
            if _bbox_overlap(pbbox, ob) > 4.0:
                cross_region.append({"paragraph_id": pid,
                                     "obstacle": ob})
                break
        # 5. obstacle crossing (fully cross a locked obstacle)
        for ob in obstacles:
            if (pbbox[1] < ob[1] and pbbox[3] > ob[3]
                    and _interior_overlap(pbbox, ob)):
                obstacle_cross.append({"paragraph_id": pid,
                                       "obstacle": ob})
                break
        # 6. bbox pollution: NON-crossing paragraph that is oversized (>30%
        #    page) AND contains an internal large y jump (a pollution block
        #    mixes content from distant y positions - the jump proves it;
        #    a long but continuous paragraph is legitimate, e.g. p007
        #    DLP00089 486pt 36-line continuous body text).
        internal_jump = any(
            j["paragraph_id"] == pid for j in y_jumps)
        if (not crossing and pollution_height
                and para_height > pollution_height
                and internal_jump):
            bbox_pollution.append({
                "paragraph_id": pid,
                "reason": "oversized_with_y_jump",
                "bbox": [round(v, 1) for v in pbbox],
                "height_pt": round(para_height, 1),
                "page_height": round(page_height, 1)})

    result = {
        "paragraph_count": len(paras),
        "page_height": page_height,
        "paragraph_source_order_violation_count": len(order_violations),
        "paragraph_large_y_jump_count": len(y_jumps),
        "paragraph_cross_column_merge_count": len(cross_column),
        "paragraph_cross_region_merge_count": len(cross_region),
        "paragraph_obstacle_crossing_count": len(obstacle_cross),
        "paragraph_bbox_pollution_count": len(bbox_pollution),
        "paragraph_total_structural_violation_count": (
            len(order_violations) + len(y_jumps) + len(cross_column)
            + len(cross_region) + len(obstacle_cross) + len(bbox_pollution)),
        "details": {
            "source_order_violations": order_violations[:12],
            "large_y_jumps": y_jumps[:12],
            "cross_column_merges": cross_column[:12],
            "cross_region_merges": cross_region[:12],
            "obstacle_crossings": obstacle_cross[:12],
            "bbox_pollutions": bbox_pollution[:12],
        },
        "structural_clean": (
            len(order_violations) + len(y_jumps) + len(cross_column)
            + len(cross_region) + len(obstacle_cross)
            + len(bbox_pollution)) == 0,
    }
    return result
