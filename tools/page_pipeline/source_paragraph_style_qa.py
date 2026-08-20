# -*- coding: utf-8 -*-
"""QA-first evidence for source-derived paragraph first-line indents.

The source decision is geometric.  A body line is an indent candidate only
when its x0 is moderately to the right of the robust column body edge, its
font matches the column body font, and the following body line returns to the
body edge.  Text content is retained only as review evidence; it never enters
the candidate decision.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import pymupdf
from PIL import Image, ImageDraw, ImageFont


PT_PER_CSS_PX = 72.0 / 96.0
MIN_INDENT_EM = 0.80
MAX_INDENT_EM = 2.50
BODY_EDGE_TOLERANCE_EM = 0.35
FONT_RELATIVE_TOLERANCE = 0.12
INDENT_ABSOLUTE_TOLERANCE_PT = 0.75


def _bbox(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return []
    return [round(float(value), 3) for value in values]


def _mode(values: Iterable[float], quantum: float) -> float:
    rows = [float(value) for value in values if float(value) > 0.0]
    if not rows:
        return 0.0
    buckets = Counter(round(value / quantum) * quantum for value in rows)
    winner = min(buckets, key=lambda value: (-buckets[value], value))
    members = [value for value in rows
               if abs(value - winner) <= quantum * 0.55]
    members.sort()
    return members[len(members) // 2] if members else winner


def extract_source_page_lines(pdf_path: str | Path,
                              page_index: int) -> dict[str, Any]:
    """Read vector-text line geometry from a source PDF; OCR is never used."""
    document = pymupdf.open(str(pdf_path))
    try:
        page = document[int(page_index)]
        width = float(page.rect.width)
        height = float(page.rect.height)
        lines = []
        for block_index, block in enumerate(page.get_text("dict")["blocks"]):
            if int(block.get("type", -1)) != 0:
                continue
            for line_index, line in enumerate(block.get("lines") or []):
                spans = list(line.get("spans") or [])
                box = _bbox(line.get("bbox"))
                if not box or not spans:
                    continue
                text = "".join(str(span.get("text") or "") for span in spans)
                sizes = [float(span.get("size") or 0.0) for span in spans
                         if float(span.get("size") or 0.0) > 0.0]
                size = _mode(sizes, 0.10) if sizes else 0.0
                center_x = (box[0] + box[2]) / 2.0
                lines.append({
                    "block_index": block_index,
                    "line_index": line_index,
                    "page_line_index": len(lines),
                    "column": 0 if center_x < width / 2.0 else 1,
                    "bbox": box,
                    "font_size": round(size, 3),
                    "text": text,
                })
        return {"page_width": width, "page_height": height,
                "page_index": int(page_index), "lines": lines,
                "ocr_used": False}
    finally:
        document.close()


def _column_statistics(page_geometry: dict[str, Any]
                       ) -> dict[int, dict[str, float]]:
    output = {}
    for column in (0, 1):
        lines = [row for row in page_geometry.get("lines") or []
                 if int(row.get("column", -1)) == column]
        if not lines:
            continue
        body_font = _mode((float(row.get("font_size") or 0.0)
                           for row in lines), 0.25)
        body_lines = [row for row in lines
                      if body_font > 0.0
                      and abs(float(row.get("font_size") or 0.0) - body_font)
                      <= body_font * FONT_RELATIVE_TOLERANCE]
        body_left = _mode((float(row["bbox"][0]) for row in body_lines), 0.25)
        body_right = _mode((float(row["bbox"][2]) for row in body_lines), 0.50)
        output[column] = {
            "body_font_size": round(body_font, 3),
            "body_left_x": round(body_left, 3),
            "body_right_x": round(body_right, 3),
        }
    return output


def detect_source_first_line_indent_candidates(
        page_geometry: dict[str, Any]) -> dict[str, Any]:
    """Detect positive first-line indentation from line geometry alone."""
    stats = _column_statistics(page_geometry)
    by_block: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in page_geometry.get("lines") or []:
        key = (int(row.get("column", -1)), int(row.get("block_index", -1)))
        by_block.setdefault(key, []).append(row)
    records = []
    for (column, block_index), rows in sorted(by_block.items()):
        rows.sort(key=lambda row: (float(row["bbox"][1]),
                                   float(row["bbox"][0])))
        column_stats = stats.get(column) or {}
        body_left = float(column_stats.get("body_left_x") or 0.0)
        body_right = float(column_stats.get("body_right_x") or 0.0)
        body_font = float(column_stats.get("body_font_size") or 0.0)
        if body_font <= 0.0 or body_right <= body_left:
            continue
        for index, line in enumerate(rows):
            size = float(line.get("font_size") or 0.0)
            if size <= 0.0 or abs(size - body_font) > (
                    body_font * FONT_RELATIVE_TOLERANCE):
                continue
            indent_pt = float(line["bbox"][0]) - body_left
            indent_em = indent_pt / size
            if not (MIN_INDENT_EM <= indent_em <= MAX_INDENT_EM):
                continue
            if index + 1 >= len(rows):
                continue
            next_line = rows[index + 1]
            next_size = float(next_line.get("font_size") or 0.0)
            returns_to_edge = (
                abs(float(next_line["bbox"][0]) - body_left)
                <= max(size, next_size) * BODY_EDGE_TOLERANCE_EM)
            source_width = float(line["bbox"][2]) - float(line["bbox"][0])
            column_width = body_right - body_left
            substantive_width = source_width >= column_width * 0.55
            if not returns_to_edge or not substantive_width:
                continue
            confidence = min(
                1.0,
                0.70
                + 0.15 * min(source_width / max(column_width, 1.0), 1.0)
                + 0.15 * (1.0 - min(abs(next_line["bbox"][0]
                                           - body_left)
                                      / max(size, 1.0), 1.0)))
            records.append({
                "style_id": "SPS-P%03d-C%d-B%d-L%d" % (
                    int(page_geometry.get("page_index") or 0) + 1,
                    column, block_index, int(line.get("line_index") or 0)),
                "paragraph_id": None,
                "flow_fragment_id": None,
                "slot_id": None,
                "semantic_role": "body",
                "source_block_index": block_index,
                "source_line_index": int(line.get("line_index") or 0),
                "source_page_line_index": int(
                    line.get("page_line_index") or 0),
                "source_line_bbox": _bbox(line.get("bbox")),
                "body_left_x": round(body_left, 3),
                "first_line_x": round(float(line["bbox"][0]), 3),
                "first_line_indent_pt": round(indent_pt, 3),
                "first_line_indent_em": round(indent_em, 3),
                "source_font_size": round(size, 3),
                "confidence": round(confidence, 3),
                "source_has_first_line_indent": True,
                "geometry_evidence": {
                    "following_line_returns_to_body_edge": True,
                    "substantive_first_line_width": True,
                    "candidate_text_used": False,
                },
                "source_text_preview": str(line.get("text") or "")[:160],
            })
    return {
        "schema_version": "visual_v07.source_paragraph_style_candidates.v1",
        "measurement_truth": "source PDF vector-text line geometry",
        "ocr_used": False,
        "text_content_used_for_detection": False,
        "column_statistics": stats,
        "records": records,
        "metrics": {
            "source_first_line_indent_candidate_count": len(records),
        },
        "decision": "red_ready" if records else "no_candidate",
    }


def measure_target_first_line_indents(
        html_path: str | Path, *,
        screenshot_path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Measure rendered target segment first lines in Chromium."""
    from playwright.sync_api import sync_playwright

    path = Path(html_path).resolve()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=[
            "--no-sandbox", "--disable-gpu", "--allow-file-access-from-files"])
        page = browser.new_page(viewport={"width": 900, "height": 1200},
                                device_scale_factor=2)
        page.goto(path.as_uri(), wait_until="networkidle")
        page.wait_for_function(
            "Array.from(document.images).every(image => image.complete)")
        raw = page.evaluate(r"""() => Object.fromEntries(
          Array.from(document.querySelectorAll(
            '.source-paragraph-segment[data-source-style-id]')).map(el => {
            const parent=el.closest('.paragraph-block');
            const parentRect=parent.getBoundingClientRect();
            const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
            const boxes=[]; let node;
            while(node=walker.nextNode()){
              if(!node.data.trim()) continue;
              const range=document.createRange(); range.selectNodeContents(node);
              for(const rect of range.getClientRects()){
                if(rect.width>0.01&&rect.height>0.01)
                  boxes.push([rect.x,rect.y,rect.right,rect.bottom]);
              }
            }
            boxes.sort((a,b)=>a[1]-b[1]||a[0]-b[0]);
            const first=boxes[0]||null;
            const style=getComputedStyle(el);
            return [el.dataset.sourceStyleId,{
              style_id:el.dataset.sourceStyleId,
              flow_fragment_id:parent.dataset.flowFragment||'',
              slot_id:parent.dataset.sourceSlotId||'',
              source_indent_em:parseFloat(el.dataset.sourceIndentEm||'0')||0,
              parent_rect:[parentRect.x,parentRect.y,parentRect.right,parentRect.bottom],
              first_rect:first,
              font_size_px:parseFloat(style.fontSize)||0,
              computed_text_indent_px:parseFloat(style.textIndent)||0
            }];
          }))""")
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
    output = {}
    for style_id, row in raw.items():
        parent = row["parent_rect"]
        first = row["first_rect"]
        actual = ((first[0] - parent[0]) * PT_PER_CSS_PX
                  if first else 0.0)
        font_pt = float(row["font_size_px"]) * PT_PER_CSS_PX
        output[style_id] = {
            "style_id": style_id,
            "flow_fragment_id": row["flow_fragment_id"],
            "slot_id": row["slot_id"],
            "target_parent_bbox": [round(value * PT_PER_CSS_PX, 3)
                                   for value in parent],
            "target_first_line_bbox": (
                [round(value * PT_PER_CSS_PX, 3) for value in first]
                if first else []),
            "target_font_size": round(font_pt, 3),
            "target_first_line_indent_pt": round(actual, 3),
            "target_first_line_indent_em": round(
                actual / font_pt, 3) if font_pt else 0.0,
            "computed_text_indent_pt": round(
                float(row["computed_text_indent_px"]) * PT_PER_CSS_PX, 3),
        }
    return output


