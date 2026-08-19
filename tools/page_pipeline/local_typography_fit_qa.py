# -*- coding: utf-8 -*-
"""QA-first proof and final audit for browser-measured local typography fit.

The QA unit is one complete translated soft ``.paragraph-block``.  A block
is a candidate when its final Chromium height exceeds the source-derived
capacity before the next block.  Every attempted fit level is rendered and
measured in Chromium; formula children, spans, and script gaps remain owned
by their parent block and are never independent fit units.
"""
from __future__ import annotations

import html as html_lib
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from region_local_packing import PACK_GAP

PT_PER_CSS_PX = 72.0 / 96.0

# QA-first experimental ladder.  Production later supplies its own
# TypographyFitPolicy; keeping this probe independent prevents the QA from
# merely asserting the implementation under test.
QA_FIRST_BODY_LEVELS = (
    {"fit_level": "L0", "font_scale": 1.0,
     "line_height_scale": 1.0, "wrapping": "source"},
    {"fit_level": "L1", "font_scale": 1.0,
     "line_height_scale": 1.0, "wrapping": "optimized"},
    {"fit_level": "L2", "font_scale": 1.0,
     "line_height_scale": 0.98, "wrapping": "optimized"},
    {"fit_level": "L3", "font_scale": 0.98,
     "line_height_scale": 0.98, "wrapping": "optimized"},
    {"fit_level": "L4", "font_scale": 0.95,
     "line_height_scale": 0.94, "wrapping": "optimized"},
)


def _paragraph_tag(html: str, fragment_id: str) -> re.Match[str] | None:
    escaped = html_lib.escape(fragment_id, quote=True)
    pattern = re.compile(
        r'<div\b(?=[^>]*\bclass="[^"]*\bparagraph-block\b[^"]*")'
        r'(?=[^>]*\bdata-flow-fragment="%s")[^>]*>'
        % re.escape(escaped), re.IGNORECASE)
    return pattern.search(html)


def _style_number(style: str, property_name: str) -> float:
    match = re.search(r"(?i)(?:^|;)\s*%s\s*:\s*([-+0-9.]+)"
                      % re.escape(property_name), style)
    if match is None:
        raise ValueError("missing %s in paragraph style" % property_name)
    return float(match.group(1))


def patch_typography_level(html: str, fragment_id: str, *,
                           source_font_size: float,
                           source_line_height: float,
                           font_scale: float,
                           line_height_scale: float,
                           wrapping: str) -> str:
    """Patch only one complete paragraph block for an isolated QA trial."""
    matched = _paragraph_tag(html, fragment_id)
    if matched is None:
        raise ValueError("RenderIdentity not found: %s" % fragment_id)
    tag = matched.group(0)
    style_match = re.search(r'\bstyle="([^"]*)"', tag, re.IGNORECASE)
    if style_match is None:
        raise ValueError("paragraph has no inline style: %s" % fragment_id)
    style = style_match.group(1)
    replacements = {
        "font-size": source_font_size * font_scale,
        "line-height": source_line_height * line_height_scale,
    }
    for name, value in replacements.items():
        style = re.sub(
            r"(?i)((?:^|;)\s*%s\s*:)\s*[-+0-9.]+(?:pt|px)?"
            % re.escape(name),
            lambda match, v=value: "%s%.3fpt" % (match.group(1), v),
            style, count=1)
    if wrapping == "optimized":
        for name, value in (("overflow-wrap", "anywhere"),
                            ("word-break", "break-word"),
                            ("line-break", "loose"),
                            ("hyphens", "none"),
                            ("text-wrap", "pretty")):
            property_pattern = (
                r"(?i)((?:^|;)\s*%s\s*:)[^;]*" % re.escape(name))
            if re.search(property_pattern, style):
                style = re.sub(
                    property_pattern,
                    lambda match, v=value: "%s%s" % (match.group(1), v),
                    style, count=1)
            else:
                style += ";%s:%s" % (name, value)
    new_tag = tag[:style_match.start(1)] + style + tag[style_match.end(1):]
    return html[:matched.start()] + new_tag + html[matched.end():]


def _with_base(html: str, source_html: str | Path) -> str:
    base = Path(source_html).resolve().parent.as_uri().rstrip("/") + "/"
    return html.replace("<head>", '<head><base href="%s">' % base, 1)


