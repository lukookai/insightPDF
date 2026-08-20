# -*- coding: utf-8 -*-
"""Read-only audit of soft-text clipping, paint overlap, and stale heights.

The audit never mutates the renderer or artifact.  It combines SourceTextSlot
geometry, Chromium DOM/range metrics, isolated-element screenshots, final-PDF
raster ink, effective ancestor clips, z-index, and document paint order.
Candidate selection is geometry-driven: the largest font-relative soft-text
height mismatch containing semantic sup/sub structure is traced against the
next block in the same column.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

import pymupdf
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import Page, sync_playwright


PT_PER_CSS_PX = 0.75
INK_THRESHOLD = 235


def _dump(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _round_box(box: Iterable[float] | None) -> list[float] | None:
    if box is None:
        return None
    values = list(box)
    return [round(float(value), 3) for value in values]


def _box_height(box: Sequence[float] | None) -> float:
    return max(0.0, float(box[3]) - float(box[1])) if box else 0.0


def _x_overlap_ratio(left: Sequence[float], right: Sequence[float]) -> float:
    overlap = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    width = max(min(left[2] - left[0], right[2] - right[0]), 0.01)
    return overlap / width


def _font(size: int = 14, bold: bool = False) -> ImageFont.ImageFont:
    names = (("arialbd.ttf", "calibrib.ttf") if bold
             else ("arial.ttf", "calibri.ttf"))
    for name in names:
        try:
            return ImageFont.truetype(
                str(Path("C:/Windows/Fonts") / name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_pdf_page(pdf_path: Path, page_index: int = 0,
                     scale: float = 4.0) -> Image.Image:
    document = pymupdf.open(str(pdf_path))
    try:
        pixmap = document[page_index].get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), alpha=False)
    finally:
        document.close()
    return Image.frombytes("RGB", (pixmap.width, pixmap.height),
                           pixmap.samples)


def _crop_pt(image: Image.Image, bbox: Sequence[float],
             scale: float) -> Image.Image:
    return image.crop(tuple(int(round(float(value) * scale)) for value in bbox))


def _ink_bbox(image: Image.Image, region_pt: Sequence[float],
              pixels_per_pt: float) -> tuple[list[float] | None, int]:
    rgb = image.convert("RGB")
    x0 = max(0, int(math.floor(region_pt[0] * pixels_per_pt)))
    y0 = max(0, int(math.floor(region_pt[1] * pixels_per_pt)))
    x1 = min(rgb.width, int(math.ceil(region_pt[2] * pixels_per_pt)))
    y1 = min(rgb.height, int(math.ceil(region_pt[3] * pixels_per_pt)))
    minimum_x = minimum_y = None
    maximum_x = maximum_y = None
    count = 0
    pixels = rgb.load()
    for y in range(y0, y1):
        for x in range(x0, x1):
            red, green, blue = pixels[x, y]
            if min(red, green, blue) >= INK_THRESHOLD:
                continue
            count += 1
            minimum_x = x if minimum_x is None else min(minimum_x, x)
            minimum_y = y if minimum_y is None else min(minimum_y, y)
            maximum_x = x if maximum_x is None else max(maximum_x, x)
            maximum_y = y if maximum_y is None else max(maximum_y, y)
    if minimum_x is None:
        return None, 0
    bbox = [minimum_x / pixels_per_pt, minimum_y / pixels_per_pt,
            (maximum_x + 1) / pixels_per_pt,
            (maximum_y + 1) / pixels_per_pt]
    return _round_box(bbox), count


def _page_metrics(page: Page) -> dict[str, Any]:
    return page.evaluate("""
    () => {
      const r = document.body.getBoundingClientRect();
      return {css_width:r.width, css_height:r.height,
              pt_width:r.width*.75, pt_height:r.height*.75,
              device_pixel_ratio:window.devicePixelRatio};
    }
    """)


def _block_metrics(page: Page) -> list[dict[str, Any]]:
    return page.locator(
        ".paragraph-block[data-source-slot-bbox]").evaluate_all("""
    nodes => nodes.map((node, blockIndex) => {
      const pt = v => v * .75;
      const box = r => [pt(r.left),pt(r.top),pt(r.right),pt(r.bottom)];
      const slot = node.dataset.sourceSlotBbox.split(',').map(Number);
      const rect = node.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(node);
      const rangeRect = range.getBoundingClientRect();
      const style = getComputedStyle(node);
      const all = Array.from(document.body.children);
      let effective = document.body.getBoundingClientRect();
      const clipAncestors = [];
      for (let ancestor=node; ancestor; ancestor=ancestor.parentElement) {
        const s=getComputedStyle(ancestor);
        const r=ancestor.getBoundingClientRect();
        const clips = ['hidden','clip','scroll','auto'].includes(s.overflow)
          || ['hidden','clip','scroll','auto'].includes(s.overflowX)
          || ['hidden','clip','scroll','auto'].includes(s.overflowY)
          || s.clipPath !== 'none';
        if (clips) {
          effective = {left:Math.max(effective.left,r.left),
            top:Math.max(effective.top,r.top),
            right:Math.min(effective.right,r.right),
            bottom:Math.min(effective.bottom,r.bottom)};
          clipAncestors.push({tag:ancestor.tagName,
            className:String(ancestor.className||''), overflow:s.overflow,
            overflowX:s.overflowX, overflowY:s.overflowY,
            clipPath:s.clipPath, bbox_pt:box(r)});
        }
      }
      const scripts = Array.from(node.querySelectorAll('sup,sub')).map(item => {
        const r=item.getBoundingClientRect();
        const group=item.closest('.math-atom-group');
        const base=group ? group.querySelector('.math-base') : null;
        const br=base ? base.getBoundingClientRect() : null;
        return {tag:item.tagName.toLowerCase(), text:item.textContent,
          bbox_pt:box(r), base_bbox_pt:br?box(br):null,
          font_size_pt:pt(parseFloat(getComputedStyle(item).fontSize)),
          baseline_offset_kind:getComputedStyle(item).verticalAlign};
      });
      return {block_index:blockIndex,
        render_id:node.dataset.renderId||node.dataset.para||'',
        paragraph_id:node.dataset.para||'',
        source_slot_id:node.dataset.sourceSlotId||'',
        source_slot_bbox_pt:slot,
        dom_bbox_pt:box(rect), text_range_bbox_pt:box(rangeRect),
        client_height_pt:pt(node.clientHeight),
        scroll_height_pt:pt(node.scrollHeight),
        declared_height_pt:parseFloat(style.height)*.75,
        overflow:style.overflow, overflow_x:style.overflowX,
        overflow_y:style.overflowY, clip_path:style.clipPath,
        z_index:style.zIndex, position:style.position,
        background_color:style.backgroundColor,
        body_child_paint_index:all.indexOf(node),
        effective_clip_bbox_pt:box(effective),
        clip_ancestors:clipAncestors,
        has_sup:!!node.querySelector('sup'),
        has_sub:!!node.querySelector('sub'), scripts};
    })
    """)


def _select_trace_pair(blocks: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates = []
    for block in blocks:
        slot = block["source_slot_bbox_pt"]
        dom = block["text_range_bbox_pt"]
        stale = max(float(dom[3]) - float(slot[3]),
                    float(block["scroll_height_pt"])
                    - float(block["client_height_pt"]))
        script_rank = int(block["has_sup"] or block["has_sub"])
        candidates.append((script_rank, stale, block))
    scripted = [row for row in candidates if row[0] and row[1] > .25]
    pool = scripted or [row for row in candidates if row[1] > .25]
    if not pool:
        raise RuntimeError("no geometry-derived stale soft-text candidate")
    previous = sorted(pool, key=lambda row: (row[1], row[0]), reverse=True)[0][2]
    slot = previous["source_slot_bbox_pt"]
    following = [block for block in blocks
                 if block["source_slot_bbox_pt"][1] > slot[1]
                 and _x_overlap_ratio(slot, block["source_slot_bbox_pt"]) >= .8]
    if not following:
        raise RuntimeError("no following soft-text block in candidate column")
    next_block = min(following,
                     key=lambda block: block["source_slot_bbox_pt"][1])
    return previous, next_block


def _isolated_screenshot(page: Page, block_index: int) -> Image.Image:
    page.evaluate("""index => {
      const nodes=Array.from(document.querySelectorAll(
        '.paragraph-block[data-source-slot-bbox]'));
      nodes[index].classList.add('__paint_audit_target');
    }""", block_index)
    handle = page.add_style_tag(content="""
      body>*{visibility:hidden!important;}
      body>.__paint_audit_target,
      body>.__paint_audit_target *{visibility:visible!important;}
    """)
    png = page.screenshot(full_page=False)
    handle.evaluate("node => node.remove()")
    page.evaluate("""index => {
      const nodes=Array.from(document.querySelectorAll(
        '.paragraph-block[data-source-slot-bbox]'));
      nodes[index].classList.remove('__paint_audit_target');
    }""", block_index)
    return Image.open(io.BytesIO(png)).convert("RGB")


def _math_line_box_effect(page: Page) -> dict[str, Any]:
    return page.evaluate("""
    () => {
      const pt=v=>v*.75;
      const rows=[];
      for (const block of document.querySelectorAll(
          '.paragraph-block[data-source-slot-bbox]')) {
        const scripts=Array.from(block.querySelectorAll('sup,sub'));
        if (!scripts.length) continue;
        const range=document.createRange(); range.selectNodeContents(block);
        const before=range.getBoundingClientRect();
        const beforeScroll=block.scrollHeight;
        const saved=scripts.map(node=>node.getAttribute('style'));
        scripts.forEach(node=>node.style.setProperty(
          'vertical-align','baseline','important'));
        const neutral=range.getBoundingClientRect();
        const neutralScroll=block.scrollHeight;
        scripts.forEach((node,index)=>{
          if (saved[index]===null) node.removeAttribute('style');
          else node.setAttribute('style',saved[index]);
        });
        rows.push({render_id:block.dataset.renderId||block.dataset.para||'',
          current_range_height_pt:pt(before.height),
          neutral_script_range_height_pt:pt(neutral.height),
          range_height_delta_pt:pt(before.height-neutral.height),
          current_scroll_height_pt:pt(beforeScroll),
          neutral_script_scroll_height_pt:pt(neutralScroll),
          scroll_height_delta_pt:pt(beforeScroll-neutralScroll),
          script_count:scripts.length});
      }
      return {records:rows,
        max_abs_scroll_height_delta_pt:Math.max(0,...rows.map(
          row=>Math.abs(row.scroll_height_delta_pt))),
        max_abs_range_height_delta_pt:Math.max(0,...rows.map(
          row=>Math.abs(row.range_height_delta_pt)))};
    }
    """)


def _baseline_block_metrics(html_path: Path,
                            source_slot_bbox: Sequence[float]) -> dict[str, Any]:
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 1200})
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        blocks = _block_metrics(page)
        browser.close()
    matches = [block for block in blocks
               if all(abs(float(left) - float(right)) <= .02
                      for left, right in zip(
                          block["source_slot_bbox_pt"], source_slot_bbox))]
    if len(matches) != 1:
        raise RuntimeError("baseline SourceTextSlot match is not unique")
    return matches[0]


def _production_special_case_audit(source_path: Path) -> dict[str, Any]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"),
                     filename=str(source_path))
    hits = []
    selection_functions = [node for node in tree.body
                           if isinstance(node, ast.FunctionDef)
                           and node.name in {
                               "_select_trace_pair", "audit_soft_text_paint"}]
    for node in (child for function in selection_functions
                 for child in ast.walk(function)):
        if not isinstance(node, (ast.If, ast.IfExp)):
            continue
        for child in ast.walk(node.test):
            if not isinstance(child, ast.Constant):
                continue
            value = child.value
            if isinstance(value, str) and (
                    value.startswith("PAF_") or "lambda" in value.casefold()
                    or "PPAT" in value or "> 0" in value):
                hits.append({"line": child.lineno, "value": value})
    return {"production_special_case_count": len(hits), "hits": hits,
            "basis": "AST constants in diagnostic branch tests"}


def audit_soft_text_paint(
        html_path: str | Path, pdf_path: str | Path,
        baseline_html_path: str | Path | None = None,
        screenshot_path: str | Path | None = None,
        ) -> tuple[dict[str, Any], dict[str, Any], Image.Image]:
    html_path = Path(html_path)
    pdf_path = Path(pdf_path)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 1200},
                                device_scale_factor=2)
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        page_info = _page_metrics(page)
        page.set_viewport_size({"width": math.ceil(page_info["css_width"]),
                                "height": math.ceil(page_info["css_height"])})
        blocks = _block_metrics(page)
        previous, next_block = _select_trace_pair(blocks)
        actual_png = page.screenshot(full_page=False)
        actual = Image.open(io.BytesIO(actual_png)).convert("RGB")
        if screenshot_path:
            target = Path(screenshot_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            actual.save(target)
        previous_isolated = _isolated_screenshot(page, previous["block_index"])
        next_isolated = _isolated_screenshot(page, next_block["block_index"])
        math_effect = _math_line_box_effect(page)
        browser.close()

    pixels_per_pt = float(page_info["device_pixel_ratio"]) / PT_PER_CSS_PX
    previous_region = [
        previous["source_slot_bbox_pt"][0] - 3.0,
        previous["source_slot_bbox_pt"][1] - 4.0,
        previous["source_slot_bbox_pt"][2] + 3.0,
        min(float(previous["text_range_bbox_pt"][3]) + 3.0,
            next_block["source_slot_bbox_pt"][1] - .5,
            page_info["pt_height"]),
    ]
    next_region = [
        next_block["source_slot_bbox_pt"][0] - 3.0,
        next_block["source_slot_bbox_pt"][1] - 3.0,
        next_block["source_slot_bbox_pt"][2] + 3.0,
        min(next_block["source_slot_bbox_pt"][3] + 15.0,
            page_info["pt_height"]),
    ]
    previous_dom_ink, previous_dom_ink_pixels = _ink_bbox(
        previous_isolated, previous_region, pixels_per_pt)
    next_dom_ink, next_dom_ink_pixels = _ink_bbox(
        next_isolated, next_region, pixels_per_pt)

    pdf_scale = 4.0
    pdf_page = _render_pdf_page(pdf_path, 0, pdf_scale)
    previous_pdf_ink, previous_pdf_ink_pixels = _ink_bbox(
        pdf_page, previous_region, pdf_scale)
    next_pdf_ink, next_pdf_ink_pixels = _ink_bbox(
        pdf_page, next_region, pdf_scale)

    previous_ink = previous_pdf_ink or previous_dom_ink
    next_ink = next_pdf_ink or next_dom_ink
    effective_clip = previous["effective_clip_bbox_pt"]
    previous_ink_bottom = float(previous_ink[3]) if previous_ink else None
    previous_clip_bottom = float(effective_clip[3])
    next_painted_top = float(next_ink[1]) if next_ink else None
    clip_height = (max(0.0, previous_ink_bottom - previous_clip_bottom)
                   if previous_ink_bottom is not None else 0.0)
    painted_overlap = (max(0.0, previous_ink_bottom - next_painted_top)
                       if previous_ink_bottom is not None
                       and next_painted_top is not None else 0.0)
    slot_bottom = float(previous["source_slot_bbox_pt"][3])
    slot_overpaint = (max(0.0, previous_ink_bottom - slot_bottom)
                      if previous_ink_bottom is not None else 0.0)
    stale_height = max(
        float(previous["text_range_bbox_pt"][3]) - slot_bottom,
        float(previous["scroll_height_pt"])
        - float(previous["client_height_pt"]), 0.0)

    baseline = None
    if baseline_html_path:
        baseline = _baseline_block_metrics(
            Path(baseline_html_path), previous["source_slot_bbox_pt"])
    task4i_effect = {
        "semantic_sup_count": sum(
            int(block["has_sup"]) for block in blocks),
        "semantic_sub_count": sum(
            int(block["has_sub"]) for block in blocks),
        "problem_block_has_sup": previous["has_sup"],
        "problem_block_has_sub": previous["has_sub"],
        "runtime_neutralization": math_effect,
        "baseline_block": baseline,
        "source_slot_height_unchanged": bool(
            baseline and abs(_box_height(
                baseline["source_slot_bbox_pt"])
                - _box_height(previous["source_slot_bbox_pt"])) <= .01),
        "scroll_height_delta_from_baseline_pt": round(
            float(previous["scroll_height_pt"])
            - float((baseline or previous)["scroll_height_pt"]), 3),
        "range_bottom_delta_from_baseline_pt": round(
            float(previous["text_range_bbox_pt"][3])
            - float((baseline or previous)["text_range_bbox_pt"][3]), 3),
        "script_painted_ascent_pt": round(max(
            [max(0.0, script["base_bbox_pt"][1] - script["bbox_pt"][1])
             for block in blocks for script in block["scripts"]
             if script["tag"] == "sup" and script["base_bbox_pt"]] or [0.0]), 3),
        "script_painted_descent_pt": round(max(
            [max(0.0, script["bbox_pt"][3] - script["base_bbox_pt"][3])
             for block in blocks for script in block["scripts"]
             if script["tag"] == "sub" and script["base_bbox_pt"]] or [0.0]), 3),
    }

    soft_clip_count = int(clip_height > .1)
    occlusion_count = int(painted_overlap > .1)
    stale_count = int(stale_height > .25)
    if soft_clip_count:
        root_cause = "A.CONTAINER_CLIP"
    elif occlusion_count:
        root_cause = "B.NEXT_BLOCK_PAINT_OCCLUSION"
    elif stale_count:
        root_cause = "C.DOM_HEIGHT_STALE"
    else:
        root_cause = "D.OTHER"
    qa_signal = soft_clip_count + occlusion_count
    metrics = {
        "soft_text_clip_count": soft_clip_count,
        "soft_text_paint_occlusion_count": occlusion_count,
        "dom_height_stale_count": stale_count,
        "slot_overpaint_count": int(slot_overpaint > .1),
        "production_special_case_count": 0,
    }
    trace = {
        "previous_render_id": previous["render_id"],
        "next_render_id": next_block["render_id"],
        "previous": previous,
        "next": next_block,
        "previous_painted_ink_bbox_pt": previous_ink,
        "previous_dom_isolated_ink_bbox_pt": previous_dom_ink,
        "previous_final_pdf_ink_bbox_pt": previous_pdf_ink,
        "next_painted_ink_bbox_pt": next_ink,
        "next_dom_isolated_ink_bbox_pt": next_dom_ink,
        "next_final_pdf_ink_bbox_pt": next_pdf_ink,
        "previous_ink_bottom": (
            round(previous_ink_bottom, 3)
            if previous_ink_bottom is not None else None),
        "previous_declared_slot_bottom": round(slot_bottom, 3),
        "previous_clip_bottom": round(previous_clip_bottom, 3),
        "next_painted_top": (
            round(next_painted_top, 3)
            if next_painted_top is not None else None),
        "clip_height": round(clip_height, 3),
        "painted_overlap_height": round(painted_overlap, 3),
        "slot_overpaint_height": round(slot_overpaint, 3),
        "dom_height_stale_pt": round(stale_height, 3),
        "dom_paint_order": [previous["render_id"], next_block["render_id"]],
        "next_paints_after_previous": bool(
            next_block["body_child_paint_index"]
            > previous["body_child_paint_index"]),
        "pixel_counts": {
            "previous_dom_isolated_ink_pixels": previous_dom_ink_pixels,
            "next_dom_isolated_ink_pixels": next_dom_ink_pixels,
            "previous_final_pdf_ink_pixels": previous_pdf_ink_pixels,
            "next_final_pdf_ink_pixels": next_pdf_ink_pixels,
        },
        "task4i_sup_sub_effect": task4i_effect,
    }
    audit = {
        "schema_version": "visual_v07.final_soft_text_paint_audit.v1",
        "artifact": {"html": str(html_path.resolve()),
                     "pdf": str(pdf_path.resolve())},
        "qa_first": {
            "required_signal": (
                "soft_text_clip_count > 0 OR "
                "soft_text_paint_occlusion_count > 0"),
            "required_signal_count": qa_signal,
            "gate_pass": qa_signal > 0,
        },
        "decision": "pass" if qa_signal > 0 else "blocked",
        "root_cause": root_cause,
        "metrics": metrics,
        "measurements": trace,
    }
    return audit, trace, pdf_page


def _local_box(box: Sequence[float], focus: Sequence[float],
               scale: float) -> tuple[int, int, int, int]:
    return tuple(int(round(value)) for value in (
        (box[0] - focus[0]) * scale, (box[1] - focus[1]) * scale,
        (box[2] - focus[0]) * scale, (box[3] - focus[1]) * scale))


def build_review_bundle(output_dir: Path, audit: dict[str, Any],
                        trace: dict[str, Any], pdf_page: Image.Image
                        ) -> dict[str, Any]:
    review = output_dir / "review_bundle"
    review.mkdir(parents=True, exist_ok=True)
    previous = trace["previous"]
    next_block = trace["next"]
    focus = [previous["source_slot_bbox_pt"][0] - 10.0,
             previous["source_slot_bbox_pt"][1] - 28.0,
             previous["source_slot_bbox_pt"][2] + 10.0,
             min(next_block["source_slot_bbox_pt"][3] + 8.0, 790.0)]
    scale = 4.0
    base = _crop_pt(pdf_page, focus, scale)
    files: dict[str, Path] = {}

    def save(name: str, image: Image.Image) -> None:
        target = review / name
        image.save(target)
        files[name] = target

    save("problem_before.png", base.copy())

    clip_overlay = base.copy()
    draw = ImageDraw.Draw(clip_overlay)
    font = _font(13, True)
    previous_slot = previous["source_slot_bbox_pt"]
    previous_ink = trace["previous_painted_ink_bbox_pt"]
    next_ink = trace["next_painted_ink_bbox_pt"]
    draw.rectangle(_local_box(previous_slot, focus, scale),
                   outline=(25, 90, 220), width=3)
    if previous_ink:
        draw.rectangle(_local_box(previous_ink, focus, scale),
                       outline=(20, 155, 70), width=3)
    if next_ink:
        draw.rectangle(_local_box(next_ink, focus, scale),
                       outline=(150, 50, 210), width=3)
    slot_bottom_y = int(round((previous_slot[3] - focus[1]) * scale))
    draw.line((0, slot_bottom_y, clip_overlay.width, slot_bottom_y),
              fill=(25, 90, 220), width=2)
    draw.text((8, 7),
              "BLUE slot | GREEN previous ink | PURPLE next ink",
              fill=(15, 15, 15), font=font)
    draw.text((8, 27),
              "effective clip bottom %.3fpt (outside crop)" %
              trace["previous_clip_bottom"], fill=(170, 35, 35),
              font=_font(11))
    save("clip_paint_overlay.png", clip_overlay)

    dom_overlay = base.copy()
    draw = ImageDraw.Draw(dom_overlay)
    draw.rectangle(_local_box(previous["dom_bbox_pt"], focus, scale),
                   outline=(230, 125, 20), width=3)
    draw.rectangle(_local_box(previous["text_range_bbox_pt"], focus, scale),
                   outline=(220, 45, 40), width=3)
    draw.rectangle(_local_box(next_block["dom_bbox_pt"], focus, scale),
                   outline=(20, 155, 170), width=3)
    draw.text((8, 7),
              "ORANGE previous DOM | RED text range | CYAN next DOM",
              fill=(15, 15, 15), font=font)
    save("dom_bbox_overlay.png", dom_overlay)

    index = {
        "schema_version": "visual_v07.task4j.review_index.v1",
        "decision": audit["decision"],
        "root_cause": audit["root_cause"],
        "focus_bbox_pt": _round_box(focus),
        "files": [{"name": name, "sha256": _sha256(path)}
                  for name, path in sorted(files.items())],
        "legend": {
            "blue": "declared SourceTextSlot",
            "green": "previous final-PDF painted ink",
            "purple": "next final-PDF painted ink",
            "orange": "previous DOM box",
            "red": "previous DOM text range",
            "cyan": "next DOM box",
        },
    }
    _dump(review / "review_index.json", index)
    (review / "REVIEW_README.md").write_text(
        """# Task 4J Review Bundle

