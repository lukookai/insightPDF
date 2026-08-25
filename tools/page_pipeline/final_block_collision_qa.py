# -*- coding: utf-8 -*-
"""Final-render block-to-block collision truth QA.

Collision units are complete ``.paragraph-block`` RenderIdentity elements.
The primary geometry is Chromium's final DOM measurement; the PDF text layer
is independently sampled at the DOM line positions and recorded alongside it.
No source span, bold child, inline formula, or script gap is a collision unit.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageDraw, ImageFont
from math_aware_block_measurement import apply_math_aware_measurements
from region_local_packing import PACK_GAP

PT_PER_CSS_PX = 72.0 / 96.0


def _bbox(rect: dict[str, float], scale: float = PT_PER_CSS_PX) -> list[float]:
    return [round(float(rect["x"]) * scale, 3),
            round(float(rect["y"]) * scale, 3),
            round(float(rect["right"]) * scale, 3),
            round(float(rect["bottom"]) * scale, 3)]


def _union(boxes: list[list[float]]) -> list[float] | None:
    boxes = [box for box in boxes if len(box) == 4]
    if not boxes:
        return None
    return [round(min(box[0] for box in boxes), 3),
            round(min(box[1] for box in boxes), 3),
            round(max(box[2] for box in boxes), 3),
            round(max(box[3] for box in boxes), 3)]


def capture_dom_from_page(
        page: Any, screenshot_path: str | Path | None = None) -> dict:
    """Capture the collision ledger from an already-loaded Chromium page."""
    result = page.evaluate(r"""() => {
          const rr = r => ({x:r.x,y:r.y,width:r.width,height:r.height,
                            right:r.right,bottom:r.bottom});
          const blocks=[];
          for(const [domIndex,el] of Array.from(
              document.querySelectorAll('.paragraph-block')).entries()){
            const rect=el.getBoundingClientRect();
            const cs=getComputedStyle(el);
            const rowMap=new Map();
            const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
            let node;
            while(node=walker.nextNode()){
              for(let i=0;i<node.data.length;i++){
                if(!node.data[i].trim()) continue;
                const range=document.createRange();
                range.setStart(node,i); range.setEnd(node,i+1);
                for(const r of range.getClientRects()){
                  if(r.width<0.05||r.height<0.05) continue;
                  const key=Math.round(r.y*2)/2;
                  const old=rowMap.get(key);
                  rowMap.set(key,old ? {
                    x:Math.min(old.x,r.x),y:Math.min(old.y,r.y),
                    right:Math.max(old.right,r.right),
                    bottom:Math.max(old.bottom,r.bottom)
                  } : {x:r.x,y:r.y,right:r.right,bottom:r.bottom});
                }
              }
            }
            for(const child of el.querySelectorAll('.formula-inline,img,svg')){
              const r=child.getBoundingClientRect();
              if(r.width<0.05||r.height<0.05) continue;
              const key=Math.round(r.y*2)/2;
              const old=rowMap.get(key);
              rowMap.set(key,old ? {
                x:Math.min(old.x,r.x),y:Math.min(old.y,r.y),
                right:Math.max(old.right,r.right),
                bottom:Math.max(old.bottom,r.bottom)
              } : {x:r.x,y:r.y,right:r.right,bottom:r.bottom});
            }
            const lineRects=Array.from(rowMap.values())
              .sort((a,b)=>a.y-b.y).map(r=>({x:r.x,y:r.y,
                width:r.right-r.x,height:r.bottom-r.y,
                right:r.right,bottom:r.bottom}));
            const mathGroups=Array.from(
              el.querySelectorAll('.math-atom-group')).map(group=>{
                const base=group.querySelector('.math-base');
                const baseRect=base ? base.getBoundingClientRect() : null;
                return {group_id:group.dataset.mathGroupId||'',
                  bbox:rr(group.getBoundingClientRect()),
                  atoms:Array.from(group.querySelectorAll('.math-atom')).map(
                    atom=>({atom_id:atom.dataset.atomId||'',
                      role:Array.from(atom.classList).find(value=>
                        value.startsWith('math-')&&value!=='math-atom')
                        ?.replace('math-','')||'',
                      text:atom.textContent||'',
                      bbox:rr(atom.getBoundingClientRect()),
                      base_bbox:baseRect?rr(baseRect):null}))};
              });
            blocks.push({dom_index:domIndex,render_id:el.dataset.renderId||'',
              paragraph_id:el.dataset.para||'',
              flow_fragment_id:el.dataset.flowFragment||'',
              semantic_role:el.dataset.role||'body',
              render_source:el.dataset.renderSource||'',
              geometry_locked:el.dataset.geometryLocked==='true',
              source_slot_id:el.dataset.sourceSlotId||'',
              source_slot_bbox:el.dataset.sourceSlotBbox||'',
              repack_used:el.dataset.repackUsed==='true',
              fill_applied:el.dataset.localFillApplied==='true',
              fill_level:el.dataset.localFillLevel||'F0',
              fill_font_scale:parseFloat(
                el.dataset.localFillFontScale||'1')||1,
              fill_line_height_scale:parseFloat(
                el.dataset.localFillLineHeightScale||'1')||1,
              text:(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim(),
              rect:rr(rect),line_rects:lineRects,math_groups:mathGroups,
              style_left_pt:parseFloat(el.style.left)||0,
              style_top_pt:parseFloat(el.style.top)||0,
              style_width_pt:parseFloat(el.style.width)||0,
              font_size_px:parseFloat(cs.fontSize)||0,
              line_height_px:parseFloat(cs.lineHeight)||0});
          }
          const hard=[];
          for(const el of document.querySelectorAll(
              '.formula-seg,.figure-region,.figure-img,.translated-cell')){
            hard.push({kind:el.className||el.tagName,
              formula_id:el.dataset.formula||'',
              segment_id:el.dataset.segment||'',
              figure_id:el.dataset.figure||'',
              region_id:el.dataset.region||'',
              cell_id:el.dataset.cellId||el.dataset.cell||'',
              rect:rr(el.getBoundingClientRect())});
          }
          return {body:rr(document.body.getBoundingClientRect()),blocks,hard};
        }""")
    if screenshot_path is not None:
        target = Path(screenshot_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        body = result["body"]
        page.screenshot(path=str(target), clip={
            "x": 0, "y": 0, "width": body["width"],
            "height": body["height"]})
        result["screenshot"] = str(target)
    else:
        result["screenshot"] = None
    return result


def _capture_dom(html_path: str | Path, screenshot_path: str | Path) -> dict:
    """Compatibility wrapper for standalone QA callers.

    The fast production path calls :func:`capture_dom_from_page` so it can
    reuse the page that performed Typography measurement and final printing.
    """
    from playwright.sync_api import sync_playwright

    html_path = Path(html_path).resolve()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox",
                                                   "--disable-gpu"])
        page = browser.new_page(viewport={"width": 900, "height": 1200},
                                device_scale_factor=2)
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts && document.fonts.ready")
        page.wait_for_function(
            "Array.from(document.images).every(image => image.complete)")
        result = capture_dom_from_page(page, screenshot_path)
        browser.close()
    return result


def _source_metadata(page_model: dict) -> dict[str, dict[str, Any]]:
    metadata = {}
    for region in page_model.get("regions") or []:
        if region.get("type") != "text" or not region.get("payload"):
            continue
        para = region["payload"]
        pid = str(para.get("paragraph_id") or "")
        metadata[pid] = {
            "source_bbox": list(region.get("bbox") or []),
            "reading_order_source": region.get("reading_order"),
            "source_column": para.get("column"),
        }
    return metadata


def _role(role: str) -> str:
    role = (role or "body").lower()
    if "heading" in role:
        return "heading"
    if "caption" in role:
        return "caption"
    return "body"


def _estimate_height(text: str, font_size: float, line_height: float,
                     width: float) -> float:
    """Use the production estimator when importable; deterministic fallback."""
    try:
        from flow_layout import estimate_paragraph_height
        ratio = line_height / max(font_size, 0.1)
        return float(estimate_paragraph_height(
            text, font_size, width, {}, line_height=ratio))
    except Exception:  # noqa: BLE001
        chars_per_line = max(int(width / max(font_size, 0.1)), 1)
        lines = max(math.ceil(len(text) / chars_per_line), 1)
        return lines * line_height


def _x_overlap(a: list[float], b: list[float]) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))


def _assign_tracks(blocks: list[dict], hard_boxes: list[list[float]],
                   page_height: float) -> None:
    lefts: list[float] = []
    for block in sorted(blocks, key=lambda item: item["planned_bbox"][0]):
        left = block["planned_bbox"][0]
        match = next((index for index, value in enumerate(lefts)
                      if abs(value - left) <= 8.0), None)
        if match is None:
            lefts.append(left)
            match = len(lefts) - 1
        block["column"] = match
    lefts.sort()
    # Re-number after sort so left-to-right columns are stable.
    for block in blocks:
        block["column"] = min(range(len(lefts)),
                              key=lambda index: abs(
                                  lefts[index] - block["planned_bbox"][0]))
        separators = sorted({round(box[1], 2) for box in hard_boxes
                             if _x_overlap(block["planned_bbox"], box)
                             > min(block["planned_bbox"][2]
                                   - block["planned_bbox"][0],
                                   box[2] - box[0]) * 0.25})
        region_index = sum(top < block["planned_bbox"][1]
                           for top in separators)
        block["region_id"] = "column_%d_region_%d" % (
            block["column"], region_index)
        block["region_top"] = (0.0 if region_index == 0
                               else separators[region_index - 1])
        block["region_bottom"] = (separators[region_index] - 2.0
                                  if region_index < len(separators)
                                  else page_height)


def _pdf_word_assignment(pdf_path: str | Path,
                         blocks: list[dict]) -> dict[str, list[float] | None]:
    doc = pymupdf.open(str(pdf_path))
    try:
        words = doc[0].get_text("words")
    finally:
        doc.close()
    assigned: dict[str, list[list[float]]] = {
        block["flow_fragment_id"]: [] for block in blocks}
    line_candidates = []
    for block in blocks:
        for line in block["dom_line_bboxes"]:
            line_candidates.append((block, line,
                                    (line[1] + line[3]) / 2.0))
    for word in words:
        word_box = [float(v) for v in word[:4]]
        centre_y = (word_box[1] + word_box[3]) / 2.0
        candidates = []
        for block, line, line_centre in line_candidates:
            overlap = _x_overlap(word_box, line)
            if overlap <= 0.1:
                continue
            delta = abs(centre_y - line_centre)
            tolerance = max(3.0, block["line_height"] * 0.42)
            if delta <= tolerance:
                candidates.append((delta, -overlap, block))
        if candidates:
            _, _, owner = min(candidates, key=lambda item: (item[0], item[1]))
            assigned[owner["flow_fragment_id"]].append(word_box)
    return {key: _union(value) for key, value in assigned.items()}


def _raster_ink_bbox(image: Image.Image, region: list[float],
                     pixels_per_pt: float,
                     threshold: int = 235) -> list[float] | None:
    """Return actual non-background ink inside one owner-constrained region."""
    rgb = image.convert("RGB")
    x0 = max(0, int(math.floor((region[0] - 0.5) * pixels_per_pt)))
    y0 = max(0, int(math.floor((region[1] - 0.5) * pixels_per_pt)))
    x1 = min(rgb.width, int(math.ceil(
        (region[2] + 0.5) * pixels_per_pt)))
    y1 = min(rgb.height, int(math.ceil(
        (region[3] + 0.5) * pixels_per_pt)))
    minimum_x = minimum_y = None
    maximum_x = maximum_y = None
    pixels = rgb.load()
    for y in range(y0, y1):
        for x in range(x0, x1):
            if min(pixels[x, y]) >= threshold:
                continue
            minimum_x = x if minimum_x is None else min(minimum_x, x)
            minimum_y = y if minimum_y is None else min(minimum_y, y)
            maximum_x = x if maximum_x is None else max(maximum_x, x)
            maximum_y = y if maximum_y is None else max(maximum_y, y)
    if minimum_x is None:
        return None
    return [round(minimum_x / pixels_per_pt, 3),
            round(minimum_y / pixels_per_pt, 3),
            round((maximum_x + 1) / pixels_per_pt, 3),
            round((maximum_y + 1) / pixels_per_pt, 3)]


def _pdf_painted_ink_assignment(
        pdf_path: str | Path, blocks: list[dict],
        text_boxes: dict[str, list[float] | None], *,
        scale: float = 4.0) -> dict[str, list[float] | None]:
    """Measure final raster ink inside each text owner's final glyph region."""
    document = pymupdf.open(str(pdf_path))
    try:
        pixmap = document[0].get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), alpha=False)
    finally:
        document.close()
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height),
                            pixmap.samples)
    assigned: dict[str, list[float] | None] = {}
    for block in blocks:
        identity = block["flow_fragment_id"]
        owner_region = text_boxes.get(identity)
        assigned[identity] = (_raster_ink_bbox(image, owner_region, scale)
                              if owner_region else None)
    return assigned


