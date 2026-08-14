# -*- coding: utf-8 -*-
"""TypographyResolver (Phase 4D.2B).

Maps a frozen PageModel paragraph (or an explicit typography role + heading
level) to concrete CSS typography tokens from the balanced Chinese profile,
and resolves the explicit CJK/Latin/Code font fallback chain.

This module only *reads* the page model; it never mutates paragraph
reconstruction, column geometry, ownership, or translation.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from typography_profile import DocumentTypographyProfile, ROLE_SECTION

# CJK font availability probe: the balanced profile requests "Noto Serif SC";
# the machine must provide the actual file or the chain falls back explicitly.
_CJK_FONT_PROBE = [
    ("Noto Serif SC", [
        r"C:\Windows\Fonts\NotoSerifSC-VF.ttf",
        r"C:\Windows\Fonts\NotoSerifSC-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSerifSC-VF.ttf",
        "/usr/share/fonts/noto-cjk/NotoSerifSC-Regular.otf",
    ]),
    ("Noto Serif CJK SC", [
        r"C:\Windows\Fonts\NotoSerifCJKsc-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    ]),
    ("Source Han Serif SC", [
        r"C:\Windows\Fonts\SourceHanSerifSC-Regular.otf",
        "/usr/share/fonts/opentype/source-han-serif/SourceHanSerifSC-Regular.otf",
    ]),
    ("SimSun", [
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\SIM SUN.TTC",
    ]),
]

_FALLBACK_CJK = ["Noto Serif SC", "Noto Serif CJK SC",
                 "Source Han Serif SC", "SimSun", "serif"]

_HEADING_LEVEL_RE = re.compile(
    r"^([A-Z]|\d+)((?:\.\d+){0,3})(?:\s|(?=[\u3400-\u9fff]))")
_MARKUP_RE = re.compile(r"\{\{(?:END_)?(?:BOLD|ITALIC|MONO)_\d+\}\}")
_OBJECT_RE = re.compile(r"\{\{(?:FORMULA|CODE)_[^}]+\}\}")


def _clean_text(text: str | None) -> str:
    value = _MARKUP_RE.sub("", text or "")
    value = _OBJECT_RE.sub("\u25a1", value)
    return re.sub(r"\s+", " ", value).strip()


def heading_level(text: str | None) -> int | None:
    """Return 1/2/3 for section/subsection/subsubsection headings."""
    value = _clean_text(text)
    match = _HEADING_LEVEL_RE.match(value)
    if not match:
        return None
    depth = match.group(2).count(".")
    return min(3, depth + 1)


class TypographyResolver:
    """Resolves typography tokens + the font fallback chain for one render."""

    def __init__(self, profile: DocumentTypographyProfile | None = None):
        self.profile = profile or DocumentTypographyProfile()
        self._probe_fonts()

    # --------------------------------------------------------------- fonts --
    def _probe_fonts(self):
        available = []
        for family, paths in _CJK_FONT_PROBE:
            if any(os.path.exists(p) for p in paths):
                available.append(family)
        self.available_cjk = available or ["SimSun"]
        requested = self.profile.cjk_font
        self.requested_cjk = requested
        # resolved = requested if it (or an alias) is actually present,
        # otherwise the first available candidate (an explicit, recorded
        # fallback -- never a silent substitution).
        self.resolved_cjk = (
            requested if requested in available
            else (available[0] if available else "SimSun"))
        self.cjk_font_file = self._font_file(self.resolved_cjk)

    @staticmethod
    def _font_file(family: str) -> str | None:
        for fam, paths in _CJK_FONT_PROBE:
            if fam == family:
                for p in paths:
                    if os.path.exists(p):
                        return p
        return None

    def body_font_family(self) -> str:
        """CSS font-family for prose.

        'Balanced Latin' (Times) -> 'Balanced CJK' (Noto ideographs) ->
        SimSun (punctuation / fullwidth, preserving the exact 4D.1C advance
        metrics) -> serif.  Keeping the punctuation in SimSun means the line
        width and break points stay identical to the 4D.1C baseline, so no
        fullwidth closing paren at a line end overflows the frozen gutter.
        """
        return "'Balanced Latin','Balanced CJK','SimSun',serif"

    def font_chain_report(self) -> dict:
        return {
            "requested_cjk": self.requested_cjk,
            "resolved_cjk": self.resolved_cjk,
            "available_cjk": self.available_cjk,
            "cjk_font_file": self.cjk_font_file,
            "explicit_fallback_used": self.resolved_cjk != self.requested_cjk,
            "css_family": self.body_font_family(),
        }

    def font_face_css(self) -> str:
        """@font-face rules: 'Balanced Latin' (Times, Latin-only) and
        'Balanced CJK' (resolved CJK file, CJK IDEOGRAPHS only).

        The unicode-range is restricted to ideographs so that punctuation and
        fullwidth forms keep their 4D.1C SimSun advance metrics (which keeps
        line breaks -- and the frozen gutter -- identical to baseline)."""
        latin = ("@font-face{font-family:'Balanced Latin';"
                 "src:local('Times New Roman');"
                 "unicode-range:U+0000-024F,U+1E00-1EFF;}")
        cjk = self.cjk_font_face()
        return latin + cjk

    def cjk_font_face(self) -> str:
        """@font-face rule pinning the resolved CJK file (never silent).

        Covers CJK ideographs / radicals / compatibility ideographs only.
        Punctuation, fullwidth forms and kana intentionally fall through to
        SimSun so their advance widths stay identical to the 4D.1C baseline.
        """
        f = self.cjk_font_file
        if not f:
            return ""
        url = Path(f).as_uri()
        return (
            "@font-face{font-family:'Balanced CJK';src:url('%s') format('truetype');"
            "font-weight:100 900;font-style:normal;font-display:block;"
            "unicode-range:U+2E80-2EFF,U+3400-4DBF,U+4E00-9FFF,U+F900-FAFF;}"
            % url)

    # ---------------------------------------------------------------- roles --
    def paragraph_role(self, payload: dict) -> tuple[str, int | None]:
        """Return (typography_role, heading_level_or_None) for a paragraph."""
        style_role = (payload.get("style_role") or "body").lower()
        # Heading level MUST be read from the SOURCE text: the translated
        # Chinese drops the "A" / "4.3" number prefix, so a heading such as
        # "A Information Bottleneck..." -> "综述生成中的信息瓶颈" would be
        # demoted from section to subsection if we used the translation.
        src = payload.get("translation_source_text") or payload.get(
            "source_text") or ""
        if style_role == "heading":
            level = heading_level(src)
            if level:
                return {1: "section_heading", 2: "subsection_heading",
                        3: "subsubsection_heading"}[level], level
            return "subsection_heading", 2
        if style_role == "list_item":
            return "list_item", None
        if style_role in ("caption", "figure_caption", "table_caption"):
            return "caption", None
        return "body", None

    def tokens_for(self, role: str, level: int | None = None,
                   body_size: float | None = None) -> dict:
        """Resolved CSS tokens for a typography role."""
        base = body_size or self.profile.body_font_size_pt
        if role in ("section_heading", "subsection_heading",
                    "subsubsection_heading") and level:
            size = base * self.profile.heading_ratio(level)
            weight = self.profile.heading_weight(level)
            lh = size * self.profile.heading_line_height_ratio(level)
        else:
            size = base * self.profile.role_ratio(role)
            weight = self.profile.role_weight(role)
            lh = size * self.profile.role_line_height_ratio(role)
        return {
            "role": role,
            "font_size": round(size, 3),
            "font_weight": weight,
            "line_height": round(lh, 3),
        }

    def paragraph_tokens(self, payload: dict,
                         body_size: float | None = None) -> dict:
        role, level = self.paragraph_role(payload)
        tokens = self.tokens_for(role, level, body_size)
        tokens["heading_level"] = level
        return tokens

    # ------------------------------------------------------------ reporting --
    def resolution_report(self) -> dict:
        return {
            "font": self.font_chain_report(),
            "profile": self.profile.summary(),
        }
