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
LOCK_PAINT_TOLERANCE = 0.15


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
        if block.get("geometry_locked"):
            # Task 4B locked blocks already exhausted their slot-local
            # L0-L4 ladder.  A remaining collision is a hard BLOCK, never a
            # reason to enter legacy successor-moving behavior.
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


def apply_flow_content_top_offset(
        flows: list[dict[str, Any]], fragment_id: str,
        offset: float) -> list[dict[str, Any]]:
    """Move glyph paint inside a locked envelope without moving the block."""
    updated = copy.deepcopy(flows)
    item = _flow_item_map(updated).get(str(fragment_id))
    if item is None:
        raise ValueError("paragraph flow item not found: %s" % fragment_id)
    original_geometry = (item.get("source_slot_left"), item.get("flow_y"),
                         item.get("source_slot_width"),
                         item.get("source_slot_height"))
    item["source_slot_content_top_offset"] = round(float(offset), 3)
    if original_geometry != (
            item.get("source_slot_left"), item.get("flow_y"),
            item.get("source_slot_width"), item.get("source_slot_height")):
        raise AssertionError("content offset changed source slot geometry")
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


def _set_data_attr(tag: str, name: str, value: str) -> str:
    """Set one audit attribute without accumulating duplicate HTML attrs."""
    pattern = r'\b%s="[^"]*"' % re.escape(name)
    attribute = '%s="%s"' % (
        name, html_lib.escape(str(value), quote=True))
    if re.search(pattern, tag, re.IGNORECASE):
        return re.sub(pattern, attribute, tag, count=1,
                      flags=re.IGNORECASE)
    return tag[:-1] + " " + attribute + ">"


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
    for name, value in {
            "data-local-fit-level": level.fit_level,
            "data-local-font-scale": "%.3f" % level.font_scale,
            "data-local-line-height-scale": "%.3f" % (
                level.line_height_scale),
            "data-local-wrapping": level.wrapping,
    }.items():
        new_tag = _set_data_attr(new_tag, name, value)
    return html_text[:matched.start()] + new_tag + html_text[matched.end():]


