# -*- coding: utf-8 -*-
"""Script-boundary spacing (Phase 4D.2B).

Implements the balanced ``mixed_script`` policy: a small optical gap at real
CJK <-> Latin / CJK <-> digit transitions ONLY.  The policy is explicitly
NOT ``word-spacing`` and NOT an inserted ASCII space -- it is an empty inline
span with a fixed ``em`` width, so the gap is measurable and reversible.

Exceptions (never split / never gap inside) are guaranteed by construction
because only *CJK ideographs* count as the CJK side of a boundary; every other
character -- ASCII punctuation, fullwidth parentheses and punctuation, ``%``,
``.`` ``:`` ``/`` ``@`` ``-`` ``\u00d7`` -- is NEUTRAL:

    * ``100%``          -> digit/digit/percent   (no gap)
    * ``Fig.2``         -> Latin/./digit         (no gap)
    * ``(Wang et al., 2024b)`` -> Latin inside parens (no gap, parens neutral)
    * ``LLM\u00d7MapReduce-V2`` -> Latin/\u00d7/Latin (no gap)
    * ``\u4f7f\u7528LLM\u8fdb\u884c`` -> CJK/Latin/CJK (gap on both sides)
"""
from __future__ import annotations

import re
from typing import Iterable

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"[0-9]")

# URLs / emails are kept atomic so a gap can never land inside them.
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s\u3000]+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Citation: parenthetical whose content is Latin and contains a year or
# "et al." (author-year citations) -- kept atomic.
_CITATION_RE = re.compile(
    r"[（(][^（）()]*(?:et al\.|[（(]?19|20)\d{2}[a-z]?[^（）()]*[）)]")


def _is_cjk(ch: str) -> bool:
    return bool(_CJK.match(ch))


def _is_latin(ch: str) -> bool:
    return bool(_LATIN.match(ch))


def _is_digit(ch: str) -> bool:
    return bool(_DIGIT.match(ch))


def _boundary(a: str, b: str) -> bool:
    a_cjk = _is_cjk(a)
    b_cjk = _is_cjk(b)
    a_alnum = _is_latin(a) or _is_digit(a)
    b_alnum = _is_latin(b) or _is_digit(b)
    return (a_cjk and b_alnum) or (a_alnum and b_cjk)


def _gap_span(em: float) -> str:
    return ('<span class="script-gap" '
            'style="display:inline-block;width:%.3fem"></span>' % em)


def _span_ranges(text: str, pattern) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in pattern.finditer(text)]


def _protected_ranges(text: str, protected_terms: Iterable[str]) -> list[tuple[int, int]]:
    ranges = []
    for term in protected_terms:
        start = 0
        while True:
            idx = text.find(term, start)
            if idx < 0:
                break
            ranges.append((idx, idx + len(term)))
            start = idx + len(term)
    return ranges


def insert_script_gaps(text: str, latin_gap_em: float = 0.12,
                       number_gap_em: float = 0.12) -> tuple[str, list[dict]]:
    """Return ``(html, gaps)`` where ``gaps`` records every inserted boundary.

    Gaps are inserted only around maximal ``[A-Za-z0-9]`` runs of length >= 2
    (a Latin word / identifier / multi-digit number).  A single isolated
    letter or digit (e.g. the ``1`` in ``第1层``) is NOT gapped, so the gap can
    never orphan a lone character into its own line -- this keeps the renderer
    free of ``single_char_line`` defects while still spacing real script
    transitions.

    ``gaps`` entries: ``{"pos": int, "left": str, "right": str, "kind": str}``
    with kind in ``{"cjk_latin", "cjk_number"}``.
    """
    if not text:
        return "", []
    gap_positions: dict[int, str] = {}
    for match in re.finditer(r"[A-Za-z0-9]+", text):
        start, end = match.start(), match.end()
        if end - start < 2:
            continue
        run = match.group()
        kind = "cjk_number" if _DIGIT.search(run) else "cjk_latin"
        if start > 0 and _is_cjk(text[start - 1]):
            gap_positions[start] = kind
        if end < len(text) and _is_cjk(text[end]):
            gap_positions[end] = kind
    out: list[str] = []
    gaps: list[dict] = []
    for i, ch in enumerate(text):
        if i in gap_positions:
            kind = gap_positions[i]
            em = number_gap_em if kind == "cjk_number" else latin_gap_em
            out.append(_gap_span(em))
            gaps.append({"pos": i, "left": text[i - 1] if i else "",
                         "right": ch, "kind": kind})
        out.append(ch)
    return "".join(out), gaps


def _inside(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in ranges)


def script_boundary_audit(text: str, gaps: list[dict],
                          protected_terms: Iterable[str] = ()) -> dict:
    """Measure ScriptBoundaryQA violations against the gap insertion output."""
    prot = _protected_ranges(text, protected_terms)
    urls = _span_ranges(text, _URL_RE) + _span_ranges(text, _EMAIL_RE)
    cites = _span_ranges(text, _CITATION_RE)

    illegal_space = 0
    protected_split = 0
    url_violation = 0
    citation_violation = 0
    for gap in gaps:
        pos = gap["pos"]
        # a gap span is empty -> it can never be a real space
        if _inside(pos, prot):
            protected_split += 1
        if _inside(pos, urls):
            url_violation += 1
        if _inside(pos, cites):
            citation_violation += 1
    return {
        "gap_count": len(gaps),
        "illegal_inserted_space_count": illegal_space,
        "protected_token_split_count": protected_split,
        "citation_spacing_violation": citation_violation,
        "url_spacing_violation": url_violation,
        "kind_counts": {
            "cjk_latin": sum(1 for g in gaps if g["kind"] == "cjk_latin"),
            "cjk_number": sum(1 for g in gaps if g["kind"] == "cjk_number"),
        },
    }
