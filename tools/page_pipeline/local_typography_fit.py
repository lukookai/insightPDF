# -*- coding: utf-8 -*-
"""Browser-measured, source-relative typography fit for soft text blocks.

The fit ladder is deliberately bounded and role-aware.  It changes only the
typography of one complete paragraph flow item at a time, never its position
or region.  A caller must render every proposed level in Chromium and return
the resulting DOM measurement.  When the role floor is still too tall, the
returned flow graph keeps that floor and the caller may invoke the existing
browser-measured region-local repack as a second-stage fallback.

Tables and their LogicalCells are explicitly delegated to Task 1's cell-local
fit.  Formula, figure, table, and reserved-region anchors are not candidates.
"""
from __future__ import annotations

import copy
import html as html_lib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from region_local_packing import PACK_GAP

PT_PER_CSS_PX = 72.0 / 96.0
FIT_TOLERANCE = 0.5


@dataclass(frozen=True)
class TypographyFitLevel:
    fit_level: str
    font_scale: float
    line_height_scale: float
    wrapping: str


class TypographyFitPolicy:
    """Return a bounded, source-relative ladder for one semantic role."""

    _BODY = (
        TypographyFitLevel("L0", 1.00, 1.00, "source"),
        TypographyFitLevel("L1", 1.00, 1.00, "optimized"),
        TypographyFitLevel("L2", 1.00, 0.98, "optimized"),
        TypographyFitLevel("L3", 0.98, 0.98, "optimized"),
        TypographyFitLevel("L4", 0.95, 0.94, "optimized"),
    )
    _HEADING = (
        TypographyFitLevel("L0", 1.00, 1.00, "source"),
        TypographyFitLevel("L1", 1.00, 1.00, "optimized"),
        TypographyFitLevel("L2", 1.00, 0.99, "optimized"),
        TypographyFitLevel("L3", 0.99, 0.99, "optimized"),
        TypographyFitLevel("L4", 0.98, 0.98, "optimized"),
    )
    _CAPTION = (
        TypographyFitLevel("L0", 1.00, 1.00, "source"),
        TypographyFitLevel("L1", 1.00, 1.00, "optimized"),
        TypographyFitLevel("L2", 1.00, 0.97, "optimized"),
        TypographyFitLevel("L3", 0.97, 0.97, "optimized"),
        TypographyFitLevel("L4", 0.93, 0.92, "optimized"),
    )
    _FOOTNOTE = (
        TypographyFitLevel("L0", 1.00, 1.00, "source"),
        TypographyFitLevel("L1", 1.00, 1.00, "optimized"),
        TypographyFitLevel("L2", 1.00, 0.97, "optimized"),
        TypographyFitLevel("L3", 0.97, 0.97, "optimized"),
        TypographyFitLevel("L4", 0.94, 0.93, "optimized"),
    )

    def __init__(self, role: str | None):
        self.role = self.canonical_role(role)

    @staticmethod
    def canonical_role(role: str | None) -> str:
        value = str(role or "body").strip().lower().replace("-", "_")
        if value in {"heading", "title", "section_heading", "subheading"}:
            return "heading"
        if value in {"caption", "figure_caption", "table_caption"}:
            return "caption"
        if value in {"footnote", "footer", "note"}:
            return "footnote"
        if value in {"table_cell", "cell", "logical_cell"}:
            return "table_cell"
        if value in {"formula", "equation", "figure", "image", "table",
                     "reserved", "reserved_region", "hard_anchor"}:
            return value
        return "body"

    @property
    def delegated(self) -> bool:
        return self.role == "table_cell"

    @property
    def eligible(self) -> bool:
        return self.role in {"body", "heading", "caption", "footnote"}

    @property
    def levels(self) -> tuple[TypographyFitLevel, ...]:
        if self.role == "heading":
            return self._HEADING
        if self.role == "caption":
            return self._CAPTION
        if self.role == "footnote":
            return self._FOOTNOTE
        if self.role == "body":
            return self._BODY
        return ()

    @property
    def min_font_scale(self) -> float:
        return min((level.font_scale for level in self.levels), default=1.0)

    @property
    def min_line_height_scale(self) -> float:
        return min((level.line_height_scale for level in self.levels),
                   default=1.0)