def source_paragraph_style_qa(
        style_model: dict[str, Any], html_path: str | Path, *,
        collision_qa: dict[str, Any] | None = None,
        screenshot_path: str | Path | None = None) -> dict[str, Any]:
    """Audit missing/wrong/false indents and immutable slot geometry."""
    expected = {str(row.get("style_id") or ""): row
                for row in style_model.get("records") or []
                if row.get("source_has_first_line_indent")}
    measured = measure_target_first_line_indents(
        html_path, screenshot_path=screenshot_path)
    records = []
    for style_id, source in expected.items():
        target = measured.get(style_id) or {}
        expected_em = float(source.get("first_line_indent_em") or 0.0)
        expected_pt = expected_em * float(
            target.get("target_font_size")
            or source.get("source_font_size") or 0.0)
        actual_pt = float(target.get("target_first_line_indent_pt") or 0.0)
        tolerance = max(
            INDENT_ABSOLUTE_TOLERANCE_PT,
            expected_pt * 0.12)
        missing = (not target or actual_pt < max(expected_pt * 0.50, 0.5))
        wrong = bool(target) and not missing and abs(actual_pt - expected_pt) > tolerance
        records.append({
            **source, **target,
            "expected_target_indent_pt": round(expected_pt, 3),
            "indent_tolerance_pt": round(tolerance, 3),
            "final_first_line_indent_missing": missing,
            "final_first_line_indent_wrong": wrong,
            "false_first_line_indent": False,
        })
    false_rows = []
    for style_id, target in measured.items():
        if style_id in expected:
            continue
        false_rows.append({
            **target,
            "source_has_first_line_indent": False,
            "final_first_line_indent_missing": False,
            "final_first_line_indent_wrong": False,
            "false_first_line_indent": True,
        })
    records.extend(false_rows)
    slot_mutations = 0
    for row in records:
        source_box = _bbox(row.get("source_slot_bbox"))
        target_box = _bbox(row.get("target_parent_bbox"))
        mutated = bool(source_box and target_box and any(
            abs(source_box[index] - target_box[index]) > 0.15
            for index in range(4)))
        row["slot_geometry_mutation"] = mutated
        slot_mutations += int(mutated)
    collision_count = int(((collision_qa or {}).get("metrics") or {}).get(
        "final_block_collision_count", 0))
    metrics = {
        "source_has_first_line_indent_count": len(expected),
        "first_line_indent_missing_count": sum(
            bool(row.get("final_first_line_indent_missing"))
            for row in records),
        "first_line_indent_wrong_count": sum(
            bool(row.get("final_first_line_indent_wrong"))
            for row in records),
        "false_first_line_indent_count": len(false_rows),
        "slot_geometry_mutation_count": slot_mutations,
        "final_block_collision_count": collision_count,
    }
    hard = (
        "first_line_indent_missing_count", "first_line_indent_wrong_count",
        "false_first_line_indent_count", "slot_geometry_mutation_count",
        "final_block_collision_count",
    )
    if all(metrics[name] == 0 for name in hard):
        decision = "pass"
    elif (metrics["source_has_first_line_indent_count"] > 0
          and metrics["first_line_indent_missing_count"] > 0
          and not measured):
        decision = "red_confirmed"
    else:
        decision = "blocked"
    return {
        "schema_version": "visual_v07.source_paragraph_style_qa.v1",
        "decision": decision,
        "measurement_truth": {
            "source": "source PDF vector-text line geometry",
            "target": "Chromium text-node Range rectangles",
            "ocr_used": False,
            "ordinary_source_line_breaks_copied": False,
        },
        "metrics": metrics,
        "records": records,
    }


