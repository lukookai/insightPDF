# -*- coding: utf-8 -*-
"""Phase 3D: Atomic SVG formula rendering.

FormulaBlock -> SVG -> HTML -> PDF, page-3 focus (plus one known display
formula from p11).

SVG sources (svg_mode):
  * vector_preserved - real vector SVG via ``Page.get_svg_image(text_as_path=True)``
    (mupdf converts every glyph into a <path>; the formula region is cut out
    with a viewBox crop).
  * raster_embedded  - high-res PNG base64 wrapped in an <image> inside the SVG
    (used only when the vector export fails).
  * mixed            - the region contains raster content (image/XObject)
    alongside vector paths.

No formula semantics, no LaTeX, no MathML/KaTeX, no OCR. The formula is an
atomic visual object placed at its original bbox; inline formulas are placed
back into their text line as atomic <img> placeholders.
"""
from __future__ import annotations

import base64
import io
import json
import re
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "实现源码" / "pdf_translator"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

PDF = "runs/diag_src_2504.pdf"


def page_svg(pdf: str, page_index: int, text_as_path: bool = True) -> str:
    doc = pymupdf.open(pdf)
    pg = doc[page_index]
    svg = pg.get_svg_image(text_as_path=text_as_path)
    doc.close()
    return svg


def crop_svg(full_svg: str, bbox) -> str:
    """Cut a region out of the full-page vector SVG via viewBox.

    Keeps every <path> (and defs) untouched -- the browser shows only the
    viewBox window, so geometry stays 1:1 in PDF points.
    """
    x0, y0, x1, y1 = [float(v) for v in bbox]
    w, h = x1 - x0, y1 - y0
    # replace the root <svg ...> opening tag: keep attrs, swap viewBox/width/height
    m = re.match(r"(<svg[^>]*?)(/?>)", full_svg, re.DOTALL)
    if not m:
        raise ValueError("not an SVG document")
    head = m.group(1)
    head = re.sub(r'viewBox="[^"]*"', '', head)
    head = re.sub(r'\swidth="[^"]*"', '', head)
    head = re.sub(r'\sheight="[^"]*"', '', head)
    new_head = ('%s width="%.3f" height="%.3f" viewBox="%.3f %.3f %.3f %.3f"'
                % (head, w, h, x0, y0, w, h))
    return new_head + m.group(2) + full_svg[m.end():]


def raster_embedded_svg(pdf: str, page_index: int, bbox, zoom: float = 4.0) -> str:
    """Fallback: high-res PNG clip wrapped in an SVG <image>."""
    doc = pymupdf.open(pdf)
    pg = doc[page_index]
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                        clip=pymupdf.Rect(*bbox), alpha=False)
    doc.close()
    buf = io.BytesIO()
    pix.save(buf, format="png")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    x0, y0, x1, y1 = [float(v) for v in bbox]
    w, h = x1 - x0, y1 - y0
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%.3f" height="%.3f" '
            'viewBox="0 0 %.3f %.3f">'
            '<image x="0" y="0" width="%.3f" height="%.3f" '
            'preserveAspectRatio="none" href="data:image/png;base64,%s"/>'
            '</svg>' % (w, h, w, h, w, h, b64))


def region_has_image(pdf: str, page_index: int, bbox) -> bool:
    doc = pymupdf.open(pdf)
    pg = doc[page_index]
    x0, y0, x1, y1 = [float(v) for v in bbox]
    has = False
    for iref in pg.get_images(full=True):
        try:
            for r in pg.get_image_rects(iref[0]):
                if not (r.x1 < x0 or r.x0 > x1 or r.y1 < y0 or r.y0 > y1):
                    has = True
                    break
        except Exception:
            continue
        if has:
            break
    doc.close()
    return has


def export_formula_svg(pdf: str, page_index: int, bbox) -> dict:
    """Return {"svg": str, "svg_mode": str, "fallback_used": str}."""
    x0, y0, x1, y1 = [float(v) for v in bbox]
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return {"error": "empty bbox"}
    try:
        full = page_svg(pdf, page_index, text_as_path=True)
        svg = crop_svg(full, bbox)
        has_img = region_has_image(pdf, page_index, bbox)
        mode = "mixed" if has_img else "vector_preserved"
        return {"svg": svg, "svg_mode": mode, "fallback_used": "none"}
    except Exception as exc:  # noqa: BLE001
        svg = raster_embedded_svg(pdf, page_index, bbox)
        return {"svg": svg, "svg_mode": "raster_embedded",
                "fallback_used": "png",
                "fallback_reason": str(exc)[:200]}


def write_svg_file(svg: str, path: Path):
    path.write_text(svg, encoding="utf-8")
    return path


if __name__ == "__main__":
    # smoke test: F36 complex formula on page 3
    bbox = [306.927, 549.881, 526.219, 572.286]
    out = Path("runs/fyresults/formula_svg")
    out.mkdir(parents=True, exist_ok=True)
    res = export_formula_svg(PDF, 2, bbox)
    print("svg_mode:", res.get("svg_mode"), "| fallback:", res.get("fallback_used"))
    p = write_svg_file(res["svg"], out / "test_f36.svg")
    print("wrote", p, res["svg"][:80].replace("\n", " "))
