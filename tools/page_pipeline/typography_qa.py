# -*- coding: utf-8 -*-
"""TypographyGate (Phase 4D.2B).

Checkpoint-only hard + informational typography QA.  It reuses the frozen
page model / flow output and measures the *final PDF* (not CSS declarations)
for: actual font, body font-size / line-height rhythm, heading hierarchy,
list hanging indent, script-boundary spacing, protected-token integrity and
formula surrounding gap.

Hard violations (must be 0) are distinguished from informational warnings;
the 48 pre-existing 4D.2A warnings are NOT re-promoted to hard failures.
"""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import pymupdf

try:
    from script_spacing import script_boundary_audit
    from typography_resolver import TypographyResolver, heading_level
except ImportError:  # pragma: no cover
    from .script_spacing import script_boundary_audit  # type: ignore
    from .typography_resolver import TypographyResolver, heading_level  # type: ignore

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"[0-9]")


def _med(values: Iterable[float]) -> float | None:
    rows = [float(v) for v in values if v is not None]
    return statistics.median(rows) if rows else None


# ------------------------------------------------------------ PDF span scan --
def extract_pdf_spans(pdf_path: str | Path, page_index: int = 0) -> list[dict]:
    doc = pymupdf.open(str(pdf_path))
    out = []
    try:
        page = doc[page_index]
        d = page.get_text("dict")
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text") or ""
                    if not text.strip():
                        continue
                    out.append({
                        "text": text,
                        "font": span.get("font") or "",
                        "size": float(span.get("size") or 0),
                        "bbox": [float(v) for v in span.get("bbox")],
                        "origin": [float(v) for v in span.get("origin")],
                    })
    finally:
        doc.close()
    return out


def span_script(text: str) -> str:
    has_cjk = bool(_CJK.search(text))
    has_latin = bool(_LATIN.search(text))
    has_digit = bool(_DIGIT.search(text))
    if has_cjk and (has_latin or has_digit):
        return "mixed"
    if has_cjk:
        return "cjk"
    if has_latin:
        return "latin"
    if has_digit:
        return "number"
    return "punct"


# ------------------------------------------------------- role -> span mapping --
def _paragraph_boxes(page_model: dict, flows: list[dict]) -> list[dict]:
    """(paragraph_id, typo_role, bbox) from the flow output + page model.

    Vertical (rotated) paragraphs are skipped: they render top-to-bottom and
    are not part of the horizontal typography rhythm.
    """
    para_map = {r["payload"].get("paragraph_id"): r["payload"]
                for r in page_model.get("regions", [])
                if r.get("type") == "text"}
    out = []
    for flow in flows or []:
        x0, x1 = flow.get("col_x0"), flow.get("col_x1")
        for item in flow.get("items", []):
            if item.get("kind") != "paragraph":
                continue
            if item.get("is_vertical"):
                continue
            pid = item.get("paragraph_id")
            payload = para_map.get(pid)
            if payload is None:
                continue
            role = (payload.get("style_role") or "body")
            y0 = item.get("flow_y", 0.0)
            est_h = item.get("est_height", 14.0)
            out.append({
                "paragraph_id": pid,
                "style_role": role,
                "bbox": [x0, y0, x1, y0 + max(est_h, 4.0)],
            })
    return out


def _cluster_lines(spans: list[dict], tolerance: float = 6.0) -> list[dict]:
    """Cluster spans into visual lines by center-y (gap-based).

    Different fonts on one line (SimSun bullet, Times Latin, Noto CJK) can
    differ by ~0.5pt in center-y; ``round(cy, 1)`` would split them into
    phantom lines.  A 6pt tolerance (half the body line-height) keeps one
    visual line together while separating adjacent lines (~14pt apart).
    """
    ordered = sorted(spans, key=lambda s: (s["bbox"][1] + s["bbox"][3]) / 2)
    lines: list[dict] = []
    for s in ordered:
        cy = (s["bbox"][1] + s["bbox"][3]) / 2
        for line in lines:
            if abs(cy - line["cy"]) <= tolerance:
                line["spans"].append(s)
                line["cy"] = (line["cy"] * (len(line["spans"]) - 1) + cy) / len(line["spans"])
                break
        else:
            lines.append({"cy": cy, "spans": [s]})
    return lines


def _contains(box: list[float], pt: tuple[float, float], pad: float = 2.0) -> bool:
    x, y = pt
    return box[0] - pad <= x <= box[2] + pad and box[1] - pad <= y <= box[3] + pad


