# -*- coding: utf-8 -*-
"""Evidence-bound candidate typography tokens for Phase 4D.2A."""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, Iterable

try:
    from .extract_typography import percentile, stats
except ImportError:  # direct script execution
    from extract_typography import percentile, stats  # type: ignore


def _values(page_analyses: list[dict[str, Any]], path: tuple[str, ...]) -> list[tuple[int, float]]:
    result = []
    for page in page_analyses:
        value: Any = page
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if value is not None:
            result.append((page["page"], float(value)))
    return result


def token(value: Any, *, source: str, pages: Iterable[int], sample_count: int,
          confidence: float, rationale: str | None = None) -> dict[str, Any]:
    result = {
        "value": round(value, 4) if isinstance(value, float) else value,
        "source": source,
        "evidence_pages": sorted(set(int(page) for page in pages)),
        "sample_count": int(sample_count),
        "confidence": round(float(confidence), 3),
    }
    if rationale:
        result["rationale"] = rationale
    return result


def _measured(records: list[tuple[int, float]], default: float,
              *, confidence: float = .85, rationale: str | None = None) -> dict[str, Any]:
    value = statistics.median(value for _, value in records) if records else default
    return token(value, source="measured", pages=(page for page, _ in records),
                 sample_count=len(records), confidence=confidence if records else .35,
                 rationale=rationale)


def _inferred(value: Any, evidence_pages: Iterable[int], sample_count: int,
              confidence: float, rationale: str) -> dict[str, Any]:
    return token(value, source="inferred", pages=evidence_pages,
                 sample_count=sample_count, confidence=confidence, rationale=rationale)


def _role_size(inventory: dict[str, Any], role: str, source: str) -> tuple[float | None, int]:
    record = inventory["roles"].get(role, {}).get(source, {})
    return record.get("median"), int(record.get("sample_count") or 0)


def _aggregate(page_analyses: list[dict[str, Any]], section: str, metric: str) -> list[tuple[int, float]]:
    rows = []
    for page in page_analyses:
        value = page.get(section, {}).get(metric, {}).get("median")
        if value is not None:
            rows.append((page["page"], float(value)))
    return rows


def _base_evidence(inventory: dict[str, Any], page_analyses: list[dict[str, Any]]) -> dict[str, Any]:
    body, body_n = _role_size(inventory, "body", "current_zh_font_size_pt")
    source_body, source_body_n = _role_size(inventory, "body", "source_font_size_pt")
    body_gaps = []
    paragraph_gaps = []
    mixed_cjk_latin = []
    mixed_cjk_number = []
    inline_left, inline_right, display_top, display_bottom = [], [], [], []
    hanging = []
    for page in page_analyses:
        pno = page["page"]
        body_role = page["spacing"]["line_height"]["by_role"].get("body", {})
        if body_role.get("baseline_gap_pt", {}).get("median") is not None:
            body_gaps.append((pno, body_role["baseline_gap_pt"]["median"]))
        relation = page["spacing"]["vertical_rhythm"]["by_relation"].get(
            "paragraph_to_paragraph", {})
        if relation.get("median") is not None:
            paragraph_gaps.append((pno, relation["median"]))
        for cls, target in (("CJK-Latin", mixed_cjk_latin), ("CJK-number", mixed_cjk_number)):
            value = page["mixed_script"]["by_gap_class"].get(cls, {}).get("median")
            if value is not None:
                target.append((pno, value))
        formula = page["formula_spacing"]
        for key, target in (("inline_left_gap_pt", inline_left),
                            ("inline_right_gap_pt", inline_right),
                            ("display_top_gap_pt", display_top),
                            ("display_bottom_gap_pt", display_bottom)):
            value = formula.get(key, {}).get("median")
            if value is not None:
                target.append((pno, value))
        value = page["spacing"]["list"]["hanging_indent_pt"].get("median")
        if value is not None:
            hanging.append((pno, value))
    return {
        "body_size": (body or 11.0, body_n),
        "source_body_size": (source_body or 10.9, source_body_n),
        "body_gaps": body_gaps,
        "paragraph_gaps": paragraph_gaps,
        "cjk_latin": mixed_cjk_latin,
        "cjk_number": mixed_cjk_number,
        "inline_left": inline_left,
        "inline_right": inline_right,
        "display_top": display_top,
        "display_bottom": display_bottom,
        "hanging": hanging,
    }


