from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any


def _union(boxes: list[list[float]]) -> list[float]:
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _bbox_gap(a: list[float], b: list[float]) -> tuple[float, float]:
    """Horizontal and vertical gap between two boxes (negative if they overlap)."""
    gap_x = max(a[0], b[0]) - min(a[2], b[2])
    gap_y = max(a[1], b[1]) - min(a[3], b[3])
    return gap_x, gap_y


def _merge_edges(values: list[float], tol: float) -> list[float]:
    """Collapse nearly-coincident edge coordinates into band boundaries."""
    values = sorted(set(round(v, 2) for v in values))
    if not values:
        return []
    merged = [values[0]]
    for v in values[1:]:
        if v - merged[-1] <= tol:
            merged[-1] = (merged[-1] + v) / 2.0
        else:
            merged.append(v)
    return merged


def _left_band(edges: list[float], x: float) -> int:
    """Band index whose left edge is the largest edge <= x (cell start)."""
    idx = 0
    for i, e in enumerate(edges):
        if e <= x + 0.5:
            idx = i
        else:
            break
    return min(idx, len(edges) - 2)


def _right_band(edges: list[float], x: float) -> int:
    """Band index whose right edge is the smallest edge >= x (cell end)."""
    for i, e in enumerate(edges):
        if e >= x - 0.5:
            return max(0, i - 1)
    return len(edges) - 2


def _looks_like_api_error(text: str) -> bool:
    markers = ("很抱歉", "请提供", "准确翻译", "缩写", "不需要翻译")
    return any(m in text for m in markers)


def _symbol_ratio(text: str) -> float:
    chars = len(text.strip())
    if chars == 0:
        return 0.0
    symbols = sum(1 for ch in text if ch in "✓✔×✗✘•●◦▪▫☑☒→←↑↓↔⇌≈±") or sum(
        1 for ch in text if not ch.isalnum() and not ch.isspace()
    )
    return symbols / chars


def _cell_display(
    source_text: str,
    translated_text: str,
    content_type: str,
) -> tuple[str, str]:
    """Return (display_text, td_background) for a cell.

    * Good Chinese translation -> opaque-ish white to mask original English.
    * Short symbol-heavy unchanged cells (e.g. checkmarks) -> transparent so
      the original alignment / bars / colours remain visible.
    * API error / untranslatable -> transparent, fall back to source text.
    * abandon_cell -> transparent by default.
    """
    if content_type == "abandon_cell":
        return source_text, "transparent"
    if _looks_like_api_error(translated_text):
        return source_text, "transparent"
    has_cjk = bool(re.search(r"[\u3400-\u9fff]", translated_text))
    text = translated_text.strip()
    unchanged = text == source_text.strip()
    # Short symbol cells: let the original PDF rendering show through.
    if not has_cjk and unchanged and len(text) < 40 and _symbol_ratio(text) > 0.35:
        return text, "transparent"
    return translated_text, "rgba(255,255,255,0.93)"


def _cell_bbox(cell):
    """Handle either a raw cell dict or a (source, translated) tuple."""
    return cell[0]["bbox"] if isinstance(cell, tuple) else cell["bbox"]


def _area(bbox: list[float]) -> float:
    return max(1.0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))