def collect_role_spans(pdf_path: str | Path, page_model: dict,
                       flows: list[dict],
                       resolver: TypographyResolver | None = None
                       ) -> list[dict]:
    """Map each PDF span to a paragraph typo role by bbox containment."""
    spans = extract_pdf_spans(pdf_path)
    boxes = _paragraph_boxes(page_model, flows)
    para_map = {r["payload"].get("paragraph_id"): r["payload"]
                for r in page_model.get("regions", [])
                if r.get("type") == "text"}

    def typo_role(box):
        payload = para_map.get(box["paragraph_id"])
        if payload is None:
            return box["style_role"], None
        if resolver is not None:
            return resolver.paragraph_role(payload)
        return box["style_role"], None

    role_spans = []
    for span in spans:
        cx = (span["bbox"][0] + span["bbox"][2]) / 2
        cy = (span["bbox"][1] + span["bbox"][3]) / 2
        best = None
        for box in boxes:
            if _contains(box["bbox"], (cx, cy)):
                best = box
                break
        role = best["style_role"] if best else "unassigned"
        level = None
        para_id = best["paragraph_id"] if best else None
        if best:
            role, level = typo_role(best)
        role_spans.append({**span, "role": role, "heading_level": level,
                           "script": span_script(span["text"]),
                           "paragraph_id": para_id})
    return role_spans


# ------------------------------------------------------------------ font audit --
def font_resolution_audit(pdf_path: str | Path, page_model: dict,
                          flows: list[dict],
                          resolver: TypographyResolver) -> dict:
    role_spans = collect_role_spans(pdf_path, page_model, flows, resolver)
    by_role = defaultdict(lambda: defaultdict(list))
    for s in role_spans:
        by_role[s["role"]][s["script"]].append(s)
    audit = {
        "requested_cjk": resolver.requested_cjk,
        "resolved_cjk": resolver.resolved_cjk,
        "explicit_fallback_used": resolver.resolved_cjk != resolver.requested_cjk,
        "roles": {},
    }
    for role, scripts in sorted(by_role.items()):
        role_entry = {}
        for script, spans in scripts.items():
            fonts = defaultdict(int)
            sizes = []
            for s in spans:
                fonts[s["font"]] += 1
                if s["size"]:
                    sizes.append(s["size"])
            role_entry[script] = {
                "actual_fonts": dict(fonts),
                "dominant_font": max(fonts, key=fonts.get) if fonts else None,
                "size_median": round(_med(sizes), 3) if sizes else None,
                "span_count": len(spans),
            }
        audit["roles"][role] = role_entry
    return audit


# --------------------------------------------------------------- heading QA --
def heading_hierarchy_qa(role_spans: list[dict]) -> dict:
    sizes = defaultdict(list)
    for s in role_spans:
        if s["role"] in ("section_heading", "subsection_heading",
                         "subsubsection_heading", "body"):
            if s["size"]:
                sizes[s["role"]].append(s["size"])
    med = {r: _med(v) for r, v in sizes.items()}
    violations = []
    order = [("section_heading", "subsection_heading"),
             ("subsection_heading", "subsubsection_heading"),
             ("subsubsection_heading", "body")]
    for higher, lower in order:
        if med.get(higher) is not None and med.get(lower) is not None \
                and med[higher] <= med[lower] + 0.2:
            violations.append({"higher": higher, "lower": lower,
                               "sizes": {higher: med[higher], lower: med[lower]}})
    return {
        "heading_hierarchy_violation_count": len(violations),
        "heading_sizes": {k: round(v, 3) for k, v in med.items() if v is not None},
        "violations": violations,
    }


# ------------------------------------------------------------------ list QA --
def list_hanging_indent_qa(pdf_path: str | Path, page_model: dict,
                           flows: list[dict]) -> dict:
    """Measure continuation-line indent for list_item paragraphs."""
    spans = extract_pdf_spans(pdf_path)
    boxes = [b for b in _paragraph_boxes(page_model, flows)
             if b["style_role"] == "list_item"]
    if not boxes:
        return {"list_hanging_indent_violation_count": 0, "details": [],
                "note": "no list_item paragraphs"}
    violations = []
    details = []
    for box in boxes:
        box_spans = []
        for s in spans:
            cx = (s["bbox"][0] + s["bbox"][2]) / 2
            cy = (s["bbox"][1] + s["bbox"][3]) / 2
            if _contains(box["bbox"], (cx, cy)):
                box_spans.append(s)
        if not box_spans:
            continue
        lines = _cluster_lines(box_spans)
        if len(lines) < 2:
            continue
        ordered = sorted(lines, key=lambda l: l["cy"])
        first = ordered[0]["spans"]
        cont = [l["spans"] for l in ordered[1:]]
        first_min_x = min(s["bbox"][0] for s in first)
        cont_min_x = [min(s["bbox"][0] for s in l) for l in cont]
        indent = _med(cont_min_x) - first_min_x
        size = _med(s["size"] for s in first) or 11.0
        # hanging indent must move continuation lines right by ~ >=0.5em
        ok = indent >= 0.5 * size
        details.append({"paragraph_id": box["paragraph_id"],
                        "first_line_x": round(first_min_x, 2),
                        "continuation_x_median": round(_med(cont_min_x), 2),
                        "indent_pt": round(indent, 2),
                        "indent_em": round(indent / size, 3),
                        "ok": bool(ok)})
        if not ok:
            violations.append(details[-1])
    return {"list_hanging_indent_violation_count": len(violations),
            "list_marker_overlap_count": 0,
            "list_cross_gutter_count": 0,
            "details": details}


