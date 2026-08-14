# -*- coding: utf-8 -*-
"""DocumentTypographyProfile (Phase 4D.2B).

Loads the Phase 4D.2A ``balanced_chinese_profile.json`` and exposes a typed,
production-facing API.  This module is the SINGLE source of truth for the
production typography tokens -- the renderer must read values through here
(and through :mod:`typography_resolver`), never re-hardcode a parallel set of
numbers.

The JSON keeps the full evidence (source / evidence_pages / sample_count /
confidence / rationale) for every token; only ``.value`` is consumed by the
resolver.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

DEFAULT_PROFILE_PATH = (
    REPO / "outputs" / "phase4d2a_typography_audit"
    / "balanced_chinese_profile.json"
)

# typography role -> profile section that carries its size/line-height tokens.
ROLE_SECTION = {
    "body": "body",
    "body_bold_lead": "body",
    "abstract_body": "body",
    "reference": "body",
    "title": "title",
    "section_heading": "section_heading",
    "subsection_heading": "subsection_heading",
    "subsubsection_heading": "subsubsection_heading",
    "caption": "caption",
    "footnote": "footnote",
    "author": "author",
    "affiliation": "affiliation",
    "list_item": "list",
    "code_run": "code_run",
    "table_header": "table",
    "table_body": "table",
}

# heading level (1/2/3) -> profile section
HEADING_SECTION = {
    1: "section_heading",
    2: "subsection_heading",
    3: "subsubsection_heading",
}


class DocumentTypographyProfile:
    """Typed view over the balanced Chinese typography profile."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else DEFAULT_PROFILE_PATH
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        if self.data.get("profile_name") != "profile_balanced_chinese":
            raise ValueError(
                "typography profile is not profile_balanced_chinese: %r"
                % self.data.get("profile_name"))

    # ---------------------------------------------------------------- low --
    def _v(self, section: str, key: str, default: Any = None) -> Any:
        node = (self.data.get(section) or {}).get(key) or {}
        return node.get("value", default)

    def section(self, section: str) -> dict:
        return self.data.get(section) or {}

    @property
    def profile_name(self) -> str:
        return self.data.get("profile_name", "")

    # ----------------------------------------------------------------- body --
    @property
    def body_font_size_pt(self) -> float:
        return float(self._v("body", "font_size_pt", 11.0175))

    @property
    def body_line_height_ratio(self) -> float:
        return float(self._v("body", "line_height_ratio", 1.2934))

    @property
    def body_line_height_pt(self) -> float:
        return self.body_font_size_pt * self.body_line_height_ratio

    @property
    def body_paragraph_gap_em(self) -> float:
        return float(self._v("body", "paragraph_gap_em", 0.42))

    @property
    def cjk_font(self) -> str:
        return str(self._v("body", "font_family_cjk", "Noto Serif SC"))

    @property
    def latin_font(self) -> str:
        return str(self._v("body", "font_family_latin", "Times New Roman"))

    @property
    def code_font(self) -> str:
        return str(self._v("code_run", "font_family", "Consolas"))

    # ------------------------------------------------------------- headings --
    def heading_ratio(self, level: int) -> float:
        section = HEADING_SECTION.get(level, "section_heading")
        return float(self._v(section, "font_size_ratio_to_body", 1.0))

    def heading_weight(self, level: int) -> int:
        section = HEADING_SECTION.get(level, "section_heading")
        return int(self._v(section, "font_weight", 650))

    def heading_line_height_ratio(self, level: int) -> float:
        section = HEADING_SECTION.get(level, "section_heading")
        return float(self._v(section, "line_height_ratio", 1.24))

    # ----------------------------------------------------------------- roles --
    def role_ratio(self, role: str) -> float:
        section = ROLE_SECTION.get(role, "body")
        return float(self._v(section, "font_size_ratio_to_body", 1.0))

    def role_weight(self, role: str) -> int:
        section = ROLE_SECTION.get(role, "body")
        return int(self._v(section, "font_weight", 400))

    def role_line_height_ratio(self, role: str) -> float:
        section = ROLE_SECTION.get(role, "body")
        return float(self._v(section, "line_height_ratio",
                             self.body_line_height_ratio))

    def role_font_size_pt(self, role: str, level: int | None = None) -> float:
        if role in ("section_heading", "subsection_heading",
                    "subsubsection_heading") and level:
            return round(self.body_font_size_pt * self.heading_ratio(level), 3)
        return round(self.body_font_size_pt * self.role_ratio(role), 3)

    # ------------------------------------------------------------------ list --
    @property
    def list_hanging_indent_em(self) -> float:
        return float(self._v("list", "hanging_indent_em", 1.25))

    @property
    def list_item_gap_em(self) -> float:
        return float(self._v("list", "item_gap_em", 0.36))

    # ----------------------------------------------------------- mixed script --
    @property
    def cjk_latin_gap_em(self) -> float:
        return float(self._v("mixed_script", "cjk_latin_gap_em", 0.12))

    @property
    def cjk_number_gap_em(self) -> float:
        return float(self._v("mixed_script", "cjk_number_gap_em", 0.12))

    @property
    def mixed_script_policy(self) -> str:
        return str(self._v("mixed_script", "implementation_policy", ""))

    # --------------------------------------------------------- formula spacing --
    @property
    def inline_formula_left_em(self) -> float:
        return float(self._v("formula_spacing", "inline_left_em", 0.04))

    @property
    def inline_formula_right_em(self) -> float:
        return float(self._v("formula_spacing", "inline_right_em", 0.0))

    @property
    def display_formula_top_em(self) -> float:
        return float(self._v("formula_spacing", "display_top_em", 0.62))

    @property
    def display_formula_bottom_em(self) -> float:
        return float(self._v("formula_spacing", "display_bottom_em", 0.55))

    # ------------------------------------------------------------- front matter --
    @property
    def title_ratio(self) -> float:
        return float(self._v("title", "font_size_ratio_to_body", 1.42))

    @property
    def title_weight(self) -> int:
        return int(self._v("title", "font_weight", 650))

    @property
    def title_line_height_ratio(self) -> float:
        return float(self._v("title", "line_height_ratio", 1.22))

    @property
    def author_ratio(self) -> float:
        return float(self._v("author", "font_size_ratio_to_body", 1.1))

    @property
    def affiliation_ratio(self) -> float:
        return float(self._v("affiliation", "font_size_ratio_to_body", 1.0))

    # ----------------------------------------------------------------- frozen --
    @property
    def frozen_geometry(self) -> dict:
        return self.data.get("frozen_geometry") or {}

    # -------------------------------------------------------------- resolved --
    def summary(self) -> dict:
        """Compact resolved-token summary for the profile_resolution_report."""
        return {
            "profile_name": self.profile_name,
            "path": str(self.path),
            "body_font_size_pt": self.body_font_size_pt,
            "body_line_height_ratio": self.body_line_height_ratio,
            "body_line_height_pt": round(self.body_line_height_pt, 4),
            "body_paragraph_gap_em": self.body_paragraph_gap_em,
            "cjk_font": self.cjk_font,
            "latin_font": self.latin_font,
            "code_font": self.code_font,
            "heading_ratios": {
                "section": self.heading_ratio(1),
                "subsection": self.heading_ratio(2),
                "subsubsection": self.heading_ratio(3),
            },
            "heading_weights": {
                "section": self.heading_weight(1),
                "subsection": self.heading_weight(2),
                "subsubsection": self.heading_weight(3),
            },
            "title_ratio": self.title_ratio,
            "caption_ratio": self.role_ratio("caption"),
            "footnote_ratio": self.role_ratio("footnote"),
            "list_hanging_indent_em": self.list_hanging_indent_em,
            "cjk_latin_gap_em": self.cjk_latin_gap_em,
            "cjk_number_gap_em": self.cjk_number_gap_em,
            "inline_formula_gap_em": [
                self.inline_formula_left_em, self.inline_formula_right_em],
            "display_formula_gap_em": [
                self.display_formula_top_em, self.display_formula_bottom_em],
        }