def _measure_html(html_path: str | Path, fragment_id: str,
                  screenshot_path: str | Path) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    html_path = Path(html_path).resolve()
    screenshot_path = Path(screenshot_path).resolve()
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=[
            "--no-sandbox", "--disable-gpu", "--allow-file-access-from-files"])
        page = browser.new_page(viewport={"width": 900, "height": 1200},
                                device_scale_factor=2)
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.wait_for_function(
            "Array.from(document.images).every(image => image.complete)")
        result = page.evaluate(r"""fragmentId => {
          const el=document.querySelector(
            `.paragraph-block[data-flow-fragment="${CSS.escape(fragmentId)}"]`);
          if(!el) return null;
          const r=el.getBoundingClientRect(); const cs=getComputedStyle(el);
          return {render_id:el.dataset.renderId||'',
            flow_fragment_id:el.dataset.flowFragment||'',
            semantic_role:el.dataset.role||'body',
            render_source:el.dataset.renderSource||'',
            rect:{x:r.x,y:r.y,right:r.right,bottom:r.bottom,
                  width:r.width,height:r.height},
            font_size_px:parseFloat(cs.fontSize)||0,
            line_height_px:parseFloat(cs.lineHeight)||0};
        }""", fragment_id)
        if result is None:
            browser.close()
            raise ValueError("measured RenderIdentity missing: %s" % fragment_id)
        body = page.evaluate(r"""() => {
          const r=document.body.getBoundingClientRect();
          return {width:r.width,height:r.height};
        }""")
        page.screenshot(path=str(screenshot_path), clip={
            "x": 0, "y": 0, "width": body["width"],
            "height": body["height"]})
        browser.close()
    rect = result.pop("rect")
    result.update({
        "dom_measured_bbox": [round(rect["x"] * PT_PER_CSS_PX, 3),
                              round(rect["y"] * PT_PER_CSS_PX, 3),
                              round(rect["right"] * PT_PER_CSS_PX, 3),
                              round(rect["bottom"] * PT_PER_CSS_PX, 3)],
        "dom_measured_height": round(rect["height"] * PT_PER_CSS_PX, 3),
        "final_font_size": round(result.pop("font_size_px")
                                  * PT_PER_CSS_PX, 3),
        "final_line_height": round(result.pop("line_height_px")
                                    * PT_PER_CSS_PX, 3),
        "screenshot": str(screenshot_path),
    })
    return result