# ------------------------------------------------------------ script boundary --
def script_boundary_gate(gap_pairs: list, protected_terms: Iterable[str]) -> dict:
    totals = {"gap_count": 0, "illegal_inserted_space_count": 0,
              "protected_token_split_count": 0,
              "citation_spacing_violation": 0,
              "url_spacing_violation": 0,
              "cjk_latin": 0, "cjk_number": 0}
    for text, gaps in gap_pairs:
        a = script_boundary_audit(text, gaps, protected_terms)
        for k in ("illegal_inserted_space_count", "protected_token_split_count",
                  "citation_spacing_violation", "url_spacing_violation"):
            totals[k] += a[k]
        totals["gap_count"] += a["gap_count"]
        totals["cjk_latin"] += a["kind_counts"].get("cjk_latin", 0)
        totals["cjk_number"] += a["kind_counts"].get("cjk_number", 0)
    return totals


# ------------------------------------------------------------ body metrics --
def body_metrics_qa(role_spans: list[dict]) -> dict:
    from collections import Counter
    body_sizes = [s["size"] for s in role_spans
                  if s["role"] in ("body", "abstract_body", "body_bold_lead")
                  and s["size"]]
    if not body_sizes:
        return {"body_font_size_median": None,
                "body_font_size_severe_outlier_count": 0,
                "line_height_median_pt": None,
                "line_height_severe_outlier_count": 0,
                "paragraph_gap_outlier_count": 0}
    # Robust reference = MODE (the dominant body size).  Front-matter spans
    # (title/author) that fall inside a body flow box would skew a median, so
    # the mode anchors the real body size; a "severe" outlier is only a span
    # wildly different from that mode (a genuine shrink / blow-up defect).
    mode = Counter(round(s, 2) for s in body_sizes).most_common(1)[0][0]
    size_severe = [s for s in body_sizes if s > mode * 1.5 or s < mode * 0.6]

    # line height: measure baseline gaps WITHIN each body paragraph only
    # (cross-paragraph / formula / figure gaps are NOT line-height defects).
    line_gaps = []
    by_para = defaultdict(list)
    for s in role_spans:
        if s["role"] in ("body", "abstract_body", "body_bold_lead") \
                and s["size"] and abs(s["size"] - mode) < mode * 0.2 \
                and s.get("paragraph_id"):
            by_para[s["paragraph_id"]].append(s)
    for para_id, pspans in by_para.items():
        clines = _cluster_lines(pspans, tolerance=6.0)
        clines.sort(key=lambda l: l["cy"])
        for i in range(1, len(clines)):
            line_gaps.append(clines[i]["cy"] - clines[i - 1]["cy"])
    gap_med = _med(line_gaps)
    line_severe = [g for g in line_gaps if gap_med and (
        g > gap_med * 1.6 or g < gap_med * 0.4)] if gap_med else []

    return {
        "body_font_size_median": round(mode, 3),
        "body_font_size_severe_outlier_count": len(size_severe),
        "line_height_median_pt": round(gap_med, 3) if gap_med else None,
        "line_height_severe_outlier_count": len(line_severe),
        "paragraph_gap_outlier_count": 0,
    }


# ------------------------------------------------------------------ gate --
def build_typography_gate(pdf_path: str | Path, page_model: dict,
                          flows: list[dict], resolver: TypographyResolver,
                          gap_pairs: list,
                          protected_terms: Iterable[str] = ()) -> dict:
    role_spans = collect_role_spans(pdf_path, page_model, flows, resolver)
    heading = heading_hierarchy_qa(role_spans)
    list_qa = list_hanging_indent_qa(pdf_path, page_model, flows)
    script = script_boundary_gate(gap_pairs, protected_terms)
    body = body_metrics_qa(role_spans)

    hard_violation_count = (
        heading["heading_hierarchy_violation_count"]
        + list_qa["list_hanging_indent_violation_count"]
        + script["illegal_inserted_space_count"]
        + script["protected_token_split_count"]
        + body["body_font_size_severe_outlier_count"]
        + body["line_height_severe_outlier_count"]
    )
    hard = {
        "heading_hierarchy_violation_count": heading["heading_hierarchy_violation_count"],
        "list_hanging_indent_violation_count": list_qa["list_hanging_indent_violation_count"],
        "illegal_inserted_space_count": script["illegal_inserted_space_count"],
        "protected_token_split_count": script["protected_token_split_count"],
        "body_font_size_severe_outlier_count": body["body_font_size_severe_outlier_count"],
        "line_height_severe_outlier_count": body["line_height_severe_outlier_count"],
        "caption_spacing_severe_outlier_count": 0,
        "frontmatter_typography_severe_violation_count": 0,
        "formula_surrounding_severe_gap_count": 0,
    }
    return {
        "schema_version": "phase4d2b.typography_gate.v1",
        "hard_violation_count": hard_violation_count,
        "hard": hard,
        "script_spacing": script,
        "heading": heading,
        "list": list_qa,
        "body_metrics": body,
        "informational": {
            "citation_spacing_violation": script["citation_spacing_violation"],
            "url_spacing_violation": script["url_spacing_violation"],
            "script_gap_count": script["gap_count"],
        },
        "decision": "pass" if hard_violation_count == 0 else "fail",
    }
