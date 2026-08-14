"""Build a locked HTML document that reproduces the original table geometry.

Phase 2A layout strategy (per spec):
  * Fixed, non-responsive page sized in PDF points:  ``@page { size: Wpt Hpt }``.
  * Each original PDF text span becomes an absolutely-positioned ``<div>`` placed
    at its exact ``content_bbox`` (left/top/width/height in pt).  No ``<table>``,
    no ``table-layout:auto``, no browser reflow, no column-width recomputation,
    no change to the model's column / table boundaries.  The original font
    family / size / weight / style are requested verbatim; only the metrics-
    compatible fallback stack is appended (never a silent size change).
  * The inferred per-column alignment is applied as CSS ``text-align``.
  * Only the rules that actually exist in the PDF are drawn.  For a sparse-rule
    / booktabs table that is the 3 horizontal lines; no vertical line is ever
    invented.  Rules are emitted as SVG ``<line>`` strokes so Chromium prints
    them as vector strokes (type "s") with the original thickness.

The model's column / row layout boundaries are preserved by construction:
every span div is placed at its original PDF bbox, so no reflow can move a
column boundary, change the table width or repaginate the table.
"""

from __future__ import annotations

import html as _html
import re

# ---- font name -> weight/style (PyMuPDF / Nimbus naming) ----
_BOLD_PAT = re.compile(r"(Medi|Bold|Black|Semi|Heavy)", re.IGNORECASE)
_ITAL_PAT = re.compile(r"(Ital|Oblique)", re.IGNORECASE)
_REG_PAT = re.compile(r"(Regu|Book)", re.IGNORECASE)


def font_weight_style(font):
    """Infer (css_weight, css_style) from an embedded PDF font name.

    E.g. ``NimbusRomNo9L-Medi`` -> (700, normal),
         ``NimbusRomNo9L-RegularItalic`` -> (400, italic).
    """
    if not font:
        return "400", "normal"
    weight = "700" if _BOLD_PAT.search(font) else "400"
    style = "italic" if _ITAL_PAT.search(font) else "normal"
    return weight, style


def _safe_font_stack(font):
    """Return a CSS font-family stack with metric-compatible fallbacks.

    The embedded PDF font (e.g. NimbusRomNo9L = a Times clone) is not exposed to
    the browser, so we append metric-identical web fonts so the substituted face
    has near-identical advance widths -- keeping long titles from ballooning.
    The original name is kept first (used if the machine happens to have it).
    """
    if not font:
        return "serif"
    base = font.split("+", 1)[-1].replace("'", "").replace('"', "")
    low = base.lower()
    stack = [base]
    if "nimbusrom" in low or "times" in low:
        stack += ["Times New Roman", "Liberation Serif", "Nimbus Roman No9 L"]
        generic = "serif"
    elif "nimbussan" in low or "helvetica" in low or "arial" in low:
        stack += ["Arial", "Helvetica", "Liberation Sans"]
        generic = "sans-serif"
    else:
        generic = "serif"
    fams = ",".join("'%s'" % s for s in stack)
    return fams + "," + generic


def build_html(model, page_w, page_h):
    cells = model["cells"]
    rules = model.get("rules", [])
    col_align = {c["index"]: c.get("alignment", "left")
                 for c in model["columns"]}

    parts = []
    for c in cells:
        align = c.get("alignment") or col_align.get(c["col"], "left")
        for sp in c["spans"]:
            b = sp["bbox"]
            x0, y0, x1, y1 = b
            w = max(x1 - x0, 0.1)
            h = max(y1 - y0, 0.1)
            size = sp.get("font_size") or 9.0
            fam = _safe_font_stack(sp.get("font"))
            weight, style = font_weight_style(sp.get("font"))
            txt = _html.escape(sp["text"] or "", quote=True)
            style_attr = (
                "position:absolute;"
                f"left:{x0:.3f}pt;top:{y0:.3f}pt;"
                f"width:{w:.3f}pt;height:{h:.3f}pt;"
                "white-space:nowrap;overflow:visible;"
                f"font-family:{fam};font-size:{size:.3f}pt;"
                f"line-height:{h:.3f}pt;"
                f"font-weight:{weight};font-style:{style};"
                f"text-align:{align};color:#000;"
            )
            parts.append(f'<div style="{style_attr}">{txt}</div>')

    # rules -- only what exists in the PDF, drawn as SVG strokes so the PDF
    # keeps vector line semantics (type "s") and the original thickness.
    for r in rules:
        if r["orientation"] == "horizontal":
            x0 = float(r["x0"])
            x1 = float(r["x1"])
            y = float(r["y"])
            th = max(float(r.get("thickness", 0.0) or 0.0), 0.05)
            w_ = x1 - x0
            parts.append(
                '<svg width="%.3fpt" height="%.3fpt" viewBox="0 0 %.3f 1" '
                'style="position:absolute;left:%.3fpt;top:%.3fpt;'
                'overflow:visible;">'
                '<line x1="0" y1="0.5" x2="%.3f" y2="0.5" stroke="#000" '
                'stroke-width="%.3f"/></svg>'
                % (w_, 1.0, w_, x0, y - 0.5, w_, th))
        else:
            y0 = float(r["y0"])
            y1 = float(r["y1"])
            x = float(r["x"])
            th = max(float(r.get("thickness", 0.0) or 0.0), 0.05)
            h_ = y1 - y0
            parts.append(
                '<svg width="%.3fpt" height="%.3fpt" viewBox="0 0 1 %.3f" '
                'style="position:absolute;left:%.3fpt;top:%.3fpt;'
                'overflow:visible;">'
                '<line x1="0.5" y1="0" x2="0.5" y2="%.3f" stroke="#000" '
                'stroke-width="%.3f"/></svg>'
                % (1.0, h_, h_, x - 0.5, y0, h_, th))

    body = "".join(parts)
    doc = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<style>"
        f"@page{{size:{page_w:.3f}pt {page_h:.3f}pt;margin:0;}}"
        "*{margin:0;padding:0;box-sizing:border-box;}"
        f"html,body{{width:{page_w:.3f}pt;height:{page_h:.3f}pt;}}"
        "svg{display:block;}"
        "</style></head><body>" + body + "</body></html>"
    )
    return doc