def _font(size: int = 15) -> ImageFont.ImageFont:
    for value in ("C:/Windows/Fonts/arial.ttf",
                  "C:/Windows/Fonts/msyh.ttc",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(value).exists():
            return ImageFont.truetype(value, size)
    return ImageFont.load_default()


def render_first_line_indent_overlay(
        style_qa: dict[str, Any], screenshot_path: str | Path,
        output_path: str | Path, *, page_width: float,
        page_height: float, title: str = "Source Paragraph First-Line Indent"
        ) -> Path:
    """Draw target slot/first-line evidence plus a compact audit ledger."""
    with Image.open(screenshot_path) as source:
        page = source.convert("RGB")
    records = list(style_qa.get("records") or [])
    panel_width = 930
    row_height = 112
    height = max(page.height, 55 + row_height * len(records))
    canvas = Image.new("RGB", (page.width + panel_width, height), "white")
    canvas.paste(page, (0, 0))
    draw = ImageDraw.Draw(canvas)
    font = _font(15)
    small = _font(13)
    draw.text((page.width + 12, 10), title, fill="#111111", font=font)
    sx = page.width / float(page_width)
    sy = page.height / float(page_height)
    for index, row in enumerate(records, start=1):
        defect = any(row.get(name) for name in (
            "final_first_line_indent_missing", "final_first_line_indent_wrong",
            "false_first_line_indent", "slot_geometry_mutation"))
        color = "#D7191C" if defect else "#0A9B50"
        box = _bbox(row.get("target_parent_bbox")
                     or row.get("source_slot_bbox")
                     or row.get("source_line_bbox"))
        if box:
            pixel = (round(box[0] * sx), round(box[1] * sy),
                     round(box[2] * sx), round(box[3] * sy))
            draw.rectangle(pixel, outline=color, width=4)
            draw.rectangle((pixel[0], pixel[1], pixel[0] + 25,
                            pixel[1] + 21), fill=color)
            draw.text((pixel[0] + 5, pixel[1] + 2), str(index),
                      fill="white", font=small)
        y = 42 + (index - 1) * row_height
        draw.rectangle((page.width + 8, y - 2, canvas.width - 8,
                        y + row_height - 8), outline=color, width=2)
        lines = [
            "#%d %s | %s | %s" % (
                index, row.get("style_id"), row.get("flow_fragment_id"),
                "DEFECT" if defect else "PASS"),
            "source left=%.3f first=%.3f indent=%.3fpt / %.3fem" % (
                float(row.get("body_left_x") or 0.0),
                float(row.get("first_line_x") or 0.0),
                float(row.get("first_line_indent_pt") or 0.0),
                float(row.get("first_line_indent_em") or 0.0)),
            "target indent=%.3fpt / %.3fem | font=%.3fpt" % (
                float(row.get("target_first_line_indent_pt") or 0.0),
                float(row.get("target_first_line_indent_em") or 0.0),
                float(row.get("target_font_size") or 0.0)),
            "missing=%s wrong=%s false=%s geometry=%s" % (
                str(bool(row.get("final_first_line_indent_missing"))).lower(),
                str(bool(row.get("final_first_line_indent_wrong"))).lower(),
                str(bool(row.get("false_first_line_indent"))).lower(),
                str(bool(row.get("slot_geometry_mutation"))).lower()),
        ]
        for offset, line in enumerate(lines):
            draw.text((page.width + 15, y + 4 + offset * 23), line,
                      fill="#222222", font=small)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target)
    return target


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-pdf", required=True)
    parser.add_argument("--page-index", type=int, required=True)
    parser.add_argument("--html", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--screenshot")
    args = parser.parse_args()
    geometry = extract_source_page_lines(args.source_pdf, args.page_index)
    model = detect_source_first_line_indent_candidates(geometry)
    result = source_paragraph_style_qa(
        model, args.html, screenshot_path=args.screenshot)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(json.dumps({"decision": result["decision"],
                      "metrics": result["metrics"]},
                     ensure_ascii=False, indent=2))
    return 0 if result["decision"] in {"pass", "red_confirmed"} else 2


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "BODY_EDGE_TOLERANCE_EM", "INDENT_ABSOLUTE_TOLERANCE_PT",
    "MAX_INDENT_EM", "MIN_INDENT_EM",
    "detect_source_first_line_indent_candidates",
    "extract_source_page_lines", "measure_target_first_line_indents",
    "render_first_line_indent_overlay", "source_paragraph_style_qa",
]