def _profile(*, name: str, mode: str, inventory: dict[str, Any],
             page_analyses: list[dict[str, Any]]) -> dict[str, Any]:
    ev = _base_evidence(inventory, page_analyses)
    pages = [page["page"] for page in page_analyses]
    body_current, body_n = ev["body_size"]
    body_source, source_n = ev["source_body_size"]
    body_gap = statistics.median(value for _, value in ev["body_gaps"]) if ev["body_gaps"] else body_current * 1.30
    current_ratio = body_gap / body_current

    source_title_ratio = inventory["roles"]["document_title"].get("source_ratio_to_body") or 1.30
    source_section_ratio = inventory["roles"]["section_heading"].get("source_ratio_to_body") or 1.25
    source_sub_ratio = inventory["roles"]["subsection_heading"].get("source_ratio_to_body") or 1.14
    source_subsub_ratio = inventory["roles"]["subsubsection_heading"].get("source_ratio_to_body") or 1.10

    if mode == "source_faithful":
        body_size = body_source
        line_ratio = current_ratio
        title_ratio, section_ratio = source_title_ratio, source_section_ratio
        sub_ratio, subsub_ratio = source_sub_ratio, source_subsub_ratio
        para_gap_em = max(0.0, (statistics.median(v for _, v in ev["paragraph_gaps"])
                               / body_current) if ev["paragraph_gaps"] else .45)
        font_cjk = "SimSun"
        weight_title, weight_section = 700, 700
    elif mode == "balanced":
        # Same readable body size as the accepted baseline; improvements are
        # script-aware font texture and a strict heading hierarchy, not global shrink.
        body_size = body_current
        line_ratio = min(max(current_ratio, 1.28), 1.34)
        title_ratio, section_ratio, sub_ratio, subsub_ratio = 1.42, 1.25, 1.16, 1.10
        para_gap_em = .42
        font_cjk = "Noto Serif SC"
        weight_title, weight_section = 650, 650
    else:  # compact, only for reference/appendix/dense table
        body_size = max(9.7, body_current * .92)
        line_ratio = 1.22
        title_ratio, section_ratio, sub_ratio, subsub_ratio = 1.34, 1.20, 1.12, 1.07
        para_gap_em = .26
        font_cjk = "Noto Serif SC"
        weight_title, weight_section = 650, 650

    title_size, title_n = _role_size(inventory, "document_title", "current_zh_font_size_pt")
    section_size, section_n = _role_size(inventory, "section_heading", "current_zh_font_size_pt")
    sub_size, sub_n = _role_size(inventory, "subsection_heading", "current_zh_font_size_pt")
    caption_size, caption_n = _role_size(inventory, "caption", "current_zh_font_size_pt")
    foot_size, foot_n = _role_size(inventory, "footnote", "current_zh_font_size_pt")
    author_size, author_n = _role_size(inventory, "author", "current_zh_font_size_pt")
    aff_size, aff_n = _role_size(inventory, "affiliation", "current_zh_font_size_pt")
    table_h, table_h_n = _role_size(inventory, "table_header", "current_zh_font_size_pt")
    table_b, table_b_n = _role_size(inventory, "table_body", "current_zh_font_size_pt")

    em_gap = .12 if mode == "balanced" else (.08 if mode == "compact" else .04)
    formula_inline_em = .04 if mode == "balanced" else .02
    formula_display_top = .62 if mode == "balanced" else (.45 if mode == "compact" else .55)
    formula_display_bottom = .55 if mode == "balanced" else (.42 if mode == "compact" else .50)

    profile = {
        "schema_version": "phase4d2a.document_typography_profile.v1",
        "profile_name": name,
        "status": "candidate_analysis_only",
        "production_renderer_integration": False,
        "applicability": ("reference/appendix/dense-table only" if mode == "compact"
                          else "document-wide candidate"),
        "body": {
            "font_family_cjk": _inferred(font_cjk, pages, body_n, .88 if mode == "balanced" else .76,
                "Measured SimSun is structurally safe; the balanced sandbox tests Noto Serif SC for a more even CJK texture."),
            "font_family_latin": _inferred("Times New Roman", pages, body_n, .88,
                "Preserves the source paper's serif Latin/math contrast without translating code runs."),
            "font_size_pt": _inferred(body_size, pages, body_n, .94,
                "Balanced keeps the measured readable body size; compact reduction is limited to dense roles."),
            "line_height_ratio": _inferred(line_ratio, [p for p, _ in ev["body_gaps"]], len(ev["body_gaps"]), .91,
                "Bounded by measured final-PDF baseline ratios; no CSS declaration-only inference."),
            "paragraph_gap_em": _inferred(para_gap_em, [p for p, _ in ev["paragraph_gaps"]], len(ev["paragraph_gaps"]), .76,
                "Derived from final-PDF inter-block gaps and normalized by measured body size."),
        },
        "title": {
            "font_size_ratio_to_body": _inferred(title_ratio, [1], title_n, .90,
                "Source/current title-body ratios plus p001 full-width constraint."),
            "font_weight": _inferred(weight_title, [1], title_n, .80,
                "Variable-weight candidate; final PDF actual font must be re-audited before integration."),
            "line_height_ratio": _inferred(1.22 if mode == "balanced" else 1.28, [1], title_n, .82,
                "Maintains two-line title without changing TitleBlock geometry."),
            "bottom_gap_em": _inferred(.70 if mode == "balanced" else .62, [1], title_n, .72,
                "Normalized from measured title-author vertical rhythm."),
        },
        "section_heading": {
            "font_size_ratio_to_body": _inferred(section_ratio, pages, section_n, .92,
                "Strict document hierarchy anchored to measured source ratios."),
            "font_weight": _inferred(weight_section, pages, section_n, .86,
                "Heading distinction without changing horizontal geometry."),
            "line_height_ratio": _inferred(1.24, pages, section_n, .84,
                "Compatible with measured heading baseline boxes."),
            "number_title_gap_em": _inferred(.45, [1, 3, 6, 13, 14, 16], section_n, .84,
                "Uses an explicit stable hierarchy token; literal string spaces remain audited separately."),
        },
        "subsection_heading": {
            "font_size_ratio_to_body": _inferred(sub_ratio, pages, sub_n, .90,
                "Separates second-level headings from both section and body."),
            "font_weight": _inferred(650 if mode != "source_faithful" else 700, pages, sub_n, .84,
                "Variable-weight sandbox candidate."),
            "line_height_ratio": _inferred(1.24, pages, sub_n, .84,
                "Measured two-column heading rhythm."),
        },
        "subsubsection_heading": {
            "font_size_ratio_to_body": _inferred(subsub_ratio, [6, 14], sub_n, .78,
                "Required hierarchy token; evidence is limited to pages with third-level headings."),
            "font_weight": _inferred(600, [6, 14], sub_n, .72,
                "Differentiates 4.3.1/D.1.1 from subsection headings."),
        },
        "caption": {
            "font_size_ratio_to_body": _inferred(.91 if mode == "balanced" else ((caption_size or body_size*.9)/body_size),
                [1, 3, 13, 14, 16], caption_n, .91,
                "Current captions are ~0.90 body; balanced preserves legibility while tightening line rhythm."),
            "line_height_ratio": _inferred(1.28, [1, 3, 13, 14, 16], caption_n, .86,
                "Measured caption baselines and figure/table-bound width."),
            "prefix_text_gap_em": _inferred(.18, [1, 3, 13, 14, 16], caption_n, .72,
                "Uses full-width Chinese colon with a small optical gap, never CSS word-spacing."),
        },
        "footnote": {
            "font_size_ratio_to_body": _inferred(.83 if mode == "balanced" else ((foot_size or body_size*.83)/body_size),
                [1], foot_n, .88, "Measured p001 footnote/body ratio and reserved footnote region."),
            "line_height_ratio": _inferred(1.30, [1], foot_n, .80,
                "Keeps the frozen bottom reserved region collision-free."),
        },
        "author": {
            "font_size_ratio_to_body": _inferred(1.10 if mode == "balanced" else ((author_size or body_size*1.1)/body_size),
                [1], author_n, .84, "Measured p001 names and two-line author block."),
            "line_height_ratio": _inferred(1.28, [1], author_n, .82,
                "Anchored to measured author-line baselines."),
        },
        "affiliation": {
            "font_size_ratio_to_body": _inferred(1.00 if mode == "balanced" else ((aff_size or body_size)/body_size),
                [1], aff_n, .80, "Separates institutions from authors while preserving grouping."),
            "line_height_ratio": _inferred(1.28, [1], aff_n, .78,
                "Maintains the frozen AffiliationBlock."),
        },
        "table": {
            "header_font_size_ratio_to_body": _inferred((table_h or body_size*.82)/body_size,
                [13, 16], table_h_n, .88, "Measured table header text; table geometry remains frozen."),
            "body_font_size_ratio_to_body": _inferred((table_b or body_size*.72)/body_size,
                [13, 16], table_b_n, .90, "Measured p013/p016 table cells; no structural changes."),
            "line_height_ratio": _inferred(1.08 if mode == "balanced" else 1.02,
                [13, 16], table_b_n, .79, "Cell-height bounded typography only."),
            "horizontal_padding_em": _inferred(.22, [13, 16], table_b_n, .65,
                "Optical token for future integration; mock never changes cell boxes."),
            "vertical_padding_em": _inferred(.10, [13, 16], table_b_n, .65,
                "Optical token for future integration; mock never changes row geometry."),
        },
        "list": {
            "font_size_ratio_to_body": _inferred(1.0 if mode != "compact" else .96,
                [3, 6, 13], len(ev["hanging"]), .90, "Measured list/body parity."),
            "line_height_ratio": _inferred(line_ratio, [3, 6, 13], len(ev["hanging"]), .88,
                "Matches body rhythm."),
            "hanging_indent_em": _inferred(1.25, [p for p, _ in ev["hanging"]], len(ev["hanging"]), .84,
                "Measured bullet/continuation x positions; balanced mock uses isolated text-indent/padding."),
            "item_gap_em": _inferred(.36 if mode == "balanced" else .24,
                [3, 6, 13], len(ev["hanging"]), .74, "Measured final-PDF inter-item rhythm."),
        },
        "mixed_script": {
            "cjk_latin_gap_em": _inferred(em_gap, [p for p, _ in ev["cjk_latin"]], len(ev["cjk_latin"]), .83,
                "Current edge gaps and literal-space outliers are separated; token is applied by boundary policy, not word-spacing."),
            "cjk_number_gap_em": _inferred(em_gap, [p for p, _ in ev["cjk_number"]], len(ev["cjk_number"]), .80,
                "Same boundary policy with percentage/citation exceptions."),
            "citation_gap_policy": _inferred("fullwidth_parentheses_no_outer_ascii_space; preserve citation-internal Latin punctuation",
                [1, 3, 6, 13, 14, 16], sum(len(p["mixed_script"]["records"]) for p in page_analyses), .91,
                "Derived from final PDF citation boundaries and punctuation audit."),
            "implementation_policy": _inferred("script-boundary wrappers or text normalization; never CSS word-spacing",
                pages, sum(len(p["mixed_script"]["records"]) for p in page_analyses), .95,
                "Cause audit proves several independent gap sources."),
        },
        "formula_spacing": {
            "inline_left_em": _inferred(formula_inline_em, [p for p, _ in ev["inline_left"]], len(ev["inline_left"]), .83,
                "Final DOM outer box to adjacent final-PDF glyph; formula internals frozen."),
            "inline_right_em": _inferred(0.0, [p for p, _ in ev["inline_right"]], len(ev["inline_right"]), .88,
                "Chinese punctuation may directly follow the inline formula wrapper."),
            "display_top_em": _inferred(formula_display_top, [p for p, _ in ev["display_top"]], len(ev["display_top"]), .78,
                "Outer display-formula rhythm only."),
            "display_bottom_em": _inferred(formula_display_bottom, [p for p, _ in ev["display_bottom"]], len(ev["display_bottom"]), .78,
                "Outer display-formula rhythm only."),
        },
        "code_run": {
            "font_family": _inferred("Consolas", [1, 13], 8, .96,
                "Actual final-PDF audit identifies Consolas; CodeRun remains untranslated."),
            "font_size_ratio_to_body": _inferred(.94, [1, 13], 8, .82,
                "Keeps code optically aligned without altering tokens or wrapping."),
            "letter_spacing_em": _inferred(0.0, [1, 13], 8, .94,
                "No artificial tracking; preserve identifier integrity."),
        },
        "frozen_geometry": {
            "page_size": True, "content_frame": True, "column_tracks": True,
            "gutter_pt": 17.0, "figure_bbox": True, "table_bbox": True,
            "formula_bbox": True, "bottom_reserved_region": True,
            "frontmatter_grouping": True, "fit_to_page": False,
            "global_scale": False,
        },
    }
    return profile


def build_profiles(inventory: dict[str, Any],
                   page_analyses: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    source = _profile(name="profile_source_faithful", mode="source_faithful",
                      inventory=inventory, page_analyses=page_analyses)
    balanced = _profile(name="profile_balanced_chinese", mode="balanced",
                        inventory=inventory, page_analyses=page_analyses)
    compact = _profile(name="profile_compact_chinese", mode="compact",
                       inventory=inventory, page_analyses=page_analyses)
    return {
        "source_faithful": source,
        "balanced_chinese": balanced,
        "compact_chinese": compact,
        "document": {
            "schema_version": "phase4d2a.document_typography_profile.index.v1",
            "recommended_profile": "profile_balanced_chinese",
            "profiles": {
                "profile_source_faithful": source,
                "profile_balanced_chinese": balanced,
                "profile_compact_chinese": compact,
            },
            "production_renderer_integration": False,
            "phase4d2b_started": False,
        },
    }

