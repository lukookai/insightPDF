# -*- coding: utf-8 -*-
"""SourceTextSlot: immutable source-owned regions for visual soft text.

Geometry is assembled from objects the visual pipeline already owns:
LogicalParagraph source fragments, SourceVisualGroup union geometry,
PageLayoutGrid column ownership, source span typography, and recovered-prose
layout provenance.  This module does not parse a PDF, perform OCR, or alter
rendering.  Table cells remain external frozen owners of their LogicalCell
bboxes; hard anchors are never ordinary text slots.
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from typing import Any, Iterable


HARD_ROLES = {
    "formula", "display_formula", "equation", "figure", "image", "table",
    "table_anchor", "reserved", "reserved_region", "hard_anchor",
}


def _bbox(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return []
    return [round(float(value), 3) for value in values]


def _role(value: Any) -> str:
    role = str(value or "body").strip().lower().replace("-", "_")
    if role in {"title", "section_heading", "subheading"}:
        return "heading"
    if role in {"figure_caption", "table_caption"}:
        return "caption"
    if role in {"footer", "note"}:
        return "footnote"
    return role


def _soft_role(value: Any) -> bool:
    role = _role(value)
    return role not in HARD_ROLES and role not in {
        "table_cell", "logical_cell", "cell"}


def _page_index(page_model: dict[str, Any]) -> int:
    page_number = int(page_model.get("page") or 1)
    return max(0, page_number - 1)


def _source_line_height(paragraph: dict[str, Any],
                        span_ids: set[str], font_size: float) -> float:
    heights = []
    for line in paragraph.get("lines") or []:
        spans = line.get("spans") or []
        selected = [span for span in spans
                    if not span_ids or str(span.get("id")) in span_ids]
        boxes = [_bbox(span.get("bbox")) for span in selected]
        boxes = [box for box in boxes if box]
        if boxes:
            heights.append(max(box[3] for box in boxes)
                           - min(box[1] for box in boxes))
    if heights:
        return round(float(statistics.median(heights)), 3)
    return round(max(float(font_size or 0.0) * 1.2, 0.0), 3)


def _reading_order(value: Any, fallback: int) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, (list, tuple)) and value:
        tail = value[-1]
        if isinstance(tail, (int, float)):
            return int(round(float(tail) * 1000))
    return fallback


@dataclass(frozen=True)
class SourceTextSlot:
    slot_id: str
    page_index: int
    column: int
    region_id: str
    source_owner_id: str
    logical_paragraph_id: str
    source_fragment_id: str
    render_fragment_id: str
    visual_group_id: str | None
    semantic_role: str
    source_bbox: list[float]
    source_width: float
    source_height: float
    source_font_size: float
    source_line_height: float
    reading_order: int
    source_topology: dict[str, Any]
    slot_confidence: float
    render_identity_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _baseline_maps(blocks: Iterable[dict[str, Any]]) -> tuple[dict, dict]:
    by_fragment = {}
    by_render = {}
    for block in blocks:
        fragment = str(block.get("flow_fragment_id") or "")
        render_id = str(block.get("render_id") or block.get("paragraph_id")
                        or "")
        if fragment:
            by_fragment[fragment] = block
        if render_id:
            by_render.setdefault(render_id, []).append(block)
    return by_fragment, by_render


def _visual_group_dict(group: Any) -> dict[str, Any]:
    return group.to_dict() if hasattr(group, "to_dict") else dict(group)


def _table_external_owners(page_model: dict[str, Any]) -> list[dict[str, Any]]:
    owners = []
    for region in page_model.get("regions") or []:
        if region.get("type") != "table":
            continue
        table = region.get("payload") or {}
        table_id = str(region.get("region_id") or table.get("table_id") or "")
        for index, cell in enumerate(table.get("cells") or []):
            owners.append({
                "owner_type": "external_frozen_logical_cell",
                "table_id": table_id,
                "cell_id": str(cell.get("cell_id") or cell.get("id")
                               or "%s-C%d" % (table_id, index)),
                "bbox": _bbox(cell.get("bbox") or cell.get("cell_bbox")),
                "geometry_authority": "Task1.LogicalCell",
            })
    return owners


def build_source_text_slots(
        page_model: dict[str, Any], *,
        visual_groups: Iterable[Any] | None = None,
        layout_baseline_blocks: Iterable[dict[str, Any]] | None = None,
        recovered_blocks: Iterable[dict[str, Any]] | None = None,
        layout_grid: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one page's slots without reading the source PDF.

    ``layout_baseline_blocks`` is existing pre-mutation final-geometry
    provenance.  It supplies region ownership and the reconstructed visual
    region for recovered prose that is intentionally absent from the stitched
    PageModel.  It never overrides ordinary LogicalParagraph/SourceFragment
    geometry.
    """
    baseline_blocks = list(layout_baseline_blocks or [])
    baseline_by_fragment, baseline_by_render = _baseline_maps(baseline_blocks)
    page_index = _page_index(page_model)
    page_number = page_index + 1
    groups = [_visual_group_dict(group) for group in (visual_groups or [])]
    group_by_member = {}
    for group in groups:
        for member in group.get("member_paragraph_ids") or []:
            group_by_member[str(member)] = group

    paragraph_by_id = {}
    for region in page_model.get("regions") or []:
        if region.get("type") != "text":
            continue
        paragraph = region.get("payload") or {}
        paragraph_id = str(paragraph.get("paragraph_id")
                           or region.get("region_id") or "")
        if paragraph_id:
            paragraph_by_id[paragraph_id] = paragraph

    slots: list[SourceTextSlot] = []
    consumed_group_members: set[str] = set()
    slot_counter = 0

    # Multi-member SourceVisualGroups are one source visual unit and therefore
    # one slot.  A single-member group still annotates its member slot below.
    for group in groups:
        members = [str(value) for value in
                   group.get("member_paragraph_ids") or []]
        if len(members) < 2 or not all(member in paragraph_by_id
                                       for member in members):
            continue
        first = paragraph_by_id[members[0]]
        source_box = _bbox(group.get("source_union_bbox"))
        if not source_box:
            continue
        role = _role(group.get("dominant_role")
                     or first.get("semantic_role")
                     or first.get("style_role"))
        if not _soft_role(role):
            continue
        render_fragment = members[0] + "-G0"
        baseline = baseline_by_fragment.get(render_fragment) or (
            baseline_by_render.get(members[0]) or [{}])[0]
        font_size = float(first.get("base_font_size") or 0.0)
        slots.append(SourceTextSlot(
            slot_id="STS-P%03d-%04d" % (page_number, slot_counter),
            page_index=page_index,
            column=int(baseline.get("column", -1)),
            region_id=str(baseline.get("region_id") or "source_visual_group"),
            source_owner_id=str(group.get("group_id") or members[0]),
            logical_paragraph_id=members[0],
            source_fragment_id="GROUP:%s" % group.get("group_id"),
            render_fragment_id=render_fragment,
            visual_group_id=str(group.get("group_id") or "") or None,
            semantic_role=role, source_bbox=source_box,
            source_width=round(source_box[2] - source_box[0], 3),
            source_height=round(source_box[3] - source_box[1], 3),
            source_font_size=round(font_size, 3),
            source_line_height=_source_line_height(first, set(), font_size),
            reading_order=slot_counter,
            source_topology={
                "owner_type": "source_visual_group",
                "group_type": group.get("group_type"),
                "member_paragraph_ids": members,
                "expected_topology": group.get("expected_topology"),
                "relation_owner_id": group.get("relation_owner_id"),
                "geometry_origin": "SourceVisualGroup.source_union_bbox",
            },
            slot_confidence=float(group.get("confidence") or 0.0),
            render_identity_ids=[members[0], render_fragment, *members],
        ))
        slot_counter += 1
        consumed_group_members.update(members)

    for paragraph_id, paragraph in paragraph_by_id.items():
        if paragraph_id in consumed_group_members:
            continue
        role = _role(paragraph.get("semantic_role")
                     or paragraph.get("style_role"))
        if not _soft_role(role):
            continue
        fragments = paragraph.get("source_fragments") or []
        if not fragments:
            fragments = [{
                "flow_fragment_id": paragraph_id + "-F0",
                "source_fragment_id": paragraph_id + "-SOURCE",
                "column": paragraph.get("column", -1),
                "bbox": paragraph.get("bbox") or [],
                "col_x0": paragraph.get("col_x0"),
                "col_x1": paragraph.get("col_x1"),
                "base_font_size": paragraph.get("base_font_size"),
                "span_ids": paragraph.get("span_ids") or [],
            }]
        group = group_by_member.get(paragraph_id)
        for fragment_index, fragment in enumerate(fragments):
            if "page_number" in fragment \
                    and int(fragment["page_number"]) != page_number:
                continue
            fragment_box = _bbox(fragment.get("bbox") or paragraph.get("bbox"))
            if not fragment_box:
                continue
            group_box = (_bbox(group.get("source_union_bbox"))
                         if group and len(group.get(
                             "member_paragraph_ids") or []) == 1 else [])
            visual_box = group_box or [
                float(fragment.get("col_x0")
                      if fragment.get("col_x0") is not None
                      else fragment_box[0]),
                fragment_box[1],
                float(fragment.get("col_x1")
                      if fragment.get("col_x1") is not None
                      else fragment_box[2]),
                fragment_box[3],
            ]
            visual_box = _bbox(visual_box)
            render_fragment = str(fragment.get("flow_fragment_id")
                                  or "%s-F%d" % (paragraph_id,
                                                 fragment_index))
            baseline = baseline_by_fragment.get(render_fragment) or (
                baseline_by_render.get(paragraph_id) or [{}])[0]
            font_size = float(fragment.get("base_font_size")
                              or paragraph.get("base_font_size") or 0.0)
            span_ids = {str(value) for value in
                        fragment.get("span_ids") or []}
            topology = {
                "owner_type": ("source_visual_group" if group
                               else "source_fragment"),
                "geometry_origin": (
                    "SourceVisualGroup.source_union_bbox" if group
                    else "LogicalParagraph.source_fragments + column bounds"),
                "fragment_index": fragment_index,
                "continuation": bool(fragment.get("continuation")),
                "cross_page_continuation": bool(
                    paragraph.get("cross_page_continuation")),
                "source_span_ids": sorted(span_ids),
                "semantic_role_origin": "DocumentSemanticState/PageModel",
            }
            if group:
                topology.update({
                    "group_type": group.get("group_type"),
                    "expected_topology": group.get("expected_topology"),
                    "relation_owner_id": group.get("relation_owner_id"),
                })
            slots.append(SourceTextSlot(
                slot_id="STS-P%03d-%04d" % (page_number, slot_counter),
                page_index=page_index,
                column=int(baseline.get("column",
                                        fragment.get("column", -1))),
                region_id=str(baseline.get("region_id")
                              or "source_column_%s" % fragment.get(
                                  "column", -1)),
                source_owner_id=paragraph_id,
                logical_paragraph_id=str(
                    paragraph.get("logical_paragraph_id") or paragraph_id),
                source_fragment_id=str(fragment.get("source_fragment_id")
                                       or render_fragment),
                render_fragment_id=render_fragment,
                visual_group_id=(str(group.get("group_id")) if group else None),
                semantic_role=role, source_bbox=visual_box,
                source_width=round(visual_box[2] - visual_box[0], 3),
                source_height=round(visual_box[3] - visual_box[1], 3),
                source_font_size=round(font_size, 3),
                source_line_height=_source_line_height(
                    paragraph, span_ids, font_size),
                reading_order=_reading_order(
                    paragraph.get("reading_order"), slot_counter),
                source_topology=topology,
                slot_confidence=(float(group.get("confidence") or 0.0)
                                 if group else 1.0),
                render_identity_ids=[paragraph_id, render_fragment],
            ))
            slot_counter += 1

    # Explicit recovered blocks are preferred when supplied.  They are
    # already source-derived objects emitted by prose recovery; no PDF work
    # occurs here.
    recovered_by_fragment = {}
    for recovered in recovered_blocks or []:
        for fragment in recovered.get("source_fragments") or []:
            recovered_by_fragment[str(fragment.get("flow_fragment_id")
                                        or recovered.get("paragraph_id"))] = (
                                            recovered, fragment)

    existing_keys = {key for slot in slots
                     for key in slot.render_identity_ids}
    for block in baseline_blocks:
        render_id = str(block.get("render_id") or block.get("paragraph_id")
                        or "")
        render_fragment = str(block.get("flow_fragment_id") or render_id)
        if not render_id or render_fragment in existing_keys \
                or render_id in existing_keys:
            continue
        role = _role(block.get("semantic_role"))
        if not _soft_role(role):
            continue
        recovered_pair = recovered_by_fragment.get(render_fragment)
        recovered = recovered_pair[0] if recovered_pair else {}
        fragment = recovered_pair[1] if recovered_pair else {}
        # The pre-mutation planned bbox is the recovery layout's frozen visual
        # region.  The raw recovered source bbox remains provenance below and
        # is not conflated with the renderer-facing slot.
        source_box = _bbox(block.get("planned_bbox"))
        if not source_box:
            source_box = _bbox(fragment.get("bbox") or recovered.get("bbox"))
        if not source_box:
            continue
        font_size = float(block.get("font_size")
                          or fragment.get("base_font_size")
                          or recovered.get("base_font_size") or 0.0)
        line_height = float(block.get("line_height") or 0.0)
        slots.append(SourceTextSlot(
            slot_id="STS-P%03d-%04d" % (page_number, slot_counter),
            page_index=page_index,
            column=int(block.get("column") or fragment.get("column") or 0),
            region_id=str(block.get("region_id") or "recovery_region"),
            source_owner_id=str(recovered.get("source_formula_region_id")
                                or render_id),
            logical_paragraph_id=str(recovered.get("logical_paragraph_id")
                                     or block.get("paragraph_id") or render_id),
            source_fragment_id=str(fragment.get("source_fragment_id")
                                   or render_fragment),
            render_fragment_id=render_fragment,
            visual_group_id=None, semantic_role=role,
            source_bbox=source_box,
            source_width=round(source_box[2] - source_box[0], 3),
            source_height=round(source_box[3] - source_box[1], 3),
            source_font_size=round(font_size, 3),
            source_line_height=round(
                line_height or max(font_size * 1.2, 0.0), 3),
            reading_order=int(block.get("reading_order") or slot_counter),
            source_topology={
                "owner_type": "recovered_prose",
                "geometry_origin": "pre_mutation_recovery_layout_baseline",
                "recovery_relation_owner_id": recovered.get(
                    "source_formula_region_id"),
                "raw_recovered_source_bbox": _bbox(
                    fragment.get("bbox") or recovered.get("bbox")),
                "render_source": block.get("render_source"),
                "semantic_role_origin": "RecoveredProseBlock",
            },
            slot_confidence=0.9 if recovered_pair else 0.85,
            render_identity_ids=[render_id, render_fragment],
        ))
        slot_counter += 1
        existing_keys.update({render_id, render_fragment})

    slots.sort(key=lambda slot: (slot.page_index, slot.column,
                                 slot.source_bbox[1], slot.reading_order,
                                 slot.slot_id))
    # Reissue deterministic ids after topology sorting.
    normalized = []
    for index, slot in enumerate(slots):
        values = slot.to_dict()
        values["slot_id"] = "STS-P%03d-%04d" % (page_number, index)
        values["reading_order"] = index
        normalized.append(SourceTextSlot(**values).to_dict())
    return {
        "schema_version": "visual_v07.source_text_slot.v1",
        "page_index": page_index,
        "geometry_authority": "source_objects_only",
        "source_objects": [
            "LogicalParagraph", "SourceFragment", "SourceVisualGroup",
            "DocumentSemanticState", "PageLayoutGrid", "source_spans",
            "RecoveredProseBlock/pre-mutation layout provenance",
        ],
        "slots": normalized,
        "source_text_slot_count": len(normalized),
        "external_frozen_slot_owners": _table_external_owners(page_model),
        "hard_anchor_slots_excluded": True,
        "pdf_reparsed": False,
        "ocr_used": False,
        "layout_grid_provenance": {
            "content_frame": (layout_grid or {}).get("content_frame"),
            "columns": (layout_grid or {}).get("columns"),
            "gutter": (layout_grid or {}).get("gutter"),
        },
    }


__all__ = ["SourceTextSlot", "build_source_text_slots"]