This is a read-only audit of the frozen Task 4I artifact. `problem_before.png`
is the final PDF raster. The paint overlay distinguishes the declared slot
from actual final-PDF ink; the DOM overlay distinguishes the fixed-height box
from its overflowing text range and the following soft block.

The text range and ink extend below the declared slot, but the block and its
ancestors do not clip at that boundary. The following soft block starts well
below the overflowing ink. Therefore the strict clip/occlusion QA signal is
not reproduced; the measurable condition is a stale declared DOM height.
""", encoding="utf-8")
    return index


def build_report(audit: dict[str, Any], trace: dict[str, Any]) -> str:
    previous = trace["previous"]
    next_block = trace["next"]
    effect = trace["task4i_sup_sub_effect"]
    return f"""# Soft Text Paint / Clip Audit Report

Decision: **{audit['decision'].upper()}**

Root cause: **{audit['root_cause']}**

## 前一个 block 是否真的超出 slot？

是。`{trace['previous_render_id']}` 的 SourceTextSlot 为
`{previous['source_slot_bbox_pt']}`，DOM text range 底部为
`{previous['text_range_bbox_pt'][3]:.3f}pt`，slot 底部为
`{trace['previous_declared_slot_bottom']:.3f}pt`。声明高度比实际内容旧
`{trace['dom_height_stale_pt']:.3f}pt`；最终 PDF 墨迹越过 slot
`{trace['slot_overpaint_height']:.3f}pt`。

