"""Cell Renderer for Phase 2B translated mode.

Translated mode forbids "one div per source span".  Instead every *logical
cell* becomes one positioned text container placed at ``cell.layout_bbox``:

  * text-align follows the TableModel's inferred alignment;
  * the horizontal anchor is ``cell.anchor_x`` (left edge for left-aligned,
    text centre for centre-aligned) via explicit offsets -- never a reflow;
  * the vertical position is derived from the original row baseline
    (``cell.baseline``) using a CJK ascent factor, so the browser's normal
    document flow cannot move the cell;
  * the whole table geometry (columns / rows / rules / table bbox) is frozen
    by construction -- nothing in this module touches model boundaries.

Numeric / percent cells are rendered with the Latin metric-compatible stack
(Times) at the original size so their digits stay put; text cells use the
detected CJK serif stack.  ``font_size`` can be overridden per cell by the fit
policy (font-shrink step) and every override is recorded in the model.

``clip=True`` switches cell containers to ``overflow:hidden`` for the *final*
deliverable; overflow detection always happens on an unclipped render first.
"""

from __future__ import annotations

import html as _html

from render import font_weight_style, _safe_font_stack

# Vertical placement: the model's ``cell.baseline`` is the *line-box bottom*
# (PyMuPDF span bbox y1).  The div top is placed at baseline - LINE_BOX_EM *
# font_size with line-height == font_size, so the rendered line-box bottom
# (PyMuPDF bbox y1) lands back on the original baseline.  Times has a 1.0 em
# line box; SimSun measures ~0.936 em in PyMuPDF's view, so the CJK factor is
# calibrated at runtime from the first render round.
LINE_BOX_EM_CJK = 1.0
LINE_BOX_EM_LATIN = 1.0


def _cell_id(row, col):
    return "R%dC%d" % (row, col)


def _display_text(cell):
    st = cell.get("translation_status")
    if st == "translated" and cell.get("translated_text"):
        return cell["translated_text"]
    return cell.get("source_text") or ""


