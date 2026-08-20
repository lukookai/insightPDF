# -*- coding: utf-8 -*-
"""Source-relative paragraph styling inside immutable SourceTextSlots.

Only geometrically proven paragraph boundaries are represented.  Ordinary
source PDF line wrapping is never copied to the target.  Indentation is kept
in em so LocalTypographyFit/Fill can change the font size without changing
the source proportion.
"""
from __future__ import annotations

import copy
import html as html_lib
import re
from pathlib import Path
from typing import Any, Iterable


PARAGRAPH_BREAK_PREFIX = "SOURCE_PARA_BREAK"
PARAGRAPH_BREAK_RE = re.compile(
    r"\{\{SOURCE_PARA_BREAK_(\d{3})\}\}")
SOURCE_STYLE_ROLES = {
    "body", "body_bold_lead", "abstract_body", "recovered_prose",
}


def _bbox(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return []
    return [round(float(value), 3) for value in values]


def _canonical_role(value: Any) -> str:
    role = str(value or "body").strip().lower().replace("-", "_")
    return "body" if role == "recovered_prose" else role


def _same_bbox(first: Any, second: Any, tolerance: float = 0.55) -> bool:
    one = _bbox(first)
    two = _bbox(second)
    return bool(one and two and all(
        abs(one[index] - two[index]) <= tolerance for index in range(4)))


def _source_sentence_boundaries(text: str) -> list[int]:
    """Return prose sentence ends, excluding common abbreviation periods."""
    value = str(text or "")
    abbreviations = {
        "fig", "eq", "sec", "ref", "dr", "mr", "mrs", "ms", "prof",
        "e.g", "i.e", "et al",
    }
    output = []
    for match in re.finditer(r"[.!?]", value):
        pos = match.start()
        if value[pos] == ".":
            before = value[:pos]
            token_match = re.search(r"([A-Za-z]+(?:\s+al)?)$", before)
            token = token_match.group(1).lower() if token_match else ""
            after = value[pos + 1:]
            next_match = re.search(r"\S", after)
            next_char = (after[next_match.start()] if next_match else "")
            if token in abbreviations or (next_char and next_char.isdigit()):
                continue
        output.append(match.end())
    return output


def _target_sentence_boundaries(text: str) -> list[int]:
    value = str(text or "")
    cjk = [match.end() for match in re.finditer(r"[。！？]", value)]
    if cjk:
        return cjk
    return _source_sentence_boundaries(value)


def infer_recovered_paragraph_styles(
        paragraph_id: str, semantic_role: str,
        source_lines: list[dict[str, Any]],
        page_candidate_model: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach page-level geometric indent evidence to one recovered block."""
    if _canonical_role(semantic_role) not in SOURCE_STYLE_ROLES:
        return []
    candidates = list(page_candidate_model.get("records") or [])
    lines = sorted(source_lines, key=lambda row: (
        float((row.get("bbox") or [0, 0, 0, 0])[1]),
        float((row.get("bbox") or [0, 0, 0, 0])[0])))
    joined = " ".join(str(row.get("text") or "") for row in lines).strip()
    boundaries = _source_sentence_boundaries(joined)
    records = []
    offset = 0
    for local_index, line in enumerate(lines):
        text = str(line.get("text") or "")
        match = next((row for row in candidates
                      if _same_bbox(row.get("source_line_bbox"),
                                    line.get("bbox"))), None)
        if match is not None:
            sentence_ordinal = sum(end <= offset for end in boundaries)
            marker = "{{%s_%03d}}" % (
                PARAGRAPH_BREAK_PREFIX, len(records) + 1)
            records.append({
                **copy.deepcopy(match),
                "paragraph_id": str(paragraph_id),
                "semantic_role": _canonical_role(semantic_role),
                "source_paragraph_index": len(records),
                "source_local_line_index": local_index,
                "source_text_offset": offset,
                "source_sentence_ordinal": sentence_ordinal,
                "marker_token": marker,
                "target_mapping_route": None,
                "target_mapping_success": False,
            })
        offset += len(text)
        if local_index + 1 < len(lines):
            offset += 1
    return records


def mark_translation_source(source_text: str,
                            styles: list[dict[str, Any]]) -> str:
    """Insert protected paragraph-boundary tokens at geometric offsets."""
    output = str(source_text or "")
    for row in sorted(styles, key=lambda value: int(
            value.get("source_text_offset") or 0), reverse=True):
        offset = max(0, min(int(row.get("source_text_offset") or 0),
                            len(output)))
        token = str(row.get("marker_token") or "")
        if token:
            output = output[:offset] + token + " " + output[offset:]
    return output


def _target_style_offsets(target_text: str, source_text: str,
                          styles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map source paragraph boundaries to target offsets.

    Protected tokens are authoritative.  The sentence-ordinal path exists for
    frozen pre-Task-4D translations that predate the tokens; it maps only
    paragraph boundaries, never source line wraps.
    """
    target = str(target_text or "")
    source = str(source_text or "")
    target_boundaries = _target_sentence_boundaries(target)
    output = []
    for style in styles:
        row = copy.deepcopy(style)
        token = str(row.get("marker_token") or "")
        token_match = re.search(re.escape(token), target) if token else None
        if token_match is not None:
            offset = token_match.start()
            route = "protected_paragraph_boundary_token"
        else:
            ordinal = int(row.get("source_sentence_ordinal") or 0)
            if ordinal == 0:
                offset = 0
            elif ordinal <= len(target_boundaries):
                offset = target_boundaries[ordinal - 1]
            else:
                continue
            route = "frozen_sentence_ordinal_alignment"
        row["target_text_offset"] = int(offset)
        row["target_mapping_route"] = route
        row["target_mapping_success"] = True
        output.append(row)
    return sorted(output, key=lambda row: int(row["target_text_offset"]))


def target_paragraph_segments(
        target_text: str, source_text: str,
        styles: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]],
                                               list[dict[str, Any]]]:
    """Return clean target text and paragraph-only segment boundaries."""
    clean = str(target_text or "")
    for token in PARAGRAPH_BREAK_RE.findall(clean):
        clean = clean.replace("{{%s_%s}}" % (PARAGRAPH_BREAK_PREFIX, token), "")
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    mapped = _target_style_offsets(target_text, source_text, styles)
    # Token removal changes offsets.  Recompute token-based offsets in clean
    # by counting visible text before each token.
    for row in mapped:
        if row.get("target_mapping_route") != (
                "protected_paragraph_boundary_token"):
            continue
        token = str(row.get("marker_token") or "")
        original = str(target_text or "")
        raw_offset = original.find(token)
        if raw_offset >= 0:
            prefix = PARAGRAPH_BREAK_RE.sub("", original[:raw_offset])
            row["target_text_offset"] = len(prefix)
    mapped.sort(key=lambda row: int(row["target_text_offset"]))
    segments = []
    cursor = 0
    for index, row in enumerate(mapped):
        offset = max(cursor, min(int(row["target_text_offset"]), len(clean)))
        if offset > cursor:
            segments.append({
                "text": clean[cursor:offset],
                "target_start": cursor, "target_end": offset,
                "source_style": None,
            })
        end = (int(mapped[index + 1]["target_text_offset"])
               if index + 1 < len(mapped) else len(clean))
        end = max(offset, min(end, len(clean)))
        segments.append({
            "text": clean[offset:end],
            "target_start": offset, "target_end": end,
            "source_style": row,
        })
        cursor = end
    if cursor < len(clean):
        segments.append({
            "text": clean[cursor:],
            "target_start": cursor, "target_end": len(clean),
            "source_style": None,
        })
    return clean, segments, mapped


