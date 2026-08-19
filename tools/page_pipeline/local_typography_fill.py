# -*- coding: utf-8 -*-
"""Bounded typography expansion inside immutable SourceTextSlots.

LocalTypographyFill is the inverse companion to LocalTypographyFit.  It is
eligible only after Task 4B has locked geometry and only when Chromium/source
ink occupancy proves that translated text is materially shorter.  Every
non-zero level is measured in Chromium and collision-checked; no placement,
anchor, region, or page operation exists in this module.
"""
from __future__ import annotations

import copy
import html as html_lib
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable

from local_typography_fill_qa import (
    EXCESSIVE_OCCUPANCY_ALLOWANCE,
    ROLE_FILL_CAPS,
    SLOT_TOLERANCE,
)
from local_typography_fit import TypographyFitPolicy


FILL_TARGET_ABSOLUTE_GAP = 0.035
# Closing at least 30% of a material gap is a meaningful visual convergence;
# the absolute 3.5% envelope still governs already-near-source blocks.  This
# avoids chasing the last 1-2pt and keeps caption/short-line cases at F0 when
# the role cap cannot make a sufficient difference.
FILL_TARGET_REMAINING_FRACTION = 0.70
FILL_TARGET_MIN_OCCUPANCY_IMPROVEMENT = 0.04
FILL_TARGET_MIN_PAINTED_IMPROVEMENT = 4.0


@dataclass(frozen=True)
class TypographyFillLevel:
    fill_level: str
    font_scale: float
    line_height_scale: float


class TypographyFillPolicy:
    """Conservative, source-relative F0-F4 ladder by semantic role."""

    _BODY = (
        TypographyFillLevel("F0", 1.000, 1.000),
        TypographyFillLevel("F1", 1.000, 1.025),
        TypographyFillLevel("F2", 1.000, 1.050),
        TypographyFillLevel("F3", 1.015, 1.060),
        TypographyFillLevel("F4", 1.030, 1.080),
    )
    _CAPTION = (
        TypographyFillLevel("F0", 1.000, 1.000),
        TypographyFillLevel("F1", 1.000, 1.020),
        TypographyFillLevel("F2", 1.000, 1.040),
        TypographyFillLevel("F3", 1.010, 1.050),
        TypographyFillLevel("F4", 1.020, 1.060),
    )
    _HEADING = (
        TypographyFillLevel("F0", 1.000, 1.000),
        TypographyFillLevel("F1", 1.000, 1.010),
        TypographyFillLevel("F2", 1.000, 1.020),
        TypographyFillLevel("F3", 1.005, 1.025),
        TypographyFillLevel("F4", 1.010, 1.030),
    )
    _FOOTNOTE = _HEADING

    def __init__(self, role: str | None):
        self.role = TypographyFitPolicy.canonical_role(role)

    @property
    def levels(self) -> tuple[TypographyFillLevel, ...]:
        if self.role == "body":
            return self._BODY
        if self.role == "caption":
            return self._CAPTION
        if self.role == "heading":
            return self._HEADING
        if self.role == "footnote":
            return self._FOOTNOTE
        return ()

    @property
    def eligible(self) -> bool:
        return bool(self.levels)

    @property
    def caps(self) -> dict[str, float]:
        return ROLE_FILL_CAPS.get(
            self.role, {"font_scale": 1.0, "line_height_scale": 1.0})


FillTrialRenderer = Callable[
    [Any, str, list[str]], dict[str, Any]]
FillLevelApplier = Callable[
    [Any, str, TypographyFillLevel, dict[str, Any]], Any]