def qa_first_probe(before_collision_qa: dict[str, Any], *,
                   source_html_path: str | Path,
                   out_dir: str | Path,
                   levels: tuple[dict[str, Any], ...] =
                   QA_FIRST_BODY_LEVELS) -> dict[str, Any]:
    """Prove, before production changes, that bounded local fit has effect."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source_html_path = Path(source_html_path)
    source_html = source_html_path.read_text(encoding="utf-8")
    by_fragment = {row["flow_fragment_id"]: row
                   for row in before_collision_qa.get("blocks") or []}
    records = []
    seen = set()
    for collision in before_collision_qa.get("collisions") or []:
        fragment_id = collision["predecessor_fragment_id"]
        if fragment_id in seen:
            continue
        seen.add(fragment_id)
        block = by_fragment[fragment_id]
        successor = by_fragment[collision["successor_fragment_id"]]
        if block.get("render_source") != "canonical_target":
            continue
        capacity = (float(successor["assigned_top"])
                    - float(block["assigned_top"]) - PACK_GAP)
        source_font = float(block["font_size"])
        source_line = float(block["line_height"])
        attempts = []
        selected = None
        for level in levels:
            trial_html = patch_typography_level(
                source_html, fragment_id, source_font_size=source_font,
                source_line_height=source_line,
                font_scale=float(level["font_scale"]),
                line_height_scale=float(level["line_height_scale"]),
                wrapping=str(level["wrapping"]))
            trial_path = out_dir / ("%s_%s.html" % (
                fragment_id.replace("-", "_"), level["fit_level"]))
            trial_path.write_text(_with_base(trial_html, source_html_path),
                                  encoding="utf-8")
            measured = _measure_html(
                trial_path, fragment_id,
                out_dir / ("%s_%s.png" % (
                    fragment_id.replace("-", "_"), level["fit_level"])))
            fits = measured["dom_measured_height"] <= capacity + 0.5
            attempt = {**level, **measured, "region_capacity": round(capacity, 3),
                       "fits_original_region": fits}
            attempts.append(attempt)
            if fits:
                selected = attempt
                break
        records.append({
            "render_id": block["render_id"],
            "paragraph_id": block["paragraph_id"],
            "flow_fragment_id": fragment_id,
            "semantic_role": block["semantic_role"],
            "source_font_size": source_font,
            "source_line_height": source_line,
            "region_capacity": round(capacity, 3),
            "measured_height_before": block["dom_measured_height"],
            "measured_height_after": (selected or attempts[-1])[
                "dom_measured_height"],
            "final_font_size": (selected or attempts[-1])["final_font_size"],
            "final_line_height": (selected or attempts[-1])[
                "final_line_height"],
            "font_scale": (selected or attempts[-1])["font_scale"],
            "line_height_scale": (selected or attempts[-1])[
                "line_height_scale"],
            "fit_level": ((selected or attempts[-1])["fit_level"]),
            "fit_success": selected is not None,
            "repack_used": False,
            "original_top_preserved": True,
            "attempts": attempts,
        })
    qa = local_typography_fit_qa(records)
    before_defects = sum(
        row["measured_height_before"] > row["region_capacity"] + 0.5
        for row in records)
    effect = sum(row["fit_success"]
                 and row["measured_height_after"]
                 <= row["region_capacity"] + 0.5
                 for row in records)
    return {
        "schema_version": "visual_v07.local_typography_fit_qa_first.v1",
        "decision": ("red_confirmed" if before_defects > 0 and effect > 0
                     else "not_proven"),
        "old_logic_requires_repack_count": before_defects,
        "bounded_local_fit_effect_count": effect,
        "records": records,
        "qa": qa,
    }


def local_typography_fit_qa(records: list[dict[str, Any]], *,
                            heading_hierarchy_violations: int = 0) -> dict:
    """Audit minimum-level selection, floors, and repack precedence."""
    candidate_count = len(records)
    applied_count = sum(row.get("fit_level") not in (None, "L0")
                        for row in records)
    success_count = sum(bool(row.get("fit_success")) for row in records)
    unresolved_count = sum(not row.get("fit_success")
                           and not row.get("repack_used") for row in records)
    unnecessary_repack = sum(bool(row.get("fit_success"))
                             and bool(row.get("repack_used"))
                             for row in records)
    excessive = 0
    font_floor = 0
    line_floor = 0
    for row in records:
        attempts = row.get("attempts") or []
        first_fit = next((attempt["fit_level"] for attempt in attempts
                          if attempt.get("fits_original_region")), None)
        if first_fit and row.get("fit_level") != first_fit:
            excessive += 1
        min_font = float(row.get("min_font_scale", 0.95))
        min_line = float(row.get("min_line_height_scale", 0.94))
        font_floor += float(row.get("font_scale", 1.0)) < min_font - 1e-6
        line_floor += (float(row.get("line_height_scale", 1.0))
                       < min_line - 1e-6)
    metrics = {
        "typography_fit_candidate_count": candidate_count,
        "typography_fit_applied_count": applied_count,
        "typography_fit_success_count": success_count,
        "typography_fit_unresolved_count": unresolved_count,
        "unnecessary_repack_count": unnecessary_repack,
        "excessive_typography_compression_count": excessive,
        "font_floor_violation_count": int(font_floor),
        "line_height_floor_violation_count": int(line_floor),
        "heading_hierarchy_violation_count": int(
            heading_hierarchy_violations),
    }
    hard_keys = (
        "typography_fit_unresolved_count", "unnecessary_repack_count",
        "excessive_typography_compression_count",
        "font_floor_violation_count", "line_height_floor_violation_count",
        "heading_hierarchy_violation_count",
    )
    return {
        "schema_version": "visual_v07.local_typography_fit_qa.v1",
        "decision": "pass" if all(metrics[key] == 0 for key in hard_keys)
                    else "fail",
        "metrics": metrics,
        "records": records,
    }


def render_typography_fit_overlay(
        records: list[dict[str, Any]], final_collision_qa: dict[str, Any],
        screenshot_path: str | Path, out_path: str | Path, *,
        page_width: float, page_height: float) -> None:
    """Label every fitted block with its complete source/final budget."""
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    sx, sy = image.width / page_width, image.height / page_height
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
    by_fragment = {
        str(block.get("flow_fragment_id") or ""): block
        for block in final_collision_qa.get("blocks") or []
    }
    for index, record in enumerate(records):
        fragment_id = str(record.get("flow_fragment_id") or "")
        block = by_fragment.get(fragment_id)
        if not block:
            continue
        box = block["final_bbox"]
        xy = [box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy]
        draw.rectangle(xy, outline=(0, 125, 210, 245), width=4)
        label = (
            "%s | %s | %s\n"
            "font %.3f -> %.3f pt (x%.3f) | line x%.3f\n"
            "height %.3f -> %.3f pt | capacity %.3f pt | repack %s"
            % (record.get("render_id"), record.get("semantic_role"),
               record.get("fit_level"), record.get("source_font_size", 0.0),
               record.get("final_font_size", 0.0),
               record.get("font_scale", 1.0),
               record.get("line_height_scale", 1.0),
               record.get("measured_height_before", 0.0),
               record.get("measured_height_after", 0.0),
               record.get("region_capacity", 0.0),
               "yes" if record.get("repack_used") else "no"))
        # Put the first label at the top-left margin because a tall body block
        # may begin near the page top; subsequent labels are stacked.
        x = max(8, min(image.width - 520, int(xy[0])))
        y = 12 + index * 70
        bbox = draw.multiline_textbbox((x, y), label, font=font, spacing=3)
        draw.rectangle((bbox[0] - 5, bbox[1] - 4, bbox[2] + 5,
                        bbox[3] + 4), fill=(255, 255, 255, 235),
                       outline=(0, 125, 210, 245), width=2)
        draw.multiline_text((x, y), label, fill=(0, 75, 135, 255),
                            font=font, spacing=3)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Local typography fit QA-first")
    parser.add_argument("--before-qa", required=True)
    parser.add_argument("--html", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    before = json.loads(Path(args.before_qa).read_text(encoding="utf-8"))
    result = qa_first_probe(before, source_html_path=args.html,
                            out_dir=args.out_dir)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps({
        "decision": result["decision"],
        "old_logic_requires_repack_count": result[
            "old_logic_requires_repack_count"],
        "bounded_local_fit_effect_count": result[
            "bounded_local_fit_effect_count"],
        "metrics": result["qa"]["metrics"],
        "records": result["records"],
    }, ensure_ascii=False))
    return 0 if result["decision"] == "red_confirmed" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