def _blocks_by_fragment(collision_qa: dict[str, Any]) -> dict[str, dict]:
    return {str(row.get("flow_fragment_id") or row.get("render_id") or ""): row
            for row in collision_qa.get("blocks") or []}


def build_fit_candidates(collision_qa: dict[str, Any], *,
                         gap: float = PACK_GAP) -> list[dict[str, Any]]:
    """Derive soft predecessor candidates and their source-region capacity."""
    by_fragment = _blocks_by_fragment(collision_qa)
    candidates = []
    seen: set[str] = set()
    for collision in collision_qa.get("collisions") or []:
        fragment_id = str(collision.get("predecessor_fragment_id") or "")
        if not fragment_id or fragment_id in seen:
            continue
        seen.add(fragment_id)
        block = by_fragment.get(fragment_id)
        successor = by_fragment.get(str(
            collision.get("successor_fragment_id") or ""))
        if not block or not successor:
            continue
        policy = TypographyFitPolicy(block.get("semantic_role"))
        if not policy.eligible:
            continue
        if block.get("render_source") not in (None, "", "canonical_target"):
            continue
        if (block.get("column"), block.get("region_id")) != (
                successor.get("column"), successor.get("region_id")):
            continue
        capacity = (float(successor["assigned_top"])
                    - float(block["assigned_top"]) - float(gap))
        if capacity <= 0:
            continue
        candidates.append({
            "render_id": block.get("render_id"),
            "paragraph_id": block.get("paragraph_id"),
            "flow_fragment_id": fragment_id,
            "semantic_role": policy.role,
            "column": int(block.get("column") or 0),
            "region_id": str(block.get("region_id") or ""),
            "successor_render_id": successor.get("render_id"),
            "successor_fragment_id": successor.get("flow_fragment_id"),
            "source_font_size": float(block["font_size"]),
            "source_line_height": float(block["line_height"]),
            "source_assigned_top": float(block["assigned_top"]),
            "successor_source_top": float(successor["assigned_top"]),
            "region_capacity": round(capacity, 3),
            "measured_height_before": float(block["dom_measured_height"]),
            "min_font_scale": policy.min_font_scale,
            "min_line_height_scale": policy.min_line_height_scale,
        })
    return candidates


