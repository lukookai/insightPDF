# -*- coding: utf-8 -*-
"""Typography tokens (Phase 4D.1 + 4D.2B) -- relative hierarchy inferred from
the source PDF, overridable by the Phase 4D.2A balanced Chinese profile.

Round 1 (4D.1) delivers the source-relative hierarchy.  Phase 4D.2B adds the
balanced Chinese profile path: when ``profile`` is a
:class:`typography_profile.DocumentTypographyProfile`, the returned token dict
carries the balanced values (Noto Serif SC body, strict heading hierarchy,
caption/footnote/list/author/affiliation sizes).  The CJK family used by the
front-matter / caption / footnote CSS is supplied by the caller.

Source-ratio evidence (document profile, measured):
    body  ~10.8 pt (mode; abstract body 10.1)
    title 14.3 pt  -> title/body ratio ~1.32
    author / affiliation / abstract heading / section heading ~12.0
                     -> ~1.11
    caption ~10.1  -> ~0.94
    footnote ~9.0  -> ~0.83
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from page_layout_grid import _body_size_estimate  # noqa: E402  (reuse size mode)

# ---------------------------------------------------------------- 4D.1 tokens --
_DEFAULT_RATIOS = {
    "title": 1.32,
    "author": 1.11,
    "affiliation": 1.11,
    "abstract_heading": 1.11,
    "heading1": 1.11,
    "heading2": 1.05,
    "heading3": 1.0,
    "caption": 0.94,
    "footnote": 0.83,
}


def _balanced_tokens(profile, body_size):
    """Token dict from the Phase 4D.2A balanced Chinese profile.

    Balanced tokens are anchored to the profile's document body size (a fixed
    measured value), never to a per-page estimate -- this keeps every role
    ratio identical to the 4D.2A balanced mock.
    """
    body = profile.body_font_size_pt
    lh = profile.body_line_height_ratio
    title = body * profile.title_ratio
    author = body * profile.author_ratio
    affiliation = body * profile.affiliation_ratio
    caption = body * profile.role_ratio("caption")
    footnote = body * profile.role_ratio("footnote")
    return {
        "body_font_size": round(body, 3),
        "body_line_height": round(body * lh, 3),
        "title_font_size": round(title, 3),
        "title_line_height": round(title * profile.title_line_height_ratio, 3),
        "title_weight": profile.title_weight,
        "author_font_size": round(author, 3),
        "author_line_height": round(author * 1.28, 3),
        "affiliation_font_size": round(affiliation, 3),
        "affiliation_line_height": round(affiliation * 1.28, 3),
        "abstract_heading_font_size": round(body * 1.11, 3),
        "heading1_font_size": round(body * profile.heading_ratio(1), 3),
        "heading2_font_size": round(body * profile.heading_ratio(2), 3),
        "heading3_font_size": round(body * profile.heading_ratio(3), 3),
        "heading1_weight": profile.heading_weight(1),
        "heading2_weight": profile.heading_weight(2),
        "heading3_weight": profile.heading_weight(3),
        "caption_font_size": round(caption, 3),
        "caption_line_height": round(caption * profile.role_line_height_ratio("caption"), 3),
        "footnote_font_size": round(footnote, 3),
        "footnote_line_height": round(footnote * profile.role_line_height_ratio("footnote"), 3),
        "list_hanging_indent_em": profile.list_hanging_indent_em,
        "cjk_latin_gap_em": profile.cjk_latin_gap_em,
        "cjk_number_gap_em": profile.cjk_number_gap_em,
        "inline_formula_left_em": profile.inline_formula_left_em,
        "inline_formula_right_em": profile.inline_formula_right_em,
        "display_formula_top_em": profile.display_formula_top_em,
        "display_formula_bottom_em": profile.display_formula_bottom_em,
        # basic vertical rhythm (round 1, pt; flow keeps its own gap logic)
        "paragraph_gap": 8.0,
        "title_to_author_gap": 12.0,
        "author_to_affiliation_gap": 10.0,
        "affiliation_to_frontmatter_band_gap": 14.0,
        "heading_gap_before": 10.0,
        "heading_gap_after": 6.0,
        "display_formula_gap_before": 8.0,
        "display_formula_gap_after": 8.0,
        "figure_gap": 10.0,
        "caption_gap": 4.0,
        "footnote_top_gap": 16.0,
        "_balanced": True,
        "_profile": profile,
        "_source_ratios": {
            "title": profile.title_ratio,
            "author": profile.author_ratio,
            "affiliation": profile.affiliation_ratio,
            "heading1": profile.heading_ratio(1),
            "heading2": profile.heading_ratio(2),
            "heading3": profile.heading_ratio(3),
            "caption": profile.role_ratio("caption"),
            "footnote": profile.role_ratio("footnote"),
        },
    }


def build_typography(body_size=None, ratios=None, profile=None):
    """Typography token dict.

    ``body_size``: measured body font size (pt); defaults to 10.8.
    ``ratios``: optional overrides of the source-relative ratios.
    ``profile``: optional :class:`typography_profile.DocumentTypographyProfile`
    -> returns the balanced Chinese tokens (Phase 4D.2B).
    """
    if profile is not None:
        return _balanced_tokens(profile, body_size)

    body = body_size or 10.8
    r = dict(_DEFAULT_RATIOS)
    if ratios:
        r.update(ratios)

    def size(k):
        return round(body * r[k], 2)

    tokens = {
        "body_font_size": round(body, 2),
        "body_line_height": round(body * 1.30, 2),
        "title_font_size": size("title"),
        "title_line_height": round(size("title") * 1.32, 2),
        "title_weight": 700,
        "author_font_size": size("author"),
        "author_line_height": round(size("author") * 1.35, 2),
        "affiliation_font_size": size("affiliation"),
        "affiliation_line_height": round(size("affiliation") * 1.35, 2),
        "abstract_heading_font_size": size("abstract_heading"),
        "heading1_font_size": size("heading1"),
        "heading2_font_size": size("heading2"),
        "heading3_font_size": size("heading3"),
        "caption_font_size": size("caption"),
        "caption_line_height": round(size("caption") * 1.30, 2),
        "footnote_font_size": size("footnote"),
        "footnote_line_height": round(size("footnote") * 1.35, 2),
        "list_hanging_indent_em": 0.0,
        "cjk_latin_gap_em": 0.0,
        "cjk_number_gap_em": 0.0,
        "inline_formula_left_em": 0.0,
        "inline_formula_right_em": 0.0,
        "display_formula_top_em": 0.0,
        "display_formula_bottom_em": 0.0,
        # basic vertical rhythm (round 1, pt)
        "paragraph_gap": 8.0,
        "title_to_author_gap": 12.0,
        "author_to_affiliation_gap": 10.0,
        "affiliation_to_frontmatter_band_gap": 14.0,
        "heading_gap_before": 10.0,
        "heading_gap_after": 6.0,
        "display_formula_gap_before": 8.0,
        "display_formula_gap_after": 8.0,
        "figure_gap": 10.0,
        "caption_gap": 4.0,
        "footnote_top_gap": 16.0,
        "_balanced": False,
    }
    tokens["_source_ratios"] = r
    return tokens


# --------------------------------------------------------------- helpers --
def typography_css(tokens, cjk_family="SimSun"):
    """CSS for the front-matter / heading / caption / footnote classes."""
    abstract_lh = round(tokens["abstract_heading_font_size"] * 1.3, 2)
    return (
        ".title-block{font-family:%s,serif;font-weight:%d;text-align:center;"
        "font-size:%.2fpt;line-height:%.2fpt;}"
        ".author-block{font-family:%s,serif;text-align:center;"
        "font-size:%.2fpt;line-height:%.2fpt;}"
        ".affiliation-block{font-family:%s,serif;text-align:center;"
        "font-size:%.2fpt;line-height:%.2fpt;}"
        ".abstract-heading{font-family:%s,serif;font-weight:700;"
        "font-size:%.2fpt;line-height:%.2fpt;}"
        ".caption-block{font-family:%s,serif;font-weight:400;"
        "font-size:%.2fpt;line-height:%.2fpt;}"
        ".footnote-block{font-family:%s,serif;font-size:%.2fpt;"
        "line-height:%.2fpt;color:#000;}"
        % (cjk_family, tokens["title_weight"], tokens["title_font_size"],
           tokens["title_line_height"], cjk_family,
           tokens["author_font_size"], tokens["author_line_height"],
           cjk_family, tokens["affiliation_font_size"],
           tokens["affiliation_line_height"], cjk_family,
           tokens["abstract_heading_font_size"], abstract_lh, cjk_family,
           tokens["caption_font_size"], tokens["caption_line_height"],
           cjk_family, tokens["footnote_font_size"],
           tokens["footnote_line_height"]))
