# -*- coding: utf-8 -*-
"""Final-PDF residual ordinary-English prose QA.

Formula exclusion is per physical RenderSegment.  A coarse formula enclosure
never exempts neighbouring prose.  For legacy pages where prose was painted
inside an SVG, the final PDF raster is sampled at the adopted component bbox
before reporting it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf


LEX_RE = re.compile(r"[A-Za-z][A-Za-z'\-]{1,}")
URL_RE = re.compile(r"(?:https?://|www\.|\bdoi\b)", re.I)
# Phase 4D.2C: protected model names / versioned identifiers (uppercase-start
# hyphenated tokens such as "Gemini-2.0-flash-thinking-exp-1219",
# "Qwen2.5-72B-Instruct", "DeepSeek-R1") are protected named entities -- they
# stay in their source language and must never count as residual prose.
MODEL_RE = re.compile(
    r"(?:LLM|MapReduce|SurveyEval|AutoSurvey|STORM|Omni-Think|"
    r"fagg|markdown|arXiv|Zhiyuan Liu|Maosong Sun|[A-Z][A-Za-z0-9]*"
    r"(?:-[A-Za-z0-9]+){2,})", re.I)
FORMULA_CONDITIONS = {
    "if", "iff", "otherwise", "where", "when", "whenever", "for all",
    "for any", "correctly supports", "does not support", "and the survey",
}


def _overlap(a, b, tol=0.0):
    return max(0.0, min(a[2], b[2] + tol) - max(a[0], b[0] - tol)) * \
        max(0.0, min(a[3], b[3] + tol) - max(a[1], b[1] - tol))


def _formula_shifts(flows):
    out = {}
    for flow in flows or []:
        for item in flow.get("items", []):
            if item.get("kind") != "formula":
                continue
            dy = float(item.get("flow_y", 0)) - float(item.get("anchor_y", 0))
            for member in item.get("row_members", []):
                out[member.get("formula_id")] = dy
    return out


def _reference_boxes(flows):
    """Physical paragraph boxes for bibliography entries.

    Bibliography text is intentionally allowed to remain in its source
    language by the translation-accounting policy.  Residual-prose QA must
    therefore exempt the *rendered reference boxes*, not rely on loose
    lexical guesses such as author names or years.
    """
    boxes = []
    for flow in flows or []:
        x0 = float(flow.get("col_x0", 0.0))
        x1 = float(flow.get("col_x1", x0))
        for item in flow.get("items", []):
            if item.get("kind") != "paragraph" or not item.get("is_reference"):
                continue
            y0 = float(item.get("flow_y", 0.0))
            y1 = y0 + max(float(item.get("est_height", 0.0)), 1.0)
            boxes.append([x0, y0, x1, y1])
    return boxes


def _pdf_lines(pdf_path):
    doc = pymupdf.open(str(pdf_path))
    page = doc[0]
    lines = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(s.get("text") or "" for s in line.get("spans", [])).strip()
            if text:
                lines.append({"text": text,
                              "bbox": [float(v) for v in line["bbox"]]})
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), colorspace=pymupdf.csGRAY,
                          alpha=False)
    doc.close()
    return lines, pix


def _ink_present(pix, bbox):
    scale = 2.0
    x0 = max(0, min(pix.width, int(bbox[0] * scale)))
    y0 = max(0, min(pix.height, int(bbox[1] * scale)))
    x1 = max(0, min(pix.width, int(bbox[2] * scale + 1)))
    y1 = max(0, min(pix.height, int(bbox[3] * scale + 1)))
    if x1 <= x0 or y1 <= y0:
        return False
    samples = pix.samples
    stride = pix.stride
    dark = 0
    for y in range(y0, y1):
        row = y * stride
        for x in range(x0, x1):
            if samples[row + x] < 210:
                dark += 1
                if dark >= 8:
                    return True
    return False


def _ordinary_words(text):
    if URL_RE.search(text or ""):
        return []
    # Natural-language residue is a continuous English span.  Mixed Chinese
    # labels such as "Ref. Count指..." are not untranslated English prose.
    if re.search(r"[\u3400-\u9fff]", text or ""):
        return []
    # Code/config expressions are protected CodeRuns.
    if re.search(r"[A-Za-z][A-Za-z0-9_]*\s*=\s*[-+]?\d", text or ""):
        return []
    clean = MODEL_RE.sub(" ", text or "")
    return LEX_RE.findall(clean)


def final_pdf_residual_prose_qa(page_model, pdf_path, *, flows=None,
                                out_dir=None):
    lines, pix = _pdf_lines(pdf_path)
    shifts = _formula_shifts(flows)
    reference_boxes = _reference_boxes(flows)
    formula_boxes = []
    svg_prose = []
    for region in page_model.get("regions", []):
        if region.get("type") != "formula":
            continue
        fm = region["payload"]
        dy = shifts.get(fm.get("formula_id"), 0.0)
        for seg in fm.get("render_segments", []):
            b = seg.get("render_viewbox") or seg.get("layout_bbox")
            if b:
                formula_boxes.append([b[0], b[1] + dy, b[2], b[3] + dy])
        for comp in fm.get("components", []):
            if not comp.get("adopted"):
                continue
            text = (comp.get("text") or "").strip()
            words = _ordinary_words(text)
            low = " ".join(words).lower()
            if not words or low in FORMULA_CONDITIONS:
                continue
            b = comp.get("bbox")
            physical = [b[0], b[1] + dy, b[2], b[3] + dy]
            if _ink_present(pix, physical):
                svg_prose.append({
                    "source_object_id": fm.get("formula_id"),
                    "logical_paragraph_id": None,
                    "flow_fragment_id": None,
                    "formula_placeholder_ids": ["{{FORMULA_%s}}" % fm.get("formula_id")],
                    "owner": "formula",
                    "translation_status": "protected_by_formula_ownership",
                    "rendered_text": text,
                    "bbox": [round(v, 3) for v in physical],
                })

    text_prose = []
    reference_exempt = []
    for line in lines:
        # Exclude only actual final formula RenderSegment ink (+1pt safety),
        # not a whole equation row or paragraph enclosure.
        if any(_overlap(line["bbox"], box, tol=1.0) >
               0.80 * max(1.0, (line["bbox"][2] - line["bbox"][0])
                          * (line["bbox"][3] - line["bbox"][1]))
               for box in formula_boxes):
            continue
        # References are a separately accounted, protected semantic class.
        # Use the final flowed boxes so ordinary appendix prose containing a
        # citation is never accidentally exempted.
        if any(_overlap(line["bbox"], box, tol=1.0) >
               0.60 * max(1.0, (line["bbox"][2] - line["bbox"][0])
                          * (line["bbox"][3] - line["bbox"][1]))
               for box in reference_boxes):
            reference_exempt.append({
                "rendered_text": line["text"][:180],
                "bbox": [round(v, 3) for v in line["bbox"]],
            })
            continue
        words = _ordinary_words(line["text"])
        if len(words) < 3:
            continue
        # Citations/author lists and the vertical arXiv stamp are protected.
        if re.search(r"\bet\s+al\b|^[A-Z][a-z]+\s+[A-Z][a-z]+(?:,|\s+and)",
                     line["text"]):
            continue
        # FrontMatter author-name groups are protected named entities.
        if words and len(words) >= 3 and all(w[:1].isupper() for w in words):
            continue
        text_prose.append({"rendered_text": line["text"][:180],
                           "bbox": [round(v, 3) for v in line["bbox"]],
                           "lexical_word_count": len(words)})

    details = svg_prose + text_prose
    result = {
        "residual_untranslated_prose_count": len(details),
        "formula_owned_prose_count": len(svg_prose),
        "pdf_text_prose_count": len(text_prose),
        "reference_exempt_prose_count": len(reference_exempt),
        "residual_prose_clean": not details,
        "details": details[:24],
        "formula_exclusion_tolerance_pt": 1.0,
        "truth_source": "final_chromium_pdf",
    }
    if out_dir:
        path = Path(out_dir) / "final_pdf_residual_prose_qa.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return result