def _flow_item_map(flows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapped = {}
    for flow in flows:
        for item in flow.get("items") or []:
            if item.get("kind") != "paragraph":
                continue
            key = str(item.get("flow_fragment_id")
                      or item.get("paragraph_id") or "")
            if key:
                mapped[key] = item
    return mapped


def apply_flow_fit_level(flows: list[dict[str, Any]], fragment_id: str,
                         level: TypographyFitLevel) -> list[dict[str, Any]]:
    """Apply one relative typography level without changing any geometry."""
    updated = copy.deepcopy(flows)
    item = _flow_item_map(updated).get(str(fragment_id))
    if item is None:
        raise ValueError("paragraph flow item not found: %s" % fragment_id)
    original_top = item.get("flow_y")
    item.update({
        "local_typography_fit_applied": level.fit_level != "L0",
        "local_typography_fit_level": level.fit_level,
        "local_typography_font_scale": level.font_scale,
        "local_typography_line_height_scale": level.line_height_scale,
        "local_typography_wrapping": level.wrapping,
    })
    if item.get("flow_y") != original_top:
        raise AssertionError("local typography fit changed paragraph top")
    return updated


def _safe_wrapping(style: str, wrapping: str) -> str:
    if wrapping != "optimized":
        return style
    # These improve legal CJK break opportunities while preserving protected
    # Latin/math runs.  ``word-break:break-all`` and ``line-break:anywhere``
    # are intentionally forbidden because they split identifiers.
    values = {
        "overflow-wrap": "anywhere",
        "word-break": "break-word",
        "line-break": "loose",
        "hyphens": "none",
        "text-wrap": "pretty",
    }
    for name, value in values.items():
        pattern = r"(?i)((?:^|;)\s*%s\s*:)[^;]*" % re.escape(name)
        if re.search(pattern, style):
            style = re.sub(pattern,
                           lambda match, v=value: "%s%s" % (match.group(1), v),
                           style, count=1)
        else:
            style += ";%s:%s" % (name, value)
    return style


def _paragraph_tag(html_text: str, fragment_id: str) -> re.Match[str] | None:
    fragment = html_lib.escape(fragment_id, quote=True)
    return re.search(
        r'<div\b(?=[^>]*\bclass="[^"]*\bparagraph-block\b[^"]*")'
        r'(?=[^>]*\bdata-flow-fragment="%s")[^>]*>'
        % re.escape(fragment), html_text, re.IGNORECASE)


def patch_html_fit_level(html_text: str, fragment_id: str, *,
                         source_font_size: float,
                         source_line_height: float,
                         level: TypographyFitLevel) -> str:
    """Patch one frozen complete paragraph; never changes ``top``/``left``."""
    matched = _paragraph_tag(html_text, fragment_id)
    if matched is None:
        raise ValueError("paragraph RenderIdentity not found: %s" % fragment_id)
    tag = matched.group(0)
    style_match = re.search(r'\bstyle="([^"]*)"', tag, re.IGNORECASE)
    if style_match is None:
        raise ValueError("paragraph has no inline style: %s" % fragment_id)
    style = style_match.group(1)
    for name, value in (
            ("font-size", source_font_size * level.font_scale),
            ("line-height", source_line_height * level.line_height_scale)):
        pattern = (r"(?i)((?:^|;)\s*%s\s*:)\s*[-+0-9.]+(?:pt|px)?"
                   % re.escape(name))
        if not re.search(pattern, style):
            raise ValueError("paragraph style missing %s: %s"
                             % (name, fragment_id))
        style = re.sub(pattern,
                       lambda match, v=value: "%s%.3fpt" % (match.group(1), v),
                       style, count=1)
    style = _safe_wrapping(style, level.wrapping)
    new_tag = tag[:style_match.start(1)] + style + tag[style_match.end(1):]
    audit_attrs = (
        ' data-local-fit-level="%s" data-local-font-scale="%.3f"'
        ' data-local-line-height-scale="%.3f" data-local-wrapping="%s"'
        % (level.fit_level, level.font_scale, level.line_height_scale,
           level.wrapping))
    new_tag = new_tag[:-1] + audit_attrs + ">"
    return html_text[:matched.start()] + new_tag + html_text[matched.end():]


def _with_base(html_text: str, source_html_path: str | Path) -> str:
    base = Path(source_html_path).resolve().parent.as_uri().rstrip("/") + "/"
    return html_text.replace("<head>", '<head><base href="%s">' % base, 1)


def measure_paragraph_blocks(html_path: str | Path,
                             fragment_ids: Iterable[str], *,
                             screenshot_path: str | Path | None = None
                             ) -> dict[str, dict[str, Any]]:
    """Measure complete paragraph blocks in Chromium (the fit truth)."""
    from playwright.sync_api import sync_playwright

    html_path = Path(html_path).resolve()
    ids = [str(value) for value in fragment_ids]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=[
            "--no-sandbox", "--disable-gpu", "--allow-file-access-from-files"])
        page = browser.new_page(viewport={"width": 900, "height": 1200},
                                device_scale_factor=2)
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.wait_for_function(
            "Array.from(document.images).every(image => image.complete)")
        raw = page.evaluate(r"""ids => Object.fromEntries(ids.map(id => {
          const el=document.querySelector(
            `.paragraph-block[data-flow-fragment="${CSS.escape(id)}"]`);
          if(!el) return [id,null];
          const r=el.getBoundingClientRect(), cs=getComputedStyle(el);
          return [id,{render_id:el.dataset.renderId||'',
            flow_fragment_id:el.dataset.flowFragment||'',
            semantic_role:el.dataset.role||'body',
            render_source:el.dataset.renderSource||'',
            rect:[r.x,r.y,r.right,r.bottom],
            font_size_px:parseFloat(cs.fontSize)||0,
            line_height_px:parseFloat(cs.lineHeight)||0}];
        }))""", ids)
        if screenshot_path is not None:
            target = Path(screenshot_path).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            body = page.evaluate(r"""() => {
              const r=document.body.getBoundingClientRect();
              return {width:r.width,height:r.height};
            }""")
            page.screenshot(path=str(target), clip={
                "x": 0, "y": 0, "width": body["width"],
                "height": body["height"]})
        browser.close()
    missing = [fragment_id for fragment_id in ids if raw.get(fragment_id) is None]
    if missing:
        raise ValueError("measured paragraph missing: %s" % ", ".join(missing))
    result = {}
    for fragment_id in ids:
        row = raw[fragment_id]
        rect = row.pop("rect")
        font_size_px = row.pop("font_size_px")
        line_height_px = row.pop("line_height_px")
        result[fragment_id] = {
            **row,
            "dom_measured_bbox": [round(value * PT_PER_CSS_PX, 3)
                                  for value in rect],
            "dom_measured_height": round(
                (rect[3] - rect[1]) * PT_PER_CSS_PX, 3),
            "final_font_size": round(
                font_size_px * PT_PER_CSS_PX, 3),
            "final_line_height": round(
                line_height_px * PT_PER_CSS_PX, 3),
        }
    return result


