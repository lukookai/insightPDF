from __future__ import annotations

import math
import re
from dataclasses import dataclass


_CJK_RANGES = (
    (0x2E80, 0x2FFF),
    (0x3000, 0x303F),
    (0x3040, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),
)


@dataclass(frozen=True)
class FitProfile:
    absolute_minimum_font: float
    minimum_font: float
    maximum_font: float
    preferred_line_height: float
    minimum_line_height: float
    default_indent: float
    usable_height_ratio: float


@dataclass(frozen=True)
class TextFit:
    font_size: float
    line_height: float
    letter_spacing: float
    text_indent: float
    estimated_lines: int
    occupancy: float
    overflow: bool


PROFILES = {
    "title": FitProfile(5.5, 7.0, 18.0, 1.24, 1.12, 0.0, 0.98),
    "image_caption": FitProfile(4.5, 5.5, 8.5, 1.25, 1.12, 0.0, 0.95),
    "page_footnote": FitProfile(4.5, 5.2, 7.5, 1.20, 1.10, 0.0, 0.96),
    "figure_text": FitProfile(2.4, 3.0, 8.0, 1.12, 1.00, 0.0, 0.86),
    "text": FitProfile(4.8, 6.2, 10.5, 1.35, 1.16, 2.0, 0.94),
}


def _is_cjk(char: str) -> bool:
    codepoint = ord(char)
    return any(start <= codepoint <= end for start, end in _CJK_RANGES)


def character_width(char: str) -> float:
    """Approximate glyph width in em for a Chinese serif body font."""
    if _is_cjk(char):
        return 1.0
    if char.isspace():
        return 0.28
    if char in ".,;:!?'\"`·，。；：！？、（）()[]{}":
        return 0.36
    if char in "ilI|":
        return 0.30
    if char in "mwMW@%&":
        return 0.88
    if char.isupper():
        return 0.64
    if char.isdigit():
        return 0.56
    if char.isascii():
        return 0.52
    return 0.80


def visual_units(text: str, letter_spacing: float = 0.0) -> float:
    visible = [char for char in text if char not in "\r\n"]
    if not visible:
        return 0.0
    return sum(character_width(char) for char in visible) + max(
        0, len(visible) - 1
    ) * letter_spacing


def estimate_lines(
    text: str,
    width: float,
    font_size: float,
    *,
    letter_spacing: float = 0.0,
    text_indent: float = 0.0,
) -> int:
    """Estimate CSS line wrapping without invoking a rendering engine."""
    # A small horizontal reserve absorbs differences between the deterministic
    # width model and the concrete CJK font selected by the PDF renderer.
    capacity = max(1.0, (width * 0.98 - 1.0) / max(font_size, 0.1))
    paragraphs = [part.strip() for part in re.split(r"[\r\n]+", text) if part.strip()]
    if not paragraphs:
        return 1
    lines = 0
    for paragraph in paragraphs:
        units = visual_units(paragraph, letter_spacing)
        first_capacity = max(1.0, capacity - text_indent)
        if units <= first_capacity:
            lines += 1
        else:
            lines += 1 + math.ceil((units - first_capacity) / capacity)
    return max(1, lines)


def _largest_fitting_font(
    text: str,
    width: float,
    height: float,
    profile: FitProfile,
    *,
    line_height: float,
    letter_spacing: float,
    text_indent: float,
) -> tuple[float, int, float, bool]:
    # Keep the semantic minimum whenever it fits. If character density or a
    # shallow OCR box makes that impossible, expand the search down to the
    # profile's absolute readability boundary.
    semantic_lines = estimate_lines(
        text,
        width,
        profile.minimum_font,
        letter_spacing=letter_spacing,
        text_indent=text_indent,
    )
    semantic_required = semantic_lines * profile.minimum_font * line_height + 1.0
    if semantic_required <= height * profile.usable_height_ratio:
        low = profile.minimum_font
    else:
        low = profile.absolute_minimum_font
    high = profile.maximum_font
    best = low
    for _ in range(26):
        size = (low + high) / 2
        lines = estimate_lines(
            text,
            width,
            size,
            letter_spacing=letter_spacing,
            text_indent=text_indent,
        )
        required = lines * size * line_height + 1.0
        usable_height = height * profile.usable_height_ratio
        if required <= usable_height:
            best = size
            low = size
        else:
            high = size
    lines = estimate_lines(
        text,
        width,
        best,
        letter_spacing=letter_spacing,
        text_indent=text_indent,
    )
    required = lines * best * line_height + 1.0
    overflow = required > height * profile.usable_height_ratio + 0.05
    occupancy = required / max(height, 1.0)
    return best, lines, occupancy, overflow


def fit_text_style(
    text: str,
    width: float,
    height: float,
    *,
    region_type: str = "text",
) -> TextFit:
    """Jointly optimize font size and spacing for one fixed PDF region.

    The score favors normal leading and spacing over gaining a small amount of
    font size through aggressive compression. Only when a region is dense will
    it tighten line height, letter spacing or first-line indentation.
    """
    profile = PROFILES.get(region_type, PROFILES["text"])
    line_heights = [
        profile.preferred_line_height,
        max(profile.minimum_line_height, profile.preferred_line_height - 0.07),
        profile.minimum_line_height,
    ]
    letter_spacings = [0.0, -0.01, -0.02]
    indents = [profile.default_indent]
    if profile.default_indent:
        indents.extend([1.0, 0.0])

    candidates: list[tuple[float, TextFit]] = []
    for line_height in line_heights:
        for letter_spacing in letter_spacings:
            for indent in indents:
                size, lines, occupancy, overflow = _largest_fitting_font(
                    text,
                    width,
                    height,
                    profile,
                    line_height=line_height,
                    letter_spacing=letter_spacing,
                    text_indent=indent,
                )
                # Readability score expressed in font-size points. Compact
                # leading has the largest penalty, then tracking and indent.
                score = (
                    size
                    - 4.0 * (profile.preferred_line_height - line_height)
                    - 18.0 * abs(letter_spacing)
                    - 0.12 * (profile.default_indent - indent)
                    - (100.0 if overflow else 0.0)
                    - 10.0 * max(0.0, occupancy - 1.0)
                )
                fit = TextFit(
                    font_size=round(size, 2),
                    line_height=round(line_height, 3),
                    letter_spacing=round(letter_spacing, 3),
                    text_indent=round(indent, 2),
                    estimated_lines=lines,
                    occupancy=round(occupancy, 3),
                    overflow=overflow,
                )
                candidates.append((score, fit))

    _, result = max(candidates, key=lambda item: item[0])
    return result
