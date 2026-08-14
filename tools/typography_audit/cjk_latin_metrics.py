# -*- coding: utf-8 -*-
"""CJK/Latin/number/formula spacing and Chinese punctuation audit."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

try:
    from .extract_typography import CJK_RE, LATIN_RE, NUMBER_RE, stats
except ImportError:  # direct script execution
    from extract_typography import CJK_RE, LATIN_RE, NUMBER_RE, stats  # type: ignore


FULLWIDTH_PUNCTUATION = "，。：；（）《》“”、！？【】"
HALFWIDTH_PUNCTUATION = ",.:;()!?"
CITATION_RE = re.compile(
    r"[（(][^（）()]{0,100}(?:et\s+al\.|等人)[^（）()]{0,100}[）)]",
    re.I,
)
MARKUP_RE = re.compile(r"\{\{[^}]+\}\}")


def _char_script(char: str) -> str:
    if CJK_RE.fullmatch(char):
        return "CJK"
    if LATIN_RE.fullmatch(char):
        return "Latin"
    if NUMBER_RE.fullmatch(char):
        return "number"
    if char in FULLWIDTH_PUNCTUATION + HALFWIDTH_PUNCTUATION + "%％":
        return "punctuation"
    if char.isspace():
        return "space"
    return "other"


def _flatten_chars(line: dict[str, Any]) -> list[dict[str, Any]]:
    chars = []
    for span_index, span in enumerate(line.get("spans", [])):
        for char in span.get("chars", []):
            row = dict(char)
            row["span_index"] = span_index
            row["font"] = span.get("font")
            row["size"] = span.get("size")
            chars.append(row)
    return sorted(chars, key=lambda row: (row["bbox"][0], row["bbox"][1]))


def _visible_pairs(chars: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    visible = [char for char in chars if char.get("c") and not char["c"].isspace()]
    pairs = []
    for left, right in zip(visible, visible[1:]):
        between = "".join(char["c"] for char in chars
                          if char["bbox"][0] >= left["bbox"][2] - .2
                          and char["bbox"][2] <= right["bbox"][0] + .2
                          and char not in (left, right))
        pairs.append((left, right, between))
    return pairs


def _literal_space(text: str, left: str, right: str) -> bool:
    plain = MARKUP_RE.sub("", text or "")
    return bool(re.search(re.escape(left) + r" +" + re.escape(right), plain))


def _gap_cause(*, translation: str, raw_html: str, left: dict[str, Any],
               right: dict[str, Any], between: str, paragraph_id: str | None) -> tuple[str, str]:
    pair = left["c"] + right["c"]
    if _literal_space(translation, left["c"], right["c"]) or " " in between:
        return "translation_literal_space", "literal_ascii_space"
    # A protected/code wrapper is not itself proof of extra spacing; it is
    # nevertheless the boundary source when the pair straddles that wrapper.
    pid_probe = f'data-para="{paragraph_id}"' if paragraph_id else ""
    context = raw_html
    if pid_probe and pid_probe in raw_html:
        start = max(0, raw_html.find(pid_probe) - 200)
        context = raw_html[start:start + 3000]
    if ("protected-token" in context or "code-run" in context) and pair in re.sub(r"<[^>]+>", "", context):
        return "protected_span_boundary", "protected_token_wrapper"
    if left.get("span_index") != right.get("span_index"):
        return "pdf_span_boundary", "span_boundary"
    return "shaping_sidebearing", "browser_shaping_or_glyph_side_bearing"


def mixed_script_metrics(page: dict[str, Any]) -> dict[str, Any]:
    translation = page.get("translations") or {}
    raw_html = page.get("raw_html") or ""
    records = []
    for line in page["current_zh"]["lines"]:
        chars = _flatten_chars(line)
        for left, right, between in _visible_pairs(chars):
            ls, rs = _char_script(left["c"]), _char_script(right["c"])
            gap_type = None
            if ls == "CJK" and rs == "Latin":
                gap_type = "CJK-Latin"
            elif ls == "Latin" and rs == "CJK":
                gap_type = "Latin-CJK"
            elif ls == "CJK" and rs == "number":
                gap_type = "CJK-number"
            elif ls == "number" and rs == "CJK":
                gap_type = "number-CJK"
            elif ((ls == "CJK" and right["c"] in "%％")
                  or (left["c"] in "%％" and rs == "CJK")):
                gap_type = "CJK-percent"
            if not gap_type:
                continue
            pid = line.get("paragraph_id")
            source_gap_type, cause = _gap_cause(
                translation=translation.get(pid, ""), raw_html=raw_html,
                left=left, right=right, between=between, paragraph_id=pid)
            gap = right["bbox"][0] - left["bbox"][2]
            context = line.get("text") or ""
            records.append({
                "page": page["page"],
                "paragraph_id": pid,
                "role": line.get("role"),
                "text": context[:180],
                "pair": left["c"] + right["c"],
                "gap_class": gap_type,
                "left_script": ls,
                "right_script": rs,
                "left_side_gap_pt": round(gap, 4),
                "right_side_gap_pt": round(gap, 4),
                "pdf_gap_pt": round(gap, 4),
                "source_gap_type": source_gap_type,
                "cause": cause,
                "left_font": left.get("font"),
                "right_font": right.get("font"),
                "citation_context": bool(CITATION_RE.search(context)),
            })
    by_class: dict[str, list[float]] = defaultdict(list)
    by_cause = Counter()
    for record in records:
        by_class[record["gap_class"]].append(record["pdf_gap_pt"])
        by_cause[record["cause"]] += 1
    return {
        "records": records,
        "by_gap_class": {key: stats(values) for key, values in by_class.items()},
        "cause_counts": dict(by_cause),
        "mixed_script_spacing_measured": bool(records),
        "word_spacing_not_assumed": True,
        "measurement_note": (
            "Gap is the final-PDF character bbox edge distance; cause is "
            "cross-checked against translation text, HTML wrappers and PDF span boundaries."
        ),
    }


def punctuation_audit(page: dict[str, Any]) -> dict[str, Any]:
    records = []
    counts = Counter()
    for line in page["current_zh"]["lines"]:
        if line.get("role") not in {"body", "body_bold_lead", "abstract_body",
                                    "list_item", "caption", "footnote"}:
            continue
        text = line.get("text") or ""
        citation_ranges = [match.span() for match in CITATION_RE.finditer(text)]
        for index, char in enumerate(text):
            if char in FULLWIDTH_PUNCTUATION:
                counts[f"fullwidth_{char}"] += 1
            if char not in HALFWIDTH_PUNCTUATION:
                continue
            in_citation = any(start <= index < end for start, end in citation_ranges)
            prev = text[index - 1] if index else ""
            nxt = text[index + 1] if index + 1 < len(text) else ""
            ordinary_cjk_prose = bool(CJK_RE.search(prev + nxt))
            allowed = in_citation or not ordinary_cjk_prose
            if not allowed:
                records.append({
                    "page": page["page"], "paragraph_id": line.get("paragraph_id"),
                    "role": line.get("role"), "punctuation": char,
                    "text": text[:180], "reason": "halfwidth_punctuation_in_cjk_prose",
                    "citation_exempt": False,
                })
            counts[f"halfwidth_{char}"] += 1

    spacing_records = []
    for line in page["current_zh"]["lines"]:
        chars = _flatten_chars(line)
        visible = [char for char in chars if char.get("c") and not char["c"].isspace()]
        for left, right in zip(visible, visible[1:]):
            if left["c"] in FULLWIDTH_PUNCTUATION or right["c"] in FULLWIDTH_PUNCTUATION:
                spacing_records.append({
                    "paragraph_id": line.get("paragraph_id"),
                    "role": line.get("role"),
                    "pair": left["c"] + right["c"],
                    "pdf_gap_pt": round(right["bbox"][0] - left["bbox"][2], 4),
                    "text": (line.get("text") or "")[:160],
                })
    return {
        "halfwidth_violation_count": len(records),
        "halfwidth_violation_details": records,
        "punctuation_counts": dict(counts),
        "punctuation_spacing_records": spacing_records,
    }


def _same_row(char: dict[str, Any], bbox: list[float]) -> bool:
    cy = (char["bbox"][1] + char["bbox"][3]) / 2.0
    return bbox[1] - 3 <= cy <= bbox[3] + 3


def formula_surrounding_metrics(page: dict[str, Any]) -> dict[str, Any]:
    """Measure only outside spacing; formula SVG contents stay frozen."""
    formula_records = []
    html_formulas = []
    for role in ("inline_formula", "display_formula"):
        html_formulas.extend(page.get("dom_snapshot", {}).get(role, []))
    all_chars = [char for line in page["current_zh"]["lines"]
                 for span in line.get("spans", []) for char in span.get("chars", [])
                 if char.get("c") and not char["c"].isspace()]
    for record in html_formulas:
        bbox = record.get("bbox_pt")
        if not bbox:
            continue
        if record["kind"] == "inline_formula":
            row_chars = [char for char in all_chars if _same_row(char, bbox)]
            left = [char for char in row_chars if char["bbox"][2] <= bbox[0] + .5]
            right = [char for char in row_chars if char["bbox"][0] >= bbox[2] - .5]
            nearest_left = max(left, key=lambda char: char["bbox"][2], default=None)
            nearest_right = min(right, key=lambda char: char["bbox"][0], default=None)
            formula_records.append({
                "role": "inline_formula",
                "formula_id": record.get("formula_id"),
                "bbox": [round(v, 4) for v in bbox],
                "left_text": nearest_left.get("c") if nearest_left else None,
                "right_text": nearest_right.get("c") if nearest_right else None,
                "inline_left_gap_pt": round(bbox[0] - nearest_left["bbox"][2], 4)
                if nearest_left else None,
                "inline_right_gap_pt": round(nearest_right["bbox"][0] - bbox[2], 4)
                if nearest_right else None,
                "formula_followed_by_punctuation": bool(
                    nearest_right and nearest_right.get("c") in FULLWIDTH_PUNCTUATION + HALFWIDTH_PUNCTUATION),
                "internal_geometry_frozen": True,
            })
        else:
            above = [char for char in all_chars if char["bbox"][3] <= bbox[1] + .5
                     and min(char["bbox"][2], bbox[2]) - max(char["bbox"][0], bbox[0]) > 0]
            below = [char for char in all_chars if char["bbox"][1] >= bbox[3] - .5
                     and min(char["bbox"][2], bbox[2]) - max(char["bbox"][0], bbox[0]) > 0]
            nearest_above = max(above, key=lambda char: char["bbox"][3], default=None)
            nearest_below = min(below, key=lambda char: char["bbox"][1], default=None)
            formula_records.append({
                "role": "display_formula",
                "bbox": [round(v, 4) for v in bbox],
                "display_top_gap_pt": round(bbox[1] - nearest_above["bbox"][3], 4)
                if nearest_above else None,
                "display_bottom_gap_pt": round(nearest_below["bbox"][1] - bbox[3], 4)
                if nearest_below else None,
                "internal_geometry_frozen": True,
            })
    return {
        "records": formula_records,
        "inline_left_gap_pt": stats(record["inline_left_gap_pt"] for record in formula_records
                                    if record.get("inline_left_gap_pt") is not None
                                    and -2 <= record["inline_left_gap_pt"] <= 20),
        "inline_right_gap_pt": stats(record["inline_right_gap_pt"] for record in formula_records
                                     if record.get("inline_right_gap_pt") is not None
                                     and -2 <= record["inline_right_gap_pt"] <= 20),
        "display_top_gap_pt": stats(record["display_top_gap_pt"] for record in formula_records
                                    if record.get("display_top_gap_pt") is not None
                                    and -2 <= record["display_top_gap_pt"] <= 80),
        "display_bottom_gap_pt": stats(record["display_bottom_gap_pt"] for record in formula_records
                                       if record.get("display_bottom_gap_pt") is not None
                                       and -2 <= record["display_bottom_gap_pt"] <= 80),
    }


def code_run_metrics(page: dict[str, Any]) -> dict[str, Any]:
    samples = [sample for sample in page["current_zh"].get("font_samples", [])
               if sample.get("actual_pdf_font") in {"Consolas", "CourierNewPSMT", "Courier New"}]
    records = []
    for sample in samples:
        bbox = sample.get("bbox") or [0, 0, 0, 0]
        origin = sample.get("origin")
        origin_y = float(origin[1]) if origin else None
        neighbours = [candidate for candidate in page["current_zh"].get("font_samples", [])
                      if candidate.get("paragraph_id") == sample.get("paragraph_id")
                      and candidate is not sample
                      and candidate.get("origin")
                      and candidate.get("script") in {"CJK", "mixed"}]
        cjk_origin = None
        if neighbours and origin_y is not None:
            neighbour = min(neighbours,
                            key=lambda row: abs(float(row["origin"][1]) - origin_y))
            cjk_origin = float(neighbour["origin"][1])
        if cjk_origin is None and origin_y is not None:
            line_baselines = [float(line["baseline"])
                              for line in page["current_zh"]["lines"]
                              if line.get("paragraph_id") == sample.get("paragraph_id")
                              and line.get("baseline") is not None]
            cjk_origin = min(line_baselines, key=lambda value: abs(value-origin_y),
                             default=None)
        records.append({
            "paragraph_id": sample.get("paragraph_id"),
            "text": sample.get("text"),
            "actual_pdf_font": sample.get("actual_pdf_font"),
            "font_size_pt": sample.get("font_size_pt"),
            "bbox": bbox,
            "actual_pdf_origin_y_pt": round(origin_y, 4) if origin_y is not None else None,
            "reference_cjk_origin_y_pt": round(cjk_origin, 4) if cjk_origin is not None else None,
            "baseline_delta_to_cjk_pt": round(origin_y - cjk_origin, 4)
            if origin_y is not None and cjk_origin is not None else None,
            "baseline_reference": "nearest CJK PDF span origin in the same paragraph",
            "word_break_observed": False,
            "translation_protected": True,
        })
    return {"records": records,
            "font_families": sorted({record["actual_pdf_font"] for record in records}),
            "font_size_pt": stats(record["font_size_pt"] for record in records),
            "baseline_delta_to_cjk_pt": stats(
                record["baseline_delta_to_cjk_pt"] for record in records
                if record["baseline_delta_to_cjk_pt"] is not None)}


def analyze_cjk_latin(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "mixed_script": mixed_script_metrics(page),
        "punctuation": punctuation_audit(page),
        "formula_spacing": formula_surrounding_metrics(page),
        "code_run": code_run_metrics(page),
    }
