# -*- coding: utf-8 -*-
"""Apply high-confidence SourceTextSlot geometry to translated soft text.

The lock changes only paragraph geometry and source-equivalent typography.
Figures, formulas, tables, reserved regions, and table cells never enter this
module.  Low-confidence, ambiguous, metadata, or non-delivery blocks retain
their legacy flow and remain eligible for the existing repack fallback.
"""
from __future__ import annotations

import copy
import html as html_lib
import re
from typing import Any, Iterable


MIN_GEOMETRY_LOCK_CONFIDENCE = 0.85

ELIGIBLE_SOFT_ROLES = {
    "body", "body_bold_lead", "abstract_body", "list_item", "heading",
    "caption", "footnote", "title", "author", "affiliation",
    "front_matter_prose", "recovered_prose",
}
METADATA_ROLES = {
    "page_number", "header", "footer", "header_footer", "marginal",
    "metadata", "decorative",
}
PAGE_METADATA_RE = re.compile(
    r"^(?:page\s*\d+\s*of\s*\d+|"
    r"第?\s*\d+\s*页\s*[,，/]?\s*(?:共\s*)?\d+\s*页)$", re.I)


def canonical_soft_role(value: Any) -> str:
    role = str(value or "body").strip().lower().replace("-", "_")
    if role in {"section_heading", "subsection_heading", "subheading"}:
        return "heading"
    if role in {"figure_caption", "table_caption"}:
        return "caption"
    if role == "note":
        return "footnote"
    return role


def is_translation_required_soft(role: Any, text: Any,
                                 render_source: Any = None) -> bool:
    """Document-general delivery-text gate; no page/id/file conditions."""
    canonical = canonical_soft_role(role)
    if canonical in METADATA_ROLES or canonical not in ELIGIBLE_SOFT_ROLES:
        return False
    if render_source not in (None, "", "canonical_target"):
        return False
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if PAGE_METADATA_RE.fullmatch(normalized):
        return False
    visible = re.sub(r"\{\{[^{}]+\}\}", "", normalized).strip()
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", visible))


