# -*- coding: utf-8 -*-
"""RegionLocalPacking (visual-v04).

For a fixed-canvas page the global flow is forbidden (hard anchors stay in
place).  But inside ONE source-derived soft region, soft text blocks must
NOT share illegal y-space: when the region contains several soft blocks
(recovered prose + ordinary paragraphs), they are packed locally in reading
order using their ACTUAL block heights.

Rules (document-general, no page/filename/id specials):

  * packing happens ONLY inside the region between two hard separators
    (formula rows / obstacle bands / page bottom) -- never across them;
  * reading order is preserved (source anchor_y order);
  * soft-soft overlap = 0 by construction (cursor advances by block height);
  * hard anchor invasion = 0 (the region bottom is a hard separator);
  * region overflow -> fit unresolved -> capacity_unresolved -> BLOCK
    (never spill into the next region / next page);
  * display formulas keep flow_y == anchor_y (never displaced).

``pack_region`` takes the ordered soft blocks of one region plus the
region bounds and returns per-block {top, est_height, fit_ok}.
"""

from __future__ import annotations

from typing import Any, Dict, List

from flow_layout import estimate_paragraph_height  # noqa: E402
from fixed_canvas_fit import BASE_LINE_HEIGHT  # noqa: E402

# inter-paragraph gap inside a packed region (pt)
PACK_GAP = 3.0
# minimum top advance for a zero-height block
MIN_ADVANCE = 8.0


def pack_region(blocks: List[Dict[str, Any]], region_top: float,
                region_bottom: float, col_width: float,
                width_map: Dict[str, float] | None = None) -> Dict[str, Any]:
    """Pack soft blocks vertically inside one region.

    ``blocks``: ordered by reading order; each carries:
        paragraph_id / render_text / base_font_size / anchor_y
    Rules:
      * reading order preserved; each block starts at max(cursor, its
        SOURCE anchor_y) so source-vertical placement is respected and a
        block never moves UP beyond where it began on the source page
        (fixed-canvas philosophy: soft text adapts inside its own region,
        never displaced to an unrelated band);
      * soft-soft overlap = 0 by construction (cursor advances by the
        previous block's real height);
      * region overflow -> fit=False -> capacity_unresolved -> BLOCK.
    Returns:
        {"placed": [{paragraph_id, top, est_height, overflow}...],
         "total_height": float, "fit": bool}
    """
    width_map = width_map or {}
    cursor = region_top
    placed = []
    total_h = 0.0
    for b in blocks:
        fsize = b.get("base_font_size") or 10.0
        lscale = float(b.get("line_height_scale") or 1.0)
        est_h = estimate_paragraph_height(
            b.get("render_text") or "", fsize, col_width, width_map,
            line_height=BASE_LINE_HEIGHT * lscale)
        anchor = float(b.get("anchor_y") or region_top)
        # never move a block above its source top; pack after the cursor
        top = max(cursor, anchor)
        bottom = top + est_h
        overflow = bottom > region_bottom + 1.0
        placed.append({
            "paragraph_id": b.get("paragraph_id"),
            "flow_fragment_id": b.get("flow_fragment_id"),
            "anchor_y": anchor,
            "top": round(top, 3),
            "est_height": round(est_h, 3),
            "bottom": round(bottom, 3),
            "overflow": overflow,
            "font_scale": 1.0,
            "line_height_scale": lscale,
        })
        advance = max(est_h, MIN_ADVANCE)
        cursor = bottom + PACK_GAP
        total_h = max(total_h, bottom - region_top)
    fit = not any(p["overflow"] for p in placed)
    return {"placed": placed, "total_height": round(total_h, 3),
            "fit": fit}