def _cluster_cells(cells: list[Any], gap_tol: float = 8.0) -> list[dict]:
    """Group nearby cell blocks into table clusters.

    Cells with wildly different areas are not merged (avoids swallowing a
    mis-classified figure caption that merely touches a real table cell).
    """
    max_area_ratio = 8.0
    clusters: list[dict] = []
    for cell in cells:
        bbox = _cell_bbox(cell)
        area = _area(bbox)
        placed = False
        for cl in clusters:
            gap_x, gap_y = _bbox_gap(bbox, cl["bbox"])
            if gap_x <= gap_tol and gap_y <= gap_tol:
                avg = sum(_area(_cell_bbox(c)) for c in cl["cells"]) / len(cl["cells"])
                if max(area, avg) / min(area, avg) <= max_area_ratio:
                    cl["cells"].append(cell)
                    cl["bbox"] = _union([cl["bbox"], bbox])
                    placed = True
                    break
        if not placed:
            clusters.append({"bbox": list(bbox), "cells": [cell]})
    # Merge clusters until stable, still respecting area ratio.
    changed = True
    while changed:
        changed = False
        merged: list[dict] = []
        for cl in clusters:
            found = False
            for existing in merged:
                gap_x, gap_y = _bbox_gap(cl["bbox"], existing["bbox"])
                if gap_x <= gap_tol and gap_y <= gap_tol:
                    cells_all = existing["cells"] + cl["cells"]
                    areas = [_area(_cell_bbox(c)) for c in cells_all]
                    if max(areas) / min(areas) <= max_area_ratio:
                        existing["cells"] = cells_all
                        existing["bbox"] = _union([existing["bbox"], cl["bbox"]])
                        found = True
                        changed = True
                        break
            if not found:
                merged.append(cl)
        clusters = merged
    return clusters


def build_table_html(
    cluster_bbox: list[float],
    cells: list[tuple[dict, dict]],
    table_index: int,
) -> str:
    """Build a positioned HTML <table> from clustered pdf2json cells.

    ``cells`` is a list of (source_cell, translated_cell) pairs.
    The coordinate space is the same top-left system used by the BabelDOC
    HTML overlay.
    """
    x0, y0, x1, y1 = (float(v) for v in cluster_bbox)
    tw = max(1.0, x1 - x0)
    th = max(1.0, y1 - y0)
    xs = _merge_edges(
        [c[0]["bbox"][0] for c in cells] + [c[0]["bbox"][2] for c in cells],
        tol=6,
    )
    ys = _merge_edges(
        [c[0]["bbox"][1] for c in cells] + [c[0]["bbox"][3] for c in cells],
        tol=6,
    )
    ncols = len(xs) - 1
    nrows = len(ys) - 1
    grid: list[list[Any]] = [[None] * ncols for _ in range(nrows)]
    for src, tr in cells:
        cb = src["bbox"]
        ci = min(_left_band(xs, cb[0]), ncols - 1)
        cj = min(_right_band(xs, cb[2]), ncols - 1)
        ri = min(_left_band(ys, cb[1]), nrows - 1)
        rj = min(_right_band(ys, cb[3]), nrows - 1)
        colspan = max(1, cj - ci + 1)
        rowspan = max(1, rj - ri + 1)
        content_type = str(src.get("content_type") or "cell")
        display, bg = _cell_display(
            str(src.get("content") or ""),
            str(tr.get("content") or ""),
            content_type,
        )
        fs = float(src.get("font_size") or 8.0)
        fs = max(4.0, min(fs, 11.0))
        if grid[ri][ci] is None or grid[ri][ci] is False:
            grid[ri][ci] = {
                "content": display,
                "bg": bg,
                "fs": round(fs, 2),
                "colspan": colspan,
                "rowspan": rowspan,
            }
        for rr in range(ri, min(ri + rowspan, nrows)):
            for cc in range(ci, min(ci + colspan, ncols)):
                if not (rr == ri and cc == ci):
                    grid[rr][cc] = False
    col_pct = [round((xs[c + 1] - xs[c]) / tw * 100, 2) for c in range(ncols)]
    row_pct = [round((ys[r + 1] - ys[r]) / th * 100, 2) for r in range(nrows)]
    colgroup = "".join(f'<col style="width:{p}%">' for p in col_pct)
    rows_html = []
    for r in range(nrows):
        cells_html = []
        for c in range(ncols):
            cell = grid[r][c]
            if cell is False:
                continue
            if cell is None:
                cells_html.append('<td style="border:1px solid #555;background:transparent"></td>')
                continue
            style = (
                f"border:1px solid #555;padding:1.5pt;vertical-align:middle;"
                f"text-align:center;font-size:{cell['fs']}pt;line-height:1.15;"
                f"word-break:normal;overflow-wrap:anywhere;"
                f"background:{cell['bg']}"
            )
            attrs = ""
            if cell["colspan"] > 1:
                attrs += f' colspan="{cell["colspan"]}"'
            if cell["rowspan"] > 1:
                attrs += f' rowspan="{cell["rowspan"]}"'
            text = html.escape(cell["content"]) if cell["content"] else "&nbsp;"
            cells_html.append("<td" + attrs + ' style="' + style + '">' + text + "</td>")
        rows_html.append(
            '<tr style="height:' + str(row_pct[r]) + '%">' + "".join(cells_html) + "</tr>"
        )
    table_inner = (
        '<table style="width:100%;height:100%;border-collapse:collapse;'
        "table-layout:fixed;font-family:'Noto Serif CJK SC','Source Han Serif SC',serif;"
        'color:#111;background:transparent">'
        "<colgroup>" + colgroup + "</colgroup>" + "".join(rows_html) + "</table>"
    )
    # The wrapper is transparent; only individual translated cells mask the
    # original English with a white background.  This preserves table images,
    # coloured bars and grid lines in cells that are unchanged symbols/numbers.
    return (
        '<div class="scheme2-table" data-table="' + str(table_index) + '" '
        'style="position:absolute;left:' + str(x0 - 1) + "pt;top:" + str(y0 - 1) + "pt;"
        "width:" + str(tw + 2) + "pt;height:" + str(th + 2) + "pt;background:transparent;z-index:3;"
        'overflow:hidden;box-sizing:border-box">'
        + table_inner
        + "</div>"
    )


