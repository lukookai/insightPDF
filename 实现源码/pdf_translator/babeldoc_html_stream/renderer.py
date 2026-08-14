from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from .table_overlay import _merge_edges, _left_band, _right_band


# ---------------------------------------------------------------------------
# Streaming HTML middleware
# ---------------------------------------------------------------------------
#
# Unlike the original scheme-1 renderer (absolute-positioned <article> overlays
# on a per-page background raster), this renderer emits a *flow* document:
# blocks are laid out in reading order using normal HTML flow (headings,
# paragraphs, captions, flow <table>).  There is no background raster and no
# absolute positioning, so the document can be progressively rendered — the
# `StreamingHTMLBuilder` returns one HTML fragment per translated block, which
# is exactly what a streaming typesetter would append to the DOM as translation
# finishes.
# ---------------------------------------------------------------------------

_SERIF = "'Noto Serif CJK SC','Source Han Serif SC','Noto Serif CJK',serif"

# Roles that carry real flow content (everything else is dropped).
_KEEP_ROLES = {
    "title",
    "heading",
    "body",
    "abandon",
    "figure_caption",
    "table_caption",
    "formula_caption",
}
# BabelDOC sentinels left behind when typesetting was skipped / a translation
# failed — never emit these as content.
_BROKEN = {"fallback_line", "plain text"}
# When typesetting is disabled, BabelDOC sometimes copies the *role name*
# (e.g. "title", "figure") into translated_text. Those are not real content.
_SENTINEL_TEXT = _BROKEN | {
    "title", "heading", "body", "figure", "figure_caption", "table_caption",
    "formula_caption", "figure_text", "table_text", "abandon", "abstract",
}


_CSS = """
*{box-sizing:border-box}
:root{--serif:'Noto Serif CJK SC','Source Han Serif SC',serif}
body{margin:0;background:#e9e9e9;font-family:var(--serif);color:#111}
#flow{max-width:820px;margin:0 auto;background:#fff;padding:46px 56px;
  box-shadow:0 2px 14px #999;font-family:var(--serif);font-size:10pt}
.page{break-after:page}
.page:last-child{break-after:auto}
h1.doc-title{text-align:center;font-weight:700;line-height:1.32;margin:0 0 18pt}
h2.doc-heading{font-weight:700;text-align:left;margin:14pt 0 6pt;line-height:1.28}
p.doc-para{text-align:justify;line-height:1.62;margin:5pt 0;overflow-wrap:anywhere}
p.figure-caption,p.table-caption{text-align:justify;line-height:1.5;
  margin:4pt 0 12pt;color:#222}
p.footnote{color:#555;line-height:1.4;margin:2pt 0}
.flow-table{border-collapse:collapse;margin:12pt auto;table-layout:fixed;
  width:100%;font-family:var(--serif);color:#111;font-size:9pt}
.flow-table td{border:1px solid #555;padding:3pt;vertical-align:middle;
  text-align:center;word-break:normal;overflow-wrap:anywhere;background:#fff}
.flow-table tr:first-child td{font-weight:700;background:#f1f1f1}
.inline-formula{display:inline-block;vertical-align:-0.2em;max-width:90%}
@media screen{.page{padding:18px 0 36px;border-bottom:1px dashed #ccc}}
@media print{#flow{box-shadow:none;margin:0;max-width:none;padding:0}
  body{background:#fff}.page:last-child{break-after:auto}}
"""


