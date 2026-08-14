"""Table-type classification within a detected table ROI.

IMPORTANT (correction vs. the earlier diagnostic): a table with zero vertical
lines is NOT automatically a raster table.  Booktabs / sparse-rule survey
tables use only a few horizontal rules and deliberately omit vertical rules,
while still being real, selectable PDF text.  A table is only treated as
*raster* when ALL of the following hold:

  * there are essentially no usable PDF text objects inside the ROI, AND
  * there is no usable vector structure (no h/v rules, no rectangles), AND
  * an image / XObject covers the ROI.

Only the raster class is meant to be routed to an OCR / vision fallback later.
"""

from __future__ import annotations

# A real grid needs at least this many *internal* (non-edge) vertical lines
# together with internal horizontal lines.  Sparse-rule tables have 0 internal
# vertical lines, so they are clearly distinguished.
GRID_INTERNAL_MIN = 2


def _intersect(bbox, roi, margin=2.0):
    x0, y0, x1, y1 = bbox
    rx0, ry0, rx1, ry1 = roi
    return not (x1 < rx0 - margin or x0 > rx1 + margin or
                y1 < ry0 - margin or y0 > ry1 + margin)


def _internal_lines(lines, roi, axis):
    """Lines whose spanning coordinate is strictly inside the ROI
    (i.e. not merely the table's outer edge)."""
    rx0, ry0, rx1, ry1 = roi
    out = []
    for l in lines:
        if axis == "v":
            x = (l["x0"] + l["x1"]) / 2.0
            if rx0 + 2.0 < x < rx1 - 2.0:
                out.append(l)
        else:
            y = (l["y0"] + l["y1"]) / 2.0
            if ry0 + 2.0 < y < ry1 - 2.0:
                out.append(l)
    return out


def classify_table(roi, texts_in, h_lines_in, v_lines_in, rectangles_in,
                   has_image=False):
    """Return (table_type, info_dict).

    table_type in {full_grid, sparse_rule, borderless, raster}.
    """
    n_text = len(texts_in)
    h_count = len(h_lines_in)
    v_count = len(v_lines_in)
    r_count = len(rectangles_in)

    has_text = n_text >= 5
    internal_v = _internal_lines(v_lines_in, roi, "v")
    internal_h = _internal_lines(h_lines_in, roi, "h")

    info = {
        "n_text": n_text,
        "h_lines": h_count,
        "v_lines": v_count,
        "internal_v_lines": len(internal_v),
        "internal_h_lines": len(internal_h),
        "rectangles": r_count,
        "has_image": bool(has_image),
        "has_text": has_text,
    }

    # --- raster: no usable text AND no usable structure AND image covers ROI ---
    if (not has_text and h_count == 0 and v_count == 0 and r_count == 0
            and has_image):
        info["reason"] = "no text, no vector structure, image covers ROI"
        return "raster", info
    # absolute fallback: nothing usable at all
    if not has_text and h_count == 0 and v_count == 0 and r_count == 0 \
            and not has_image:
        info["reason"] = "no text and no vector structure"
        return "raster", info

    # --- full_grid: internal vertical + horizontal rules forming a grid ---
    if (has_text and len(internal_v) >= GRID_INTERNAL_MIN
            and len(internal_h) >= GRID_INTERNAL_MIN):
        info["reason"] = ("internal v-lines>=%d and h-lines>=%d -> grid"
                          % (GRID_INTERNAL_MIN, GRID_INTERNAL_MIN))
        return "full_grid", info

    # --- sparse_rule: real text + some (few) rules, but not a full grid ---
    if has_text and (h_count > 0 or v_count > 0):
        info["reason"] = ("real text + %d h / %d v rules, not a full grid"
                          % (h_count, v_count))
        return "sparse_rule", info

    # --- borderless: real text, no rules, rely on text 2-D alignment ---
    if has_text:
        info["reason"] = "real text, no h/v rules; rely on text alignment"
        return "borderless", info

    info["reason"] = "fallthrough"
    return "raster", info