def _slot_indexes(slot_model: dict[str, Any]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_fragment = {}
    by_id = {}
    for slot in slot_model.get("slots") or []:
        fragment = str(slot.get("render_fragment_id") or "")
        slot_id = str(slot.get("slot_id") or "")
        if fragment:
            by_fragment[fragment] = slot
        if slot_id:
            by_id[slot_id] = slot
    return by_fragment, by_id


def _page_model_span_lines(page_model: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for region in page_model.get("regions") or []:
        if str(region.get("type") or "").lower() != "text":
            continue
        paragraph = region.get("payload") or {}
        for line_index, line in enumerate(paragraph.get("lines") or []):
            spans = list(line.get("spans") or [])
            boxes = [_bbox(span.get("bbox")) for span in spans]
            boxes = [box for box in boxes if box]
            if not boxes:
                continue
            output.append({
                "paragraph_id": paragraph.get("paragraph_id"),
                "line_index": line_index,
                "span_ids": [str(span.get("id") or "") for span in spans],
                "bbox": [min(box[0] for box in boxes),
                         min(box[1] for box in boxes),
                         max(box[2] for box in boxes),
                         max(box[3] for box in boxes)],
                "text": "".join(str(span.get("text") or "")
                                 for span in spans),
            })
    return output


def _normal_slot_styles(slot: dict[str, Any], page_model: dict[str, Any],
                        page_candidates: dict[str, Any],
                        paragraph_id: str, role: str) -> list[dict[str, Any]]:
    if _canonical_role(role) not in SOURCE_STYLE_ROLES:
        return []
    span_ids = set(str(value) for value in (
        slot.get("source_topology") or {}).get("source_span_ids") or [])
    lines = [line for line in _page_model_span_lines(page_model)
             if span_ids.intersection(line["span_ids"])]
    candidates = list(page_candidates.get("records") or [])
    output = []
    for line in lines:
        match = next((row for row in candidates
                      if _same_bbox(row.get("source_line_bbox"),
                                    line.get("bbox"))), None)
        if match is None:
            continue
        # A normal LogicalParagraph slot is already one paragraph.  Only its
        # first source line may style the target block.
        first_line = min(lines, key=lambda value: value["bbox"][1])
        if not _same_bbox(line.get("bbox"), first_line.get("bbox")):
            continue
        output.append({
            **copy.deepcopy(match),
            "paragraph_id": paragraph_id,
            "semantic_role": _canonical_role(role),
            "source_paragraph_index": 0,
            "source_local_line_index": 0,
            "source_text_offset": 0,
            "source_sentence_ordinal": 0,
            "marker_token": "{{%s_001}}" % PARAGRAPH_BREAK_PREFIX,
            "target_mapping_route": None,
            "target_mapping_success": False,
        })
    return output


def apply_source_paragraph_styles_to_flows(
        flows: list[dict[str, Any]], page_model: dict[str, Any],
        slot_model: dict[str, Any], page_candidates: dict[str, Any]
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Attach target paragraph segments without changing any geometry."""
    updated = copy.deepcopy(flows)
    by_fragment, by_id = _slot_indexes(slot_model)
    records = []
    negatives = []
    for flow in updated:
        for item in flow.get("items") or []:
            if item.get("kind") != "paragraph" or not item.get("geometry_locked"):
                continue
            role = _canonical_role(
                (item.get("_para") or {}).get("semantic_role")
                or item.get("style_role") or "body")
            if role not in SOURCE_STYLE_ROLES:
                continue
            fragment = str(item.get("flow_fragment_id") or "")
            paragraph_id = str((item.get("_para") or {}).get("paragraph_id")
                               or item.get("paragraph_id") or "")
            slot = (by_id.get(str(item.get("source_text_slot_id") or ""))
                    or by_fragment.get(fragment) or {})
            para = item.get("_para") or {}
            styles = copy.deepcopy(para.get("source_paragraph_styles") or [])
            if not styles:
                styles = _normal_slot_styles(
                    slot, page_model, page_candidates, paragraph_id, role)
            source_text = str(para.get("source_text")
                              or item.get("source_text") or "")
            target_text = str(item.get("render_text") or "")
            mapping_target_text = str(
                para.get("target_text_with_paragraph_markers")
                or target_text)
            if not styles:
                negatives.append({
                    "paragraph_id": paragraph_id,
                    "flow_fragment_id": fragment,
                    "slot_id": slot.get("slot_id"),
                    "semantic_role": role,
                    "source_has_first_line_indent": False,
                })
                continue
            clean, segments, mapped = target_paragraph_segments(
                mapping_target_text, source_text, styles)
            # The initial layout deliberately receives marker-free text.  A
            # retained protected marker is mapping evidence only; after
            # removal its clean text must be the rendered target.
            if clean != target_text:
                clean, segments, mapped = target_paragraph_segments(
                    target_text, source_text, styles)
            if not mapped:
                for style in styles:
                    records.append({
                        **style,
                        "flow_fragment_id": fragment,
                        "slot_id": slot.get("slot_id"),
                        "source_slot_bbox": _bbox(slot.get("source_bbox")),
                        "target_mapping_success": False,
                    })
                continue
            item["render_text"] = clean
            item["source_paragraph_segments"] = segments
            item["source_paragraph_style_applied"] = True
            item["source_paragraph_style_count"] = len(mapped)
            for style in mapped:
                style.update({
                    "flow_fragment_id": fragment,
                    "slot_id": slot.get("slot_id"),
                    "source_slot_bbox": _bbox(slot.get("source_bbox")),
                })
                records.append(style)
    return updated, {
        "schema_version": "visual_v07.source_paragraph_style.v1",
        "geometry_policy": "SourceTextSlot x/y/width/height immutable",
        "indent_unit": "source-relative em",
        "ordinary_source_line_breaks_copied": False,
        "records": records,
        "negative_records": negatives,
        "metrics": {
            "source_first_line_indent_count": len(records),
            "target_mapping_success_count": sum(
                bool(row.get("target_mapping_success")) for row in records),
            "negative_body_paragraph_count": len(negatives),
        },
    }


def _paragraph_parts(html_text: str, fragment: str) -> tuple[int, int, str, str]:
    pattern = re.compile(
        r'<div\b(?=[^>]*\bclass="[^"]*\bparagraph-block\b[^"]*")'
        r'(?=[^>]*\bdata-flow-fragment="%s")[^>]*>' % re.escape(fragment),
        re.IGNORECASE)
    match = pattern.search(html_text)
    if match is None:
        raise ValueError("source paragraph style target missing: %s" % fragment)
    closing = re.search(r"</div\s*>", html_text[match.end():], re.IGNORECASE)
    if closing is None:
        raise ValueError("source paragraph style closing div missing: %s"
                         % fragment)
    end = match.end() + closing.start()
    return match.start(), end + len(closing.group(0)), match.group(0), \
        html_text[match.end():end]


def _visible_text(value: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", value))


def _visible_offset_to_html(value: str, offset: int) -> int:
    if offset <= 0:
        return 0
    visible = 0
    index = 0
    while index < len(value):
        if value[index] == "<":
            closing = value.find(">", index + 1)
            if closing < 0:
                break
            index = closing + 1
            continue
        if value[index] == "&":
            closing = value.find(";", index + 1, index + 16)
            if closing >= 0:
                if visible >= offset:
                    return index
                visible += len(html_lib.unescape(value[index:closing + 1]))
                index = closing + 1
                continue
        if visible >= offset:
            return index
        visible += 1
        index += 1
    return len(value)


def _segment_html(content: str, segments: list[dict[str, Any]]) -> str:
    outer = re.match(
        r'(?s)^(<span\b[^>]*\bclass="[^"]*slot-text-content[^"]*"[^>]*>)(.*)(</span>)$',
        content)
    prefix, body, suffix = (outer.group(1), outer.group(2), outer.group(3)) \
        if outer else ("", content, "")
    plain = _visible_text(body)
    expected = "".join(str(row.get("text") or "") for row in segments)
    if plain != expected:
        raise ValueError("target segment text does not match frozen HTML")
    boundaries = [0]
    for row in segments:
        boundaries.append(int(row.get("target_end") or 0))
    html_offsets = [_visible_offset_to_html(body, value) for value in boundaries]
    rendered = []
    for index, segment in enumerate(segments):
        raw = body[html_offsets[index]:html_offsets[index + 1]]
        style = segment.get("source_style") or {}
        if style:
            indent = float(style.get("first_line_indent_em") or 0.0)
            rendered.append(
                '<span class="source-paragraph-segment" '
                'data-source-style-id="%s" data-source-indent-em="%.3f" '
                'style="display:block;text-indent:%.3fem;">%s</span>' % (
                    html_lib.escape(str(style.get("style_id") or ""), quote=True),
                    indent, indent, raw))
        else:
            rendered.append(
                '<span class="source-paragraph-segment" '
                'style="display:block;text-indent:0;">%s</span>' % raw)
    return prefix + "".join(rendered) + suffix


def apply_source_paragraph_styles_to_html(
        html_text: str, style_model: dict[str, Any],
        source_text_by_fragment: dict[str, str]) -> tuple[str, dict[str, Any]]:
    """Frozen-artifact equivalent; only paragraph content markup changes."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in style_model.get("records") or []:
        if row.get("target_mapping_success"):
            grouped.setdefault(str(row.get("flow_fragment_id") or ""), []).append(row)
    updated = html_text
    applied = []
    for fragment, styles in grouped.items():
        start, end, opening, content = _paragraph_parts(updated, fragment)
        target_text = _visible_text(content)
        source_text = str(source_text_by_fragment.get(fragment) or "")
        clean, segments, mapped = target_paragraph_segments(
            target_text, source_text, styles)
        if clean != target_text or not mapped:
            continue
        new_content = _segment_html(content, segments)
        count_attr = 'data-source-paragraph-style-count="%d"' % len(mapped)
        if re.search(r'\bdata-source-paragraph-style-count="[^"]*"', opening):
            opening = re.sub(
                r'\bdata-source-paragraph-style-count="[^"]*"',
                count_attr, opening, count=1)
        else:
            opening = opening[:-1] + " " + count_attr + ">"
        closing = re.search(r"</div\s*>$", updated[start:end], re.IGNORECASE)
        closing_text = closing.group(0) if closing else "</div>"
        updated = updated[:start] + opening + new_content + closing_text + updated[end:]
        applied.extend(mapped)
    result = copy.deepcopy(style_model)
    result["records"] = applied
    result.setdefault("metrics", {})["target_mapping_success_count"] = len(applied)
    return updated, result


__all__ = [
    "PARAGRAPH_BREAK_PREFIX", "PARAGRAPH_BREAK_RE",
    "SOURCE_STYLE_ROLES", "apply_source_paragraph_styles_to_flows",
    "apply_source_paragraph_styles_to_html",
    "infer_recovered_paragraph_styles", "mark_translation_source",
    "target_paragraph_segments",
]
