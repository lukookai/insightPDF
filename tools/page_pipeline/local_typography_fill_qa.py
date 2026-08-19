# -*- coding: utf-8 -*-
"""QA for source-relative typography fill inside locked text slots.

The candidate decision is visual, never textual: source occupancy comes from
the source spans already present in the PageModel, with SourceInkGeometry as
the fallback for recovered prose.  Target occupancy comes from Chromium text
range rectangles recorded by Task 4B.  Slot geometry remains immutable.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont


MIN_FILL_OCCUPANCY_GAP = 0.05
MIN_SOURCE_OCCUPANCY = 0.45
SLOT_TOLERANCE = 0.15
EXCESSIVE_OCCUPANCY_ALLOWANCE = 0.04

ROLE_FILL_CAPS = {
    "body": {"font_scale": 1.03, "line_height_scale": 1.08},
    "heading": {"font_scale": 1.01, "line_height_scale": 1.03},
    "caption": {"font_scale": 1.02, "line_height_scale": 1.06},
    "footnote": {"font_scale": 1.01, "line_height_scale": 1.03},
}


def _bbox(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return []
    return [round(float(value), 3) for value in values]


def _union(boxes: Iterable[Any]) -> list[float]:
    values = [_bbox(box) for box in boxes]
    values = [box for box in values if box]
    if not values:
        return []
    return [round(min(box[0] for box in values), 3),
            round(min(box[1] for box in values), 3),
            round(max(box[2] for box in values), 3),
            round(max(box[3] for box in values), 3)]


def _canonical_role(value: Any) -> str:
    role = str(value or "body").strip().lower().replace("-", "_")
    if role in {"title", "section_heading", "subheading"}:
        return "heading"
    if role in {"figure_caption", "table_caption"}:
        return "caption"
    if role in {"note", "footer"}:
        return "footnote"
    return role if role in ROLE_FILL_CAPS else "body"


def _clamp_to_slot(box: list[float], slot: list[float]) -> list[float]:
    if len(box) != 4 or len(slot) != 4:
        return []
    clamped = [max(box[0], slot[0]), max(box[1], slot[1]),
               min(box[2], slot[2]), min(box[3], slot[3])]
    return _bbox(clamped) if (clamped[2] > clamped[0]
                              and clamped[3] > clamped[1]) else []


def _outside(box: list[float], slot: list[float],
             tolerance: float = SLOT_TOLERANCE) -> bool:
    if len(box) != 4 or len(slot) != 4:
        return True
    return (box[0] < slot[0] - tolerance
            or box[1] < slot[1] - tolerance
            or box[2] > slot[2] + tolerance
            or box[3] > slot[3] + tolerance)


def _overlap_area(first: list[float], second: list[float]) -> float:
    if len(first) != 4 or len(second) != 4:
        return 0.0
    width = max(0.0, min(first[2], second[2])
                - max(first[0], second[0]))
    height = max(0.0, min(first[3], second[3])
                 - max(first[1], second[1]))
    return width * height


def _page_span_index(page_model: dict[str, Any]) -> dict[str, list[float]]:
    spans = {}
    for region in page_model.get("regions") or []:
        if region.get("type") != "text":
            continue
        paragraph = region.get("payload") or {}
        for line in paragraph.get("lines") or []:
            for span in line.get("spans") or []:
                span_id = str(span.get("id") or "")
                box = _bbox(span.get("bbox"))
                if span_id and box:
                    spans[span_id] = box
    return spans


def source_painted_geometry(
        page_model: dict[str, Any], slot_model: dict[str, Any], *,
        source_ink: Any | None = None) -> dict[str, dict[str, Any]]:
    """Measure source text paint for every slot without using text length."""
    span_index = _page_span_index(page_model)
    result = {}
    for slot in slot_model.get("slots") or []:
        slot_id = str(slot.get("slot_id") or "")
        slot_box = _bbox(slot.get("source_bbox"))
        span_ids = [str(value) for value in
                    (slot.get("source_topology") or {}).get(
                        "source_span_ids") or []]
        span_boxes = [span_index[value] for value in span_ids
                      if value in span_index]
        painted = _clamp_to_slot(_union(span_boxes), slot_box)
        origin = "source_spans" if painted else ""
        if not painted and source_ink is not None and slot_box:
            painted = _clamp_to_slot(
                _bbox(source_ink.text_ink_bbox(slot_box)), slot_box)
            origin = "source_ink_geometry" if painted else ""
        slot_height = max(slot_box[3] - slot_box[1], 0.0) \
            if slot_box else 0.0
        painted_height = max(painted[3] - painted[1], 0.0) \
            if painted else 0.0
        result[slot_id] = {
            "slot_id": slot_id,
            "slot_height": round(slot_height, 3),
            "source_painted_bbox": painted,
            "source_painted_top": painted[1] if painted else None,
            "source_painted_bottom": painted[3] if painted else None,
            "source_painted_height": round(painted_height, 3),
            "source_occupancy_ratio": round(
                painted_height / slot_height, 6) if slot_height else 0.0,
            "source_painted_origin": origin or "unavailable",
        }
    return result


def _figure_boxes(page_model: dict[str, Any]) -> list[dict[str, Any]]:
    figures = []
    for region in page_model.get("regions") or []:
        if str(region.get("type") or "").lower() != "figure":
            continue
        box = _bbox(region.get("bbox"))
        if box:
            figures.append({
                "anchor_id": str(region.get("region_id") or "figure"),
                "anchor_type": "figure", "bbox": box,
            })
    return figures


def _collision_fragments(collision_qa: dict[str, Any]) -> set[str]:
    fragments = set()
    for row in collision_qa.get("collisions") or []:
        for key in ("predecessor_fragment_id", "successor_fragment_id"):
            value = str(row.get(key) or "")
            if value:
                fragments.add(value)
    return fragments


def detect_typography_fill_candidates(
        page_model: dict[str, Any], slot_model: dict[str, Any],
        lock_trace: dict[str, Any], collision_qa: dict[str, Any], *,
        source_ink: Any | None = None,
        translation_closure_pass: bool = True,
        min_occupancy_gap: float = MIN_FILL_OCCUPANCY_GAP) -> dict[str, Any]:
    """Return QA-FIRST candidate decisions for a Task 4B artifact."""
    slots = {str(row.get("slot_id") or ""): row
             for row in slot_model.get("slots") or []}
    source = source_painted_geometry(
        page_model, slot_model, source_ink=source_ink)
    figures = _figure_boxes(page_model)
    collision_fragments = _collision_fragments(collision_qa)
    page_collision_free = int((collision_qa.get("metrics") or {}).get(
        "final_block_collision_count", 0)) == 0
    # Fit has page-level priority.  Mixing expansion into a page that already
    # needed locked-slot compression creates opposing rhythms and can undo a
    # carefully closed capacity case.  This is a
    # document-general safety rule, not a page/id exception.
    page_has_fit = any(
        row.get("geometry_locked")
        and (str(row.get("fit_level") or "L0") != "L0"
             or float(row.get("font_scale") or 1.0) < 0.999
             or float(row.get("line_height_scale") or 1.0) < 0.999)
        for row in lock_trace.get("records") or [])
    records = []
    for raw in lock_trace.get("records") or []:
        if not raw.get("geometry_locked"):
            continue
        record = dict(raw)
        slot_id = str(record.get("slot_id") or "")
        slot = slots.get(slot_id) or {}
        source_row = source.get(slot_id) or {}
        source_box = _bbox(record.get("source_bbox")
                           or slot.get("source_bbox"))
        target_box = _bbox(record.get("painted_content_bbox"))
        slot_height = float(source_row.get("slot_height") or 0.0)
        target_height = max(target_box[3] - target_box[1], 0.0) \
            if target_box else 0.0
        source_ratio = float(
            source_row.get("source_occupancy_ratio") or 0.0)
        target_ratio = (target_height / slot_height) if slot_height else 0.0
        gap = source_ratio - target_ratio
        fragment = str(record.get("flow_fragment_id") or "")
        fit_applied = (str(record.get("fit_level") or "L0") != "L0"
                       or float(record.get("font_scale") or 1.0) < 0.999
                       or float(record.get("line_height_scale") or 1.0)
                       < 0.999)
        anchor_hits = [figure for figure in figures
                       if _overlap_area(source_box, figure["bbox"]) > 0.01]
        overflow = _outside(target_box, source_box)
        correctness_ok = (
            translation_closure_pass and page_collision_free
            and fragment not in collision_fragments
            and record.get("fit_success") is not False and not overflow
            and bool(target_box) and bool(
                source_row.get("source_painted_bbox")))
        if anchor_hits:
            decision = "SKIPPED_ANCHOR_OVERLAP"
            candidate = False
        elif fit_applied:
            decision = "FIT_APPLIED"
            candidate = False
        elif page_has_fit:
            decision = "SKIPPED_PAGE_HAS_FIT"
            candidate = False
        elif not correctness_ok:
            decision = "SKIPPED_CORRECTNESS_DEFECT"
            candidate = False
        elif (source_ratio >= MIN_SOURCE_OCCUPANCY
              and gap >= float(min_occupancy_gap)):
            decision = "FILL_CANDIDATE"
            candidate = True
        else:
            decision = "KEEP_NO_FILL_NEEDED"
            candidate = False
        records.append({
            **record, **source_row,
            "semantic_role": _canonical_role(record.get("semantic_role")),
            "target_painted_bbox_before": target_box,
            "target_painted_top_before": (
                target_box[1] if target_box else None),
            "target_painted_bottom_before": (
                target_box[3] if target_box else None),
            "target_painted_height_before": round(target_height, 3),
            "target_occupancy_ratio_before": round(target_ratio, 6),
            "occupancy_gap_before": round(gap, 6),
            "fill_candidate": candidate,
            "candidate_decision": decision,
            "fit_applied": fit_applied,
            "anchor_overlap_ids": [row["anchor_id"] for row in anchor_hits],
            "translation_closure_pass": bool(translation_closure_pass),
            "correctness_pass": correctness_ok,
        })
    candidates = [row for row in records if row["fill_candidate"]]
    gaps = [float(row["occupancy_gap_before"]) for row in candidates]
    return {
        "schema_version": "visual_v07.local_typography_fill_qa_first.v1",
        "measurement_truth": {
            "source": "PageModel source spans; SourceInkGeometry fallback",
            "target": "Chromium Range painted-content rectangles",
            "character_count_used": False,
        },
        "thresholds": {
            "min_fill_occupancy_gap": float(min_occupancy_gap),
            "min_source_occupancy": MIN_SOURCE_OCCUPANCY,
        },
        "records": records,
        "metrics": {
            "typography_fill_candidate_count": len(candidates),
            "fill_skipped_due_to_anchor_overlap_count": sum(
                row["candidate_decision"] == "SKIPPED_ANCHOR_OVERLAP"
                for row in records),
            "fit_applied_excluded_count": sum(
                row["candidate_decision"] == "FIT_APPLIED"
                for row in records),
            "fill_skipped_due_to_page_fit_count": sum(
                row["candidate_decision"] == "SKIPPED_PAGE_HAS_FIT"
                for row in records),
            "source_painted_geometry_missing_count": sum(
                not row.get("source_painted_bbox") for row in records),
            "occupancy_gap_before_mean": round(
                sum(gaps) / len(gaps), 6) if gaps else 0.0,
        },
        "decision": "red_confirmed" if candidates else "no_candidate",
    }


def typography_fill_qa(
        candidate_qa: dict[str, Any], fill_trace: dict[str, Any],
        final_collision_qa: dict[str, Any]) -> dict[str, Any]:
    """Audit an applied fill trace against candidates, caps, and geometry."""
    candidates = {str(row.get("flow_fragment_id") or ""): row
                  for row in candidate_qa.get("records") or []}
    final_records = []
    for raw in fill_trace.get("records") or []:
        fragment = str(raw.get("flow_fragment_id") or "")
        before = candidates.get(fragment) or {}
        record = {**before, **raw}
        role = _canonical_role(record.get("semantic_role"))
        cap = ROLE_FILL_CAPS[role]
        source_box = _bbox(record.get("source_bbox"))
        final_box = _bbox(record.get("painted_content_bbox_after")
                          or record.get("target_painted_bbox_after")
                          or record.get("painted_content_bbox"))
        envelope = _bbox(record.get("final_envelope_bbox"))
        source_ratio = float(record.get("source_occupancy_ratio") or 0.0)
        after_ratio = float(record.get("target_occupancy_ratio_after")
                            or record.get("target_occupancy_ratio_before")
                            or 0.0)
        applied = bool(record.get("fill_applied"))
        final_records.append({
            **record,
            "semantic_role": role,
            "typography_fill_unnecessary": bool(
                applied and not before.get("fill_candidate")),
            "typography_fill_excessive": bool(
                applied and after_ratio > source_ratio
                + EXCESSIVE_OCCUPANCY_ALLOWANCE),
            "typography_fill_overflow": bool(
                applied and _outside(final_box, source_box)),
            "typography_fill_slot_mutation": bool(
                applied and (len(envelope) != 4
                             or any(abs(envelope[index]
                                        - source_box[index]) > SLOT_TOLERANCE
                                    for index in range(4)))),
            "typography_fill_font_cap_violation": bool(
                float(record.get("fill_font_scale") or 1.0)
                > cap["font_scale"] + 1e-6),
            "typography_fill_line_height_cap_violation": bool(
                float(record.get("fill_line_height_scale") or 1.0)
                > cap["line_height_scale"] + 1e-6),
        })
    applied_fragments = {
        str(row.get("flow_fragment_id") or "")
        for row in final_records if row.get("fill_applied")}
    fill_collisions = [
        row for row in final_collision_qa.get("collisions") or []
        if str(row.get("predecessor_fragment_id") or "")
        in applied_fragments
        or str(row.get("successor_fragment_id") or "")
        in applied_fragments]
    metrics = {
        "typography_fill_candidate_count": int(
            (candidate_qa.get("metrics") or {}).get(
                "typography_fill_candidate_count", 0)),
        "typography_fill_applied_count": sum(
            bool(row.get("fill_applied")) for row in final_records),
        "typography_fill_success_count": sum(
            bool(row.get("fill_success")) for row in final_records),
        "typography_fill_unnecessary_count": sum(
            row["typography_fill_unnecessary"] for row in final_records),
        "typography_fill_excessive_count": sum(
            row["typography_fill_excessive"] for row in final_records),
        "typography_fill_overflow_count": sum(
            row["typography_fill_overflow"] for row in final_records),
        "typography_fill_collision_count": len(fill_collisions),
        "typography_fill_slot_mutation_count": sum(
            row["typography_fill_slot_mutation"] for row in final_records),
        "typography_fill_font_cap_violation_count": sum(
            row["typography_fill_font_cap_violation"]
            for row in final_records),
        "typography_fill_line_height_cap_violation_count": sum(
            row["typography_fill_line_height_cap_violation"]
            for row in final_records),
        "fill_skipped_due_to_anchor_overlap_count": int(
            (candidate_qa.get("metrics") or {}).get(
                "fill_skipped_due_to_anchor_overlap_count", 0)),
    }
    applied = [row for row in final_records if row.get("fill_applied")]
    before_gaps = [float(row.get("occupancy_gap_before") or 0.0)
                   for row in applied]
    after_gaps = [float(row.get("occupancy_gap_after") or 0.0)
                  for row in applied]
    metrics["occupancy_gap_before_mean"] = round(
        sum(before_gaps) / len(before_gaps), 6) if before_gaps else 0.0
    metrics["occupancy_gap_after_mean"] = round(
        sum(after_gaps) / len(after_gaps), 6) if after_gaps else 0.0
    hard_names = (
        "typography_fill_unnecessary_count",
        "typography_fill_excessive_count", "typography_fill_overflow_count",
        "typography_fill_collision_count",
        "typography_fill_slot_mutation_count",
        "typography_fill_font_cap_violation_count",
        "typography_fill_line_height_cap_violation_count",
    )
    return {
        "schema_version": "visual_v07.local_typography_fill_qa.v1",
        "decision": ("pass" if all(metrics[name] == 0
                                    for name in hard_names) else "blocked"),
        "metrics": metrics, "records": final_records,
    }


def _font(size: int = 16) -> ImageFont.ImageFont:
    for path in ("C:/Windows/Fonts/arial.ttf",
                 "C:/Windows/Fonts/msyh.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def render_typography_fill_overlay(
        fill_qa: dict[str, Any], screenshot_path: str | Path,
        output_path: str | Path, *, page_width: float,
        page_height: float, title: str = "Typography Fill") -> Path:
    """Render slot boxes plus a complete before/after occupancy ledger."""
    with Image.open(screenshot_path) as source:
        page = source.convert("RGB")
    panel_width = 1050
    row_height = 112
    records = list(fill_qa.get("records") or [])
    height = max(page.height, 55 + row_height * len(records))
    canvas = Image.new("RGB", (page.width + panel_width, height), "white")
    canvas.paste(page, (0, 0))
    draw = ImageDraw.Draw(canvas)
    font = _font(15)
    small = _font(13)
    draw.text((page.width + 12, 10), title, fill="#111111", font=font)
    scale_x = page.width / float(page_width)
    scale_y = page.height / float(page_height)
    colors = {
        "success": "#0A9B50", "keep": "#1769E0",
        "skip": "#F28C18", "defect": "#D7191C",
    }
    for index, record in enumerate(records, start=1):
        defect = any(record.get(name) for name in (
            "typography_fill_unnecessary", "typography_fill_excessive",
            "typography_fill_overflow", "typography_fill_slot_mutation",
            "typography_fill_font_cap_violation",
            "typography_fill_line_height_cap_violation"))
        decision = str(record.get("candidate_decision") or "")
        if defect:
            state, color = "RED_DEFECT", colors["defect"]
        elif record.get("fill_success"):
            state, color = "GREEN_FILL_SUCCESS", colors["success"]
        elif decision == "KEEP_NO_FILL_NEEDED":
            state, color = "BLUE_NO_FILL_NEEDED", colors["keep"]
        else:
            state, color = "ORANGE_SKIPPED", colors["skip"]
        box = _bbox(record.get("source_bbox"))
        if box:
            pixel = (round(box[0] * scale_x), round(box[1] * scale_y),
                     round(box[2] * scale_x), round(box[3] * scale_y))
            draw.rectangle(pixel, outline=color, width=4)
            draw.rectangle((pixel[0], pixel[1], pixel[0] + 25,
                            pixel[1] + 21), fill=color)
            draw.text((pixel[0] + 5, pixel[1] + 2), str(index),
                      fill="white", font=small)
        y = 42 + (index - 1) * row_height
        draw.rectangle((page.width + 8, y - 2,
                        canvas.width - 8, y + row_height - 8),
                       outline=color, width=2)
        source_occ = float(record.get("source_occupancy_ratio") or 0.0)
        before_occ = float(record.get(
            "target_occupancy_ratio_before") or 0.0)
        after_occ = float(record.get(
            "target_occupancy_ratio_after") or before_occ)
        lines = [
            "#%d %s | %s | %s | %s" % (
                index, record.get("slot_id"), record.get("render_id"),
                record.get("semantic_role"), state),
            "source-occ=%.3f | target-before=%.3f | target-after=%.3f | gap %.3f -> %.3f" % (
                source_occ, before_occ, after_occ,
                float(record.get("occupancy_gap_before") or 0.0),
                float(record.get("occupancy_gap_after")
                      if record.get("occupancy_gap_after") is not None
                      else record.get("occupancy_gap_before") or 0.0)),
            "level=%s | font=%.3f | line=%.3f | locked=%s | fit=%s | fill=%s" % (
                record.get("fill_level") or "F0",
                float(record.get("fill_font_scale") or 1.0),
                float(record.get("fill_line_height_scale") or 1.0),
                str(bool(record.get("geometry_locked"))).lower(),
                str(bool(record.get("fit_applied"))).lower(),
                str(bool(record.get("fill_applied"))).lower()),
            "decision=%s" % decision,
        ]
        for offset, line in enumerate(lines):
            draw.text((page.width + 15, y + 4 + offset * 23), line,
                      fill="#222222", font=small)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
    return output


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page-model", required=True)
    parser.add_argument("--slots", required=True)
    parser.add_argument("--lock-trace", required=True)
    parser.add_argument("--collision-qa", required=True)
    parser.add_argument("--source-pdf", required=True)
    parser.add_argument("--page-index", type=int, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    from source_ink_geometry import SourceInkGeometry

    model = _load(args.page_model)
    source_ink = SourceInkGeometry(
        args.source_pdf, model, args.page_index)
    result = detect_typography_fill_candidates(
        model, _load(args.slots), _load(args.lock_trace),
        _load(args.collision_qa), source_ink=source_ink)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(json.dumps({"decision": result["decision"],
                      "metrics": result["metrics"]},
                     ensure_ascii=False, indent=2))
    return 0 if result["decision"] == "red_confirmed" else 2


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "EXCESSIVE_OCCUPANCY_ALLOWANCE", "MIN_FILL_OCCUPANCY_GAP",
    "MIN_SOURCE_OCCUPANCY", "ROLE_FILL_CAPS",
    "detect_typography_fill_candidates", "source_painted_geometry",
    "render_typography_fill_overlay", "typography_fill_qa",
]
