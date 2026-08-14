# -*- coding: utf-8 -*-
"""Informational style-variance QA for Phase 4D.2A."""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, Iterable

try:
    from .extract_typography import stats
except ImportError:  # direct script execution
    from extract_typography import stats  # type: ignore


def _median(values: Iterable[float]) -> float | None:
    rows = [float(value) for value in values if value is not None]
    return statistics.median(rows) if rows else None


def _outliers(records: list[dict[str, Any]], key: str, *, relative: float,
              absolute: float) -> list[dict[str, Any]]:
    center = _median(record.get(key) for record in records)
    if center is None:
        return []
    limit = max(abs(center) * relative, absolute)
    return [{**record, "median": round(center, 4), "deviation": round(abs(record[key] - center), 4)}
            for record in records if record.get(key) is not None
            and abs(float(record[key]) - center) > limit]


def build_typography_consistency_qa(page_analyses: list[dict[str, Any]]) -> dict[str, Any]:
    body_sizes, line_heights, paragraph_gaps, captions, hanging = [], [], [], [], []
    mixed = []
    headings = []
    frontmatter = []

    for page in page_analyses:
        pno = page["page"]
        for record in page["spacing"]["line_height"]["records"]:
            row = {"page": pno, **record}
            if record["role"] in {"body", "body_bold_lead", "abstract_body"}:
                body_sizes.append({"page": pno, "paragraph_id": record["paragraph_id"],
                                   "font_size_pt": record["font_size_pt"]})
            line_heights.append(row)
        for record in page["spacing"]["vertical_rhythm"]["records"]:
            if record["relation"] == "paragraph_to_paragraph":
                paragraph_gaps.append({"page": pno, **record})
        for record in page["spacing"]["caption"]["records"]:
            captions.append({"page": pno, **record})
        for record in page["spacing"]["list"]["records"]:
            if record.get("hanging_indent_pt") is not None:
                hanging.append({"page": pno, **record})
        for record in page["mixed_script"]["records"]:
            mixed.append(record)
        headings.extend({"page": pno, **record}
                        for record in page["spacing"]["heading"]["records"])
        fm = page["spacing"].get("frontmatter") or {}
        if fm.get("applicable"):
            frontmatter.append({"page": pno, **fm})

    mixed_outliers = _outliers(mixed, "pdf_gap_pt", relative=1.25, absolute=2.0)
    body_outliers = _outliers(body_sizes, "font_size_pt", relative=.06, absolute=.5)
    line_outliers = _outliers(line_heights, "line_height_ratio", relative=.08, absolute=.08)
    paragraph_outliers = _outliers(paragraph_gaps, "gap_pt", relative=.70, absolute=4.0)
    caption_outliers = _outliers(captions, "font_size_pt", relative=.08, absolute=.55)
    hanging_violations = [record for record in hanging
                          if record["hanging_indent_pt"] < max(record.get("font_size_pt") or 0, 6)]

    # Heading hierarchy must be strictly descending where levels coexist.
    page_heading_sizes: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for record in headings:
        page_heading_sizes[record["page"]][record["role"]].append(record["font_size_pt"])
    hierarchy = []
    for pno, roles in page_heading_sizes.items():
        med = {role: _median(values) for role, values in roles.items()}
        if med.get("section_heading") is not None and med.get("subsection_heading") is not None \
                and med["section_heading"] <= med["subsection_heading"] + .2:
            hierarchy.append({"page": pno, "higher_role": "section_heading",
                              "lower_role": "subsection_heading", "sizes": med})
        if med.get("subsection_heading") is not None and med.get("subsubsection_heading") is not None \
                and med["subsection_heading"] <= med["subsubsection_heading"] + .2:
            hierarchy.append({"page": pno, "higher_role": "subsection_heading",
                              "lower_role": "subsubsection_heading", "sizes": med})

    fm_violations = []
    for record in frontmatter:
        for key in ("author_to_affiliation_gap_pt", "affiliation_to_abstract_heading_gap_pt"):
            value = record.get(key)
            if value is not None and (value < 4 or value > 48):
                fm_violations.append({"page": record["page"], "metric": key, "value": value})

    counts = {
        "mixed_script_gap_outlier_count": len(mixed_outliers),
        "heading_hierarchy_violation_count": len(hierarchy),
        "body_font_size_outlier_count": len(body_outliers),
        "line_height_outlier_count": len(line_outliers),
        "paragraph_gap_outlier_count": len(paragraph_outliers),
        "caption_spacing_outlier_count": len(caption_outliers),
        "list_hanging_indent_violation_count": len(hanging_violations),
        "frontmatter_spacing_violation_count": len(fm_violations),
    }
    return {
        "schema_version": "phase4d2a.typography_consistency_qa.v1",
        "gate_mode": "informational_only",
        **counts,
        "details": {
            "mixed_script_gap_outliers": mixed_outliers[:80],
            "heading_hierarchy_violations": hierarchy,
            "body_font_size_outliers": body_outliers,
            "line_height_outliers": line_outliers[:80],
            "paragraph_gap_outliers": paragraph_outliers[:80],
            "caption_spacing_outliers": caption_outliers,
            "list_hanging_indent_violations": hanging_violations,
            "frontmatter_spacing_violations": fm_violations,
        },
        "distributions": {
            "body_font_size_pt": stats(record["font_size_pt"] for record in body_sizes),
            "line_height_ratio": stats(record["line_height_ratio"] for record in line_heights),
            "paragraph_gap_pt": stats(record["gap_pt"] for record in paragraph_gaps),
            "mixed_script_gap_pt": stats(record["pdf_gap_pt"] for record in mixed),
            "list_hanging_indent_pt": stats(record["hanging_indent_pt"] for record in hanging),
        },
        "document_wide_warning_count": sum(counts.values()),
        "delivery_gate_integration": False,
    }