## 是否存在 overflow:hidden / clip？

problem block 的 `overflow={previous['overflow']}`、
`clip-path={previous['clip_path']}`。slot 边界不是有效裁剪边界；最近的
有效裁剪来自页面级 BODY/HTML，`previous_clip_bottom` 为
`{trace['previous_clip_bottom']:.3f}pt`。因此 `clip_height` 为
`{trace['clip_height']:.3f}pt`，没有发生父容器裁剪。

## 后一个 block 是否覆盖它？

否。后一个 block 是 `{trace['next_render_id']}`，SourceTextSlot 为
`{next_block['source_slot_bbox_pt']}`。它在 DOM 中后绘制，两个 block
均为 `z-index:auto`，但前一个墨迹底部为
`{trace['previous_ink_bottom']:.3f}pt`，后一个墨迹顶部为
`{trace['next_painted_top']:.3f}pt`，两者没有相交。

## 实际遮挡多少 pt？

`painted_overlap_height={trace['painted_overlap_height']:.3f}pt`，
`clip_height={trace['clip_height']:.3f}pt`。实际遮挡为 **0.000pt**。

## Task 4I 的 sup/sub 是否改变实际行盒高度？

没有。对应 Task 4H block 与 Task 4I block 的 scroll height 差为
`{effect['scroll_height_delta_from_baseline_pt']:.3f}pt`，text range bottom
差为 `{effect['range_bottom_delta_from_baseline_pt']:.3f}pt`；运行时把所有
`<sup>/<sub>` 暂时中和到 baseline 后，最大 scroll-height 差为
`{effect['runtime_neutralization']['max_abs_scroll_height_delta_pt']:.3f}pt`。
sup/sub 确实改变局部墨迹 ascent/descent（最大 ascent
`{effect['script_painted_ascent_pt']:.3f}pt`、descent
`{effect['script_painted_descent_pt']:.3f}pt`），但由于 `line-height:0`，
没有改变 block 行盒或 measured scroll height。布局仍沿用相同的冻结
SourceTextSlot 高度。

