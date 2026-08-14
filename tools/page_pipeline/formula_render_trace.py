# -*- coding: utf-8 -*-
"""End-to-end FormulaRenderTrace.

The trace closes the lifecycle from source PDF ink through detection,
ownership, FormulaModel, cropped SVG, DOM placement and independently
rasterized final-PDF ink.  IDs are derived from document/page-content and
formula evidence, never from the ordinal page number.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

import pymupdf
from PIL import Image

from formula_final_ink_qa import formula_final_ink_qa
from formula_svg_qa import analyze_formula_svg


INK_THRESHOLD = 235
SOURCE_SCALE = 4.0
CONDITION_WORDS = {
    "if", "iff", "otherwise", "where", "when", "whenever",
    "for all", "for any", "correctly supports", "does not support",
    "and the survey",
}
MATH_FONT_HINTS = (
    "cmmi", "cmsy", "cmr", "cmbx", "cmex", "msam", "msbm", "eufm",
    "mtsy", "stix", "xits", "math", "latinmodern",
)


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _bbox(box):
    return [float(v) for v in box]


def _intersect(a, b, margin=0.0):
    return not (a[2] < b[0] - margin or a[0] > b[2] + margin
                or a[3] < b[1] - margin or a[1] > b[3] + margin)


def _intersection_area(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * \
        max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _union(boxes):
    boxes = [_bbox(b) for b in boxes if b and len(b) == 4]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _is_math_font(font):
    low = (font or "").lower()
    return any(hint in low for hint in MATH_FONT_HINTS)


def _formula_regions(page_model):
    return [r for r in page_model.get("regions", [])
            if r.get("type") == "formula"]


def _source_objects(source_pdf, page_index):
    doc = pymupdf.open(str(source_pdf))
    page = doc[int(page_index)]
    spans = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text") or ""
                if not text.strip():
                    continue
                spans.append({
                    "id": "S%d" % len(spans),
                    "text": text,
                    "bbox": [float(v) for v in span["bbox"]],
                    "font": span.get("font") or "",
                    "size": float(span.get("size") or 0),
                })
    drawings = []
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect is None:
            continue
        drawings.append({
            "bbox": [float(v) for v in rect],
            "type": drawing.get("type"),
            "width": float(drawing.get("width") or 0),
        })
    page_signature = json.dumps({
        "rect": [float(v) for v in page.rect],
        "spans": [(s["text"], s["font"],
                   [round(v, 2) for v in s["bbox"]]) for s in spans],
        "drawings": [(d["type"], [round(v, 2) for v in d["bbox"]])
                     for d in drawings],
    }, ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(page_signature.encode("utf-8")).hexdigest()
    size = [float(page.rect.width), float(page.rect.height)]
    doc.close()
    return spans, drawings, fingerprint, size


def _source_ink(source_pdf, page_index, bbox, scale=SOURCE_SCALE):
    doc = pymupdf.open(str(source_pdf))
    page = doc[int(page_index)]
    clip = pymupdf.Rect(*_bbox(bbox))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip,
                          alpha=False)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    px = image.load()
    xs, ys = [], []
    for y in range(image.height):
        for x in range(image.width):
            if min(px[x, y]) < INK_THRESHOLD:
                xs.append(x)
                ys.append(y)
    if not xs:
        return {
            "source_ink_pixel_count": 0,
            "source_ink_bbox": None,
            "source_ink_area": 0.0,
            "raster_scale": float(scale),
        }
    local = [min(xs), min(ys), max(xs) + 1, max(ys) + 1]
    box = _bbox(bbox)
    ink_box = [box[0] + local[0] / scale,
               box[1] + local[1] / scale,
               box[0] + local[2] / scale,
               box[1] + local[3] / scale]
    return {
        "source_ink_pixel_count": len(xs),
        "source_ink_bbox": [round(v, 3) for v in ink_box],
        "source_ink_area": round(
            (ink_box[2] - ink_box[0]) * (ink_box[3] - ink_box[1]), 4),
        "raster_scale": float(scale),
    }


def _match_span(component, spans, tolerance=0.08):
    cb = component.get("bbox") or []
    if len(cb) != 4:
        return None
    for span in spans:
        if all(abs(float(cb[i]) - float(span["bbox"][i])) <= tolerance
               for i in range(4)):
            return span
    # Adopted/merged text can differ by a few hundredths in extraction.
    best, best_area = None, 0.0
    for span in spans:
        area = _intersection_area(_bbox(cb), span["bbox"])
        if area > best_area:
            best, best_area = span, area
    component_area = max((float(cb[2]) - float(cb[0]))
                         * (float(cb[3]) - float(cb[1])), 1e-6)
    return best if best_area / component_area >= 0.9 else None


def _trace_id(document_fingerprint, page_fingerprint, fm):
    signature = {
        "document": document_fingerprint,
        "source_page_content": page_fingerprint,
        "layout_bbox": [round(float(v), 3)
                        for v in (fm.get("layout_bbox") or [])],
        "components": [
            {
                "text": c.get("text") or "",
                "font": c.get("font") or "",
                "bbox": [round(float(v), 3)
                         for v in (c.get("bbox") or [])],
            }
            for c in (fm.get("components") or [])
        ],
    }
    raw = json.dumps(signature, ensure_ascii=False, sort_keys=True)
    return "FTR-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _column_classification(bbox, grid):
    if not grid or not bbox:
        return {"column": None, "full_width": None}
    columns = grid.get("columns") or []
    overlaps = []
    for col in columns:
        cb = [float(col["x0"]), float(bbox[1]), float(col["x1"]),
              float(bbox[3])]
        overlaps.append(_intersection_area(_bbox(bbox), cb))
    supported = [i for i, area in enumerate(overlaps) if area > 0.5]
    if len(supported) >= 2:
        return {"column": "full_width", "full_width": True}
    if supported:
        return {"column": int(supported[0]), "full_width": False}
    return {"column": None, "full_width": False}


def _capture_formula_dom(html_path):
    """Capture computed formula placement and clipping metadata in Chromium."""
    from playwright.sync_api import sync_playwright

    html_path = Path(html_path).resolve()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=["--no-sandbox", "--disable-gpu"])
        page = browser.new_page(viewport={"width": 1000, "height": 1300})
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.wait_for_function(
            "Array.from(document.images).every(i => i.complete)")
        rows = page.evaluate(
            """() => {
              const rect = r => ({x:r.x,y:r.y,right:r.right,bottom:r.bottom,
                                  width:r.width,height:r.height});
              const nodes=[];
              for(const el of document.querySelectorAll('.formula-inline,.formula-seg')){
                const img=el.matches('img')?el:el.querySelector('img');
                const cs=getComputedStyle(el), is=getComputedStyle(img||el);
                const clips=[];
                for(let p=el.parentElement;p;p=p.parentElement){
                  const s=getComputedStyle(p);
                  if(!['visible','clip'].includes(s.overflow) ||
                     !['visible','clip'].includes(s.overflowX) ||
                     !['visible','clip'].includes(s.overflowY) ||
                     (s.clipPath&&s.clipPath!=='none')){
                    clips.push({tag:p.tagName,className:p.className||'',
                      overflow:s.overflow,overflowX:s.overflowX,
                      overflowY:s.overflowY,clipPath:s.clipPath,
                      rect:rect(p.getBoundingClientRect())});
                  }
                }
                nodes.push({
                  kind:el.classList.contains('formula-seg')?'display':'inline',
                  formula:el.dataset.formula||'',segment:el.dataset.segment||'',
                  layout:el.dataset.layout||'',rect:rect(el.getBoundingClientRect()),
                  imageRect:img?rect(img.getBoundingClientRect()):null,
                  src:img?img.getAttribute('src'):null,
                  imageComplete:img?img.complete:false,
                  naturalWidth:img?img.naturalWidth:0,
                  naturalHeight:img?img.naturalHeight:0,
                  style:{display:cs.display,visibility:cs.visibility,
                    opacity:cs.opacity,position:cs.position,overflow:cs.overflow,
                    overflowX:cs.overflowX,overflowY:cs.overflowY,
                    clipPath:cs.clipPath,transform:cs.transform,zIndex:cs.zIndex,
                    width:cs.width,height:cs.height},
                  imageStyle:{display:is.display,visibility:is.visibility,
                    opacity:is.opacity,transform:is.transform,
                    clipPath:is.clipPath,width:is.width,height:is.height},
                  ancestorClips:clips
                });
              }
              return nodes;
            }""")
        browser.close()
    for row in rows:
        raw = row.get("formula") or ""
        if raw.startswith("FORMULA_"):
            raw = raw[len("FORMULA_"):]
        row["formula_id"] = raw
        rect = row["rect"]
        row["target_bbox"] = [round(rect["x"] * 72 / 96, 3),
                              round(rect["y"] * 72 / 96, 3),
                              round(rect["right"] * 72 / 96, 3),
                              round(rect["bottom"] * 72 / 96, 3)]
    return rows


def _css_rect_pt(rect):
    if not rect:
        return None
    return [round(float(rect[key]) * 72 / 96, 3)
            for key in ("x", "y", "right", "bottom")]


def _clip_envelope(render_bbox, ancestor_clips):
    clip = list(render_bbox) if render_bbox else None
    evidence = []
    for ancestor in ancestor_clips or []:
        box = _css_rect_pt(ancestor.get("rect"))
        evidence.append({**ancestor, "rect_pt": box})
        if not clip or not box:
            continue
        clip = [max(clip[0], box[0]), max(clip[1], box[1]),
                min(clip[2], box[2]), min(clip[3], box[3])]
        if clip[2] < clip[0] or clip[3] < clip[1]:
            clip = [clip[0], clip[1], clip[0], clip[1]]
    return clip, evidence


def _flow_metadata(flows):
    """Formula ID -> concrete flow interval/column evidence."""
    result = defaultdict(list)
    for flow_index, flow in enumerate(flows or []):
        for item_index, item in enumerate(flow.get("items") or []):
            flow_y = float(item.get("flow_y") or 0.0)
            height = max(0.0, float(item.get("est_height") or 0.0))
            base = {
                "flow_index": flow_index,
                "item_index": item_index,
                "flow_kind": item.get("kind"),
                "column": flow.get("column", item.get("column")),
                "column_track": [flow.get("col_x0"), flow.get("col_x1")],
                "flow_interval": [round(flow_y, 3),
                                  round(flow_y + height, 3)],
                "anchor_y": item.get("anchor_y"),
                "flow_y": item.get("flow_y"),
                "estimated_height": item.get("est_height"),
            }
            if item.get("kind") == "formula":
                for member in item.get("row_members") or []:
                    fid = member.get("formula_id")
                    if fid:
                        result[fid].append({
                            **base,
                            "formula_row_bbox": member.get("bbox"),
                            "outer_y_shift": round(
                                float(item.get("flow_y") or 0.0)
                                - float(item.get("anchor_y") or 0.0), 3),
                        })
            elif item.get("kind") == "paragraph":
                text = item.get("render_text") or ""
                for fid in re.findall(r"\{\{FORMULA_([^{}]+)\}\}", text):
                    result[fid].append({
                        **base,
                        "paragraph_id": item.get("paragraph_id"),
                        "flow_fragment_id": item.get("flow_fragment_id"),
                        "outer_y_shift": round(
                            float(item.get("flow_y") or 0.0)
                            - float(item.get("anchor_y") or 0.0), 3),
                    })
    return result


def _obstacle_interaction(target_bbox, page_model):
    details = []
    if target_bbox:
        for region in page_model.get("regions") or []:
            if region.get("type") not in {"table", "figure", "image"}:
                continue
            bbox = region.get("bbox") or (region.get("payload") or {}).get(
                "bbox")
            if not bbox:
                continue
            area = _intersection_area(_bbox(target_bbox), _bbox(bbox))
            if area > 0:
                details.append({
                    "region_id": region.get("region_id"),
                    "region_type": region.get("type"),
                    "bbox": bbox,
                    "overlap_area_pt2": round(area, 4),
                })
    return {
        "overlap_count": len(details),
        "overlap_area_pt2": round(sum(
            row["overlap_area_pt2"] for row in details), 4),
        "details": details,
    }


def _svg_path(page_dir, fm, segment, index):
    fid = fm.get("formula_id")
    if fm.get("placement") == "inline":
        return Path(page_dir) / ("inline_FORMULA_%s_%d.svg" % (fid, index))
    return Path(page_dir) / ("formula_%s_%s.svg" % (
        fid, segment.get("segment_id")))


def _segment_box(segment):
    return _bbox(segment.get("render_viewbox")
                 or segment.get("layout_bbox") or [0, 0, 0, 0])


def _component_orphans(fm):
    segments = [_segment_box(s) for s in fm.get("render_segments") or []]
    equation, condition, uncovered = [], [], []
    for component in fm.get("components") or []:
        text = re.sub(r"\s+", " ", (component.get("text") or "").strip())
        bbox = component.get("bbox") or []
        covered = (len(bbox) == 4 and any(
            _intersection_area(_bbox(bbox), segment) > 0
            for segment in segments))
        detail = {"text": text, "bbox": bbox, "covered": covered}
        if re.fullmatch(r"\(\d{1,4}\)", text):
            equation.append(detail)
        if text.lower().strip(" ,.;:") in CONDITION_WORDS:
            condition.append(detail)
        if not covered:
            uncovered.append(detail)
    return equation, condition, uncovered


def _failure_taxonomy(trace):
    defects = []
    detection = trace.get("detection") or {}
    ownership = trace.get("ownership") or {}
    model = trace.get("model") or {}
    svg = trace.get("svg") or {}
    placement = trace.get("placement") or {}
    final = trace.get("final_pdf") or {}
    if not detection.get("detected"):
        defects.append("detection_missing")
    if ownership.get("unowned_fragment_count", 0):
        defects.append("ownership_missing")
    if (ownership.get("double_owned_fragment_count", 0)
            or ownership.get("polluted_fragment_count", 0)):
        defects.append("ownership_pollution")
    if not model.get("exists"):
        defects.append("model_missing")
    if svg.get("missing_count", 0):
        defects.append("svg_missing")
    if svg.get("zero_ink_count", 0):
        defects.append("svg_blank")
    if svg.get("viewbox_crop_count", 0):
        defects.append("svg_viewbox_crop")
    if placement.get("embed_missing_count", 0):
        defects.append("embed_missing")
    if final.get("blank_placement_count", 0):
        defects.append("final_blank")
    if final.get("crop_placement_count", 0):
        defects.append("final_crop")
    if placement.get("embed_missing_count", 0):
        defects.append("orphan_formula")
    if placement.get("duplicate_embed_count", 0):
        defects.append("duplicate_formula")
    if ownership.get("equation_number_orphan_count", 0):
        defects.append("equation_number_orphan")
    if ownership.get("condition_text_orphan_count", 0):
        defects.append("condition_text_orphan")
    if final.get("renderer_disagreement_count", 0):
        defects.append("renderer_disagreement")
    # stable, de-duplicated order
    return list(dict.fromkeys(defects))


def _primary_failure(defects):
    order = [
        ("detection_missing", "detection", "unmodeled source formula ink"),
        ("ownership_missing", "ownership", "formula fragment has no formula owner"),
        ("ownership_pollution", "ownership", "formula fragment ownership is ambiguous"),
        ("model_missing", "model", "detected formula has no FormulaModel"),
        ("svg_missing", "svg_generation", "atomic SVG asset is missing"),
        ("svg_blank", "svg_generation", "atomic SVG raster has zero ink"),
        ("svg_viewbox_crop", "svg_generation", "owned ink exceeds SVG viewBox"),
        ("embed_missing", "embedding", "formula has no final DOM placement"),
        ("final_blank", "final_pdf", "SVG ink is absent from final PDF"),
        ("final_crop", "final_pdf", "final PDF formula ink is cropped"),
        ("orphan_formula", "embedding", "formula is orphaned from final DOM"),
        ("duplicate_formula", "embedding", "formula is embedded more than once"),
        ("equation_number_orphan", "ownership", "equation number is not attached"),
        ("condition_text_orphan", "ownership", "condition text is not attached"),
        ("renderer_disagreement", "final_pdf", "PDF renderers disagree on visible ink"),
    ]
    for defect, stage, reason in order:
        if defect in defects:
            return stage, reason
    return None, None


def _status(defects):
    if not defects:
        return "complete"
    if any(d.startswith("ownership_") for d in defects):
        return "ownership_error"
    if "duplicate_formula" in defects:
        return "duplicate"
    if "final_crop" in defects or "svg_viewbox_crop" in defects:
        return "cropped"
    if "final_blank" in defects or "svg_blank" in defects:
        return "blank"
    if "orphan_formula" in defects:
        return "orphan"
    return "unrendered"


def _find_crop(crop_qa, formula_id, segment_id):
    for detail in (crop_qa or {}).get("formula_crop_details") or []:
        if (detail.get("formula_id") == formula_id
                and detail.get("segment_id") == segment_id):
            return detail
    return None


def _formula_claims(regions, spans):
    claims = defaultdict(set)
    component_matches = defaultdict(list)
    for region in regions:
        fm = region.get("payload") or {}
        fid = fm.get("formula_id")
        for index, component in enumerate(fm.get("components") or []):
            span = _match_span(component, spans)
            if span:
                claims[span["id"]].add(fid)
                component_matches[fid].append((index, component, span))
            else:
                component_matches[fid].append((index, component, None))
    return claims, component_matches


def _orphan_math_fragments(spans, regions, page_model):
    formula_boxes = [
        _bbox((r.get("payload") or {}).get("layout_bbox") or r.get("bbox"))
        for r in regions
        if ((r.get("payload") or {}).get("layout_bbox") or r.get("bbox"))
    ]
    ownership = page_model.get("ownership") or {}
    formula_owned = set(ownership.get("formula_span_ids") or [])
    table_owned = set(ownership.get("table_span_ids") or [])
    figure_owned = set(ownership.get("figure_span_ids") or [])
    text_owned = set(ownership.get("text_span_ids") or [])
    near, ignored, missing = [], [], []
    for span in spans:
        # Paragraph ownership is a valid terminal category.  A paragraph-
        # owned span is not an orphan merely because its font is math-like;
        # if a FormulaModel also claims it, that conflict is detected below
        # as double ownership from the FormulaModel component evidence.
        if span["id"] in formula_owned or span["id"] in table_owned \
                or span["id"] in figure_owned or not _is_math_font(span["font"]):
            continue
        if span["id"] in text_owned:
            ignored.append({
                "span_id": span["id"], "text": (span.get("text") or "").strip(),
                "bbox": span["bbox"], "font": span["font"],
                "size": span["size"],
                "classification": "paragraph_owned_math_like_span",
                "reason": ("paragraph ownership is explicit; this is not a "
                           "confirmed missing formula"),
            })
            continue
        text = (span.get("text") or "").strip()
        is_near = any(_intersect(span["bbox"], box, margin=4.0)
                      for box in formula_boxes)
        row_near = False
        if not is_near:
            for box in formula_boxes:
                overlap_y = min(span["bbox"][3], box[3]) - max(
                    span["bbox"][1], box[1])
                gap_x = min(abs(span["bbox"][0] - box[2]),
                            abs(box[0] - span["bbox"][2]))
                if overlap_y > 1.0 and gap_x < 12.0:
                    row_near = True
                    break
        detail = {"span_id": span["id"], "text": text,
                  "bbox": span["bbox"], "font": span["font"],
                  "size": span["size"]}
        if is_near or row_near:
            detail["classification"] = "ownership_missing"
            near.append(detail)
        elif text in {"↑", "↓", "↑", "↓"}:
            detail["classification"] = "typographic_direction_marker"
            ignored.append(detail)
        elif re.fullmatch(r"\d{1,4}", text) and span["size"] <= 8.5:
            detail["classification"] = "superscript_or_citation_marker"
            ignored.append(detail)
        elif re.fullmatch(r"[\s,.;:(){}\[\]]+", text):
            detail["classification"] = "punctuation_fragment"
            ignored.append(detail)
        else:
            detail["classification"] = "detection_missing_candidate"
            missing.append(detail)
    return near, ignored, missing


def build_page_formula_trace(source_pdf, source_page_index, page_model,
                             final_pdf, final_html, page_dir, *, flows=None,
                             page_grid=None, crop_qa=None,
                             exclusivity_qa=None, out_path=None) -> dict:
    """Build complete traces for every FormulaModel on one source page."""
    page_dir = Path(page_dir)
    document_fingerprint = hashlib.sha256(
        Path(source_pdf).read_bytes()).hexdigest()
    spans, drawings, page_fingerprint, page_size = _source_objects(
        source_pdf, source_page_index)
    span_by_id = {span["id"]: span for span in spans}
    regions = _formula_regions(page_model)
    claims, component_matches = _formula_claims(regions, spans)
    dom_rows = _capture_formula_dom(final_html)
    dom_by_formula = defaultdict(list)
    for row in dom_rows:
        dom_by_formula[row["formula_id"]].append(row)
    flow_by_formula = _flow_metadata(flows)
    orphan_math, ignored_math, detection_missing = _orphan_math_fragments(
        spans, regions, page_model)
    ownership = page_model.get("ownership") or {}
    official_formula = set(ownership.get("formula_span_ids") or [])
    text_owned = set(ownership.get("text_span_ids") or [])
    exclusivity_by_formula = {
        row.get("formula_id"): row
        for row in (exclusivity_qa or {}).get("formula_records", [])
        if row.get("formula_id")
    }

    traces = []
    final_placements = []
    for region in regions:
        fm = region.get("payload") or {}
        fid = fm.get("formula_id")
        trace_id = _trace_id(document_fingerprint, page_fingerprint, fm)
        source_bbox = _bbox(fm.get("layout_bbox") or region.get("bbox"))
        source_ink = _source_ink(source_pdf, source_page_index, source_bbox)
        matched = component_matches.get(fid) or []
        owned_fragments, unowned_fragments, double_fragments = [], [], []
        candidate_ids = []
        for index, component, span in matched:
            row = {"component_index": index,
                   "text": component.get("text") or "",
                   "bbox": component.get("bbox"),
                   "adopted": bool(component.get("adopted")),
                   "source_span_id": span.get("id") if span else None}
            if span:
                candidate_ids.append(span["id"])
                row["owner_category"] = (
                    "formula-owned" if span["id"] in official_formula
                    else "paragraph-owned" if span["id"] in text_owned
                    else "explicitly-ignored")
                owned_fragments.append(row)
                formula_claims = sorted(claims[span["id"]])
                owner_claims = list(formula_claims)
                if span["id"] in text_owned:
                    owner_claims.append("paragraph")
                if span["id"] in official_formula:
                    owner_claims.append("formula-ownership-ledger")
                if len(formula_claims) > 1 or span["id"] in text_owned:
                    double_fragments.append({
                        **row,
                        "formula_claims": formula_claims,
                        "owner_claims": owner_claims,
                        "conflict_reason": (
                            "FormulaModel component is also paragraph-owned"
                            if span["id"] in text_owned
                            else "source fragment is claimed by multiple FormulaModels"),
                    })
            else:
                row["owner_category"] = "unowned"
                unowned_fragments.append(row)

        equation, condition, uncovered = _component_orphans(fm)
        excluded_ids = list(fm.get("formula_adjacent_prose_span_ids") or [])
        excluded_details = [
            {"source_span_id": span_id,
             "text": (span_by_id.get(span_id) or {}).get("text"),
             "bbox": (span_by_id.get(span_id) or {}).get("bbox"),
             "font": (span_by_id.get(span_id) or {}).get("font"),
             "reason": "formula-adjacent prose remains paragraph-owned"}
            for span_id in excluded_ids
        ]
        exclusivity = exclusivity_by_formula.get(fid) or {}
        double_render_text_svg_count = int(
            exclusivity.get("paragraph_duplicate_component_count") or 0
        ) + int(exclusivity.get("formula_text_layer_residue_count") or 0)
        polluted_fragment_count = (
            double_render_text_svg_count
            + int(exclusivity.get("foreign_translated_text_inside_formula_count")
                  or 0)
            + int(exclusivity.get("adopted_plain_prose_count") or 0)
        )
        seg_records = []
        segments = fm.get("render_segments") or []
        for index, segment in enumerate(segments):
            svg_path = _svg_path(page_dir, fm, segment, index)
            svg = analyze_formula_svg(svg_path,
                                      expected_bbox=_segment_box(segment))
            crop = _find_crop(crop_qa, fid, segment.get("segment_id"))
            svg.update({
                "formula_trace_id": trace_id,
                "formula_id": fid,
                "segment_id": segment.get("segment_id"),
                "layout_bbox": segment.get("layout_bbox"),
                "render_viewbox": segment.get("render_viewbox"),
                "viewbox_crop": crop is not None,
                "viewbox_crop_evidence": crop,
            })
            seg_records.append(svg)

        nodes = dom_by_formula.get(fid) or []
        nodes = sorted(nodes, key=lambda row: (
            row["target_bbox"][1], row["target_bbox"][0],
            row.get("segment") or ""))
        expected = max(len(segments), 1)
        placement_records = []
        for index, node in enumerate(nodes):
            if node.get("segment"):
                segment_id = node["segment"]
                seg_index = next((i for i, s in enumerate(segments)
                                  if s.get("segment_id") == segment_id), 0)
            else:
                seg_index = index % expected
                segment_id = (segments[seg_index].get("segment_id")
                              if segments else None)
            svg = seg_records[seg_index] if seg_index < len(seg_records) else {}
            element_bbox = node["target_bbox"]
            render_bbox = _css_rect_pt(node.get("imageRect")) or element_bbox
            clip_bbox, ancestor_clip_evidence = _clip_envelope(
                render_bbox, node.get("ancestorClips"))
            flow_rows = flow_by_formula.get(fid) or []
            flow_row = (flow_rows[index % len(flow_rows)]
                        if flow_rows else None)
            placement = {
                "formula_trace_id": trace_id,
                "formula_id": fid,
                "segment_id": segment_id,
                "occurrence_index": index // expected,
                "target_bbox": render_bbox,
                "element_bbox": element_bbox,
                "render_bbox": render_bbox,
                "clip_bbox": clip_bbox,
                "css_box": node.get("style"),
                "svg_viewbox": svg.get("viewbox"),
                "transform_matrix": (node.get("imageStyle") or {}).get(
                    "transform"),
                "scale": [1.0, 1.0],
                "translation": [render_bbox[0], render_bbox[1]],
                "z_order": (node.get("style") or {}).get("zIndex"),
                "overflow_clip_policy": {
                    "element": (node.get("style") or {}).get("overflow"),
                    "ancestor_clips": ancestor_clip_evidence,
                },
                "column": _column_classification(render_bbox,
                                                  page_grid)["column"],
                "flow_interval": ((flow_row or {}).get("flow_interval")),
                "flow_evidence": flow_row,
                "obstacle_interaction": _obstacle_interaction(
                    render_bbox, page_model),
                "image_loaded": bool(node.get("imageComplete")),
                "natural_width": node.get("naturalWidth"),
                "natural_height": node.get("naturalHeight"),
                "source_asset": node.get("src"),
                "svg_ink_pixels": svg.get("rasterized_ink_pixel_count", 0),
                "svg_raster_scale": svg.get("raster_scale", 1.0),
                "svg_ink_touches_edge": svg.get("ink_touches_edge", False),
            }
            placement_records.append(placement)
            final_placements.append(placement)

        vector_hits = [d for d in drawings
                       if _intersect(source_bbox, d["bbox"])]
        class_info = _column_classification(source_bbox, page_grid)
        trace = {
            "formula_trace_id": trace_id,
            "trace_id_basis": "document_sha + source_page_content_sha + formula_evidence",
            "page_number_independent_id": True,
            "page": int(page_model.get("page") or source_page_index + 1),
            "source_formula_id": fid,
            "source": {
                "bbox": source_bbox,
                "ink_bbox": source_ink["source_ink_bbox"],
                "ink_pixel_count": source_ink["source_ink_pixel_count"],
                "ink_area": source_ink["source_ink_area"],
                "source_text_fragments": [
                    {"text": c.get("text") or "", "font": c.get("font"),
                     "size": c.get("size"), "bbox": c.get("bbox")}
                    for c in fm.get("components") or []],
                "vector_drawing_evidence_count": len(vector_hits),
                "vector_drawing_evidence": vector_hits[:16],
                "display_or_inline": fm.get("placement"),
                "column": class_info["column"],
                "full_width": class_info["full_width"],
            },
            "detection": {
                "detected": True,
                "detector_source": "formula_compose",
                "detected_bbox": source_bbox,
                "confidence": fm.get("detection_confidence"),
                "candidate_ids": sorted(set(candidate_ids)),
                "merge_applied": len(fm.get("components") or []) > 1,
                "split_applied": len(segments) > 1,
                "suppressed": False,
            },
            "ownership": {
                "owner": fid,
                "owned_source_fragments": owned_fragments,
                "excluded_fragments": excluded_details,
                "adjacent_prose": fm.get("formula_adjacent_prose"),
                "equation_number_fragments": equation,
                "condition_text_fragments": condition,
                "inline_explanatory_text": excluded_details,
                "unowned_fragment_count": len(unowned_fragments),
                "unowned_fragments": unowned_fragments,
                "double_owned_fragment_count": len(double_fragments),
                "double_owned_fragments": double_fragments,
                "polluted_fragment_count": polluted_fragment_count,
                "equation_number_orphan_count": sum(
                    1 for row in equation if not row["covered"]),
                "condition_text_orphan_count": sum(
                    1 for row in condition if not row["covered"]),
                "uncovered_component_count": len(uncovered),
                "reattachment_evidence_complete": False,
            },
            "model": {
                "exists": True,
                "component_count": len(fm.get("components") or []),
                "component_bboxes": [c.get("bbox")
                                     for c in fm.get("components") or []],
                "logical_bbox": source_bbox,
                "render_viewboxes": [_segment_box(s) for s in segments],
                "atomic_group_id": trace_id,
                "display_or_inline": fm.get("placement"),
                "equation_number_attachment_count": len(equation),
                "text_condition_attachment_count": len(condition),
                "render_policy": fm.get("render_policy"),
            },
            "svg": {
                "segment_count": len(seg_records),
                "segments": seg_records,
                "missing_count": sum(not r.get("svg_exists")
                                     for r in seg_records),
                "zero_size_count": sum(bool(r.get("svg_exists"))
                                       and bool(r.get("zero_size"))
                                       for r in seg_records),
                "zero_ink_count": sum(bool(r.get("svg_exists"))
                                      and bool(r.get("zero_ink"))
                                      for r in seg_records),
                "viewbox_crop_count": sum(bool(r.get("viewbox_crop"))
                                          for r in seg_records),
                "all_segments_have_ink": bool(seg_records) and all(
                    r.get("rasterized_ink_pixel_count", 0) > 0
                    for r in seg_records),
            },
            "placement": {
                "expected_segment_count": len(segments),
                "dom_placement_count": len(nodes),
                "placements": placement_records,
                "embed_missing_count": max(0, len(segments) - len(nodes)),
                "duplicate_embed_count": max(0, len(nodes) - len(segments)),
                "parent_clip_count": sum(bool(node.get("ancestorClips"))
                                         for node in nodes),
            },
            "final_pdf": {},
            "formula_double_render_text_svg_count": (
                double_render_text_svg_count),
            "exclusivity_evidence": {
                "formula_exclusivity_pass": exclusivity.get(
                    "formula_exclusivity_pass"),
                "paragraph_duplicate_component_count": int(
                    exclusivity.get("paragraph_duplicate_component_count")
                    or 0),
                "formula_text_layer_residue_count": int(
                    exclusivity.get("formula_text_layer_residue_count") or 0),
            },
        }
        traces.append(trace)

    final = formula_final_ink_qa(
        final_pdf, final_placements, page_index=0)
    final_by_trace = defaultdict(list)
    for row in final["records"]:
        final_by_trace[row.get("formula_trace_id")].append(row)
    for trace in traces:
        rows = final_by_trace.get(trace["formula_trace_id"]) or []
        expected = trace["placement"]["expected_segment_count"]
        trace["final_pdf"] = {
            "placements": rows,
            "source_ink_pixels": trace["source"]["ink_pixel_count"],
            "svg_ink_pixels": sum(
                r.get("rasterized_ink_pixel_count", 0)
                for r in trace["svg"]["segments"]),
            "final_pymupdf_ink_pixels": sum(
                r.get("final_pymupdf_ink_pixels", 0) for r in rows),
            "final_pdfium_ink_pixels": sum(
                r.get("final_pdfium_ink_pixels", 0) for r in rows),
            "blank_placement_count": sum(bool(r.get("final_blank"))
                                         for r in rows),
            "crop_placement_count": sum(bool(r.get("final_crop"))
                                        for r in rows),
            "renderer_disagreement_count": sum(
                bool(r.get("renderer_disagreement")) for r in rows),
            "renderer_disagreement": any(
                bool(r.get("renderer_disagreement")) for r in rows),
            "expected_placement_count": expected,
            "verified_placement_count": len(rows),
        }
        defects = _failure_taxonomy(trace)
        trace["defects"] = defects
        trace["status"] = _status(defects)
        trace["failure_stage"], trace["failure_reason"] = _primary_failure(
            defects)

    result = {
        "schema_version": "phase4e1b.c2.formula_render_trace.v1",
        "page": int(page_model.get("page") or source_page_index + 1),
        "source_page_index": int(source_page_index),
        "formula_model_count": len(regions),
        "formula_trace_count": len(traces),
        "trace_coverage_ratio": round(len(traces) / max(len(regions), 1), 4),
        "formula_trace_gap_count": max(0, len(regions) - len(traces)),
        "formula_fragment_unowned_count": len(orphan_math) + sum(
            t["ownership"]["unowned_fragment_count"] for t in traces),
        "formula_fragment_double_owned_count": len({
            row["source_span_id"]
            for t in traces
            for row in t["ownership"]["double_owned_fragments"]
            if row.get("source_span_id")
        }),
        "formula_double_render_text_svg_count": sum(
            t.get("formula_double_render_text_svg_count", 0) for t in traces),
        "placement_flow_interval_gap_count": sum(
            placement.get("flow_interval") is None
            for trace in traces
            for placement in trace.get("placement", {}).get("placements", [])),
        "placement_obstacle_trace_gap_count": sum(
            placement.get("obstacle_interaction") is None
            for trace in traces
            for placement in trace.get("placement", {}).get("placements", [])),
        "detection_missing_count": len(detection_missing),
        "detection_missing_candidates": detection_missing,
        "formula_semantic_unowned_fragments": orphan_math,
        "ignored_math_glyphs": ignored_math,
        "traces": traces,
        "final_ink_qa": final,
    }
    if out_path:
        _dump(out_path, result)
    return result


def build_document_formula_trace(page_results, out_path=None) -> dict:
    traces = [trace for page in page_results for trace in page.get("traces", [])]
    model_count = sum(page.get("formula_model_count", 0)
                      for page in page_results)
    trace_ids = [trace.get("formula_trace_id") for trace in traces]
    unique_trace_ids = {trace_id for trace_id in trace_ids if trace_id}
    result = {
        "schema_version": "phase4e1b.c2.formula_document_trace.v1",
        "page_count": len(page_results),
        "formula_model_count": model_count,
        "formula_trace_count": len(traces),
        "formula_trace_coverage": round(len(traces) / max(model_count, 1), 4),
        "formula_trace_gap_count": max(0, model_count - len(traces)),
        "formula_trace_unique_id_count": len(unique_trace_ids),
        "formula_trace_id_collision_count": max(
            0, len(trace_ids) - len(unique_trace_ids)),
        "detection_missing_count": sum(
            page.get("detection_missing_count", 0) for page in page_results),
        "formula_fragment_unowned_count": sum(
            page.get("formula_fragment_unowned_count", 0)
            for page in page_results),
        "formula_fragment_double_owned_count": sum(
            page.get("formula_fragment_double_owned_count", 0)
            for page in page_results),
        "formula_double_render_text_svg_count": sum(
            page.get("formula_double_render_text_svg_count", 0)
            for page in page_results),
        "placement_flow_interval_gap_count": sum(
            page.get("placement_flow_interval_gap_count", 0)
            for page in page_results),
        "placement_obstacle_trace_gap_count": sum(
            page.get("placement_obstacle_trace_gap_count", 0)
            for page in page_results),
        "traces": traces,
        "per_page": [{k: v for k, v in page.items() if k != "traces"}
                     for page in page_results],
    }
    if out_path:
        _dump(out_path, result)
    return result


def build_failure_taxonomy(traces, out_path=None) -> dict:
    taxonomy = [
        "detection_missing", "ownership_missing", "ownership_pollution",
        "model_missing", "svg_missing", "svg_blank", "svg_viewbox_crop",
        "embed_missing", "final_blank", "final_crop", "orphan_formula",
        "duplicate_formula", "equation_number_orphan",
        "condition_text_orphan", "renderer_disagreement",
    ]
    counts = {name: 0 for name in taxonomy}
    details = {name: [] for name in taxonomy}
    for trace in traces or []:
        for defect in trace.get("defects") or []:
            if defect not in counts:
                counts[defect] = 0
                details[defect] = []
            counts[defect] += 1
            details[defect].append({
                "formula_trace_id": trace.get("formula_trace_id"),
                "page": trace.get("page"),
                "formula_id": trace.get("source_formula_id"),
                "failure_stage": trace.get("failure_stage"),
                "failure_reason": trace.get("failure_reason"),
            })
    result = {
        "schema_version": "phase4e1b.c2.formula_failure_taxonomy.v1",
        "taxonomy": taxonomy,
        "counts": counts,
        "details": details,
        "generic_failure_label_count": 0,
    }
    if out_path:
        _dump(out_path, result)
    return result
