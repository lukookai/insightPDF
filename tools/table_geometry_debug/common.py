"""Shared helpers for the table-geometry diagnostic tool.

All coordinates produced by this tool are in PDF *points* (pt) with a
top-left origin and y growing downward -- the same convention PyMuPDF uses
for :class:`fitz.Rect` and the one ``build_html_document`` (in
``pdf_translator/babeldoc_html/document.py``) converts BabelDOC's IL into via
``_top_left_box``.  Keeping both parsers in this single space is what lets us
overlay and compare them directly.
"""

from __future__ import annotations

# A graphic primitive is treated as a *line* (rather than a filled/stroked
# rectangle) when it is long on one axis and thin on the other.  These
# thresholds are in pt and intentionally generous: table borders and rules are
# usually hairlines (sub-point) but we also catch slightly thicker strokes.
LINE_MAX_THICKNESS_PT = 3.0
LINE_MIN_LENGTH_PT = 4.0


def il_box_to_topleft(box, page_height: float) -> list[float]:
    """Convert a BabelDOC IL ``Box`` (PDF y-up) to a top-left pt bbox.

    Mirrors ``_top_left_box`` in ``babeldoc_html/document.py`` exactly so the
    diagnostic output lines up with what the HTML engine sees.
    """
    x0, y0, x1, y1 = float(box.x), float(box.y), float(box.x2), float(box.y2)
    return [
        round(x0, 3),
        round(page_height - y1, 3),
        round(x1, 3),
        round(page_height - y0, 3),
    ]


def classify_primitives(rects) -> tuple[list, list, list]:
    """Split a list of axis-aligned rects into (h_lines, v_lines, rectangles).

    ``rects`` is an iterable of ``(x0, y0, x1, y1)`` (top-left pt).  A rect is a
    line when one side is <= LINE_MAX_THICKNESS_PT and the other is >=
    LINE_MIN_LENGTH_PT; horizontal if it is wider than tall, vertical otherwise.
    """
    h_lines: list[dict] = []
    v_lines: list[dict] = []
    rectangles: list[dict] = []
    for (x0, y0, x1, y1) in rects:
        w = abs(x1 - x0)
        h = abs(y1 - y0)
        if max(w, h) < LINE_MIN_LENGTH_PT:
            rectangles.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1})
            continue
        if min(w, h) <= LINE_MAX_THICKNESS_PT:
            if w >= h:
                h_lines.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1})
            else:
                v_lines.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1})
        else:
            rectangles.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1})
    return h_lines, v_lines, rectangles


def empty_page_result(width: float, height: float):
    return {
        "page": {
            "width": round(float(width), 3),
            "height": round(float(height), 3),
            "coordinate_unit": "pt",
        },
        "texts": [],
        "horizontal_lines": [],
        "vertical_lines": [],
        "rectangles": [],
        "detected_tables": [],
    }