def final_block_collision_qa_from_snapshot(
        page_model: dict, *, snapshot: dict,
        final_pdf_path: str | Path | None = None,
        extra_block_metadata: dict[str, dict[str, Any]] | None = None) -> dict:
    """Evaluate collision truth from an in-memory DOM/RenderLedger snapshot.

    ``final_pdf_path`` is optional for the pre-slot DOM-only check.  The final
    delivery gate supplies it and retains the existing PDF text/raster ink
    evidence without launching another Chromium process.
    """
    source = _source_metadata(page_model)
    source.update(extra_block_metadata or {})
    blocks = []
    for raw in snapshot["blocks"]:
        if not raw["text"]:
            # A formula-only wrapper is not a text collision unit; its inline
            # formula remains governed by the formula hard metrics.  Inline
            # content inside a non-empty paragraph stays part of that block.
            continue
        dom_bbox = _bbox(raw["rect"])
        font_size = raw["font_size_px"] * PT_PER_CSS_PX
        line_height = raw["line_height_px"] * PT_PER_CSS_PX
        estimated = _estimate_height(raw["text"], font_size, line_height,
                                     raw["style_width_pt"])
        planned = [round(raw["style_left_pt"], 3),
                   round(raw["style_top_pt"], 3),
                   round(raw["style_left_pt"] + raw["style_width_pt"], 3),
                   round(raw["style_top_pt"] + estimated, 3)]
        identity = raw["flow_fragment_id"] or raw["render_id"]
        meta = source.get(raw["render_id"], source.get(raw["paragraph_id"], {}))
        blocks.append({
            "render_id": raw["render_id"] or raw["paragraph_id"],
            "paragraph_id": raw["paragraph_id"],
            "flow_fragment_id": identity,
            "semantic_role": _role(raw["semantic_role"]),
            "column": None, "region_id": None, "reading_order": None,
            "source_bbox": list(meta.get("source_bbox") or []),
            "planned_bbox": planned,
            "dom_measured_bbox": dom_bbox,
            "dom_line_bboxes": [_bbox(rect) for rect in raw["line_rects"]],
            "math_atom_groups": [{
                "group_id": group.get("group_id") or "",
                "bbox_pt": _bbox(group["bbox"]),
                "atoms": [{
                    "atom_id": atom.get("atom_id") or "",
                    "role": atom.get("role") or "",
                    "text": atom.get("text") or "",
                    "bbox_pt": _bbox(atom["bbox"]),
                    "base_bbox_pt": (_bbox(atom["base_bbox"])
                                     if atom.get("base_bbox") else None),
                } for atom in group.get("atoms") or []],
            } for group in raw.get("math_groups") or []],
            "pdf_rendered_bbox": None,
            "final_bbox": dom_bbox,
            "predecessor_id": None, "successor_id": None,
            "font_size": round(font_size, 3),
            "line_height": round(line_height, 3),
            "estimated_height": round(estimated, 3),
            "estimator_probe_height": round(estimated, 3),
            "dom_measured_height": round(dom_bbox[3] - dom_bbox[1], 3),
            "height_delta": round((dom_bbox[3] - dom_bbox[1]) - estimated, 3),
            "assigned_top": planned[1],
            "packing_region": None,
            "render_source": raw["render_source"],
            "geometry_locked": bool(raw.get("geometry_locked")),
            "source_slot_id": raw.get("source_slot_id") or None,
            "source_slot_bbox": [
                round(float(value), 3) for value in
                str(raw.get("source_slot_bbox") or "").split(",")
                if value.strip()] if raw.get("source_slot_bbox") else [],
            "repack_used": bool(raw.get("repack_used")),
            "fill_applied": bool(raw.get("fill_applied")),
            "fill_level": str(raw.get("fill_level") or "F0"),
            "fill_font_scale": round(float(
                raw.get("fill_font_scale") or 1.0), 3),
            "fill_line_height_scale": round(float(
                raw.get("fill_line_height_scale") or 1.0), 3),
            "text_preview": raw["text"][:160],
            "dom_index": raw["dom_index"],
        })

    hard_boxes = [_bbox(row["rect"]) for row in snapshot["hard"]]
    page_height = float(snapshot["body"]["height"]) * PT_PER_CSS_PX
    _assign_tracks(blocks, hard_boxes, page_height)
    if final_pdf_path is not None:
        pdf_boxes = _pdf_word_assignment(final_pdf_path, blocks)
        painted_boxes = _pdf_painted_ink_assignment(
            final_pdf_path, blocks, pdf_boxes)
    else:
        pdf_boxes = {block["flow_fragment_id"]: None for block in blocks}
        painted_boxes = {block["flow_fragment_id"]: None for block in blocks}
    for block in blocks:
        block["pdf_rendered_bbox"] = pdf_boxes.get(block["flow_fragment_id"])
        block["final_pdf_painted_ink_bbox"] = painted_boxes.get(
            block["flow_fragment_id"])
        block["packing_region"] = block["region_id"]
    apply_math_aware_measurements(blocks)

    collisions = []
    groups: dict[tuple[int, str], list[dict]] = {}
    for block in blocks:
        groups.setdefault((block["column"], block["region_id"]), []).append(block)
    order_mismatch = 0
    for group in groups.values():
        group.sort(key=lambda item: (item["planned_bbox"][1],
                                     item["dom_index"]))
        for index, block in enumerate(group):
            block["reading_order"] = index
            block["predecessor_id"] = (group[index - 1]["render_id"]
                                       if index else None)
            block["successor_id"] = (group[index + 1]["render_id"]
                                     if index + 1 < len(group) else None)
            if index and block["final_bbox"][1] \
                    < group[index - 1]["final_bbox"][1] - 0.1:
                order_mismatch += 1
        # Frozen HTML does not carry the estimator result as an attribute.
        # Infer the height that placement actually allocated from the next
        # assigned top and the locked local gap; retain the standalone
        # estimator probe separately for the root-cause trace.
        for first, second in zip(group, group[1:]):
            allocated = max(second["assigned_top"] - first["assigned_top"]
                            - PACK_GAP, first["line_height"])
            first["estimator_probe_height"] = first["estimated_height"]
            first["estimated_height"] = round(
                min(first["estimated_height"], allocated), 3)
            first["planned_bbox"][3] = round(
                first["planned_bbox"][1] + first["estimated_height"], 3)
            first["height_delta"] = round(
                first["dom_measured_height"] - first["estimated_height"], 3)
        for first, second in zip(group, group[1:]):
            if _x_overlap(first["final_bbox"], second["final_bbox"]) \
                    <= min(first["final_bbox"][2] - first["final_bbox"][0],
                           second["final_bbox"][2] - second["final_bbox"][0]) * 0.5:
                continue
            source_gap = None
            if len(first["source_bbox"]) == 4 and len(second["source_bbox"]) == 4:
                source_gap = round(second["source_bbox"][1]
                                   - first["source_bbox"][3], 3)
            source_relation = "unknown"
            source_component = 0.10
            if source_gap is not None:
                if source_gap > 0.25 * min(first["line_height"],
                                           second["line_height"]):
                    source_relation = "separated"
                    source_component = 0.0
                elif source_gap >= -0.1:
                    source_relation = "touching"
                else:
                    source_relation = "overlapping"
                    source_component = 0.20
            font_component = 0.035 * min(first["font_size"],
                                          second["font_size"])
            line_component = 0.035 * min(first["line_height"],
                                          second["line_height"])
            tolerance = max(0.5, min(
                1.25, font_component + line_component + source_component))
            overlap = first["final_bbox"][3] - second["final_bbox"][1]
            if overlap <= tolerance:
                continue
            kind = "%s_%s" % (first["semantic_role"],
                               second["semantic_role"])
            collisions.append({
                "predecessor_id": first["render_id"],
                "successor_id": second["render_id"],
                "predecessor_fragment_id": first["flow_fragment_id"],
                "successor_fragment_id": second["flow_fragment_id"],
                "predecessor_role": first["semantic_role"],
                "successor_role": second["semantic_role"],
                "collision_type": kind,
                "overlap_height": round(overlap, 3),
                "allowed_tolerance": round(tolerance, 3),
                "allowed_tolerance_components": {
                    "font_size_component": round(font_component, 3),
                    "line_height_component": round(line_component, 3),
                    "source_relation_component": round(source_component, 3),
                    "source_relation": source_relation,
                },
                "source_gap": source_gap,
                "column": first["column"],
                "region_id": first["region_id"],
            })

    metrics = {
        "final_block_collision_count": len(collisions),
        "body_body_collision_count": sum(
            row["collision_type"] == "body_body" for row in collisions),
        "body_heading_collision_count": sum(
            row["collision_type"] == "body_heading" for row in collisions),
        "heading_body_collision_count": sum(
            row["collision_type"] == "heading_body" for row in collisions),
        "caption_body_collision_count": sum(
            row["collision_type"] == "caption_body" for row in collisions),
        "body_caption_collision_count": sum(
            row["collision_type"] == "body_caption" for row in collisions),
        "reading_order_overlap_count": len(collisions),
        "final_bbox_missing_count": sum(not block.get("final_bbox")
                                        for block in blocks),
        "final_bbox_order_mismatch_count": order_mismatch,
    }
    return {
        "schema_version": "visual_v07.final_block_collision_qa.v1",
        "decision": "pass" if all(value == 0 for value in metrics.values())
                    else "fail",
        "metrics": metrics,
        "blocks": blocks,
        "collisions": collisions,
        "geometry_truth": {
            "primary": (
                "union(chromium_dom_measured_bbox, "
                "final_pdf_painted_glyph_bbox)"
                if final_pdf_path is not None
                else "chromium_dom_measured_bbox"),
            "dom_component": "chromium_dom_measured_bbox",
            "painted_ink_component": (
                "final_pdf_raster_ink_within_text_owner_glyph_bbox"
                if final_pdf_path is not None else None),
            "painted_owner_constraint": (
                "final_pdf_text_layer_at_dom_line_positions"
                if final_pdf_path is not None else None),
            "pdf_evidence_used": final_pdf_path is not None,
            "render_identity": "data-render-id/data-flow-fragment",
            "allowed_tolerance": (
                "relative font-size + line-height + source-relation policy"),
        },
        "screenshot": snapshot["screenshot"],
    }


