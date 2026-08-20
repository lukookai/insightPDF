# -*- coding: utf-8 -*-
"""Read-only audit of source text ownership inside Figure hard anchors.

The audit deliberately does not change ownership, Figure geometry, crop
behavior, translation, or rendering.  Figure membership is inferred from
source geometry: a span belongs to the diagnostic set when its bbox centre is
inside a Figure bbox and at least half of its ink bbox overlaps that Figure.
Text content is evidence only and never participates in classification.
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from page_model import extract_source_objects  # noqa: E402


FIGURE_MEMBERSHIP_MIN_OVERLAP = 0.50
OWNER_MARGIN_PT = 3.0


def _bbox(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return []
    return [round(float(value), 3) for value in values]


def _area(box: list[float]) -> float:
    return max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0) \
        if len(box) == 4 else 0.0


def _intersection(first: list[float], second: list[float]) -> list[float]:
    if len(first) != 4 or len(second) != 4:
        return []
    box = [max(first[0], second[0]), max(first[1], second[1]),
           min(first[2], second[2]), min(first[3], second[3])]
    return box if box[2] > box[0] and box[3] > box[1] else []


def _overlap_ratio(container: list[float], item: list[float]) -> float:
    return _area(_intersection(container, item)) / max(_area(item), 1e-9)


def _center_inside(container: list[float], item: list[float]) -> bool:
    if len(container) != 4 or len(item) != 4:
        return False
    center_x = (item[0] + item[2]) / 2.0
    center_y = (item[1] + item[3]) / 2.0
    return (container[0] <= center_x <= container[2]
            and container[1] <= center_y <= container[3])


def _fully_contained(container: list[float], item: list[float],
                     margin: float = 0.0) -> bool:
    return bool(len(container) == 4 and len(item) == 4
                and container[0] - margin <= item[0]
                and container[1] - margin <= item[1]
                and container[2] + margin >= item[2]
                and container[3] + margin >= item[3])


def _margin_deficit(container: list[float], item: list[float],
                    margin: float) -> dict[str, float]:
    return {
        "left": round(max(container[0] - margin - item[0], 0.0), 3),
        "top": round(max(container[1] - margin - item[1], 0.0), 3),
        "right": round(max(item[2] - (container[2] + margin), 0.0), 3),
        "bottom": round(max(item[3] - (container[3] + margin), 0.0), 3),
    }


def _span_paragraph_index(page_model: dict[str, Any]
                          ) -> dict[str, dict[str, Any]]:
    output = {}
    for region in page_model.get("regions") or []:
        if region.get("type") != "text":
            continue
        paragraph = region.get("payload") or {}
        for span_id in paragraph.get("span_ids") or []:
            output[str(span_id)] = {
                "paragraph_id": paragraph.get("paragraph_id"),
                "logical_paragraph_id": paragraph.get(
                    "logical_paragraph_id"),
                "semantic_role": paragraph.get("semantic_role")
                                 or paragraph.get("style_role") or "body",
                "style_role": paragraph.get("style_role") or "body",
                "reading_order": paragraph.get("reading_order"),
                "region_id": region.get("region_id"),
                "source_fragments": paragraph.get("source_fragments") or [],
            }
    return output


def _slot_span_index(slot_model: dict[str, Any]
                     ) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for slot in slot_model.get("slots") or []:
        span_ids = list((slot.get("source_topology") or {}).get(
            "source_span_ids") or [])
        for span_id in span_ids:
            output.setdefault(str(span_id), []).append(slot)
    return output


def _formula_span_index(page_model: dict[str, Any],
                        spans: list[dict[str, Any]]
                        ) -> dict[str, list[str]]:
    """Map official formula spans back to matching component bboxes."""
    official = set((page_model.get("ownership") or {}).get(
        "formula_span_ids") or [])
    output: dict[str, list[str]] = {}
    for span in spans:
        span_id = str(span.get("id") or "")
        if span_id not in official:
            continue
        box = _bbox(span.get("bbox"))
        for region in page_model.get("regions") or []:
            if region.get("type") != "formula":
                continue
            formula = region.get("payload") or {}
            formula_id = str(formula.get("formula_id") or "")
            if any(all(abs(box[index] - float(
                    (component.get("bbox") or [0, 0, 0, 0])[index])) <= 0.08
                       for index in range(4))
                   for component in formula.get("components") or []):
                output.setdefault(span_id, []).append(formula_id)
    return output


def _html_truth(html_path: str | Path) -> dict[str, Any]:
    text = Path(html_path).read_text(encoding="utf-8")
    figures = set(re.findall(r'\bdata-figure="([^"]+)"', text,
                             re.IGNORECASE))
    paragraphs = set(re.findall(r'\bdata-para="([^"]+)"', text,
                                re.IGNORECASE))
    fragments = set(re.findall(r'\bdata-flow-fragment="([^"]+)"', text,
                               re.IGNORECASE))
    formulas = set(re.findall(r'\bdata-formula="([^"]+)"', text,
                              re.IGNORECASE))
    figure_positions = {}
    for match in re.finditer(r'<span\b[^>]*\bdata-figure="([^"]+)"[^>]*>',
                             text, re.IGNORECASE):
        figure_positions[match.group(1)] = match.start()
    paragraph_dom = {}
    for match in re.finditer(
            r'<div\b(?=[^>]*\bclass="[^"]*\bparagraph-block\b[^"]*")'
            r'[^>]*>.*?</div\s*>', text, re.IGNORECASE | re.DOTALL):
        block = match.group(0)
        paragraph_match = re.search(r'\bdata-para="([^"]+)"', block,
                                    re.IGNORECASE)
        fragment_match = re.search(
            r'\bdata-flow-fragment="([^"]+)"', block, re.IGNORECASE)
        visible = html_lib.unescape(re.sub(r'<[^>]+>', '', block)).strip()
        if paragraph_match:
            paragraph_dom[paragraph_match.group(1)] = {
                "flow_fragment_id": (fragment_match.group(1)
                                     if fragment_match else None),
                "visible_text": visible,
                "html_position": match.start(),
            }
    return {
        "figure_ids": figures,
        "paragraph_ids": paragraphs,
        "flow_fragment_ids": fragments,
        "formula_ids": formulas,
        "paragraph_dom": paragraph_dom,
        "figure_positions": figure_positions,
    }


def _owner_sets(page_model: dict[str, Any]) -> dict[str, set[str]]:
    ownership = page_model.get("ownership") or {}
    return {
        "table": set(ownership.get("table_span_ids") or []),
        "formula": set(ownership.get("formula_span_ids") or []),
        "figure": set(ownership.get("figure_span_ids") or []),
        "text": set(ownership.get("text_span_ids") or []),
    }


def _current_owners(span_id: str,
                    owner_sets: dict[str, set[str]]) -> list[str]:
    return [owner for owner, members in owner_sets.items()
            if span_id in members]


def _caption_records(page_model: dict[str, Any], figure: dict[str, Any],
                     slot_index: dict[str, list[dict[str, Any]]],
                     html_truth: dict[str, Any]) -> list[dict[str, Any]]:
    figure_id = str((figure.get("payload") or {}).get("figure_id")
                    or figure.get("region_id") or "")
    figure_box = _bbox(figure.get("bbox"))
    output = []
    for region in page_model.get("regions") or []:
        if region.get("type") != "text":
            continue
        paragraph = region.get("payload") or {}
        role = str(paragraph.get("semantic_role")
                   or paragraph.get("style_role") or "")
        if role != "caption" and paragraph.get("caption_for") != figure_id:
            continue
        box = _bbox(region.get("bbox"))
        # Captions are external only when their centre is outside the Figure
        # and they follow it in source reading order/vertical geometry.
        if _center_inside(figure_box, box) or box[1] < figure_box[3]:
            continue
        span_ids = [str(value) for value in paragraph.get("span_ids") or []]
        slots = [slot for span_id in span_ids
                 for slot in slot_index.get(span_id, [])]
        paragraph_id = str(paragraph.get("paragraph_id") or "")
        output.append({
            "paragraph_id": paragraph_id,
            "span_ids": span_ids,
            "bbox": box,
            "semantic_role": role,
            "caption_for": paragraph.get("caption_for") or figure_id,
            "inside_figure_bbox": False,
            "after_figure_in_source_geometry": True,
            "source_text_slot_created": bool(slots),
            "slot_ids": sorted({str(slot.get("slot_id") or "")
                                for slot in slots}),
            "rendered_as_soft_text": paragraph_id in html_truth[
                "paragraph_ids"],
            "double_owned": False,
        })
    return output


def figure_internal_text_ownership_audit(
        page_model: dict[str, Any], source_pdf_path: str | Path,
        page_index: int, source_text_slots: dict[str, Any],
        final_html_path: str | Path) -> dict[str, Any]:
    """Return complete source->owner->slot->render evidence per Figure span."""
    spans, _, _, _, _, _, _, _ = extract_source_objects(
        str(source_pdf_path), int(page_index))
    owner_sets = _owner_sets(page_model)
    paragraph_index = _span_paragraph_index(page_model)
    slot_index = _slot_span_index(source_text_slots)
    formula_index = _formula_span_index(page_model, spans)
    html_truth = _html_truth(final_html_path)
    records = []
    captions = []
    for figure in [region for region in page_model.get("regions") or []
                   if region.get("type") == "figure"]:
        figure_id = str((figure.get("payload") or {}).get("figure_id")
                        or figure.get("region_id") or "")
        figure_box = _bbox(figure.get("bbox"))
        figure_payload_ids = set(str(value) for value in
                                 (figure.get("payload") or {}).get(
                                     "source_span_ids") or [])
        figure_dom = figure_id in html_truth["figure_ids"]
        captions.extend(_caption_records(
            page_model, figure, slot_index, html_truth))
        for source_index, span in enumerate(spans):
            span_box = _bbox(span.get("bbox"))
            ratio = _overlap_ratio(figure_box, span_box)
            center_inside = _center_inside(figure_box, span_box)
            if not (center_inside
                    and ratio >= FIGURE_MEMBERSHIP_MIN_OVERLAP):
                continue
            span_id = str(span.get("id") or "")
            owners = _current_owners(span_id, owner_sets)
            current_owner = owners[0] if len(owners) == 1 else (
                "ambiguous" if owners else "unowned")
            paragraph = paragraph_index.get(span_id) or {}
            slots = slot_index.get(span_id, [])
            paragraph_id = str(paragraph.get("paragraph_id") or "")
            fragments = [str(fragment.get("flow_fragment_id") or "")
                         for fragment in paragraph.get("source_fragments")
                         or []]
            rendered_soft = bool(
                current_owner == "text"
                and paragraph_id in html_truth["paragraph_ids"]
                and (not fragments or any(fragment in html_truth[
                    "flow_fragment_ids"] for fragment in fragments)))
            final_soft_dom = html_truth["paragraph_dom"].get(
                paragraph_id) or {}
            slot_boxes = [_bbox(slot.get("source_bbox")) for slot in slots]
            slot_figure_intersections = [
                _intersection(figure_box, slot_box)
                for slot_box in slot_boxes]
            slot_figure_intersections = [
                box for box in slot_figure_intersections if box]
            figure_after_soft = bool(
                rendered_soft
                and int(html_truth["figure_positions"].get(
                    figure_id, -1)) > int(final_soft_dom.get(
                        "html_position", -1)))
            formula_ids = formula_index.get(span_id, [])
            formula_rendered = any(formula_id in html_truth["formula_ids"]
                                   for formula_id in formula_ids)
            rendered_inside_figure = bool(figure_dom and ratio > 0.0)
            separately_rendered = rendered_soft or formula_rendered
            double_owned = bool(
                rendered_inside_figure and current_owner != "figure")
            duplicate_render = bool(
                rendered_inside_figure and separately_rendered)
            ambiguous_owner = bool(
                len(owners) != 1 or current_owner != "figure")
            if current_owner == "text":
                semantic_role = paragraph.get("semantic_role") or "body"
            elif current_owner == "formula":
                semantic_role = "figure_internal_formula_annotation"
            else:
                semantic_role = "figure_internal_annotation"
            evidence_complete = bool(
                span_id and span_box and owners and figure_id and figure_dom
                and (not rendered_soft or (paragraph_id and slots)))
            records.append({
                "span_id": span_id,
                "text": str(span.get("text") or ""),
                "bbox": span_box,
                "font": span.get("font"),
                "font_size": round(float(span.get("size") or 0.0), 3),
                "source_reading_order": source_index,
                "semantic_role": semantic_role,
                "current_owner": current_owner,
                "current_owner_ledger_memberships": owners,
                "expected_owner_from_figure_geometry": "figure",
                "figure_id": figure_id,
                "figure_bbox": figure_box,
                "inside_figure_bbox": True,
                "center_inside_figure_bbox": center_inside,
                "fully_contained_in_figure_bbox": _fully_contained(
                    figure_box, span_box),
                "contained_by_current_3pt_owner_margin": _fully_contained(
                    figure_box, span_box, OWNER_MARGIN_PT),
                "owner_margin_deficit_pt": _margin_deficit(
                    figure_box, span_box, OWNER_MARGIN_PT),
                "figure_bbox_overlap_ratio": round(ratio, 4),
                "figure_payload_claims_span": span_id in figure_payload_ids,
                "source_text_slot_created": bool(slots),
                "source_text_slot_ids": sorted({
                    str(slot.get("slot_id") or "") for slot in slots}),
                "source_text_slot_bboxes": [
                    box for box in slot_boxes],
                "source_text_slot_figure_overlap_bboxes": (
                    slot_figure_intersections),
                "source_text_slot_left_exposed_before_figure_pt": (
                    round(max(figure_box[0] - slot_boxes[0][0], 0.0), 3)
                    if slot_boxes else 0.0),
                "paragraph_id": paragraph_id or None,
                "paragraph_region_id": paragraph.get("region_id"),
                "paragraph_reading_order": paragraph.get("reading_order"),
                "flow_fragment_ids": fragments,
                "formula_ids": formula_ids,
                "rendered_as_soft_text": rendered_soft,
                "final_soft_render_text": (
                    final_soft_dom.get("visible_text")
                    if rendered_soft else None),
                "figure_paints_after_soft_text": figure_after_soft,
                "rendered_as_separate_formula": formula_rendered,
                "rendered_inside_figure": rendered_inside_figure,
                "double_owned": double_owned,
                "duplicate_render": duplicate_render,
                "ambiguous_figure_text_owner": ambiguous_owner,
                "evidence_complete": evidence_complete,
                "evidence": {
                    "figure_hard_anchor_present_in_final_html": figure_dom,
                    "figure_render_policy": (figure.get("payload") or {}).get(
                        "render_policy"),
                    "figure_hard_anchor_is_source_svg_crop": True,
                    "separate_soft_dom_present": rendered_soft,
                    "separate_formula_dom_present": formula_rendered,
                    "classification_used_text_content": False,
                },
            })
    metrics = {
        "figure_internal_text_span_count": len(records),
        "figure_internal_soft_text_owner_count": sum(
            row["current_owner"] == "text" for row in records),
        "figure_internal_slot_count": sum(
            row["source_text_slot_created"] for row in records),
        "figure_internal_double_owned_count": sum(
            row["double_owned"] for row in records),
        "figure_internal_duplicate_render_count": sum(
            row["duplicate_render"] for row in records),
        "ambiguous_figure_text_owner_count": sum(
            row["ambiguous_figure_text_owner"] for row in records),
        "ownership_evidence_missing_count": sum(
            not row["evidence_complete"] for row in records),
        "external_caption_count": len(captions),
        "external_caption_misclassified_inside_figure_count": sum(
            row["inside_figure_bbox"] for row in captions),
    }
    decision = "pass" if (
        metrics["figure_internal_text_span_count"] > 0
        and metrics["ownership_evidence_missing_count"] == 0
        and metrics[
            "external_caption_misclassified_inside_figure_count"] == 0
    ) else "blocked"
    return {
        "schema_version": (
            "visual_v07.figure_internal_text_ownership_audit.v1"),
        "decision": decision,
        "diagnostic_only": True,
        "anomaly_metrics_are_allowed_nonzero": True,
        "classification_policy": {
            "text_content_used": False,
            "source_geometry": (
                "span bbox centre inside Figure bbox and overlap >= %.2f"
                % FIGURE_MEMBERSHIP_MIN_OVERLAP),
            "current_owner": "PageModel ownership ledger",
            "slot_truth": "SourceTextSlot source_topology.source_span_ids",
            "soft_render_truth": "final HTML paragraph/fragment DOM",
            "figure_render_truth": "final HTML Figure source-SVG hard anchor",
        },
        "metrics": metrics,
        "records": records,
        "external_captions": captions,
    }


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page-model", required=True)
    parser.add_argument("--source-pdf", required=True)
    parser.add_argument("--page-index", type=int, required=True)
    parser.add_argument("--source-text-slots", required=True)
    parser.add_argument("--final-html", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = figure_internal_text_ownership_audit(
        json.loads(Path(args.page_model).read_text(encoding="utf-8")),
        args.source_pdf, args.page_index,
        json.loads(Path(args.source_text_slots).read_text(encoding="utf-8")),
        args.final_html)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(json.dumps({"decision": result["decision"],
                      "metrics": result["metrics"]},
                     ensure_ascii=False, indent=2))
    return 0 if result["decision"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "FIGURE_MEMBERSHIP_MIN_OVERLAP", "OWNER_MARGIN_PT",
    "figure_internal_text_ownership_audit",
]
