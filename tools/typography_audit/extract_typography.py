# -*- coding: utf-8 -*-
"""Extract source/current PDF typography evidence for Phase 4D.2A."""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import pymupdf
from lxml import html as lxml_html

try:
    from .classify_text_role import (
        TYPOGRAPHY_ROLES,
        classify_formula,
        classify_paragraph,
        classify_table_cell,
        clean_model_text,
    )
except ImportError:  # direct script execution
    from classify_text_role import (  # type: ignore
        TYPOGRAPHY_ROLES,
        classify_formula,
        classify_paragraph,
        classify_table_cell,
        clean_model_text,
    )


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
LATIN_RE = re.compile(r"[A-Za-z]")
NUMBER_RE = re.compile(r"[0-9]")
MARKER_RE = re.compile(r"\{\{[^}]+\}\}")
STYLE_RE = re.compile(r"([\w-]+)\s*:\s*([^;]+)")


def read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: Iterable[float], q: float) -> float | None:
    seq = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not seq:
        return None
    if len(seq) == 1:
        return round(seq[0], 4)
    pos = (len(seq) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    value = seq[lo] if lo == hi else seq[lo] * (hi - pos) + seq[hi] * (pos - lo)
    return round(value, 4)


def stats(values: Iterable[float]) -> dict[str, Any]:
    seq = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not seq:
        return {"sample_count": 0, "median": None, "p10": None, "p90": None,
                "min": None, "max": None, "mad": None}
    med = statistics.median(seq)
    mad = statistics.median(abs(value - med) for value in seq)
    return {
        "sample_count": len(seq),
        "median": round(med, 4),
        "p10": percentile(seq, .10),
        "p90": percentile(seq, .90),
        "min": round(min(seq), 4),
        "max": round(max(seq), 4),
        "mad": round(mad, 4),
    }


def bbox_union(boxes: Iterable[Iterable[float]]) -> list[float] | None:
    rows = [[float(v) for v in box] for box in boxes if box and len(list(box)) == 4]
    if not rows:
        return None
    return [min(b[0] for b in rows), min(b[1] for b in rows),
            max(b[2] for b in rows), max(b[3] for b in rows)]


def bbox_intersection_area(a: Iterable[float], b: Iterable[float]) -> float:
    aa, bb = list(a), list(b)
    return max(0.0, min(aa[2], bb[2]) - max(aa[0], bb[0])) * max(
        0.0, min(aa[3], bb[3]) - max(aa[1], bb[1]))


def parse_style(style: str | None) -> dict[str, str]:
    return {key.lower(): value.strip() for key, value in STYLE_RE.findall(style or "")}


def pt_value(value: str | None) -> float | None:
    match = re.search(r"-?[0-9.]+", value or "")
    return float(match.group()) if match else None


def script_of(text: str) -> str:
    cjk, latin, number = bool(CJK_RE.search(text)), bool(LATIN_RE.search(text)), bool(NUMBER_RE.search(text))
    if cjk and (latin or number):
        return "mixed"
    if cjk:
        return "CJK"
    if latin:
        return "Latin"
    if number:
        return "number"
    return "other"


def _raw_pdf_lines(pdf_path: str | Path, page_index: int = 0) -> list[dict[str, Any]]:
    doc = pymupdf.open(str(pdf_path))
    page = doc[page_index]
    lines: list[dict[str, Any]] = []
    for block_index, block in enumerate(page.get_text("rawdict").get("blocks", [])):
        for line_index, line in enumerate(block.get("lines", [])):
            spans = []
            for span in line.get("spans", []):
                chars = [{
                    "c": char.get("c", ""),
                    "origin": [round(float(v), 4) for v in char.get("origin", (0, 0))],
                    "bbox": [round(float(v), 4) for v in char.get("bbox", (0, 0, 0, 0))],
                } for char in span.get("chars", [])]
                text = "".join(char["c"] for char in chars)
                if not text.strip():
                    continue
                spans.append({
                    "text": text,
                    "font": span.get("font") or "",
                    "size": round(float(span.get("size") or 0.0), 4),
                    "origin": [round(float(v), 4) for v in span.get("origin", (0, 0))],
                    "bbox": [round(float(v), 4) for v in span.get("bbox", (0, 0, 0, 0))],
                    "ascender": round(float(span.get("ascender") or 0.0), 4),
                    "descender": round(float(span.get("descender") or 0.0), 4),
                    "flags": int(span.get("flags") or 0),
                    "script": script_of(text),
                    "chars": chars,
                })
            if not spans:
                continue
            bbox = bbox_union(span["bbox"] for span in spans)
            origins = [span["origin"][1] for span in spans if span.get("origin")]
            lines.append({
                "block_index": block_index,
                "line_index": line_index,
                "text": "".join(span["text"] for span in spans),
                "bbox": [round(v, 4) for v in bbox] if bbox else None,
                "baseline": round(statistics.median(origins), 4) if origins else None,
                "spans": spans,
            })
    doc.close()
    return lines


def pdf_page_geometry(pdf_path: str | Path, page_index: int = 0) -> dict[str, Any]:
    doc = pymupdf.open(str(pdf_path))
    page = doc[page_index]
    result = {"width_pt": round(float(page.rect.width), 4),
              "height_pt": round(float(page.rect.height), 4),
              "rotation": int(page.rotation)}
    doc.close()
    return result


def _payloads(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [region["payload"] for region in model.get("regions", [])
            if region.get("type") == "text"]


def _source_role_regions(page_number: int, model: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for payload in _payloads(model):
        role = classify_paragraph(page_number, payload)
        rows.append({
            "paragraph_id": payload.get("paragraph_id"),
            "role": role,
            "bbox": [float(v) for v in payload.get("bbox", [])],
            "column": payload.get("column"),
            "source_text": payload.get("source_text") or "",
            "translated_text": payload.get("translated_text") or "",
            "requested_font_size_pt": float(payload.get("base_font_size") or 0.0),
            "payload": payload,
        })
    return rows


def _best_role(line: dict[str, Any], regions: list[dict[str, Any]]) -> tuple[str, str | None]:
    bbox = line.get("bbox")
    if not bbox:
        return "body", None
    candidates = []
    cy = (bbox[1] + bbox[3]) / 2.0
    cx = (bbox[0] + bbox[2]) / 2.0
    for region in regions:
        rb = region["bbox"]
        if not rb:
            continue
        area = bbox_intersection_area(bbox, rb)
        contains_center = rb[0] - 2 <= cx <= rb[2] + 2 and rb[1] - 3 <= cy <= rb[3] + 3
        if area > 0 or contains_center:
            candidates.append((area + (1000 if contains_center else 0), region))
    if not candidates:
        return "body", None
    region = max(candidates, key=lambda item: item[0])[1]
    return region["role"], region["paragraph_id"]


def _assign_current_roles(page_number: int, lines: list[dict[str, Any]],
                          regions: list[dict[str, Any]], model: dict[str, Any],
                          dom_snapshot: dict[str, Any]) -> None:
    tables = [region for region in model.get("regions", []) if region.get("type") == "table"]
    formula_boxes = []
    for role in ("inline_formula", "display_formula"):
        formula_boxes.extend((record.get("bbox_pt"), role, record.get("formula_id"))
                             for record in dom_snapshot.get(role, [])
                             if record.get("bbox_pt"))
    frontmatter_boxes = [(record.get("bbox_pt"), record.get("role"))
                         for record in dom_snapshot.get("frontmatter", [])
                         if record.get("bbox_pt") and record.get("role")]
    role_by_pid = {region["paragraph_id"]: region["role"] for region in regions}
    dom_paragraphs = [(record.get("bbox_pt"), record.get("paragraph_id"))
                      for record in dom_snapshot.get("paragraphs", [])
                      if record.get("bbox_pt") and record.get("paragraph_id")]
    for line in lines:
        bbox = line.get("bbox") or [0, 0, 0, 0]
        cx, cy = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
        # Table text has authoritative cell geometry and takes precedence.
        table_cell = None
        best_area = 0.0
        for table in tables:
            for cell in (table.get("payload") or {}).get("cells", []) or []:
                cb = cell.get("layout_bbox") or cell.get("bbox")
                area = bbox_intersection_area(bbox, cb) if cb else 0.0
                if area > best_area:
                    best_area, table_cell = area, cell
        if table_cell is not None and best_area > 0:
            line["role"] = classify_table_cell(table_cell)
            line["paragraph_id"] = table_cell.get("cell_id")
            continue
        # p001's source DLP00010 legitimately contributes to both AuthorBlock
        # and AffiliationBlock.  DOM grouping, not paragraph id, disambiguates
        # the final PDF rows while leaving that grouping frozen.
        if page_number == 1:
            fm_hits = []
            for fb, role in frontmatter_boxes:
                area = bbox_intersection_area(bbox, fb)
                inside = fb[0] - 2 <= cx <= fb[2] + 2 and fb[1] - 2 <= cy <= fb[3] + 2
                if area > 0 or inside:
                    fm_hits.append((area + (1000 if inside else 0), role))
            if fm_hits:
                line["role"] = max(fm_hits, key=lambda item: item[0])[1]
                line["paragraph_id"] = f"p001_{line['role']}"
                continue
        # The final browser box is authoritative for formula-only PDF lines.
        # A text line merely adjacent to an inline wrapper (e.g. Chinese
        # punctuation/prose after H(S)) must remain in its paragraph role.
        line_area = max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), .01)
        formula_hits = []
        for fb, role, fid in formula_boxes:
            area = bbox_intersection_area(bbox, fb)
            ratio = area / line_area
            if ratio >= .60:
                formula_hits.append((ratio, role, fid))
        if formula_hits:
            _, role, fid = max(formula_hits, key=lambda item: item[0])
            line["role"], line["paragraph_id"] = role, fid
            continue
        # Final DOM paragraph boxes follow flowed positions and therefore
        # disambiguate vertically adjacent list/body fragments more reliably
        # than source bboxes.
        dom_hits = []
        for pb, pid in dom_paragraphs:
            area = bbox_intersection_area(bbox, pb)
            contains = pb[0] - 1 <= cx <= pb[2] + 1 and pb[1] - 2 <= cy <= pb[3] + 2
            if area > 0 or contains:
                dom_hits.append((area / line_area + (10 if contains else 0), pid))
        if dom_hits:
            _, pid = max(dom_hits, key=lambda item: item[0])
            line["role"], line["paragraph_id"] = role_by_pid.get(pid, "body"), pid
            continue
        role, pid = _best_role(line, regions)
        # Formula SVG text layers can overlap flowed formula positions.  The
        # outer typography audit records those objects separately; do not
        # relabel neighbouring Chinese prose merely because source formula
        # geometry intersects it.
        line["role"], line["paragraph_id"] = role, pid


def _source_lines(page_number: int, model: dict[str, Any]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for payload in _payloads(model):
        role = classify_paragraph(page_number, payload)
        pid = payload.get("paragraph_id")
        for index, line in enumerate(payload.get("lines") or []):
            spans = []
            for span in line.get("spans") or []:
                text = span.get("text") or ""
                if not text.strip() or span.get("is_formula"):
                    continue
                bbox = [float(v) for v in span.get("bbox", [])]
                spans.append({
                    "text": text,
                    "font": span.get("font") or "",
                    "size": float(span.get("size") or 0.0),
                    "bbox": bbox,
                    "script": script_of(text),
                    "origin": [bbox[0], float(line.get("cy") or bbox[3])],
                    "chars": [],
                })
            if not spans:
                continue
            bbox = bbox_union(span["bbox"] for span in spans)
            line_role = role
            if page_number == 1 and bbox:
                cy = (bbox[1] + bbox[3]) / 2.0
                if 76 <= cy <= 112:
                    line_role = "document_title"
                elif 118 <= cy <= 153:
                    line_role = "author"
                elif 153 < cy <= 198:
                    line_role = "affiliation"
                elif 215 <= cy <= 238:
                    line_role = "abstract_heading"
                elif 238 < cy <= 570:
                    line_role = "abstract_body"
            lines.append({
                "block_index": -1,
                "line_index": index,
                "text": "".join(span["text"] for span in spans),
                "bbox": bbox,
                "baseline": float(line.get("cy") or (bbox[3] if bbox else 0.0)),
                "spans": spans,
                "role": line_role,
                "paragraph_id": pid,
            })
    # Include table cells as their own source lines.
    for region in model.get("regions", []):
        if region.get("type") != "table":
            continue
        for cell in (region.get("payload") or {}).get("cells", []) or []:
            spans = [{
                "text": span.get("text") or "",
                "font": span.get("font") or "",
                "size": float(span.get("font_size") or cell.get("source_font_size") or 0.0),
                "bbox": [float(v) for v in span.get("bbox", cell.get("bbox", []))],
                "script": script_of(span.get("text") or ""),
                "origin": [float((span.get("bbox") or cell.get("bbox"))[0]),
                           float(cell.get("baseline") or (span.get("bbox") or cell.get("bbox"))[3])],
                "chars": [],
            } for span in cell.get("spans", []) if (span.get("text") or "").strip()]
            if spans:
                lines.append({
                    "block_index": -2,
                    "line_index": int(cell.get("row") or 0),
                    "text": "".join(span["text"] for span in spans),
                    "bbox": bbox_union(span["bbox"] for span in spans),
                    "baseline": float(cell.get("baseline") or 0.0),
                    "spans": spans,
                    "role": classify_table_cell(cell),
                    "paragraph_id": cell.get("cell_id"),
                })
    return lines


def _font_samples(lines: list[dict[str, Any]], requested_font: str | None = None) -> list[dict[str, Any]]:
    rows = []
    for line in lines:
        for span in line.get("spans", []):
            text = span.get("text") or ""
            rows.append({
                "role": line.get("role"),
                "paragraph_id": line.get("paragraph_id"),
                "text": text[:120],
                "script": script_of(text),
                "requested_font": requested_font,
                "actual_pdf_font": span.get("font") or "",
                "font_size_pt": round(float(span.get("size") or 0.0), 4),
                "origin": span.get("origin"),
                "bbox": span.get("bbox"),
            })
    return rows


def _role_summary(lines: list[dict[str, Any]], body_median: float | None) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        grouped[line.get("role") or "body"].append(line)
    result = {}
    for role in TYPOGRAPHY_ROLES:
        rows = grouped.get(role, [])
        sizes = [span["size"] for row in rows for span in row.get("spans", [])
                 if span.get("size") and span.get("script") != "other"]
        baselines: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            if row.get("baseline") is not None:
                baselines[str(row.get("paragraph_id"))].append(float(row["baseline"]))
        gaps = []
        for values in baselines.values():
            ordered = sorted(set(round(v, 3) for v in values))
            gaps.extend(b - a for a, b in zip(ordered, ordered[1:]) if .5 < b - a < 80)
        size_stats = stats(sizes)
        gap_stats = stats(gaps)
        median_size = size_stats["median"]
        result[role] = {
            "font_size_pt": size_stats,
            "baseline_gap_pt": gap_stats,
            "line_height_ratio": (
                round(gap_stats["median"] / median_size, 4)
                if gap_stats["median"] and median_size else None
            ),
            "ratio_to_body": (
                round(median_size / body_median, 4)
                if median_size and body_median else None
            ),
            "line_count": len(rows),
        }
    return result


def _html_inventory(html_path: str | Path, page_number: int,
                    role_by_pid: dict[str, str]) -> dict[str, Any]:
    raw = Path(html_path).read_text(encoding="utf-8")
    doc = lxml_html.fromstring(raw)
    requested = []
    selectors = [
        ("//*[contains(concat(' ',normalize-space(@class),' '),' title-block ')]", "document_title"),
        ("//*[contains(concat(' ',normalize-space(@class),' '),' author-block ')]", "author"),
        ("//*[contains(concat(' ',normalize-space(@class),' '),' affiliation-block ')]", "affiliation"),
        ("//*[contains(concat(' ',normalize-space(@class),' '),' abstract-heading ')]", "abstract_heading"),
        ("//*[contains(concat(' ',normalize-space(@class),' '),' abstract-body ')]", "abstract_body"),
        ("//*[contains(concat(' ',normalize-space(@class),' '),' caption-block ')]", "caption"),
        ("//*[contains(concat(' ',normalize-space(@class),' '),' footnote-block ')]", "footnote"),
        ("//*[contains(concat(' ',normalize-space(@class),' '),' paragraph-block ')]", None),
        ("//*[contains(concat(' ',normalize-space(@class),' '),' translated-cell ')]", "table_body"),
        ("//*[contains(concat(' ',normalize-space(@class),' '),' code-run ')]", "code_run"),
        ("//*[contains(concat(' ',normalize-space(@class),' '),' formula-inline ')]", "inline_formula"),
        ("//*[contains(concat(' ',normalize-space(@class),' '),' formula-seg ')]", "display_formula"),
    ]
    seen = set()
    for xpath, fixed_role in selectors:
        for element in doc.xpath(xpath):
            key = id(element)
            if key in seen:
                continue
            seen.add(key)
            pid = element.get("data-para")
            role = fixed_role or role_by_pid.get(pid, element.get("data-role") or "body")
            style = parse_style(element.get("style"))
            ancestors = list(element.iterancestors())
            if fixed_role == "table_body":
                cell = element.get("data-cell") or ""
                role = "table_header" if cell.startswith("R0C") else "table_body"
            requested.append({
                "role": role,
                "paragraph_id": pid,
                "class": element.get("class") or "",
                "text": " ".join("".join(element.itertext()).split())[:180],
                "requested_font": style.get("font-family"),
                "requested_font_size_pt": pt_value(style.get("font-size")),
                "requested_line_height_pt": pt_value(style.get("line-height")),
                "font_weight": style.get("font-weight"),
                "left_pt": pt_value(style.get("left")),
                "top_pt": pt_value(style.get("top")),
                "width_pt": pt_value(style.get("width")),
                "height_pt": pt_value(style.get("height")),
                "has_literal_ascii_space": " " in (element.text_content() or ""),
                "wrapper_context": [ancestor.get("class") for ancestor in ancestors[:3]
                                    if ancestor.get("class")],
            })
    return {"path": str(Path(html_path).resolve()), "records": requested,
            "raw_html": raw}


def capture_dom_typography(html_path: str | Path) -> dict[str, Any]:
    """Capture computed DOM geometry in PDF points without editing HTML.

    Inline formula x/y positions do not exist in their CSS declarations, so
    the browser's shaped layout is the only reliable source for their outer
    box.  The formula image itself remains frozen.
    """
    from playwright.sync_api import sync_playwright

    target = Path(html_path).resolve()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        page = browser.new_page(viewport={"width": 900, "height": 1200})
        page.goto(target.as_uri(), wait_until="networkidle")
        page.wait_for_function("Array.from(document.images).every(i => i.complete)")
        page.evaluate("document.fonts && document.fonts.ready")
        result = page.evaluate(
            """() => {
              const pt = n => n * 72 / 96;
              const rect = el => {
                const r=el.getBoundingClientRect();
                return [pt(r.x),pt(r.y),pt(r.right),pt(r.bottom)];
              };
              const row = (el,kind) => {
                const cs=getComputedStyle(el), p=el.closest('[data-para]');
                return {kind, class_name:el.className || '',
                  paragraph_id:(p&&p.dataset.para)||el.dataset.para||null,
                  formula_id:el.dataset.formula || (el.parentElement&&el.parentElement.dataset.formula)||null,
                  segment_id:el.dataset.segment||null,
                  region_id:el.dataset.region||null,
                  text:(el.innerText||el.textContent||'').trim(),
                  bbox_pt:rect(el), requested_font:cs.fontFamily,
                  font_size_pt:pt(parseFloat(cs.fontSize)||0),
                  line_height_pt:pt(parseFloat(cs.lineHeight)||0),
                  font_weight:cs.fontWeight, display:cs.display,
                  vertical_align:cs.verticalAlign};
              };
              const collect=(sel,kind)=>Array.from(document.querySelectorAll(sel)).map(x=>row(x,kind));
              return {page_bbox_pt:rect(document.body),
                inline_formula:collect('.formula-inline','inline_formula'),
                display_formula:collect('.formula-seg','display_formula'),
                figure:collect('.figure-region','figure'),
                table_cell:collect('.translated-cell','table_cell'),
                code_run:collect('.code-run','code_run'),
                frontmatter:[
                  ...collect('.title-block','frontmatter').map(x=>({...x,role:'document_title'})),
                  ...collect('.author-block','frontmatter').map(x=>({...x,role:'author'})),
                  ...collect('.affiliation-block','frontmatter').map(x=>({...x,role:'affiliation'})),
                  ...collect('.abstract-heading','frontmatter').map(x=>({...x,role:'abstract_heading'})),
                  ...collect('.abstract-body','frontmatter').map(x=>({...x,role:'abstract_body'})),
                  ...collect('.caption-block','frontmatter').map(x=>({...x,role:'caption'})),
                  ...collect('.footnote-block','frontmatter').map(x=>({...x,role:'footnote'}))],
                paragraphs:collect('[data-para]','paragraph')};
            }"""
        )
        browser.close()
    result["html_path"] = str(target)
    return result


def extract_page_typography(*, page_number: int, source_pdf: str | Path,
                            current_pdf: str | Path, html_path: str | Path,
                            page_model_path: str | Path) -> dict[str, Any]:
    """Extract the complete source/current inventory for one sample page."""
    model = read_json(page_model_path)
    regions = _source_role_regions(page_number, model)
    role_by_pid = {row["paragraph_id"]: row["role"] for row in regions}
    html_inventory = _html_inventory(html_path, page_number, role_by_pid)
    dom_snapshot = capture_dom_typography(html_path)

    source_lines = _source_lines(page_number, model)
    current_lines = _raw_pdf_lines(current_pdf, 0)
    _assign_current_roles(page_number, current_lines, regions, model, dom_snapshot)

    # Requested font is resolved per role from the generated HTML.  Actual
    # font is always taken from PyMuPDF's final PDF text layer.
    requested_by_role: dict[str, str] = {}
    for record in html_inventory["records"]:
        if record.get("requested_font"):
            requested_by_role.setdefault(record["role"], record["requested_font"])

    source_body_sizes = [span["size"] for line in source_lines
                         if line.get("role") in {"body", "body_bold_lead", "abstract_body"}
                         for span in line.get("spans", []) if span.get("size")]
    current_body_sizes = [span["size"] for line in current_lines
                          if line.get("role") in {"body", "body_bold_lead", "abstract_body"}
                          for span in line.get("spans", []) if span.get("size")]
    source_body = percentile(source_body_sizes, .5)
    current_body = percentile(current_body_sizes, .5)

    font_samples = []
    for line in current_lines:
        requested = requested_by_role.get(line.get("role"))
        font_samples.extend(_font_samples([line], requested))
    fonts = Counter((sample["actual_pdf_font"], sample["script"])
                    for sample in font_samples)

    formulas = []
    for region in model.get("regions", []):
        if region.get("type") != "formula":
            continue
        formula = region.get("payload") or {}
        formulas.append({
            "formula_id": formula.get("formula_id"),
            "role": classify_formula(formula),
            "placement": formula.get("placement"),
            "type": formula.get("type"),
            "bbox": formula.get("layout_bbox") or region.get("bbox"),
            "source_text": formula.get("source_text") or "",
            "component_count": len(formula.get("components") or []),
            "frozen_internal_geometry": True,
        })

    tables = []
    for region in model.get("regions", []):
        if region.get("type") != "table":
            continue
        payload = region.get("payload") or {}
        rules = payload.get("rules") or []
        horizontal = sum(1 for rule in rules
                         if rule.get("orientation") == "horizontal"
                         or ("y0" in rule and "y1" in rule
                             and abs(float(rule["y1"]) - float(rule["y0"])) <= 1))
        vertical = sum(1 for rule in rules
                       if rule.get("orientation") == "vertical"
                       or ("x0" in rule and "x1" in rule
                           and abs(float(rule["x1"]) - float(rule["x0"])) <= 1))
        cell_geometry = []
        for cell in payload.get("cells") or []:
            lb = cell.get("layout_bbox") or cell.get("bbox")
            cb = cell.get("content_bbox") or cell.get("bbox")
            if not lb or not cb:
                continue
            cell_geometry.append({
                "cell_id": cell.get("cell_id"), "row": cell.get("row"),
                "col": cell.get("col"), "is_header": bool(cell.get("is_header")),
                "content_class": cell.get("content_class"),
                "alignment": cell.get("alignment") or "left",
                "layout_bbox": lb, "content_bbox": cb,
                "padding_left_pt": round(float(cb[0]) - float(lb[0]), 4),
                "padding_right_pt": round(float(lb[2]) - float(cb[2]), 4),
                "padding_top_pt": round(float(cb[1]) - float(lb[1]), 4),
                "padding_bottom_pt": round(float(lb[3]) - float(cb[3]), 4),
                "baseline_pt": cell.get("baseline"),
                "declared_css_padding_horizontal_pt": 0.0,
                "declared_css_padding_vertical_pt": 0.0,
            })
        tables.append({
            "table_id": region.get("region_id"),
            "bbox": region.get("bbox"),
            "row_count": len(payload.get("rows") or []),
            "column_count": len(payload.get("columns") or []),
            "cell_count": len(payload.get("cells") or []),
            "horizontal_rule_count": horizontal,
            "vertical_rule_count": vertical,
            "qa": payload.get("qa") or {},
            "cell_geometry": cell_geometry,
            "alignment_counts": dict(Counter(cell["alignment"] for cell in cell_geometry)),
            "padding": {
                "declared_css_horizontal_pt": 0.0,
                "declared_css_vertical_pt": 0.0,
                "left_pt": stats(cell["padding_left_pt"] for cell in cell_geometry),
                "right_pt": stats(cell["padding_right_pt"] for cell in cell_geometry),
                "top_pt": stats(cell["padding_top_pt"] for cell in cell_geometry),
                "bottom_pt": stats(cell["padding_bottom_pt"] for cell in cell_geometry),
                "measurement_note": (
                    "content_bbox-to-layout_bbox effective inset from frozen model; "
                    "centered-cell values include alignment whitespace, not CSS padding"
                ),
            },
        })

    return {
        "page": page_number,
        "inputs": {
            "source_pdf": str(Path(source_pdf).resolve()),
            "current_pdf": str(Path(current_pdf).resolve()),
            "html": str(Path(html_path).resolve()),
            "page_model": str(Path(page_model_path).resolve()),
            "sha256": {
                "source_pdf": sha256_file(source_pdf),
                "current_pdf": sha256_file(current_pdf),
                "html": sha256_file(html_path),
                "page_model": sha256_file(page_model_path),
            },
        },
        "geometry": {
            "source": pdf_page_geometry(source_pdf, page_number - 1),
            "current": pdf_page_geometry(current_pdf, 0),
        },
        "role_regions": [{key: value for key, value in row.items() if key != "payload"}
                         for row in regions],
        "source": {
            "lines": source_lines,
            "role_summary": _role_summary(source_lines, source_body),
            "body_font_size_median_pt": source_body,
        },
        "current_zh": {
            "lines": current_lines,
            "role_summary": _role_summary(current_lines, current_body),
            "body_font_size_median_pt": current_body,
            "font_samples": font_samples,
            "font_inventory": [
                {"actual_pdf_font": font, "script": script, "sample_count": count}
                for (font, script), count in fonts.most_common()
            ],
        },
        "html_inventory": {key: value for key, value in html_inventory.items()
                           if key != "raw_html"},
        "dom_snapshot": dom_snapshot,
        "formulas": formulas,
        "tables": tables,
    }


def build_document_inventory(pages: list[dict[str, Any]]) -> dict[str, Any]:
    source_by_role: dict[str, list[float]] = defaultdict(list)
    current_by_role: dict[str, list[float]] = defaultdict(list)
    current_gaps_by_role: dict[str, list[float]] = defaultdict(list)
    fonts = Counter()
    for page in pages:
        for line in page["source"]["lines"]:
            source_by_role[line["role"]].extend(
                span["size"] for span in line.get("spans", []) if span.get("size"))
        for line in page["current_zh"]["lines"]:
            current_by_role[line["role"]].extend(
                span["size"] for span in line.get("spans", []) if span.get("size"))
        for role, summary in page["current_zh"]["role_summary"].items():
            if summary["baseline_gap_pt"]["median"]:
                current_gaps_by_role[role].append(summary["baseline_gap_pt"]["median"])
        for item in page["current_zh"]["font_inventory"]:
            fonts[(item["actual_pdf_font"], item["script"])] += item["sample_count"]

    source_body = percentile(source_by_role["body"] + source_by_role["body_bold_lead"], .5)
    current_body = percentile(current_by_role["body"] + current_by_role["body_bold_lead"], .5)
    role_inventory = {}
    for role in TYPOGRAPHY_ROLES:
        source_stats = stats(source_by_role[role])
        current_stats = stats(current_by_role[role])
        role_inventory[role] = {
            "source_font_size_pt": source_stats,
            "current_zh_font_size_pt": current_stats,
            "source_ratio_to_body": (
                round(source_stats["median"] / source_body, 4)
                if source_stats["median"] and source_body else None),
            "current_zh_ratio_to_body": (
                round(current_stats["median"] / current_body, 4)
                if current_stats["median"] and current_body else None),
            "current_baseline_gap_pt": stats(current_gaps_by_role[role]),
        }
    return {
        "schema_version": "phase4d2a.typography_inventory.v1",
        "analysis_pages": [page["page"] for page in pages],
        "analysis_page_count": len(pages),
        "measurement_truth": "source PageModel spans + final Chromium PDF rawdict",
        "body_reference": {
            "source_font_size_median_pt": source_body,
            "current_zh_font_size_median_pt": current_body,
        },
        "roles": role_inventory,
        "actual_pdf_font_inventory": [
            {"actual_pdf_font": font, "script": script, "sample_count": count}
            for (font, script), count in fonts.most_common()
        ],
        "actual_pdf_font_audited": True,
        "pages": pages,
    }