def build_table_overlays(
    translated_prod_json: Path,
    source_prod_json: Path | None = None,
) -> dict[int, list[str]]:
    """Return page_number -> list of positioned <table> HTML strings.

    If ``source_prod_json`` is supplied, source text and geometry are taken from
    it while translated text is taken from ``translated_prod_json``.  When
    omitted, both are read from ``translated_prod_json``.
    """
    tr_data = json.loads(Path(translated_prod_json).read_text(encoding="utf-8"))
    src_data = (
        json.loads(Path(source_prod_json).read_text(encoding="utf-8"))
        if source_prod_json
        else tr_data
    )
    overlays: dict[int, list[str]] = {}
    for src_page, tr_page in zip(src_data.get("pages", []), tr_data.get("pages", [])):
        number = int(src_page["page_number"])
        cells: list[tuple[dict, dict]] = []
        for src_b, tr_b in zip(src_page.get("blocks", []), tr_page.get("blocks", [])):
            if src_b.get("content_type") in ("cell", "abandon_cell"):
                cells.append((src_b, tr_b))
        if len(cells) < 2:
            continue
        clusters = _cluster_cells(cells, gap_tol=8.0)
        for tindex, cl in enumerate(
            (c for c in clusters if len(c["cells"]) >= 2), 1
        ):
            overlays.setdefault(number, []).append(
                build_table_html(cl["bbox"], cl["cells"], tindex)
            )
    return overlays


def table_regions(translated_prod_json: Path) -> dict[int, list[list[float]]]:
    """Return page_number -> list of table bboxes for suppressing BabelDOC blocks."""
    data = json.loads(Path(translated_prod_json).read_text(encoding="utf-8"))
    regions: dict[int, list[list[float]]] = {}
    for page in data.get("pages", []):
        number = int(page["page_number"])
        cells = [
            b
            for b in page.get("blocks", [])
            if b.get("content_type") in ("cell", "abandon_cell")
        ]
        if len(cells) < 2:
            continue
        clusters = _cluster_cells(cells, gap_tol=8.0)
        for cl in clusters:
            if len(cl["cells"]) >= 2:
                bbox = cl["bbox"]
                regions.setdefault(number, []).append(
                    [bbox[0] - 1, bbox[1] - 1, bbox[2] + 1, bbox[3] + 1]
                )
    return regions