def _flow_item_map(flows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapped = {}
    for flow in flows:
        for item in flow.get("items") or []:
            if item.get("kind") != "paragraph":
                continue
            paragraph = item.get("_para") or {}
            fragment = str(item.get("flow_fragment_id")
                           or paragraph.get("paragraph_id") or "")
            if fragment:
                mapped[fragment] = item
    return mapped


def apply_flow_fill_level(
        flows: list[dict[str, Any]], fragment_id: str,
        level: TypographyFillLevel,
        candidate: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Apply relative fill typography without touching any flow geometry."""
    updated = copy.deepcopy(flows)
    item = _flow_item_map(updated).get(str(fragment_id))
    if item is None:
        raise ValueError("fill flow fragment not found: %s" % fragment_id)
    item.update({
        "local_typography_fill_applied": level.fill_level != "F0",
        "local_typography_fill_level": level.fill_level,
        "local_typography_fill_font_scale": level.font_scale,
        "local_typography_fill_line_height_scale": (
            level.line_height_scale),
    })
    return updated


def _paragraph_tag(html_text: str, fragment_id: str) -> re.Match[str] | None:
    fragment = html_lib.escape(fragment_id, quote=True)
    return re.search(
        r'<div\b(?=[^>]*\bclass="[^"]*\bparagraph-block\b[^"]*")'
        r'(?=[^>]*\bdata-flow-fragment="%s")[^>]*>'
        % re.escape(fragment), html_text, re.IGNORECASE)


def _set_style(style: str, name: str, value: str) -> str:
    pattern = r"(?i)((?:^|;)\s*%s\s*:)[^;]*" % re.escape(name)
    if re.search(pattern, style):
        return re.sub(pattern, lambda match: "%s%s" % (
            match.group(1), value), style, count=1)
    return style + ";%s:%s" % (name, value)


def _set_data_attr(tag: str, name: str, value: str) -> str:
    pattern = r'\b%s="[^"]*"' % re.escape(name)
    attribute = '%s="%s"' % (name, html_lib.escape(value, quote=True))
    if re.search(pattern, tag, re.IGNORECASE):
        return re.sub(pattern, attribute, tag, count=1,
                      flags=re.IGNORECASE)
    return tag[:-1] + " " + attribute + ">"


def patch_html_fill_level(
        html_text: str, fragment_id: str,
        level: TypographyFillLevel,
        candidate: dict[str, Any]) -> str:
    """Patch only font-size/line-height and fill audit attributes."""
    matched = _paragraph_tag(html_text, fragment_id)
    if matched is None:
        raise ValueError("fill paragraph not found: %s" % fragment_id)
    tag = matched.group(0)
    if 'data-geometry-locked="true"' not in tag.lower():
        raise ValueError("fill requires geometry-locked paragraph: %s"
                         % fragment_id)
    style_match = re.search(r'\bstyle="([^"]*)"', tag, re.IGNORECASE)
    if style_match is None:
        raise ValueError("fill paragraph has no inline style: %s"
                         % fragment_id)
    base_font = float(candidate.get("final_font_size")
                      or candidate.get("source_font_size") or 0.0)
    base_line = float(candidate.get("final_line_height")
                      or candidate.get("source_line_height") or 0.0)
    style = style_match.group(1)
    style = _set_style(style, "font-size", "%.3fpt" % (
        base_font * level.font_scale))
    style = _set_style(style, "line-height", "%.3fpt" % (
        base_line * level.line_height_scale))
    new_tag = tag[:style_match.start(1)] + style + tag[style_match.end(1):]
    attrs = {
        "data-local-fill-applied": str(level.fill_level != "F0").lower(),
        "data-local-fill-level": level.fill_level,
        "data-local-fill-font-scale": "%.3f" % level.font_scale,
        "data-local-fill-line-height-scale": "%.3f" % (
            level.line_height_scale),
    }
    for name, value in attrs.items():
        new_tag = _set_data_attr(new_tag, name, value)
    return (html_text[:matched.start()] + new_tag
            + html_text[matched.end():])


def _bbox(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return []
    return [round(float(value), 3) for value in values]


def _outside(box: list[float], slot: list[float]) -> bool:
    if len(box) != 4 or len(slot) != 4:
        return True
    return (box[0] < slot[0] - SLOT_TOLERANCE
            or box[1] < slot[1] - SLOT_TOLERANCE
            or box[2] > slot[2] + SLOT_TOLERANCE
            or box[3] > slot[3] + SLOT_TOLERANCE)


def _slot_mutated(envelope: list[float], slot: list[float]) -> bool:
    return (len(envelope) != 4 or len(slot) != 4
            or any(abs(envelope[index] - slot[index]) > SLOT_TOLERANCE
                   for index in range(4)))


def _f0_record(candidate: dict[str, Any]) -> dict[str, Any]:
    before_box = _bbox(candidate.get("target_painted_bbox_before")
                       or candidate.get("painted_content_bbox"))
    before_ratio = float(candidate.get(
        "target_occupancy_ratio_before") or 0.0)
    before_gap = float(candidate.get("occupancy_gap_before") or 0.0)
    envelope = _bbox(candidate.get("final_envelope_bbox")
                     or candidate.get("source_bbox"))
    return {
        **candidate,
        "fill_level": "F0", "fill_font_scale": 1.0,
        "fill_line_height_scale": 1.0,
        "fill_applied": False, "fill_success": False,
        "fill_target_reached": False,
        "fill_reason": str(candidate.get("candidate_decision") or "F0"),
        "painted_content_bbox_after": before_box,
        "final_envelope_bbox": envelope,
        "target_occupancy_ratio_after": round(before_ratio, 6),
        "occupancy_gap_after": round(before_gap, 6),
        "attempts": [],
    }


def _attempt(
        candidate: dict[str, Any], level: TypographyFillLevel,
        rendered: dict[str, Any], target_gap: float,
        policy: TypographyFillPolicy) -> dict[str, Any]:
    fragment = str(candidate.get("flow_fragment_id") or "")
    measurements = rendered.get("measurements") or {}
    measured = measurements.get(fragment) or {}
    collision_qa = rendered.get("collision_qa") or {}
    collision_count = int((collision_qa.get("metrics") or {}).get(
        "final_block_collision_count", 0))
    painted = _bbox(measured.get("painted_content_bbox"))
    envelope = _bbox(measured.get("dom_measured_bbox"))
    source_box = _bbox(candidate.get("source_bbox"))
    slot_height = float(candidate.get("slot_height") or 0.0)
    painted_height = max(painted[3] - painted[1], 0.0) \
        if painted else 0.0
    target_ratio = painted_height / slot_height if slot_height else 0.0
    source_ratio = float(candidate.get("source_occupancy_ratio") or 0.0)
    gap = source_ratio - target_ratio
    before_ratio = float(candidate.get(
        "target_occupancy_ratio_before") or 0.0)
    before_height = float(candidate.get(
        "target_painted_height_before") or 0.0)
    occupancy_improvement = target_ratio - before_ratio
    painted_height_improvement = painted_height - before_height
    caps = policy.caps
    slot_safe = not _outside(painted, source_box)
    geometry_safe = not _slot_mutated(envelope, source_box)
    cap_safe = (level.font_scale <= caps["font_scale"] + 1e-6
                and level.line_height_scale
                <= caps["line_height_scale"] + 1e-6)
    not_excessive = target_ratio <= (
        source_ratio + EXCESSIVE_OCCUPANCY_ALLOWANCE)
    safe = (slot_safe and geometry_safe and cap_safe
            and not_excessive and collision_count == 0)
    meaningful_improvement = (
        occupancy_improvement
        >= FILL_TARGET_MIN_OCCUPANCY_IMPROVEMENT - 1e-6
        and painted_height_improvement
        >= FILL_TARGET_MIN_PAINTED_IMPROVEMENT - 1e-6)
    target_reached = safe and (
        gap <= target_gap + 1e-6 or meaningful_improvement)
    return {
        **asdict(level),
        "painted_content_bbox": painted,
        "final_envelope_bbox": envelope,
        "target_painted_height": round(painted_height, 3),
        "target_occupancy_ratio": round(target_ratio, 6),
        "occupancy_gap": round(gap, 6),
        "occupancy_improvement": round(occupancy_improvement, 6),
        "painted_height_improvement": round(
            painted_height_improvement, 3),
        "meaningful_improvement": meaningful_improvement,
        "fill_target_gap": round(target_gap, 6),
        "slot_boundary_safe": slot_safe,
        "slot_geometry_safe": geometry_safe,
        "font_cap_safe": level.font_scale
        <= caps["font_scale"] + 1e-6,
        "line_height_cap_safe": level.line_height_scale
        <= caps["line_height_scale"] + 1e-6,
        "not_excessive": not_excessive,
        "trial_collision_count": collision_count,
        "safe": safe, "fill_target_reached": target_reached,
        "measurement_truth": "Chromium painted content + collision QA",
    }


def _selected_record(candidate: dict[str, Any],
                     attempts: list[dict[str, Any]],
                     selected: dict[str, Any] | None) -> dict[str, Any]:
    if selected is None:
        record = _f0_record(candidate)
        record["attempts"] = attempts
        record["fill_reason"] = "NO_SAFE_SUFFICIENT_LEVEL_WITHIN_CAP"
        return record
    return {
        **candidate,
        "fill_level": selected["fill_level"],
        "fill_font_scale": selected["font_scale"],
        "fill_line_height_scale": selected["line_height_scale"],
        "fill_applied": selected["fill_level"] != "F0",
        "fill_success": bool(selected["fill_target_reached"]),
        "fill_target_reached": bool(selected["fill_target_reached"]),
        "fill_reason": "FIRST_SAFE_TARGET_LEVEL",
        "painted_content_bbox_after": selected["painted_content_bbox"],
        "final_envelope_bbox": selected["final_envelope_bbox"],
        "target_occupancy_ratio_after": selected[
            "target_occupancy_ratio"],
        "occupancy_gap_after": selected["occupancy_gap"],
        "attempts": attempts,
    }


def _fill_locked_state_with_browser(
        state: Any, candidate_qa: dict[str, Any],
        render_trial: FillTrialRenderer,
        apply_level: FillLevelApplier) -> tuple[Any, dict[str, Any]]:
    current = copy.deepcopy(state)
    records = []
    for candidate in candidate_qa.get("records") or []:
        if not candidate.get("fill_candidate"):
            records.append(_f0_record(candidate))
            continue
        policy = TypographyFillPolicy(candidate.get("semantic_role"))
        if not policy.eligible:
            record = _f0_record(candidate)
            record["fill_reason"] = "ROLE_NOT_FILL_ELIGIBLE"
            records.append(record)
            continue
        fragment = str(candidate.get("flow_fragment_id") or "")
        before_gap = float(candidate.get("occupancy_gap_before") or 0.0)
        target_gap = max(FILL_TARGET_ABSOLUTE_GAP,
                         before_gap * FILL_TARGET_REMAINING_FRACTION)
        attempts = []
        selected = None
        for level in policy.levels[1:]:
            trial = apply_level(current, fragment, level, candidate)
            rendered = render_trial(
                trial, "%s_%s" % (fragment, level.fill_level), [fragment])
            attempt = _attempt(
                candidate, level, rendered, target_gap, policy)
            attempts.append(attempt)
            if not attempt["safe"]:
                # Expansion is monotonic; never try a more aggressive level
                # after the first slot/collision/cap safety failure.
                break
            if attempt["fill_target_reached"]:
                selected = attempt
                current = trial
                break
        records.append(_selected_record(candidate, attempts, selected))
    return current, {
        "schema_version": "visual_v07.local_typography_fill.v1",
        "fill_order": ["F0", "F1", "F2", "F3", "F4"],
        "measurement_truth": "Chromium each non-zero fill level",
        "geometry_policy": "SourceTextSlot x/y/width/height immutable",
        "repack_policy": "fill never enters BrowserMeasuredRepack",
        "records": records,
    }


def fill_locked_flows_with_browser(
        flows: list[dict[str, Any]], candidate_qa: dict[str, Any],
        render_trial: FillTrialRenderer
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _fill_locked_state_with_browser(
        flows, candidate_qa, render_trial, apply_flow_fill_level)


def fill_locked_html_with_browser(
        html_text: str, candidate_qa: dict[str, Any],
        render_trial: FillTrialRenderer) -> tuple[str, dict[str, Any]]:
    return _fill_locked_state_with_browser(
        html_text, candidate_qa, render_trial, patch_html_fill_level)


__all__ = [
    "FILL_TARGET_ABSOLUTE_GAP",
    "FILL_TARGET_MIN_OCCUPANCY_IMPROVEMENT",
    "FILL_TARGET_MIN_PAINTED_IMPROVEMENT",
    "FILL_TARGET_REMAINING_FRACTION",
    "TypographyFillLevel", "TypographyFillPolicy",
    "apply_flow_fill_level", "fill_locked_flows_with_browser",
    "fill_locked_html_with_browser", "patch_html_fill_level",
]