def patch_html_content_top_offset(html_text: str, fragment_id: str,
                                  offset: float) -> str:
    """Offset the inner text span while preserving the locked envelope."""
    matched = _paragraph_tag(html_text, fragment_id)
    if matched is None:
        raise ValueError("paragraph RenderIdentity not found: %s" % fragment_id)
    closing = re.search(r"</div\s*>", html_text[matched.end():],
                        re.IGNORECASE)
    if closing is None:
        raise ValueError("paragraph closing tag not found: %s" % fragment_id)
    paragraph_end = matched.end() + closing.start()
    inner = re.search(
        r'<span\b(?=[^>]*\bclass="[^"]*\bslot-text-content\b[^"]*")[^>]*>',
        html_text[matched.end():paragraph_end], re.IGNORECASE)
    if inner is None:
        raise ValueError("locked paragraph has no inner text span: %s"
                         % fragment_id)
    start = matched.end() + inner.start()
    end = matched.end() + inner.end()
    tag = html_text[start:end]
    style_match = re.search(r'\bstyle="([^"]*)"', tag, re.IGNORECASE)
    if style_match is None:
        raise ValueError("paragraph has no inline style: %s" % fragment_id)
    style = style_match.group(1)
    pattern = r"(?i)((?:^|;)\s*top\s*:)[^;]*"
    value = float(offset)
    if re.search(pattern, style):
        style = re.sub(pattern,
                       lambda match: "%s%.3fpt" % (match.group(1), value),
                       style, count=1)
    else:
        style += ";top:%.3fpt" % value
    new_tag = tag[:style_match.start(1)] + style + tag[style_match.end(1):]
    updated = html_text[:start] + new_tag + html_text[end:]
    opening = _paragraph_tag(updated, fragment_id)
    opening_tag = opening.group(0)
    attr_pattern = r'\bdata-slot-content-top-offset="[^"]*"'
    if re.search(attr_pattern, opening_tag, re.IGNORECASE):
        new_opening = re.sub(
            attr_pattern, 'data-slot-content-top-offset="%.3f"' % value,
            opening_tag, count=1, flags=re.IGNORECASE)
    else:
        new_opening = opening_tag[:-1] + (
            ' data-slot-content-top-offset="%.3f">' % value)
    return (updated[:opening.start()] + new_opening
            + updated[opening.end():])


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
          const painted=[];
          const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
          let node;
          while(node=walker.nextNode()){
            if(!node.data.trim()) continue;
            const range=document.createRange();
            range.selectNodeContents(node);
            for(const box of range.getClientRects()){
              if(box.width>0.01 && box.height>0.01)
                painted.push([box.x,box.y,box.right,box.bottom]);
            }
          }
          for(const child of el.querySelectorAll('img,svg')){
            const box=child.getBoundingClientRect();
            if(box.width>0.01 && box.height>0.01)
              painted.push([box.x,box.y,box.right,box.bottom]);
          }
          const paintedRect=painted.length ? [
            Math.min(...painted.map(box=>box[0])),
            Math.min(...painted.map(box=>box[1])),
            Math.max(...painted.map(box=>box[2])),
            Math.max(...painted.map(box=>box[3]))] : null;
          return [id,{render_id:el.dataset.renderId||'',
            flow_fragment_id:el.dataset.flowFragment||'',
            semantic_role:el.dataset.role||'body',
            render_source:el.dataset.renderSource||'',
            rect:[r.x,r.y,r.right,r.bottom],
            painted_rect:paintedRect,
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
        painted_rect = row.pop("painted_rect")
        font_size_px = row.pop("font_size_px")
        line_height_px = row.pop("line_height_px")
        painted_bbox = ([round(value * PT_PER_CSS_PX, 3)
                         for value in painted_rect]
                        if painted_rect else [])
        result[fragment_id] = {
            **row,
            "dom_measured_bbox": [round(value * PT_PER_CSS_PX, 3)
                                  for value in rect],
            "dom_measured_height": round(
                (rect[3] - rect[1]) * PT_PER_CSS_PX, 3),
            "painted_content_bbox": painted_bbox,
            "painted_content_height": round(
                painted_bbox[3] - painted_bbox[1], 3)
                if painted_bbox else 0.0,
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


def _painted_content_fits(measured: dict[str, Any],
                          source_bbox: list[float]) -> bool:
    painted = measured.get("painted_content_bbox") or []
    if len(painted) != 4 or len(source_bbox) != 4:
        return False
    tolerance = LOCK_PAINT_TOLERANCE
    return (float(painted[0]) >= float(source_bbox[0]) - tolerance
            and float(painted[1]) >= float(source_bbox[1]) - tolerance
            and float(painted[2]) <= float(source_bbox[2]) + tolerance
            and float(painted[3]) <= float(source_bbox[3]) + tolerance)


def _locked_attempt(candidate: dict[str, Any],
                    level: TypographyFitLevel,
                    measured: dict[str, Any],
                    initial_qa: dict[str, Any]) -> dict[str, Any]:
    hierarchy_ok = _heading_hierarchy_ok(
        candidate, float(measured["final_font_size"]), initial_qa)
    fits = _painted_content_fits(measured, candidate["source_bbox"]) \
        and hierarchy_ok
    return {
        **asdict(level), **measured,
        "slot_height": float(candidate["source_height"]),
        "fits_original_region": fits,
        "heading_hierarchy_ok": hierarchy_ok,
        "measurement_truth": "chromium_painted_content_trial",
    }


def _content_top_offset(candidate: dict[str, Any],
                        measured: dict[str, Any]) -> float:
    painted = measured.get("painted_content_bbox") or []
    source = candidate.get("source_bbox") or []
    if len(painted) != 4 or len(source) != 4:
        return 0.0
    lower = float(source[1]) - float(painted[1])
    upper = float(source[3]) - float(painted[3])
    if lower > upper + LOCK_PAINT_TOLERANCE:
        return 0.0
    if lower > 0.0:
        required = lower
    elif upper < 0.0:
        required = upper
    else:
        required = 0.0
    # A baseline correction larger than half the source line height is not a
    # normal font-metric offset and must not be used to conceal real overflow.
    bound = max(float(candidate.get("source_line_height") or 0.0) * 0.5,
                LOCK_PAINT_TOLERANCE)
    if abs(required) > bound:
        return 0.0
    return round(required, 3)


def _locked_record(candidate: dict[str, Any], attempts: list[dict[str, Any]],
                   selected: dict[str, Any] | None) -> dict[str, Any]:
    final = selected or attempts[-1]
    return {
        **candidate,
        "measured_height_before": attempts[0]["painted_content_height"],
        "measured_height_after": final["painted_content_height"],
        "final_envelope_bbox": final["dom_measured_bbox"],
        "painted_content_bbox": final["painted_content_bbox"],
        "final_font_size": final["final_font_size"],
        "final_line_height": final["final_line_height"],
        "font_scale": final["font_scale"],
        "line_height_scale": final["line_height_scale"],
        "fit_level": final["fit_level"],
        "fit_success": selected is not None,
        "repack_used": False,
        "attempts": attempts,
    }


def fit_locked_flows_with_browser(
        flows: list[dict[str, Any]], lock_trace: dict[str, Any],
        initial_qa: dict[str, Any], render_trial: FlowTrialRenderer
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fit every locked flow inside its slot; never invokes repack."""
    current = copy.deepcopy(flows)
    records = []
    for candidate in lock_trace.get("records") or []:
        if not candidate.get("geometry_locked"):
            records.append(copy.deepcopy(candidate))
            continue
        candidate = copy.deepcopy(candidate)
        fragment = str(candidate["flow_fragment_id"])
        policy = TypographyFitPolicy(candidate["semantic_role"])
        levels = policy.levels
        if not levels:
            records.append({**candidate, "fit_success": False,
                            "fit_failure_reason": "role_has_no_fit_policy"})
            continue
        measured = render_trial(
            current, "%s_L0" % fragment, [fragment])[fragment]
        content_offset = _content_top_offset(candidate, measured)
        if abs(content_offset) > LOCK_PAINT_TOLERANCE:
            current = apply_flow_content_top_offset(
                current, fragment, content_offset)
            measured = render_trial(
                current, "%s_L0_aligned" % fragment, [fragment])[fragment]
        candidate["content_top_offset"] = content_offset
        attempts = [_locked_attempt(candidate, levels[0], measured,
                                    initial_qa)]
        selected = attempts[0] if attempts[0]["fits_original_region"] else None
        last_trial = current
        if selected is None:
            for level in levels[1:]:
                trial = apply_flow_fit_level(current, fragment, level)
                measured = render_trial(
                    trial, "%s_%s" % (fragment, level.fit_level),
                    [fragment])[fragment]
                attempt = _locked_attempt(candidate, level, measured,
                                          initial_qa)
                attempts.append(attempt)
                last_trial = trial
                if attempt["fits_original_region"]:
                    selected = attempt
                    current = trial
                    break
        if selected is None:
            current = last_trial
        records.append(_locked_record(candidate, attempts, selected))
    return current, {
        **{key: copy.deepcopy(value) for key, value in lock_trace.items()
           if key != "records"},
        "schema_version": "visual_v07.source_text_slot_lock.v1",
        "fit_order": ["L0", "L1", "L2", "L3", "L4"],
        "measurement_truth": "chromium_painted_content_each_level",
        "records": records,
        "slot_capacity_unresolved_count": sum(
            row.get("geometry_locked") and row.get("fit_success") is False
            for row in records),
    }


def fit_locked_html_with_browser(
        html_text: str, lock_trace: dict[str, Any],
        initial_qa: dict[str, Any], *, source_html_path: str | Path,
        trial_dir: str | Path) -> tuple[str, dict[str, Any]]:
    """Frozen-HTML equivalent of locked flow fitting."""
    trial_dir = Path(trial_dir)
    trial_dir.mkdir(parents=True, exist_ok=True)
    current = html_text
    records = []
    for candidate in lock_trace.get("records") or []:
        if not candidate.get("geometry_locked"):
            records.append(copy.deepcopy(candidate))
            continue
        candidate = copy.deepcopy(candidate)
        fragment = str(candidate["flow_fragment_id"])
        policy = TypographyFitPolicy(candidate["semantic_role"])
        levels = policy.levels
        if not levels:
            records.append({**candidate, "fit_success": False,
                            "fit_failure_reason": "role_has_no_fit_policy"})
            continue
        safe_fragment = re.sub(r"[^A-Za-z0-9_.-]+", "_", fragment)

        def measure(trial_html: str, level_name: str) -> dict[str, Any]:
            path = trial_dir / ("%s_%s.html" % (safe_fragment, level_name))
            path.write_text(_with_base(trial_html, source_html_path),
                            encoding="utf-8")
            return measure_paragraph_blocks(
                path, [fragment],
                screenshot_path=trial_dir / (
                    "%s_%s.png" % (safe_fragment, level_name)))[fragment]

        measured = measure(current, "L0")
        content_offset = _content_top_offset(candidate, measured)
        if abs(content_offset) > LOCK_PAINT_TOLERANCE:
            current = patch_html_content_top_offset(
                current, fragment, content_offset)
            measured = measure(current, "L0_aligned")
        candidate["content_top_offset"] = content_offset
        attempts = [_locked_attempt(candidate, levels[0], measured,
                                    initial_qa)]
        selected = attempts[0] if attempts[0]["fits_original_region"] else None
        last_trial = current
        if selected is None:
            for level in levels[1:]:
                trial = patch_html_fit_level(
                    current, fragment,
                    source_font_size=float(candidate["source_font_size"]),
                    source_line_height=float(candidate["source_line_height"]),
                    level=level)
                measured = measure(trial, level.fit_level)
                attempt = _locked_attempt(candidate, level, measured,
                                          initial_qa)
                attempts.append(attempt)
                last_trial = trial
                if attempt["fits_original_region"]:
                    selected = attempt
                    current = trial
                    break
        if selected is None:
            current = last_trial
        records.append(_locked_record(candidate, attempts, selected))
    return current, {
        **{key: copy.deepcopy(value) for key, value in lock_trace.items()
           if key != "records"},
        "schema_version": "visual_v07.source_text_slot_lock.v1",
        "fit_order": ["L0", "L1", "L2", "L3", "L4"],
        "measurement_truth": "chromium_painted_content_each_level",
        "records": records,
        "slot_capacity_unresolved_count": sum(
            row.get("geometry_locked") and row.get("fit_success") is False
            for row in records),
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
    "TypographyFitLevel", "TypographyFitPolicy",
    "apply_flow_content_top_offset", "apply_flow_fit_level",
    "build_fit_candidates", "fit_flows_with_browser",
    "fit_html_with_browser", "fit_locked_flows_with_browser",
    "fit_locked_html_with_browser", "heading_hierarchy_violation_count",
    "mark_repack_usage", "measure_paragraph_blocks",
    "patch_html_content_top_offset", "patch_html_fit_level",
]
