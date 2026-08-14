# -*- coding: utf-8 -*-
"""Page-bottom capacity reservations for translated footnotes.

A footnote is not a normal full-width flow obstacle.  Its translated height
is measured/estimated first, then the same reserved bottom is applied to both
body columns before ColumnFlow is built.
"""
from __future__ import annotations

import math
import re

from flow_layout import _est_text_width_em


TOKEN_RE = re.compile(r"\{\{[A-Z_0-9]+\}\}")


def _overlap_area(a, b):
    if not a or not b or len(a) != 4 or len(b) != 4:
        return 0.0
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * \
        max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def footnote_paragraph_ids(page_model, frontmatter):
    """Map FrontMatter footnote rows back to their paragraph owners."""
    boxes = [f.get("bbox") for f in (frontmatter or {}).get("footnotes", [])
             if f.get("bbox") and len(f.get("bbox")) == 4]
    owners = []
    for region in page_model.get("regions", []):
        if region.get("type") != "text":
            continue
        para = region["payload"]
        bbox = para.get("bbox") or region.get("bbox")
        if any(_overlap_area(bbox, fb) > 0 for fb in boxes):
            owners.append(para.get("paragraph_id"))
    return sorted(set(x for x in owners if x))


def _render_measure_text(text, para):
    """Resolve protected runs and remove zero-width style markers."""
    protected = para.get("protected_runs") or {}

    def repl(match):
        token = match.group(0)
        if token.startswith(("{{BOLD_", "{{END_BOLD_", "{{ITALIC_",
                             "{{END_ITALIC_", "{{MONO_", "{{END_MONO_")):
            return ""
        return protected.get(token, "")

    return TOKEN_RE.sub(repl, text or "")


def build_bottom_reserved_regions(page_model, translations, grid,
                                  frontmatter, typography):
    """Return BottomReservedRegion dicts using translated rendered content.

    The deterministic line measurement uses the same width model and
    typography tokens as ColumnFlow.  The reserved height therefore follows
    the translated string (including resolved CodeRuns), never the source
    footnote bbox height.
    """
    owners = footnote_paragraph_ids(page_model, frontmatter)
    if not owners or not grid:
        return []
    by_id = {r["payload"].get("paragraph_id"): r["payload"]
             for r in page_model.get("regions", [])
             if r.get("type") == "text"}
    cf = grid.get("content_frame") or {}
    width = max(float(cf.get("x1", 0)) - float(cf.get("x0", 0)), 1.0)
    font_size = float(typography["footnote_font_size"])
    line_height = float(typography["footnote_line_height"])
    total_height = 0.0
    measurements = []
    for pid in owners:
        para = by_id[pid]
        rendered = _render_measure_text(
            translations.get(pid, para.get("translation_source_text")
                             or para.get("source_text", "")), para)
        em = _est_text_width_em(rendered, font_size=font_size)
        chars_per_line = max(width / font_size, 1.0)
        lines = max(1, int(math.ceil(em / chars_per_line)))
        height = lines * line_height
        measurements.append({"paragraph_id": pid, "rendered_text": rendered,
                             "line_count": lines, "height": round(height, 3)})
        total_height += height
    y1 = float(cf.get("y1", page_model.get("height", 0)))
    y0 = y1 - total_height
    gap = float(typography.get("footnote_top_gap", 16.0))
    return [{
        "region_type": "BottomReservedRegion",
        "owner": "footnote",
        "owner_paragraph_ids": owners,
        "width_scope": "content_frame",
        "bbox": [round(float(cf.get("x0", 0)), 3), round(y0, 3),
                 round(float(cf.get("x1", page_model.get("width", 0))), 3),
                 round(y1, 3)],
        "y0": round(y0, 3), "y1": round(y1, 3),
        "height": round(total_height, 3),
        "gap": round(gap, 3),
        "body_flow_bottom": round(y0 - gap, 3),
        "measurement": "deterministic_translated_html_equivalent",
        "measurements": measurements,
    }]