def final_block_collision_qa(
        page_model: dict, *, html_path: str | Path,
        final_pdf_path: str | Path, screenshot_path: str | Path,
        extra_block_metadata: dict[str, dict[str, Any]] | None = None) -> dict:
    """Standalone compatibility entry point with its own Chromium capture."""
    snapshot = _capture_dom(html_path, screenshot_path)
    return final_block_collision_qa_from_snapshot(
        page_model, snapshot=snapshot, final_pdf_path=final_pdf_path,
        extra_block_metadata=extra_block_metadata)


def render_collision_overlay(qa: dict, screenshot_path: str | Path,
                             out_path: str | Path,
                             page_width: float, page_height: float) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    sx, sy = image.width / page_width, image.height / page_height
    try:
        font = ImageFont.truetype("arial.ttf", 12)
    except OSError:
        font = ImageFont.load_default()
    by_id = {block["flow_fragment_id"]: block for block in qa["blocks"]}
    if not qa["collisions"]:
        for block in qa["blocks"]:
            box = block["final_bbox"]
            xy = [box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy]
            draw.rectangle(xy, outline=(0, 150, 65, 210), width=2)
        label = "PASS: final_block_collision_count=0"
        text_box = draw.textbbox((12, 12), label, font=font)
        draw.rectangle(text_box, fill=(255, 255, 255, 230))
        draw.text((12, 12), label, fill=(0, 125, 50, 255), font=font)
    for collision in qa["collisions"]:
        first = by_id[collision["predecessor_fragment_id"]]
        second = by_id[collision["successor_fragment_id"]]
        for block in (first, second):
            box = block["final_bbox"]
            xy = [box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy]
            draw.rectangle(xy, outline=(230, 0, 0, 245), width=4)
        label = ("pred=%s(%s) bbox=%s | succ=%s(%s) bbox=%s | "
                 "overlap=%.2fpt"
                 % (first["render_id"], first["semantic_role"],
                    first["final_bbox"], second["render_id"],
                    second["semantic_role"], second["final_bbox"],
                    collision["overlap_height"]))
        x = min(first["final_bbox"][0], second["final_bbox"][0]) * sx
        y = max(0, second["final_bbox"][1] * sy - 18)
        text_box = draw.textbbox((x, y), label, font=font)
        draw.rectangle(text_box, fill=(255, 255, 255, 225))
        draw.text((x, y), label, fill=(220, 0, 0, 255), font=font)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Final block collision QA")
    parser.add_argument("--model", required=True)
    parser.add_argument("--html", required=True)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--screenshot", required=True)
    parser.add_argument("--overlay")
    args = parser.parse_args()
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))
    qa = final_block_collision_qa(
        model, html_path=args.html, final_pdf_path=args.pdf,
        screenshot_path=args.screenshot)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(qa, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    if args.overlay:
        with pymupdf.open(args.pdf) as doc:
            width, height = float(doc[0].rect.width), float(doc[0].rect.height)
        render_collision_overlay(qa, args.screenshot, args.overlay,
                                 width, height)
    print(json.dumps({"decision": qa["decision"],
                      "metrics": qa["metrics"],
                      "collisions": qa["collisions"]},
                     ensure_ascii=False))
    return 0 if qa["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