def _bbox(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return []
    return [round(float(value), 3) for value in values]


def _slot_indexes(slots: Iterable[dict[str, Any]]) -> tuple[dict, dict]:
    by_fragment: dict[str, list[dict]] = {}
    by_identity: dict[str, list[dict]] = {}
    for slot in slots:
        fragment = str(slot.get("render_fragment_id") or "")
        if fragment:
            by_fragment.setdefault(fragment, []).append(slot)
        for identity in slot.get("render_identity_ids") or []:
            identity = str(identity or "")
            if identity:
                by_identity.setdefault(identity, []).append(slot)
    return by_fragment, by_identity


def _candidate_slots(fragment: str, render_id: str, by_fragment: dict,
                     by_identity: dict) -> list[dict[str, Any]]:
    rows = list(by_fragment.get(fragment) or [])
    if not rows:
        rows = list(by_identity.get(render_id) or [])
    return list({row["slot_id"]: row for row in rows}.values())


def _flow_items(flows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for flow in flows for item in (flow.get("items") or [])
            if item.get("kind") == "paragraph"]


def _flow_identity(item: dict[str, Any]) -> tuple[str, str]:
    paragraph = item.get("_para") or {}
    render_id = str(paragraph.get("paragraph_id")
                    or item.get("paragraph_id") or "")
    fragment = str(item.get("flow_fragment_id") or render_id)
    return render_id, fragment


def _flow_role(item: dict[str, Any]) -> str:
    paragraph = item.get("_para") or {}
    return canonical_soft_role(
        paragraph.get("semantic_role") or paragraph.get("style_role")
        or item.get("semantic_role") or item.get("style_role"))


def _flow_text(item: dict[str, Any]) -> str:
    # Presence matters: an explicit empty canonical render payload is not a
    # delivery block and must not fall back to source text merely to become a
    # lock/capacity candidate.
    if "render_text" in item:
        return str(item.get("render_text") or "")
    if "target_text" in item:
        return str(item.get("target_text") or "")
    return str(item.get("source_text") or "")


def _base_record(render_id: str, fragment: str, role: str,
                 slot: dict[str, Any] | None, *, reason: str,
                 geometry_locked: bool) -> dict[str, Any]:
    source = _bbox((slot or {}).get("source_bbox"))
    return {
        "render_id": render_id, "flow_fragment_id": fragment,
        "semantic_role": role,
        "slot_id": (slot or {}).get("slot_id"),
        "source_bbox": source,
        "source_width": ((source[2] - source[0]) if source else None),
        "source_height": ((source[3] - source[1]) if source else None),
        "source_font_size": (slot or {}).get("source_font_size"),
        "source_line_height": (slot or {}).get("source_line_height"),
        "slot_confidence": (slot or {}).get("slot_confidence"),
        "source_owner_id": (slot or {}).get("source_owner_id"),
        "source_fragment_id": (slot or {}).get("source_fragment_id"),
        "visual_group_id": (slot or {}).get("visual_group_id"),
        "column": (slot or {}).get("column"),
        "region_id": (slot or {}).get("region_id"),
        "source_topology": (slot or {}).get("source_topology"),
        "geometry_locked": geometry_locked,
        "lock_reason": reason,
        "fit_level": "L0", "font_scale": 1.0,
        "line_height_scale": 1.0, "fit_success": None,
        "repack_used": False, "attempts": [],
    }


def apply_geometry_locks_to_flows(
        flows: list[dict[str, Any]], slot_model: dict[str, Any], *,
        confidence_threshold: float = MIN_GEOMETRY_LOCK_CONFIDENCE
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return copied flows with unique high-confidence slot envelopes."""
    updated = copy.deepcopy(flows)
    items = _flow_items(updated)
    slots = list(slot_model.get("slots") or [])
    by_fragment, by_identity = _slot_indexes(slots)
    proposals = []
    slot_claims: dict[str, int] = {}
    for item in items:
        render_id, fragment = _flow_identity(item)
        role = _flow_role(item)
        candidates = _candidate_slots(fragment, render_id,
                                      by_fragment, by_identity)
        slot = candidates[0] if len(candidates) == 1 else None
        eligible = is_translation_required_soft(
            role, _flow_text(item), item.get("render_source"))
        proposals.append((item, render_id, fragment, role, candidates,
                          slot, eligible))
        if eligible and slot is not None:
            slot_claims[slot["slot_id"]] = slot_claims.get(
                slot["slot_id"], 0) + 1

    records = []
    for item, render_id, fragment, role, candidates, slot, eligible \
            in proposals:
        if not eligible:
            records.append(_base_record(
                render_id, fragment, role, slot,
                reason="not_translation_required_soft_delivery_text",
                geometry_locked=False))
            continue
        if not candidates:
            records.append(_base_record(
                render_id, fragment, role, None,
                reason="source_slot_mapping_missing", geometry_locked=False))
            continue
        if len(candidates) != 1:
            records.append(_base_record(
                render_id, fragment, role, None,
                reason="source_slot_mapping_ambiguous", geometry_locked=False))
            continue
        assert slot is not None
        if slot_claims.get(slot["slot_id"], 0) != 1:
            records.append(_base_record(
                render_id, fragment, role, slot,
                reason="source_visual_group_not_single_render_unit",
                geometry_locked=False))
            continue
        if float(slot.get("slot_confidence") or 0.0) \
                < confidence_threshold:
            records.append(_base_record(
                render_id, fragment, role, slot,
                reason="slot_confidence_below_lock_threshold",
                geometry_locked=False))
            continue
        source = _bbox(slot.get("source_bbox"))
        if not source:
            records.append(_base_record(
                render_id, fragment, role, slot,
                reason="source_slot_bbox_missing", geometry_locked=False))
            continue
        original_top = item.get("flow_y")
        item.update({
            "geometry_locked": True,
            "source_text_slot_id": slot["slot_id"],
            "source_slot_bbox": source,
            "source_slot_left": source[0],
            "source_slot_top": source[1],
            "source_slot_width": source[2] - source[0],
            "source_slot_height": source[3] - source[1],
            "source_slot_font_size": float(
                slot.get("source_font_size") or 0.0),
            "source_slot_line_height": float(
                slot.get("source_line_height") or 0.0),
            "source_slot_confidence": float(slot["slot_confidence"]),
            "source_slot_owner_type": str(
                (slot.get("source_topology") or {}).get("owner_type") or ""),
            "source_slot_content_top_offset": 0.0,
            "flow_y": source[1],
            "local_typography_fit_applied": False,
            "local_typography_fit_level": "L0",
            "local_typography_font_scale": 1.0,
            "local_typography_line_height_scale": 1.0,
            "local_typography_wrapping": "source",
            "browser_repack_applied": False,
        })
        record = _base_record(
            render_id, fragment, role, slot,
            reason="unique_high_confidence_source_slot",
            geometry_locked=True)
        record["pre_lock_top"] = original_top
        records.append(record)
    return updated, {
        "schema_version": "visual_v07.source_text_slot_lock.v1",
        "confidence_threshold": confidence_threshold,
        "records": records,
        "geometry_locked_block_count": sum(
            row["geometry_locked"] for row in records),
        "geometry_unlocked_block_count": sum(
            not row["geometry_locked"] for row in records),
        "repack_policy": (
            "geometry_locked=false only; locked capacity failure blocks"),
    }


def _paragraph_tag(html_text: str, fragment_id: str) -> re.Match[str] | None:
    fragment = html_lib.escape(fragment_id, quote=True)
    return re.search(
        r'<div\b(?=[^>]*\bclass="[^"]*\bparagraph-block\b[^"]*")'
        r'(?=[^>]*\bdata-flow-fragment="%s")[^>]*>'
        % re.escape(fragment), html_text, re.IGNORECASE)


def _set_style(style: str, name: str, value: str) -> str:
    pattern = r"(?i)((?:^|;)\s*%s\s*:)[^;]*" % re.escape(name)
    if re.search(pattern, style):
        return re.sub(pattern, lambda match: "%s%s" % (match.group(1), value),
                      style, count=1)
    return style + ";%s:%s" % (name, value)


def _lock_html_tag(html_text: str, fragment: str,
                   slot: dict[str, Any]) -> str:
    matched = _paragraph_tag(html_text, fragment)
    if matched is None:
        raise ValueError("paragraph RenderIdentity not found: %s" % fragment)
    tag = matched.group(0)
    style_match = re.search(r'\bstyle="([^"]*)"', tag, re.IGNORECASE)
    if style_match is None:
        raise ValueError("paragraph has no inline style: %s" % fragment)
    source = _bbox(slot.get("source_bbox"))
    values = {
        "left": "%.3fpt" % source[0], "top": "%.3fpt" % source[1],
        "width": "%.3fpt" % (source[2] - source[0]),
        "height": "%.3fpt" % (source[3] - source[1]),
        "font-size": "%.3fpt" % float(slot["source_font_size"]),
        "line-height": "%.3fpt" % float(slot["source_line_height"]),
        "overflow": "visible",
    }
    style = style_match.group(1)
    for name, value in values.items():
        style = _set_style(style, name, value)
    new_tag = tag[:style_match.start(1)] + style + tag[style_match.end(1):]
    attrs = (
        ' data-geometry-locked="true" data-source-slot-id="%s"'
        ' data-source-slot-bbox="%.3f,%.3f,%.3f,%.3f"'
        ' data-slot-confidence="%.3f" data-slot-content-top-offset="0.000"'
        ' data-repack-used="false"'
        % (html_lib.escape(str(slot["slot_id"]), quote=True),
           source[0], source[1], source[2], source[3],
           float(slot["slot_confidence"])))
    new_tag = new_tag[:-1] + attrs + ">"
    tail = html_text[matched.end():]
    closing = re.search(r"</div\s*>", tail, re.IGNORECASE)
    if closing is None:
        raise ValueError("paragraph closing tag not found: %s" % fragment)
    content = tail[:closing.start()]
    wrapped = ('<span class="slot-text-content" '
               'style="position:relative;top:0.000pt;">%s</span>'
               % content)
    return (html_text[:matched.start()] + new_tag + wrapped
            + tail[closing.start():])


def lock_html_to_source_slots(
        html_text: str, slot_model: dict[str, Any],
        final_geometry: dict[str, Any], *,
        confidence_threshold: float = MIN_GEOMETRY_LOCK_CONFIDENCE
        ) -> tuple[str, dict[str, Any]]:
    """Frozen-artifact lock using the same RenderIdentity contract."""
    slots = list(slot_model.get("slots") or [])
    by_fragment, by_identity = _slot_indexes(slots)
    proposals = []
    slot_claims: dict[str, int] = {}
    for block in final_geometry.get("blocks") or []:
        fragment = str(block.get("flow_fragment_id") or "")
        render_id = str(block.get("render_id")
                        or block.get("paragraph_id") or "")
        role = canonical_soft_role(block.get("semantic_role"))
        candidates = _candidate_slots(fragment, render_id,
                                      by_fragment, by_identity)
        slot = candidates[0] if len(candidates) == 1 else None
        eligible = is_translation_required_soft(
            role, block.get("text_preview"), block.get("render_source"))
        proposals.append((block, render_id, fragment, role, candidates,
                          slot, eligible))
        if eligible and slot is not None:
            slot_claims[slot["slot_id"]] = slot_claims.get(
                slot["slot_id"], 0) + 1

    updated = html_text
    records = []
    for block, render_id, fragment, role, candidates, slot, eligible \
            in proposals:
        reason = "unique_high_confidence_source_slot"
        locked = True
        if not eligible:
            reason, locked = "not_translation_required_soft_delivery_text", False
        elif not candidates:
            reason, locked = "source_slot_mapping_missing", False
        elif len(candidates) != 1:
            reason, locked = "source_slot_mapping_ambiguous", False
        elif slot_claims.get(slot["slot_id"], 0) != 1:
            reason, locked = "source_visual_group_not_single_render_unit", False
        elif float(slot.get("slot_confidence") or 0.0) \
                < confidence_threshold:
            reason, locked = "slot_confidence_below_lock_threshold", False
        if locked:
            updated = _lock_html_tag(updated, fragment, slot)
        record = _base_record(render_id, fragment, role, slot,
                              reason=reason, geometry_locked=locked)
        record["pre_lock_bbox"] = _bbox(block.get("dom_measured_bbox")
                                        or block.get("final_bbox"))
        records.append(record)
    return updated, {
        "schema_version": "visual_v07.source_text_slot_lock.v1",
        "confidence_threshold": confidence_threshold,
        "records": records,
        "geometry_locked_block_count": sum(
            row["geometry_locked"] for row in records),
        "geometry_unlocked_block_count": sum(
            not row["geometry_locked"] for row in records),
        "repack_policy": (
            "geometry_locked=false only; locked capacity failure blocks"),
    }


__all__ = [
    "ELIGIBLE_SOFT_ROLES", "MIN_GEOMETRY_LOCK_CONFIDENCE",
    "apply_geometry_locks_to_flows", "canonical_soft_role",
    "is_translation_required_soft", "lock_html_to_source_slots",
]