def _clean_text(value: Any) -> str:
    s = str(value or "")
    # Strip BabelDOC formula/placeholder tags that leak into translated text
    # when typesetting is disabled: <style id='1'>...</style>, <v3>, {v1} ...
    s = re.sub(r"<style\b[^>]*>.*?</style>", " ", s, flags=re.S)
    s = re.sub(r"<v\d+>", " ", s)
    s = re.sub(r"\{v\d+\}", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _is_kept_block(block: dict) -> bool:
    if block.get("role", "") not in _KEEP_ROLES:
        return False
    text = _clean_text(block.get("translated_text"))
    if not text or text in _SENTINEL_TEXT:
        return False
    # A translated body paragraph should contain Chinese; English-only body
    # fragments are leftover sentinels / untranslated labels.
    if block.get("role") == "body" and not re.search(r"[\u3400-\u9fff]", text):
        return False
    return True


def _font_size(block: dict, default: float = 9.5) -> float:
    try:
        return float(block.get("source_font_size") or default)
    except (TypeError, ValueError):
        return default


def _table_cell_display(block: dict) -> str:
    """Pick display text for a flow-table cell built from a BabelDOC block.

    Prefer the Chinese translation; fall back to the source text.
    """
    src = str(block.get("source_text") or "")
    tr = str(block.get("translated_text") or "")
    if re.search(r"[\u3400-\u9fff]", tr):
        return tr
    return src


def _union(boxes: list[list[float]]) -> list[float]:
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _cluster_table_blocks(blocks: list[dict], gap_tol: float = 12.0) -> list[dict]:
    """Group table cells into table clusters via connectivity (union-find).

    Two cells belong to the same table when their bounding boxes are within
    ``gap_tol`` in *either* the x or y direction.  A real table's rows/columns
    touch, so this keeps whole tables together even when the inter-row gap
    exceeds ``gap_tol`` (which the stricter bidirectional check would split).
    """
    n = len(blocks)
    if n == 0:
        return []
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        bi = blocks[i]["bbox"]
        for j in range(i + 1, n):
            bj = blocks[j]["bbox"]
            gap_x = max(bi[0], bj[0]) - min(bi[2], bj[2])
            gap_y = max(bi[1], bj[1]) - min(bi[3], bj[3])
            if gap_x <= gap_tol or gap_y <= gap_tol:
                union(i, j)
    groups: dict[int, list[dict]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(blocks[i])
    return [
        {"bbox": _union([c["bbox"] for c in cells]), "cells": cells}
        for cells in groups.values()
    ]


def _is_content_block(block: dict) -> bool:
    """A table_text block that actually carries recoverable text."""
    display = _table_cell_display(block)
    if not display or display in _SENTINEL_TEXT:
        return False
    return True


def _cluster_x_intervals(
    cells: list[dict], col_gap: float = 10.0
) -> list[tuple[float, float, list[dict]]]:
    """Cluster content cells into logical columns by x-interval overlap.

    BabelDOC's grid is *finer* than the real columns and its headers span
    differently from the data rows, so merging raw left/right edges produces
    spurious column boundaries.  Instead we grow column bands by *interval*
    overlap (or a small ``col_gap``).  Between real columns the gap is ~13–20pt,
    while cells belonging to the same logical column align almost exactly, so
    this collapses the fine grid into the true leaf columns — including long
    text cells such as ``LLM×MR-V2`` whose right edge would otherwise bridge two
    columns.
    """
    items = sorted(
        ((float(b["bbox"][0]), float(b["bbox"][2]), b) for b in cells),
        key=lambda t: (t[0], t[1]),
    )
    cols: list[tuple[float, float, list[dict]]] = []
    for x0, x1, b in items:
        if cols and x0 - cols[-1][1] <= col_gap:
            prev = cols[-1]
            cols[-1] = (min(prev[0], x0), max(prev[1], x1), prev[2] + [b])
        else:
            cols.append((x0, x1, [b]))
    return cols


def _col_range_for(
    cols: list[tuple[float, float, list[dict]]], x0: float, x2: float
) -> tuple[int, int]:
    """Return the (first, last) logical column index that the x-interval covers.

    A cell spans every column whose band it overlaps (with a tiny epsilon so a
    cell touching a boundary still counts).  Merged header / section cells
    therefore obtain the correct ``colspan`` for free.
    """
    first = None
    last = None
    for i, (c0, c1, _) in enumerate(cols):
        if not (x2 < c0 - 1e-6 or x0 > c1 + 1e-6):
            if first is None:
                first = i
            last = i
    if first is None:  # no overlap (degenerate) -> nearest column by centre
        cx = (x0 + x2) / 2.0
        best = 0
        bd = float("inf")
        for i, (c0, c1, _) in enumerate(cols):
            d = min(abs(cx - c0), abs(cx - c1))
            if d < bd:
                bd = d
                best = i
        return best, best
    return first, last


def _build_flow_table_from_blocks(
    blocks: list[dict], table_index: int
) -> str | None:
    """Cluster BabelDOC ``table_text`` blocks into a *flow* <table>.

    BabelDOC emits one paragraph per detected cell and its grid is *finer* than
    the real text lines — so a single text line often straddles two thin
    "sliver" cells, only one of which receives the word during source recovery.
    We therefore build the grid from **content** cells only (those that actually
    carry text, via :func:`_is_content_block`), which drops the redundant
    slivers automatically.

    Columns come from x-interval clustering (:func:`_cluster_x_intervals`),
    which yields the true leaf columns instead of the bloated edge grid.  Rows
    come from clustering the content cells' vertical *centres* — bbox edges
    would merge adjacent rows because the slivers are taller than the inter-row
    gap.  Header rows are everything above the first row that contains a digit
    (a data row); this cleanly isolates a 2-level header (top categories +
    leaf labels) from the numeric body.
    """
    content = [b for b in blocks if _is_content_block(b)]
    if len(content) < 2:
        return None
    # --- rows: cluster content-cell y centres into discrete levels ---
    ycs = sorted((b["bbox"][1] + b["bbox"][3]) / 2.0 for b in content)
    levels: list[list[float]] = []
    for y in ycs:
        if levels and y - levels[-1][-1] <= 8.0:
            levels[-1].append(y)
        else:
            levels.append([y])
    row_centers = [sum(g) / len(g) for g in levels]
    nrows = len(row_centers)
    if nrows <= 0:
        return None

    def _nearest_row(yc: float) -> int:
        best = 0
        bd = float("inf")
        for i, rc in enumerate(row_centers):
            d = abs(yc - rc)
            if d < bd:
                bd = d
                best = i
        return best

    cell_row = {id(b): _nearest_row((b["bbox"][1] + b["bbox"][3]) / 2.0) for b in content}
    # First data row = first row whose cells contain a digit.
    data_rows = {cell_row[id(b)] for b in content if re.search(r"\d", _table_cell_display(b))}
    first_data = min(data_rows) if data_rows else nrows  # no digits -> no header

    # --- columns: cluster x intervals of DATA-ROW cells only ---
    # Data cells are single numbers / short labels that never span multiple
    # logical columns, so clustering them yields the true leaf columns.  The
    # spanning header / section cells (e.g. "内容", "标准解码") are deliberately
    # excluded here and later assigned to columns purely by overlap, which gives
    # them the correct colspan without polluting the column grid.
    col_cells = [b for b in content if cell_row[id(b)] in data_rows] or content
    cols = _cluster_x_intervals(col_cells, col_gap=10.0)
    ncols = len(cols)
    if ncols <= 0:
        return None

    tw = max(1.0, cols[-1][1] - cols[0][0])
    grid: list[list[Any]] = [[None] * ncols for _ in range(nrows)]
    for b in content:
        cb = b["bbox"]
        ci, cj = _col_range_for(cols, float(cb[0]), float(cb[2]))
        ci = min(ci, ncols - 1)
        cj = min(cj, ncols - 1)
        colspan = max(1, cj - ci + 1)
        ri = cell_row[id(b)]
        rj = ri
        for i, rc in enumerate(row_centers):
            if float(cb[1]) - 2.0 <= rc <= float(cb[3]) + 2.0:
                rj = max(rj, i)
        rowspan = max(1, rj - ri + 1)
        fs = float(b.get("source_font_size") or 8.0)
        fs = max(4.0, min(fs, 11.0))
        display = _table_cell_display(b)
        if grid[ri][ci] is None or grid[ri][ci] is False:
            grid[ri][ci] = {
                "content": display,
                "fs": round(fs, 2),
                "colspan": colspan,
                "rowspan": rowspan,
            }
        for rr in range(ri, min(ri + rowspan, nrows)):
            for cc in range(ci, min(ci + colspan, ncols)):
                if not (rr == ri and cc == ci):
                    grid[rr][cc] = False
    col_pct = [
        round((cols[c][1] - cols[c][0]) / tw * 100, 2) for c in range(ncols)
    ]
    colgroup = "".join(f'<col style="width:{p}%">' for p in col_pct)
    rows_html = []
    for r in range(nrows):
        is_header = r < first_data  # rows above the first numeric row
        cells_html = []
        for c in range(ncols):
            cell = grid[r][c]
            if cell is False:
                continue
            if cell is None:
                cells_html.append('<td style="border:1px solid #555"></td>')
                continue
            style = (
                "border:1px solid #555;padding:2.5pt;vertical-align:middle;"
                "text-align:center;font-size:" + str(cell["fs"]) + "pt;line-height:1.18;"
                "word-break:break-all;overflow-wrap:anywhere;background:#fff;color:#111"
            )
            if is_header:
                style += ";font-weight:700;background:#f1f1f1"
            attrs = ""
            if cell["colspan"] > 1:
                attrs += ' colspan="' + str(cell["colspan"]) + '"'
            if cell["rowspan"] > 1:
                attrs += ' rowspan="' + str(cell["rowspan"]) + '"'
            text = html.escape(cell["content"]) if cell["content"] else "&nbsp;"
            cells_html.append("<td" + attrs + ' style="' + style + '">' + text + "</td>")
        rows_html.append("<tr>" + "".join(cells_html) + "</tr>")
    return (
        '<table class="flow-table" data-table="' + str(table_index) + '" '
        'style="width:' + str(round(tw, 1)) + 'pt;max-width:100%;border-collapse:collapse;'
        "table-layout:fixed;margin:10pt auto;font-family:var(--serif);"
        'color:#111">'
        + "<colgroup>" + colgroup + "</colgroup>"
        + "".join(rows_html) + "</table>"
    )


class StreamingHTMLBuilder:
    """Streaming-friendly HTML middleware.

    Usage (reading order, fragment by fragment)::

        b = StreamingHTMLBuilder()
        out.write(b.open_document())
        for page in pages:
            out.write(b.open_page(page.number))
            for block in page.blocks_in_reading_order:
                out.write(b.append_block(block))   # <-- per-block fragment
            out.write(b.close_page())
        out.write(b.close_document())

    Each ``append_*`` call returns a standalone HTML string, so a downstream
    consumer can insert it into the live DOM as soon as the corresponding
    translation result arrives.
    """

    def __init__(self, *, page_width: float = 595.0, page_height: float = 842.0):
        self.page_width = page_width
        self.page_height = page_height

    # -- document / page lifecycle -----------------------------------------
    def open_document(self) -> str:
        return (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            "<title>BabelDOC 流式排版 HTML 中间件</title><style>"
            + _CSS
            + "</style></head><body><main id=\"flow\">"
        )

    def open_page(self, number: int) -> str:
        return f'<section class="page" data-page="{number}">'

    def close_page(self) -> str:
        return "</section>"

    def close_document(self) -> str:
        return "</main></body></html>"

    # -- per-block fragments ------------------------------------------------
    def append_title(self, text: str, font_size: float) -> str:
        fs = max(14.0, min(font_size * 1.1, 22.0))
        return f'<h1 class="doc-title" style="font-size:{fs:.1f}pt">{html.escape(text)}</h1>'

    def append_heading(self, text: str, font_size: float) -> str:
        fs = max(11.0, min(font_size * 1.05, 16.0))
        return f'<h2 class="doc-heading" style="font-size:{fs:.1f}pt">{html.escape(text)}</h2>'

    def append_paragraph(
        self, texts: list[str], font_size: float, indent: bool = False
    ) -> str:
        fs = max(8.0, min(font_size, 12.0))
        body = " ".join(html.escape(t) for t in texts if t)
        indent_css = "text-indent:2em;" if indent else ""
        return f'<p class="doc-para" style="font-size:{fs:.1f}pt;{indent_css}">{body}</p>'

    def append_caption(self, text: str, font_size: float, kind: str = "figure") -> str:
        fs = max(7.5, min(font_size, 10.0))
        cls = "figure-caption" if kind == "figure" else "table-caption"
        return f'<p class="{cls}" style="font-size:{fs:.1f}pt">{html.escape(text)}</p>'

    def append_footnote(self, text: str, font_size: float) -> str:
        fs = max(6.5, min(font_size, 9.0))
        return f'<p class="footnote" style="font-size:{fs:.1f}pt">{html.escape(text)}</p>'

    def append_table(self, flow_table_html: str) -> str:
        return flow_table_html

    def append_block(self, block: dict) -> str:
        """Single dispatch used by a true streaming consumer."""
        role = block.get("role", "body")
        text = _clean_text(block.get("translated_text"))
        fs = _font_size(block)
        if role == "title":
            return self.append_title(text, fs)
        if role == "heading":
            return self.append_heading(text, fs)
        if role in ("figure_caption", "formula_caption"):
            return self.append_caption(text, fs, kind="figure")
        if role == "table_caption":
            return self.append_caption(text, fs, kind="table")
        if role == "abandon":
            return self.append_footnote(text, fs)
        return self.append_paragraph([text], fs, bool(block.get("first_line_indent")))


def build_streaming_html(
    document: dict,
    output: Path,
    *,
    flow_tables: dict[int, list[tuple[float, str]]] | None = None,
) -> dict:
    """Render a BabelDOC :class:`document` into a flow HTML middleware file.

    ``flow_tables`` maps page_number -> list of ``(center_y, flow_table_html)``
    produced by :func:`table_overlay.build_flow_tables`; tables are slotted into
    the reading-order flow at their vertical centre.
    """
    flow_tables = flow_tables or {}
    builder = StreamingHTMLBuilder()
    parts: list[str] = [builder.open_document()]

    # New-paragraph gap threshold in points.
    gap = 4.0
    para_buf: list[str] = []
    para_fs: float | None = None
    para_indent = False
    last_y2: float | None = None

    def flush_para() -> None:
        nonlocal para_buf, para_fs, para_indent, last_y2
        if para_buf:
            parts.append(builder.append_paragraph(para_buf, para_fs or 9.5, para_indent))
        para_buf = []
        para_fs = None
        para_indent = False
        last_y2 = None

    for page in document.get("pages", []):
        number = int(page["number"])
        parts.append(builder.open_page(number))

        text_items: list[dict] = []
        for block in page.get("blocks", []):
            if not _is_kept_block(block):
                continue
            y1 = float(block["bbox"][1])
            x1 = float(block["bbox"][0])
            y2 = float(block["bbox"][3])
            text_items.append(
                {
                    "role": block.get("role", "body"),
                    "y": y1,
                    "x": x1,
                    "y2": y2,
                    "text": _clean_text(block.get("translated_text")),
                    "fs": _font_size(block),
                    "indent": bool(block.get("first_line_indent")),
                }
            )

        # 本地从 BabelDOC 的 table_text block 聚合真矢量表格（每个单元格一个
        # block）。这样无需依赖 pdf2json 后端即可产出结构化、可选中的 <table>。
        local_tables: list[tuple[float, str]] = []
        tt_blocks = [
            b for b in page.get("blocks", []) if b.get("role") == "table_text"
        ]
        if tt_blocks:
            for cl in _cluster_table_blocks(tt_blocks, gap_tol=12.0):
                if len(cl["cells"]) < 2:
                    continue
                cy = (cl["bbox"][1] + cl["bbox"][3]) / 2.0
                htm = _build_flow_table_from_blocks(cl["cells"], len(local_tables) + 1)
                if htm:
                    local_tables.append((cy, htm))
        table_items: list[dict] = [
            {"role": "table", "y": cy, "html": html_str}
            for cy, html_str in (flow_tables.get(number, []) + local_tables)
        ]

        ordered = sorted(text_items + table_items, key=lambda it: (it["y"], it.get("x", 0)))

        for it in ordered:
            if it["role"] == "table":
                flush_para()
                parts.append(builder.append_table(it["html"]))
                last_y2 = None
                continue
            role = it["role"]
            if role == "title":
                flush_para()
                parts.append(builder.append_title(it["text"], it["fs"]))
            elif role == "heading":
                flush_para()
                parts.append(builder.append_heading(it["text"], it["fs"]))
            elif role in ("figure_caption", "formula_caption"):
                flush_para()
                parts.append(builder.append_caption(it["text"], it["fs"], kind="figure"))
            elif role == "table_caption":
                flush_para()
                parts.append(builder.append_caption(it["text"], it["fs"], kind="table"))
            elif role == "abandon":
                flush_para()
                parts.append(builder.append_footnote(it["text"], it["fs"]))
            else:  # body -> accumulate into a paragraph
                if para_buf and last_y2 is not None and (it["y"] - last_y2) > gap:
                    flush_para()
                para_buf.append(it["text"])
                para_fs = it["fs"]
                para_indent = para_indent or it["indent"]
            last_y2 = it["y2"]
        flush_para()
        parts.append(builder.close_page())

    parts.append(builder.close_document())
    source = "\n".join(parts)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(source, encoding="utf-8")
    return {"html": str(output.resolve())}


# Backwards-compatible alias so existing callers keep working.
build_babeldoc_html = build_streaming_html
