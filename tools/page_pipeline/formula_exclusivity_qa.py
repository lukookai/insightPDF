# -*- coding: utf-8 -*-
"""FormulaExclusivityQA - a FormulaGroup's source content must be rendered
EXACTLY ONCE.

Phase 4C.2 problem (p14 / p16): cases / piecewise formulas like

    h(c_i, r_j) = { 1, if r_j correctly supports c_i
                    0, otherwise }

contain NON-math Roman text ("if", "otherwise", "correctly supports",
"where", "for") inside the formula enclosure.  The current composer only
owns math-font glyphs (CMMI/CMSY/CMEX/CMR digits), so those Roman condition
spans fall through to a paragraph, get translated to Chinese ("如果 / 否则 /
正确支持") and render ON TOP of the atomic formula SVG -> duplicate render.

This QA verifies, on the FINAL PDF (not the HTML/DOM/PageModel):

* formula_source_component_closure : every source component belongs to the
  FormulaGroup (coverage) and appears in NO paragraph.
* formula_text_layer_residue_count : an atomic-SVG formula whose enclosure
  still contains PDF text-layer glyphs of its own source components.
* foreign_translated_text_inside_formula_count : Chinese (or any
  non-source) text inside a formula enclosure that duplicates SVG content.
* formula_render_duplicate_count : same source component rendered by both
  the SVG and the text layer.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for path in (HERE, REPO / "tools" / "table_html_render",
             REPO / "tools" / "formula_html_render",
             REPO / "实现源码" / "pdf_translator"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

MATH_FONT_HINTS = ("cmmi", "cmsy", "cmex", "msam", "msbm", "eufm", "mtsy",
                   "stix", "xits", "math", "latinmodern")
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
# Roman condition words typical of cases / piecewise / aligned equations.
CONDITION_WORDS = (
    "if", "otherwise", "where", "for", "with", "given", "such that",
    "subject to", "correctly supports", "when", "while", "and", "or",
    "else", "then", "cases", "otherwise", "whenever", "unless",
)

# Ordinary-language fragments that are legitimate formula semantics when
# page_model has explicitly adopted them into an atomic formula.  Keep this
# list deliberately small: adoption of any other natural-language fragment
# is a hard model-level ownership defect, even if its geometry is valid.
ADOPTED_FORMULA_CONDITIONS = {
    "if", "otherwise", "where", "correctly supports",
}

_MATH_FONT_CACHE = {}


def _is_math_font(font):
    low = (font or "").lower()
    if low in _MATH_FONT_CACHE:
        return _MATH_FONT_CACHE[low]
    ok = any(h in low for h in MATH_FONT_HINTS)
    _MATH_FONT_CACHE[low] = ok
    return ok


def _bbox_overlap(a, b):
    x = min(a[2], b[2]) - max(a[0], b[0])
    y = min(a[3], b[3]) - max(a[1], b[1])
    return max(x, 0.0) * max(y, 0.0)


def _bbox_contains(big, small, margin=1.0):
    return (big[0] - margin <= small[0] and big[2] + margin >= small[2]
            and big[1] - margin <= small[1] and big[3] + margin >= small[3])


def _component_bbox(component):
    bbox = component.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        return [float(v) for v in bbox]
    except (TypeError, ValueError):
        return None


def _segment_viewboxes(formula_model):
    """Return the SVG viewboxes that are actually rendered for a formula."""
    boxes = []
    for segment in formula_model.get("render_segments") or []:
        bbox = segment.get("render_viewbox") or segment.get("layout_bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            boxes.append({
                "segment_id": segment.get("segment_id"),
                "bbox": [float(v) for v in bbox],
            })
    # Legacy models without explicit segments render the formula layout box.
    if not boxes:
        bbox = formula_model.get("layout_bbox") or formula_model.get("ink_bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            boxes.append({"segment_id": None, "bbox": [float(v) for v in bbox]})
    return boxes


def _union_contains(boxes, component_bbox, tolerance=0.05):
    """Whether the union of rendered viewboxes covers the component bbox.

    RenderSegments may meet at a boundary, so requiring one segment to
    contain the whole component is too strict.  Coverage is checked by
    subtracting the component rectangle's intersections with all segment
    rectangles using an exact small rectangle partition.
    """
    if not boxes:
        return False, 0.0
    cb = component_bbox
    area = max(0.0, cb[2] - cb[0]) * max(0.0, cb[3] - cb[1])
    if area <= tolerance * tolerance:
        cx, cy = (cb[0] + cb[2]) / 2.0, (cb[1] + cb[3]) / 2.0
        covered = any(b[0] - tolerance <= cx <= b[2] + tolerance
                      and b[1] - tolerance <= cy <= b[3] + tolerance
                      for b in boxes)
        return covered, 1.0 if covered else 0.0

    xs = {cb[0], cb[2]}
    ys = {cb[1], cb[3]}
    for box in boxes:
        x0, y0 = max(cb[0], box[0]), max(cb[1], box[1])
        x1, y1 = min(cb[2], box[2]), min(cb[3], box[3])
        if x1 > x0 and y1 > y0:
            xs.update((x0, x1))
            ys.update((y0, y1))
    xs, ys = sorted(xs), sorted(ys)
    covered_area = 0.0
    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            if any(b[0] - tolerance <= cx <= b[2] + tolerance
                   and b[1] - tolerance <= cy <= b[3] + tolerance
                   for b in boxes):
                covered_area += (x1 - x0) * (y1 - y0)
    ratio = min(max(covered_area / area, 0.0), 1.0)
    return ratio >= 0.999, ratio


def _normalise_adopted_text(text):
    return re.sub(r"\s+", " ", (text or "").strip()).lower().strip(" ,.;:")


def _adopted_component_is_formula_semantic(component):
    """Accept only equation numbers and the frozen condition-word policy."""
    text = (component.get("text") or "").strip()
    normal = _normalise_adopted_text(text)
    if re.fullmatch(r"\(\s*\d{1,3}\s*\)", text):
        return True, "equation_number"
    if normal in ADOPTED_FORMULA_CONDITIONS:
        return True, "condition_word"
    return False, "ordinary_natural_language"


def formula_enclosure_bbox(formula_model):
    """The visual region the whole formula actually occupies.

    Uses render_segments' render_viewbox (per-segment SVG crop) union; falls
    back to layout_bbox.  Includes math glyphs, Roman condition text,
    punctuation, equation numbers, super/subscripts, fraction bars - but not
    the surrounding body sentences.
    """
    segs = formula_model.get("render_segments") or []
    if segs:
        boxes = []
        for seg in segs:
            b = seg.get("render_viewbox") or seg.get("layout_bbox")
            if b:
                boxes.append([float(v) for v in b])
        if boxes:
            return [min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes)]
    b = formula_model.get("layout_bbox")
    if b:
        return [float(v) for v in b]
    b = formula_model.get("ink_bbox")
    if b:
        return [float(v) for v in b]
    return None


def _pdf_text_spans(pdf_path, page_index=0):
    """Extract (text, bbox, font) spans from the final PDF text layer."""
    doc = pymupdf.open(str(pdf_path))
    page = doc[page_index]
    spans = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "").strip()
                if not t:
                    continue
                spans.append({"text": t,
                              "bbox": [float(v) for v in span["bbox"]],
                              "font": span.get("font") or ""})
    doc.close()
    return spans


def _page_model_spans(page_model):
    """All source spans (with owner context) of the page model."""
    spans = []
    for region in page_model.get("regions", []):
        if region.get("type") == "text":
            para = region["payload"]
            for line in para.get("lines", []):
                for s in line.get("spans", []):
                    spans.append({**s, "owner": "paragraph",
                                  "paragraph_id": para.get("paragraph_id")})
        elif region.get("type") == "formula":
            fm = region["payload"]
            for comp in fm.get("components", []):
                spans.append({**comp, "owner": "formula",
                              "formula_id": fm.get("formula_id"),
                              "comp_is_math": _is_math_font(comp.get("font"))})
    return spans


def formula_exclusivity_qa(page_model, pdf_path, out_dir=None,
                           flow_enclosures=None):
    """Audit every FormulaGroup on the page (final-PDF aware).

    ``flow_enclosures``: optional {formula_id: [x0,y0,x1,y1]} with the
    flow_locked (Phase 4C.2R) final positions -- the final PDF uses these
    coordinates, not the source layout_bbox.  Passed by run_document.
    """
    out_dir = Path(out_dir) if out_dir else Path(pdf_path).parent
    regions = page_model.get("regions", []) or []
    formula_regions = [r for r in regions if r.get("type") == "formula"]
    text_regions = [r for r in regions if r.get("type") == "text"]

    # source spans per formula (from components) and per paragraph
    pdf_spans = _pdf_text_spans(pdf_path) if pdf_path else []
    flow_enclosures = flow_enclosures or {}

    records = []
    for region in formula_regions:
        fm = region["payload"]
        fid = fm.get("formula_id")
        comps = fm.get("components") or []
        enclosure = flow_enclosures.get(fid) or formula_enclosure_bbox(fm)
        source_text = (fm.get("source_text") or "")
        math_comps = [c for c in comps if _is_math_font(c.get("font"))]
        roman_comps = [c for c in comps if not _is_math_font(c.get("font"))
                       and (c.get("text") or "").strip()
                       and not re.fullmatch(r"[\d\s,.;:()\[\]{}=+\-*/^_|<>≈±×÷′″'\"]+",
                                            (c.get("text") or "").strip())]
        segment_boxes = _segment_viewboxes(fm)
        rendered_components = []
        owned_semantic_components = []
        uncovered_components = []
        invalid_adopted_components = []
        allowlisted_adopted_components = []
        adopted_components = [c for c in comps if c.get("adopted")]
        for index, component in enumerate(comps):
            bbox = _component_bbox(component)
            if bbox is None:
                uncovered_components.append({
                    "component_index": index,
                    "text": component.get("text") or "",
                    "bbox": component.get("bbox"),
                    "reason": "missing_or_invalid_component_bbox",
                    "coverage_ratio": 0.0,
                })
                continue
            covered, ratio = _union_contains(
                [s["bbox"] for s in segment_boxes], bbox)
            detail = {
                "component_index": index,
                "text": component.get("text") or "",
                "bbox": [round(v, 3) for v in bbox],
                "coverage_ratio": round(ratio, 4),
            }
            allowed_adopted, adopted_reason = (False, None)
            if component.get("adopted"):
                allowed_adopted, adopted_reason = (
                    _adopted_component_is_formula_semantic(component))
            if covered:
                detail["closure_basis"] = "render_segment_viewbox"
                rendered_components.append(detail)
            elif allowed_adopted:
                # Formula component coverage is an ownership/semantic
                # closure metric.  Inline formula-adjacent condition words
                # (notably a leading ``where``) may sit immediately outside
                # the cropped math RenderSegment and are intentionally
                # composed by the surrounding inline formula context.  They
                # remain exclusively FormulaGroup-owned and are valid only
                # through the narrow adopted-condition allowlist.  Preserve
                # the geometric evidence instead of pretending it was in a
                # segment viewbox.
                detail["closure_basis"] = "allowlisted_adopted_semantic"
                detail["semantic_reason"] = adopted_reason
                owned_semantic_components.append(detail)
            else:
                detail["reason"] = ("no_render_segment" if not segment_boxes
                                    else "outside_render_segment_viewboxes")
                uncovered_components.append(detail)
        for index, component in enumerate(comps):
            if not component.get("adopted"):
                continue
            allowed, reason = _adopted_component_is_formula_semantic(component)
            detail = {
                "component_index": index,
                "text": component.get("text") or "",
                "bbox": component.get("bbox"),
                "reason": reason,
            }
            if allowed:
                allowlisted_adopted_components.append(detail)
            else:
                invalid_adopted_components.append({
                    **detail,
                })
        rec = {
            "formula_id": fid,
            "kind": fm.get("kind"),
            "placement": fm.get("placement"),
            "enclosure_bbox": [round(v, 2) for v in enclosure] if enclosure else None,
            "source_component_count": len(comps),
            "math_component_count": len(math_comps),
            "roman_text_component_count": len(roman_comps),
            "roman_text_owned_count": 0,
            # Schema-compatible ownership + a geometric render-closure audit.
            # Every component is owned by this FormulaGroup regardless of
            # font family (digits and punctuation are formula components too).
            "source_component_owned_count": len(comps),
            "source_component_rendered_count": len(rendered_components),
            "source_component_semantic_closure_count": len(
                owned_semantic_components),
            "source_component_semantic_closure_details": (
                owned_semantic_components),
            "source_component_covered_count": (
                len(rendered_components) + len(owned_semantic_components)),
            "source_component_uncovered_count": len(uncovered_components),
            "source_component_uncovered_details": uncovered_components,
            "formula_source_component_coverage_ratio": round(
                (len(rendered_components) + len(owned_semantic_components))
                / len(comps), 4) if comps else 1.0,
            "render_segment_count": len(segment_boxes),
            "render_segment_viewboxes": [
                {"segment_id": s["segment_id"],
                 "bbox": [round(v, 3) for v in s["bbox"]]}
                for s in segment_boxes
            ],
            "adopted_component_count": len(adopted_components),
            "adopted_allowlisted_component_count": len(
                allowlisted_adopted_components),
            "adopted_allowlisted_component_details": (
                allowlisted_adopted_components),
            "adopted_plain_prose_count": len(invalid_adopted_components),
            "adopted_plain_prose_details": invalid_adopted_components,
            "paragraph_duplicate_component_count": 0,
            "paragraph_duplicate_component_details": [],
            "formula_text_layer_residue_count": 0,
            "formula_text_layer_residue_details": [],
            "foreign_translated_text_inside_formula_count": 0,
            "foreign_translated_text_details": [],
            "formula_render_duplicate_count": 0,
            "formula_exclusivity_pass": True,
        }
        # 1) roman components owned by the formula itself
        rec["roman_text_owned_count"] = len(roman_comps)

        # 2) paragraph duplicate components: the same source component text
        #    reappearing in a paragraph that renders INSIDE the formula
        #    enclosure.  ONLY checked for display/block formulas: an inline
        #    formula's own glyphs legitimately sit inside its paragraph's
        #    bbox (p16 B1 "c_ij" inline in DLP00200) and must not count.
        #    Phase 4C.2R.1: the paragraph side uses the FINAL PDF text layer
        #    (rendered positions) -- the page model's source spans sit in
        #    source coordinates and falsely "enter" the flowed enclosure
        #    after flow shifts (p005 B22's mono "self-refinement" span at
        #    source y 736 vs rendered y 764).  Condition words are matched
        #    with word boundaries ("if" must not match inside "simplified").
        dup_detail = []
        if fm.get("placement") != "inline":
            for ps in pdf_spans:
                if not enclosure:
                    continue
                if not _bbox_contains(enclosure, ps["bbox"], margin=2.0):
                    continue
                t = ps["text"]
                if re.fullmatch(r"[\d\s,.;:()\[\]{}=+\-*/^_|<>≈±×÷′″'\"]+", t):
                    continue
                # duplicate source component text (math or roman)
                src_tokens = {c.get("text", "").strip() for c in comps
                              if len(c.get("text", "").strip()) >= 2}
                if t in src_tokens:
                    dup_detail.append({"paragraph": ps.get("paragraph_id"),
                                       "text": t, "bbox": ps["bbox"]})
                # condition words inside enclosure that are also in the SVG
                low = t.lower()
                if any(re.search(r"\b%s\b" % re.escape(w), low)
                       for w in CONDITION_WORDS):
                    dup_detail.append({"paragraph": ps.get("paragraph_id"),
                                       "text": t, "bbox": ps["bbox"],
                                       "condition_word": True})
        if dup_detail:
            rec["paragraph_duplicate_component_count"] = len(dup_detail)
            rec["paragraph_duplicate_component_details"] = dup_detail[:10]

        # 3) final-PDF text-layer residue inside the formula enclosure:
        #    atomic SVG formulas must not ALSO have their own glyphs in the
        #    PDF text layer.  Inline formulas are exempt (their SVG crop is
        #    rendered inside the paragraph flow at the same position).
        if (enclosure and fm.get("render_policy") == "atomic_svg"
                and fm.get("placement") != "inline"):
            residue = []
            for ps in pdf_spans:
                if not _bbox_contains(enclosure, ps["bbox"], margin=2.0):
                    continue
                t = ps["text"]
                if re.fullmatch(r"[\d\s,.;:()\[\]{}=+\-*/^_|<>≈±×÷′″'\"]+", t):
                    continue
                low = t.lower()
                is_condition = any(w in low for w in CONDITION_WORDS)
                is_math_like = bool(re.search(r"[A-Za-z]", t)) and any(
                    c.get("text", "").strip().lower() in low
                    for c in comps if len(c.get("text", "").strip()) >= 2)
                if is_condition or is_math_like:
                    residue.append({"text": t, "bbox": ps["bbox"]})
            rec["formula_text_layer_residue_count"] = len(residue)
            rec["formula_text_layer_residue_details"] = residue[:10]

        # 4) foreign translated text inside formula enclosure: CJK inside
        #    the enclosure that is NOT part of the SVG source (Chinese
        #    translations of condition words duplicating the SVG).
        # Inline formulas legitimately sit inside translated paragraph text;
        # CJK inside their coarse source enclosure is not foreign formula
        # content.  Duplicate SVG/text components are still checked above.
        if enclosure and fm.get("placement") != "inline":
            foreign = []
            for ps in pdf_spans:
                if not _bbox_contains(enclosure, ps["bbox"], margin=2.0):
                    continue
                if CJK_RE.search(ps["text"]):
                    foreign.append({"text": ps["text"],
                                    "bbox": ps["bbox"]})
            rec["foreign_translated_text_inside_formula_count"] = len(foreign)
            rec["foreign_translated_text_details"] = foreign[:10]

        # 5) render duplicate: a source component rendered by BOTH the SVG
        #    and the text layer
        rec["formula_render_duplicate_count"] = max(
            rec["paragraph_duplicate_component_count"],
            rec["formula_text_layer_residue_count"])

        rec["formula_exclusivity_pass"] = (
            rec["source_component_uncovered_count"] == 0
            and rec["adopted_plain_prose_count"] == 0
            and rec["paragraph_duplicate_component_count"] == 0
            and rec["formula_text_layer_residue_count"] == 0
            and rec["foreign_translated_text_inside_formula_count"] == 0
            and rec["formula_render_duplicate_count"] == 0)
        records.append(rec)

    # page-level aggregates
    total_comps = sum(r["source_component_count"] for r in records)
    # A FormulaGroup owns all of its components, including CMR digits,
    # punctuation, operators and delimiters.  Render coverage is established
    # geometrically above against the actual RenderSegment viewboxes.
    rendered = sum(r["source_component_rendered_count"] for r in records)
    semantic_closure = sum(
        r["source_component_semantic_closure_count"] for r in records)
    covered = rendered + semantic_closure
    uncovered = sum(r["source_component_uncovered_count"] for r in records)
    adopted_count = sum(r["adopted_component_count"] for r in records)
    adopted_allowlisted = sum(
        r["adopted_allowlisted_component_count"] for r in records)
    adopted_plain_prose = sum(r["adopted_plain_prose_count"] for r in records)
    dup = sum(r["paragraph_duplicate_component_count"] for r in records)
    residue = sum(r["formula_text_layer_residue_count"] for r in records)
    foreign = sum(r["foreign_translated_text_inside_formula_count"]
                  for r in records)
    blank = 0  # computed by physical/dom QA elsewhere
    result = {
        "formula_count": len(records),
        "formula_records": records,
        "formula_source_component_coverage_ratio": round(
            covered / total_comps, 4) if total_comps else 1.0,
        "formula_source_component_count": total_comps,
        "formula_source_component_covered_count": covered,
        "formula_source_component_rendered_count": rendered,
        "formula_source_component_geometrically_rendered_count": rendered,
        "formula_source_component_semantic_closure_count": semantic_closure,
        "formula_source_component_semantic_closure_details": [
            {"formula_id": r["formula_id"], **detail}
            for r in records
            for detail in r["source_component_semantic_closure_details"]
        ][:48],
        "formula_source_component_uncovered_count": uncovered,
        "formula_source_component_uncovered_details": [
            {"formula_id": r["formula_id"], **detail}
            for r in records for detail in r["source_component_uncovered_details"]
        ][:48],
        "formula_adopted_component_count": adopted_count,
        "formula_adopted_allowlisted_component_count": adopted_allowlisted,
        "formula_adopted_allowlisted_component_details": [
            {"formula_id": r["formula_id"], **detail}
            for r in records
            for detail in r["adopted_allowlisted_component_details"]
        ][:48],
        "formula_adopted_plain_prose_count": adopted_plain_prose,
        "formula_adopted_plain_prose_details": [
            {"formula_id": r["formula_id"], **detail}
            for r in records for detail in r["adopted_plain_prose_details"]
        ][:48],
        "formula_adopted_plain_prose_passed": adopted_plain_prose == 0,
        "formula_duplicate_component_count": dup,
        "paragraph_duplicate_formula_component_count": dup,
        "formula_text_layer_residue_count": residue,
        "foreign_translated_text_inside_formula_count": foreign,
        "formula_render_duplicate_count": dup + residue,
        "formula_blank_count": blank,
        "formula_exclusivity_passed": (
            uncovered == 0 and adopted_plain_prose == 0
            and dup == 0 and residue == 0 and foreign == 0
            and all(r["formula_exclusivity_pass"] for r in records)),
    }
    if out_dir:
        _dump(out_dir / "formula_exclusivity_qa.json", result)
    return result


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))
    result = formula_exclusivity_qa(model, args.pdf, out_dir=args.out)
    print(json.dumps({k: v for k, v in result.items()
                      if not isinstance(v, list)}, ensure_ascii=False, indent=2))
    return 0 if result["formula_exclusivity_passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