def _heading_hierarchy_ok(candidate: dict[str, Any], final_font: float,
                          initial_qa: dict[str, Any]) -> bool:
    if candidate["semantic_role"] != "heading":
        return True
    bodies = [float(block["font_size"])
              for block in initial_qa.get("blocks") or []
              if (block.get("column"), block.get("region_id")) ==
              (candidate["column"], candidate["region_id"])
              and TypographyFitPolicy.canonical_role(
                  block.get("semantic_role")) == "body"]
    if not bodies:
        return True
    body_font = max(bodies)
    source_font = float(candidate["source_font_size"])
    if source_font > body_font + 0.01:
        return final_font > body_font + 0.01
    # If the source did not establish a strict hierarchy, fitting may not
    # make its ratio any worse.
    source_ratio = source_font / max(body_font, 0.01)
    return final_font / max(body_font, 0.01) >= source_ratio - 1e-3


def heading_hierarchy_violation_count(
        records: list[dict[str, Any]], initial_qa: dict[str, Any]) -> int:
    return sum(
        record.get("semantic_role") == "heading"
        and not _heading_hierarchy_ok(
            record, float(record.get("final_font_size") or 0.0), initial_qa)
        for record in records)


def _l0_attempt(candidate: dict[str, Any]) -> dict[str, Any]:
    measured = float(candidate["measured_height_before"])
    return {
        "fit_level": "L0", "font_scale": 1.0,
        "line_height_scale": 1.0, "wrapping": "source",
        "dom_measured_height": measured,
        "final_font_size": float(candidate["source_font_size"]),
        "final_line_height": float(candidate["source_line_height"]),
        "region_capacity": float(candidate["region_capacity"]),
        "fits_original_region": (
            measured <= float(candidate["region_capacity"]) + FIT_TOLERANCE),
        "heading_hierarchy_ok": True,
        "measurement_truth": "chromium_dom_initial_render",
    }


def _record(candidate: dict[str, Any], attempts: list[dict[str, Any]],
            selected: dict[str, Any] | None) -> dict[str, Any]:
    final = selected or attempts[-1]
    return {
        **candidate,
        "measured_height_after": final["dom_measured_height"],
        "final_font_size": final["final_font_size"],
        "final_line_height": final["final_line_height"],
        "font_scale": final["font_scale"],
        "line_height_scale": final["line_height_scale"],
        "fit_level": final["fit_level"],
        "fit_success": selected is not None,
        "repack_used": False,
        "original_top_preserved": True,
        "attempts": attempts,
    }


FlowTrialRenderer = Callable[
    [list[dict[str, Any]], str, list[str]], dict[str, dict[str, Any]]]


