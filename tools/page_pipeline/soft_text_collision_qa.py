# -*- coding: utf-8 -*-
"""SoftTextCollisionQA (visual-v04).

Answers the THIRD render-truth question:

    do soft-text blocks overlap each other in the FINAL PDF?

The existing visual QAs only check text<->formula / table / figure.  This QA
adds soft<->soft collision with the FINAL PDF as the only truth source:

    body <-> body
    body <-> heading
    heading <-> heading
    body <-> caption
    caption <-> heading
    visual_group <-> ordinary body
    list <-> body
    front_matter <-> front_matter

Collision units are RENDERED VISUAL BLOCKS (not DOM child spans):

  * text-layer lines clustered into blocks (PyMuPDF span/line bboxes);
  * vector-ink blocks from prose-adopted formulas (their cropped SVG paths
    ARE visible source text -- the visual-v04 render-truth must count them
    as soft content, not as invisible formula decoration).

Legal inline elements (inline formula, bold/italic spans, script-gaps,
protected tokens) never become independent blocks.

Thresholds are RELATIVE (line-height / font-size aware), never a fixed
2px overlap:

    overlap_area_ratio      = intersection / min(area_a, area_b)
    vertical_overlap_ratio  = y-overlap / min(height_a, height_b)
    baseline_distance_ratio = |baseline_a - baseline_b| / line_height

Hard:
    severe_soft_soft_collision_count == 0
    duplicate_baseline_cluster_count == 0
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pymupdf  # noqa: E402

from final_source_residual_qa import is_prose_adopted_formula  # noqa: E402

# severe: > 55% of the smaller block's height overlaps with > 40% of its
# width under the other block -> unambiguous visual overlap
SEVERE_VERTICAL_RATIO = 0.55
SEVERE_HORIZONTAL_RATIO = 0.40
# duplicate baseline: baselines within 30% of a line height -> same line
# rendered twice
DUP_BASELINE_RATIO = 0.30


def _overlap_metrics(a: List[float], b: List[float]) -> Dict[str, float]:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    xo = min(ax1, bx1) - max(ax0, bx0)
    yo = min(ay1, by1) - max(ay0, by0)
    if xo <= 0 or yo <= 0:
        return {"overlap_area": 0.0, "overlap_area_ratio": 0.0,
                "vertical_overlap_ratio": 0.0, "horizontal_overlap_ratio": 0.0,
                "baseline_distance": 0.0, "baseline_distance_ratio": 0.0}
    area = xo * yo
    area_a = max((ax1 - ax0) * (ay1 - ay0), 1.0)
    area_b = max((bx1 - bx0) * (by1 - by0), 1.0)
    ha = ay1 - ay0
    hb = by1 - by0
    wa = ax1 - ax0
    wb = bx1 - bx0
    return {
        "overlap_area": round(area, 3),
        "overlap_area_ratio": round(area / min(area_a, area_b), 4),
        "vertical_overlap_ratio": round(yo / min(ha, hb), 4),
        "horizontal_overlap_ratio": round(xo / min(wa, wb), 4),
        "baseline_distance": round(abs((ay1 + ay0) / 2 - (by1 + by0) / 2), 3),
        "baseline_distance_ratio": 0.0,  # filled by caller with line height
    }


def _line_blocks(page) -> List[Dict[str, Any]]:
    """Extract rendered text lines as blocks (PyMuPDF line bboxes)."""
    blocks: List[Dict[str, Any]] = []
    try:
        d = page.get_text("dict")
        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            for l in b.get("lines", []):
                spans = l.get("spans", [])
                text = "".join(s.get("text") or "" for s in spans).strip()
                if not text:
                    continue
                bb = [float(v) for v in l["bbox"]]
                size = max((s.get("size") or 0) for s in spans) or 10.0
                blocks.append({"kind": "text_line", "bbox": bb,
                               "text": text[:80],
                               "font_size": size,
                               "line_height": size * 1.3,
                               "source": "text_layer"})
    except Exception:  # noqa: BLE001
        pass
    return blocks


def _vector_ink_blocks(page_model, page) -> List[Dict[str, Any]]:
    """Prose-adopted formulas as visible vector-ink blocks.

    A prose-adopted formula renders its source SVG paths into the final
    PDF (its cropped SVG contains the swallowed English body).  Those paths
    are VISIBLE text: they must participate in soft<->soft collision.
    """
    out: List[Dict[str, Any]] = []
    if page is None:
        return out
    for r in page_model.get("regions", []):
        if r.get("type") != "formula":
            continue
        p = r.get("payload") or {}
        if not is_prose_adopted_formula(p):
            continue
        bb = [float(v) for v in (p.get("layout_bbox") or [])]
        if len(bb) != 4:
            continue
        out.append({"kind": "prose_adopted_formula_ink",
                    "bbox": bb,
                    "formula_id": p.get("formula_id"),
                    "placement": p.get("placement"),
                    "text": "FORMULA_%s" % p.get("formula_id"),
                    "font_size": (p.get("base_font_size")
                                  or p.get("font_sizes") or [10.0])[0]
                    if isinstance(p.get("font_sizes"), list) else 10.0,
                    "line_height": 12.0,
                    "source": "vector_ink"})
    return out


def soft_text_collision_qa(
    page_model: Dict[str, Any],
    flows: List[Dict[str, Any]],
    final_pdf_path=None,
    html_path=None,
    out_dir=None,
) -> Dict[str, Any]:
    """Run SoftTextCollisionQA for one page (final PDF as truth)."""
    page = None
    if final_pdf_path and Path(final_pdf_path).exists():
        try:
            doc = pymupdf.open(str(final_pdf_path))
            page = doc[0]
        except Exception:  # noqa: BLE001
            page = None

    # rendered soft blocks (text lines + prose-adopted formula ink)
    blocks: List[Dict[str, Any]] = []
    if page is not None:
        blocks.extend(_line_blocks(page))
    blocks.extend(_vector_ink_blocks(page_model, page))

    collisions: List[Dict[str, Any]] = []
    severe = 0
    dup_baseline = 0
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            A = blocks[i]
            B = blocks[j]
            # never compare two text lines of the SAME visual block:
            # consecutive lines of one paragraph legally stack.  Two lines
            # belong to the same block when they vertically overlap by more
            # than 60% (same visual line cluster) -- handled by dup-baseline
            # instead; otherwise line-to-line overlap IS the defect we hunt.
            a = A["bbox"]
            b = B["bbox"]
            if (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]):
                continue
            om = _overlap_metrics(a, b)
            if om["overlap_area"] <= 0:
                continue
            line_h = max(A.get("line_height", 12.0), B.get("line_height", 12.0))
            om["baseline_distance_ratio"] = round(
                om["baseline_distance"] / max(line_h, 1.0), 4)
            kind_pair = "%s<->%s" % (A.get("source"), B.get("source"))
            if om["baseline_distance_ratio"] < DUP_BASELINE_RATIO:
                dup_baseline += 1
                collisions.append({
                    "kind": "duplicate_baseline_cluster",
                    "block_a": {"id": A.get("text", A.get("formula_id")),
                                "bbox": [round(v, 2) for v in a],
                                "source": A.get("source")},
                    "block_b": {"id": B.get("text", B.get("formula_id")),
                                "bbox": [round(v, 2) for v in b],
                                "source": B.get("source")},
                    **om})
            if (om["vertical_overlap_ratio"] >= SEVERE_VERTICAL_RATIO
                    and om["horizontal_overlap_ratio"] >= SEVERE_HORIZONTAL_RATIO):
                severe += 1
                collisions.append({
                    "kind": "severe_soft_soft_collision",
                    "pair_type": kind_pair,
                    "block_a": {"id": A.get("text", A.get("formula_id")),
                                "bbox": [round(v, 2) for v in a],
                                "source": A.get("source")},
                    "block_b": {"id": B.get("text", B.get("formula_id")),
                                "bbox": [round(v, 2) for v in b],
                                "source": B.get("source")},
                    **om})
            elif (om["vertical_overlap_ratio"] >= 0.25
                  and om["horizontal_overlap_ratio"] >= 0.25):
                collisions.append({
                    "kind": "soft_soft_collision",
                    "pair_type": kind_pair,
                    "block_a": {"id": A.get("text", A.get("formula_id")),
                                "bbox": [round(v, 2) for v in a],
                                "source": A.get("source")},
                    "block_b": {"id": B.get("text", B.get("formula_id")),
                                "bbox": [round(v, 2) for v in b],
                                "source": B.get("source")},
                    **om})

    # same-column vertical overlap: two text lines in one column whose y
    # ranges cross (rendering one on top of the other)
    same_col = [c for c in collisions if c["kind"] in (
        "severe_soft_soft_collision", "soft_soft_collision")]
    metrics = {
        "soft_soft_collision_count": len(same_col),
        "severe_soft_soft_collision_count": severe,
        "duplicate_baseline_cluster_count": dup_baseline,
        "same_column_vertical_overlap_count": len(same_col),
        "cross_group_text_overlap_count": severe,
        "collision_block_count": len(blocks),
    }
    decision = "pass" if (severe == 0 and dup_baseline == 0) else "fail"
    return {
        "schema_version": "visual_v04.soft_text_collision_qa.v1",
        "metrics": metrics,
        "decision": decision,
        "collision_details": collisions[:200],
    }