# ---------------------------------------------------------------------------
# Flow (<table>) builders for the streaming HTML middleware
# ---------------------------------------------------------------------------

def _flow_cell_text(source_text: str, translated_text: str, content_type: str) -> str:
    """Pick the display text for a flow-table cell.

    In flow layout there is no original PDF behind the cell, so we always show
    readable text (translated Chinese when available, otherwise the source).
    """
    if content_type == "abandon_cell":
        return source_text
    if _looks_like_api_error(translated_text):
        return source_text
    translated = (translated_text or "").strip()
    if translated:
        return translated
    return (source_text or "").strip()


def build_flow_table_html(
    cluster_bbox: list[float],
    cells: list[tuple[dict, dict]],
    table_index: int,
) -> str:
    """Build a *flow* <table> (no absolute positioning) from pdf2json cells."""
    x0, y0, x1, y1 = (float(v) for v in cluster_bbox)
    tw = max(1.0, x1 - x0)
    xs = _merge_edges(
        [c[0]["bbox"][0] for c in cells] + [c[0]["bbox"][2] for c in cells],
        tol=6,
    )
    ys = _merge_edges(
        [c[0]["bbox"][1] for c in cells] + [c[0]["bbox"][3] for c in cells],
        tol=6,
    )
    ncols = len(xs) - 1
    nrows = len(ys) - 1
    grid: list[list[Any]] = [[None] * ncols for _ in range(nrows)]
    for src, tr in cells:
        cb = src["bbox"]
        ci = min(_left_band(xs, cb[0]), ncols - 1)
        cj = min(_right_band(xs, cb[2]), ncols - 1)
        ri = min(_left_band(ys, cb[1]), nrows - 1)
        rj = min(_right_band(ys, cb[3]), nrows - 1)
        colspan = max(1, cj - ci + 1)
        rowspan = max(1, rj - ri + 1)
        content_type = str(src.get("content_type") or "cell")
        display = _flow_cell_text(
            str(src.get("content") or ""),
            str(tr.get("content") or ""),
            content_type,
        )
        fs = float(src.get("font_size") or 8.0)
        fs = max(4.0, min(fs, 11.0))
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
    col_pct = [round((xs[c + 1] - xs[c]) / tw * 100, 2) for c in range(ncols)]
    colgroup = "".join(f'<col style="width:{p}%">' for p in col_pct)
    rows_html = []
    for r in range(nrows):
        is_header = r == 0
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
        'color:#111">' + "<colgroup>" + colgroup + "</colgroup>"
        + "".join(rows_html) + "</table>"
    )


def build_flow_tables(
    translated_prod_json: Path,
    source_prod_json: Path | None = None,
) -> dict[int, list[tuple[float, str]]]:
    """Return page_number -> list of (center_y, flow_table_html).

    ``center_y`` is the vertical centre of the table in page coordinates, used
    to slot tables into the reading-order flow of the streamed document.
    """
    tr_data = json.loads(Path(translated_prod_json).read_text(encoding="utf-8"))
    src_data = (
        json.loads(Path(source_prod_json).read_text(encoding="utf-8"))
        if source_prod_json
        else tr_data
    )
    out: dict[int, list[tuple[float, str]]] = {}
    for src_page, tr_page in zip(src_data.get("pages", []), tr_data.get("pages", [])):
        number = int(src_page["page_number"])
        cells: list[tuple[dict, dict]] = []
        for src_b, tr_b in zip(src_page.get("blocks", []), tr_page.get("blocks", [])):
            if src_b.get("content_type") in ("cell", "abandon_cell"):
                cells.append((src_b, tr_b))
        if len(cells) < 2:
            continue
        clusters = _cluster_cells(cells, gap_tol=8.0)
        for cl in (c for c in clusters if len(c["cells"]) >= 2):
            cy = (cl["bbox"][1] + cl["bbox"][3]) / 2.0
            out.setdefault(number, []).append(
                (cy, build_flow_table_html(cl["bbox"], cl["cells"], len(out.get(number, [])) + 1))
            )
    return out