def fit_flows_with_browser(
        flows: list[dict[str, Any]], collision_qa: dict[str, Any],
        render_trial: FlowTrialRenderer
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Try each candidate level in order using caller-supplied Chromium QA."""
    current = copy.deepcopy(flows)
    records = []
    for candidate in build_fit_candidates(collision_qa):
        policy = TypographyFitPolicy(candidate["semantic_role"])
        attempts = [_l0_attempt(candidate)]
        selected = None
        last_trial = current
        for level in policy.levels[1:]:
            trial = apply_flow_fit_level(current,
                                         candidate["flow_fragment_id"], level)
            measured = render_trial(
                trial, "%s_%s" % (candidate["flow_fragment_id"],
                                    level.fit_level),
                [candidate["flow_fragment_id"]])[
                    candidate["flow_fragment_id"]]
            hierarchy_ok = _heading_hierarchy_ok(
                candidate, float(measured["final_font_size"]), collision_qa)
            fits = (float(measured["dom_measured_height"])
                    <= float(candidate["region_capacity"]) + FIT_TOLERANCE
                    and hierarchy_ok)
            attempt = {
                **asdict(level), **measured,
                "region_capacity": candidate["region_capacity"],
                "fits_original_region": fits,
                "heading_hierarchy_ok": hierarchy_ok,
                "measurement_truth": "chromium_dom_trial_render",
            }
            attempts.append(attempt)
            last_trial = trial
            if fits:
                selected = attempt
                current = trial
                break
        if selected is None:
            # The entire bounded budget has been tried.  Retaining the floor
            # makes the subsequent repack the true second-stage fallback.
            current = last_trial
        records.append(_record(candidate, attempts, selected))
    return current, {
        "schema_version": "visual_v07.local_typography_fit.v1",
        "fit_order": ["L0", "L1", "L2", "L3", "L4"],
        "measurement_truth": "chromium_dom_each_level",
        "records": records,
    }


def fit_html_with_browser(
        html_text: str, collision_qa: dict[str, Any], *,
        source_html_path: str | Path, trial_dir: str | Path
        ) -> tuple[str, dict[str, Any]]:
    """Frozen-artifact equivalent of :func:`fit_flows_with_browser`."""
    trial_dir = Path(trial_dir)
    trial_dir.mkdir(parents=True, exist_ok=True)
    current = html_text
    records = []
    for candidate in build_fit_candidates(collision_qa):
        policy = TypographyFitPolicy(candidate["semantic_role"])
        attempts = [_l0_attempt(candidate)]
        selected = None
        last_trial = current
        for level in policy.levels[1:]:
            trial = patch_html_fit_level(
                current, candidate["flow_fragment_id"],
                source_font_size=float(candidate["source_font_size"]),
                source_line_height=float(candidate["source_line_height"]),
                level=level)
            stem = "%s_%s" % (
                re.sub(r"[^A-Za-z0-9_.-]+", "_",
                       candidate["flow_fragment_id"]), level.fit_level)
            trial_path = trial_dir / (stem + ".html")
            trial_path.write_text(_with_base(trial, source_html_path),
                                  encoding="utf-8")
            measured = measure_paragraph_blocks(
                trial_path, [candidate["flow_fragment_id"]],
                screenshot_path=trial_dir / (stem + ".png"))[
                    candidate["flow_fragment_id"]]
            hierarchy_ok = _heading_hierarchy_ok(
                candidate, float(measured["final_font_size"]), collision_qa)
            fits = (float(measured["dom_measured_height"])
                    <= float(candidate["region_capacity"]) + FIT_TOLERANCE
                    and hierarchy_ok)
            attempt = {
                **asdict(level), **measured,
                "region_capacity": candidate["region_capacity"],
                "fits_original_region": fits,
                "heading_hierarchy_ok": hierarchy_ok,
                "measurement_truth": "chromium_dom_trial_render",
                "trial_html": str(trial_path),
            }
            attempts.append(attempt)
            last_trial = trial
            if fits:
                selected = attempt
                current = trial
                break
        if selected is None:
            current = last_trial
        records.append(_record(candidate, attempts, selected))
    return current, {
        "schema_version": "visual_v07.local_typography_fit.v1",
        "fit_order": ["L0", "L1", "L2", "L3", "L4"],
        "measurement_truth": "chromium_dom_each_level",
        "records": records,
    }


def mark_repack_usage(trace: dict[str, Any], repack: dict[str, Any]) -> dict:
    """Mark floor-exhausted candidates that legitimately entered fallback."""
    updated = copy.deepcopy(trace)
    repacked_regions = {
        (int(row.get("column") or 0), str(row.get("region_id") or ""))
        for row in repack.get("placements") or []
    }
    for record in updated.get("records") or []:
        record["repack_used"] = (
            not record.get("fit_success")
            and (int(record.get("column") or 0),
                 str(record.get("region_id") or "")) in repacked_regions)
    return updated


__all__ = [
    "TypographyFitLevel", "TypographyFitPolicy", "apply_flow_fit_level",
    "build_fit_candidates", "fit_flows_with_browser",
    "fit_html_with_browser", "heading_hierarchy_violation_count",
    "mark_repack_usage", "measure_paragraph_blocks",
    "patch_html_fit_level",
]