def build_semantic_table(model):
    """Independent semantic <table> representation of the TableModel.

    One row per logical row, one <th>/<td> per logical cell, every element
    carrying ``data-cell-id`` plus source/translated/status attributes so the
    full 105-cell structure can be recovered from the DOM with querySelector.
    This layer is display:none and takes no part in visual layout -- the
    locked visual layer above is the only thing Chromium prints, so there is
    never a double copy of text.
    """
    ncols = len(model["columns"])
    rows_sorted = sorted({c["row"] for c in model["cells"]})
    by_row = {}
    for c in model["cells"]:
        by_row.setdefault(c["row"], {})[c["col"]] = c

    def cell_attrs(cell, tag):
        st = cell.get("translation_status", "")
        attr = (
            'data-cell-id="%s" data-row="%d" data-col="%d" '
            'data-status="%s" data-source="%s"'
            % (_cell_id(cell["row"], cell["col"]), cell["row"], cell["col"],
               st, _html.escape(cell.get("source_text") or "", quote=True)))
        if st == "translated" and cell.get("translated_text"):
            attr += ' data-translated="%s"' % _html.escape(
                cell["translated_text"], quote=True)
        return "<%s %s>%s</%s>" % (
            tag, attr, _html.escape(_display_text(cell)), tag)

    out = ['<table data-semantic-table data-rows="%d" data-cols="%d">'
           % (len(rows_sorted), ncols)]
    header = [c for c in model["cells"] if c["row"] == rows_sorted[0]]
    if header:
        out.append("<thead><tr>")
        for ci in range(ncols):
            cell = header[0] if ncols == 1 else next(
                (c for c in header if c["col"] == ci), None)
            if cell:
                out.append(cell_attrs(cell, "th"))
            else:
                out.append("<th></th>")
        out.append("</tr></thead>")
    out.append("<tbody>")
    for r in rows_sorted[1:]:
        out.append("<tr>")
        cells = by_row.get(r, {})
        for ci in range(ncols):
            if ci in cells:
                out.append(cell_attrs(cells[ci], "td"))
            else:
                out.append("<td></td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _text_for_cell(cell, model):
    """Translated text (or unchanged source) plus font info."""
    st = cell.get("translation_status", "unchanged")
    if st == "translated":
        text = cell.get("translated_text") or cell.get("source_text") or ""
    else:
        text = cell.get("source_text") or ""
    is_cjk = st == "translated"
    return text, is_cjk


def build_zh_html(model, page_w, page_h, cjk_css_family, *,
                  overrides=None, clip=False, html_lang=None, cell_lang=None,
                  semantic=True):
    """One absolutely-positioned div per logical cell.

    ``overrides``: {cell_id: render_font_size_pt} applied by the fit policy.
    ``clip``: final-deliverable mode (overflow:hidden on every cell).
    ``html_lang``: value for the ``<html lang=...>`` attribute (e.g. "zh-CN").
    ``cell_lang``: per-cell ``lang=...`` attribute on translated cells.
    ``semantic``: append the display:none semantic <table> layer.
    """
    overrides = overrides or {}
    cols = {c["index"]: c for c in model["columns"]}
    parts = []
    html_lang_attr = (' lang="%s"' % html_lang) if html_lang else ""
    cell_lang_attr = (' lang="%s"' % cell_lang) if cell_lang else ""

    # rules -- only the rules that exist in the PDF (SVG strokes, like the
    # English renderer): 3 horizontal lines for this sparse-rule table, no
    # invented verticals.
    for r in model.get("rules", []):
        if r["orientation"] == "horizontal":
            x0 = float(r["x0"]); x1 = float(r["x1"]); y = float(r["y"])
            th = max(float(r.get("thickness", 0.0) or 0.0), 0.05)
            w_ = x1 - x0
            parts.append(
                '<svg width="%.3fpt" height="1.000pt" viewBox="0 0 %.3f 1" '
                'style="position:absolute;left:%.3fpt;top:%.3fpt;'
                'overflow:visible;">'
                '<line x1="0" y1="0.5" x2="%.3f" y2="0.5" stroke="#000" '
                'stroke-width="%.3f"/></svg>'
                % (w_, w_, x0, y - 0.5, w_, th))
        else:
            y0 = float(r["y0"]); y1 = float(r["y1"]); x = float(r["x"])
            th = max(float(r.get("thickness", 0.0) or 0.0), 0.05)
            h_ = y1 - y0
            parts.append(
                '<svg width="1.000pt" height="%.3fpt" viewBox="0 0 1 %.3f" '
                'style="position:absolute;left:%.3fpt;top:%.3fpt;'
                'overflow:visible;">'
                '<line x1="0.5" y1="0" x2="0.5" y2="%.3f" stroke="#000" '
                'stroke-width="%.3f"/></svg>'
                % (h_, h_, x - 0.5, y0, h_, th))

    for cell in model["cells"]:
        cid = _cell_id(cell["row"], cell["col"])
        logical_cid = str(cell.get("cell_id") or cid)
        text, is_cjk = _text_for_cell(cell, model)
        if not text:
            continue
        layout = cell["layout_bbox"]
        lx0, ly0, lx1, ly1 = layout
        col = cols.get(cell["col"], {})
        align = cell.get("alignment") or col.get("alignment", "left")
        anchor = cell.get("anchor_x")
        if anchor is None:
            anchor = col.get("anchor_x", lx0)
        baseline = cell.get("baseline")
        if baseline is None:
            baseline = ly1

        size = cell.get("render_font_size") \
            or cell.get("source_font_size") or 9.0
        size = float(overrides.get(cid, size))

        # ---- horizontal placement (anchor-preserving, no reflow) ----
        if align == "center":
            col_center = (lx0 + lx1) / 2.0
            offset = anchor - col_center          # tiny, usually ~0
            left = lx0
            width = lx1 - lx0
            text_align = "center"
            margin = f"margin-left:{offset:.3f}pt;" if abs(offset) > 0.01 else ""
        elif align == "right":
            left = lx0
            width = lx1 - lx0
            text_align = "right"
            margin = ""
        else:  # left
            left = max(anchor, lx0)
            width = lx1 - left
            text_align = "left"
            margin = ""

        # ---- vertical placement from original baseline (line-box anchor) ----
        lb = LINE_BOX_EM_CJK if is_cjk else LINE_BOX_EM_LATIN
        top = baseline - lb * size

        # ---- font ----
        fam = (cjk_css_family if is_cjk else _safe_font_stack(
            (cell.get("spans") or [{}])[0].get("font")))
        weight, style = "400", "normal"
        if not is_cjk and cell.get("spans"):
            weight, style = font_weight_style(cell["spans"][0].get("font"))

        ov = "hidden" if clip else "visible"
        style_attr = (
            "position:absolute;"
            f"left:{left:.3f}pt;top:{top:.3f}pt;"
            f"width:{max(width, 0.1):.3f}pt;height:{size:.3f}pt;"
            f"white-space:nowrap;overflow:{ov};"
            f"font-family:{fam};font-size:{size:.3f}pt;"
            f"line-height:{size:.3f}pt;"
            f"font-weight:{weight};font-style:{style};"
            f"text-align:{text_align};{margin}color:#000;"
        )
        parts.append(
            '<div class="translated-cell" data-cell="%s" data-cell-id="%s" '
            'data-row="%d" data-col="%d" data-status="%s" '
            'data-render-source="%s"%s style="%s">%s</div>'
            % (cid, _html.escape(logical_cid, quote=True), cell["row"],
               cell["col"], _html.escape(str(cell.get("translation_status")
                                              or ""), quote=True),
               _html.escape(str(cell.get("render_source") or "source_text"),
                            quote=True), cell_lang_attr, style_attr,
               _html.escape(text, quote=True)))

    body = "".join(parts)
    if semantic:
        body += '<div style="display:none" aria-hidden="true">' \
                + build_semantic_table(model) + "</div>"
    doc = (
        "<!doctype html><html%s><head><meta charset=\"utf-8\">"
        "<style>"
        f"@page{{size:{page_w:.3f}pt {page_h:.3f}pt;margin:0;}}"
        "*{margin:0;padding:0;box-sizing:border-box;}"
        f"html,body{{width:{page_w:.3f}pt;height:{page_h:.3f}pt;}}"
        ".translated-cell{overflow:visible;}"
        "</style></head><body>" + body + "</body></html>"
    )
    return doc


def collect_source_font_size(cell):
    """Median original span font size of a cell (falls back to 9.0)."""
    sizes = [float(s.get("font_size") or 0) for s in cell.get("spans", [])
             if s.get("font_size")]
    if not sizes:
        return 9.0
    sizes.sort()
    return sizes[len(sizes) // 2]