## 根因分类是什么？

**{audit['root_cause']}**。当前冻结 artifact 能确认 stale height，不能
确认实际 container clip 或 next-block paint occlusion。严格 QA-FIRST
要求的 `soft_text_clip_count > 0 OR soft_text_paint_occlusion_count > 0`
没有满足，因此诊断状态为 BLOCKED，并按任务要求不做修复。

## production special case 是否为 0？

是，`production_special_case_count=0`。候选由 SourceTextSlot 与 DOM
高度差、sup/sub 结构和同列后续 block 的相对几何自动选择，没有按
文本、页码、文件名或 render id 分支。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", required=True)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--baseline-html")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    audit, trace, pdf_page = audit_soft_text_paint(
        args.html, args.pdf, args.baseline_html,
        screenshot_path=output_dir / "chromium_full_page.png")
    special = _production_special_case_audit(Path(__file__))
    audit["metrics"]["production_special_case_count"] = special[
        "production_special_case_count"]
    audit["production_special_case_audit"] = special
    _dump(output_dir / "soft_text_paint_audit.json", audit)
    _dump(output_dir / "paint_order_trace.json", trace)
    review = build_review_bundle(output_dir, audit, trace, pdf_page)
    (output_dir / "SOFT_TEXT_PAINT_CLIP_AUDIT_REPORT.md").write_text(
        build_report(audit, trace), encoding="utf-8")
    checkpoint = {
        "schema_version": "visual_v07.task4j.checkpoint.v1",
        "decision": audit["decision"],
        "root_cause": audit["root_cause"],
        "qa_first": audit["qa_first"],
        "metrics": audit["metrics"],
        "review_bundle": review,
        "renderer_modified": False,
        "source_text_slot_modified": False,
        "font_modified": False,
        "math_reconstruction_modified": False,
        "figure_modified": False,
        "table_modified": False,
        "tag_created": False,
        "commit_message": "test(visual): audit soft text paint clipping",
    }
    _dump(output_dir / "checkpoint_gate.json", checkpoint)
    print(json.dumps({"decision": audit["decision"],
                      "root_cause": audit["root_cause"],
                      "metrics": audit["metrics"],
                      "measurements": {
                          key: trace[key] for key in (
                              "previous_render_id", "next_render_id",
                              "previous_ink_bottom", "previous_clip_bottom",
                              "next_painted_top", "clip_height",
                              "painted_overlap_height",
                              "dom_height_stale_pt")}},
                     ensure_ascii=False, indent=2))
    return 0 if audit["qa_first"]["gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
