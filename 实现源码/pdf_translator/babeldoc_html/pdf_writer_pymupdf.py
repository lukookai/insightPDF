"""Pixel-aligned PDF writer (PyMuPDF + Story, no GTK/Chromium needed).

Renders a *mono*-style translated PDF that mirrors pdf2zh / BabelDOC-native
output:

1. The source page is redacted so only the *translated* text regions have their
   original (source-language) glyphs removed -- vector borders, rules and
   figures are preserved (``graphics=LINE_ART_NONE``, ``images=IMAGE_NONE``).
2. The redacted source page is kept as a *native vector* background PDF (small,
   crisp -- same intent as pdf2zh's mono background, not a raster).
3. Translated text is written by MuPDF's ``Story``/``DocumentWriter`` at the
   exact IL ``bbox`` coordinates, using a CJK font (Noto Serif SC, the same
   design as Source Han Serif CN used by pdf2zh). ``Story`` correctly embeds
   the glyphs as a CID font, so the output is selectable and printable.
4. Text layer is composited over the background via ``show_pdf_page``.

This works for any ``source_pdf`` + translated ``document`` (the HTML engine
contract), making it a universal, GTK-free PDF renderer.
"""

from __future__ import annotations

import html
import os
import re
import pymupdf
from pathlib import Path

from pdf_translator.layout.typography import fit_text_style, estimate_lines

# Noto Serif SC == Source Han Serif CN, i.e. the typeface used by pdf2zh.
CJK_FONT = Path(r"C:\Windows\Fonts\NotoSerifSC-VF.ttf").as_posix()
_CJK_FONT = pymupdf.Font(fontfile=CJK_FONT)

_PROFILE_BY_ROLE = {
    "title": "title",
    "heading": "title",
    "figure_caption": "image_caption",
    "table_caption": "image_caption",
    "formula_caption": "image_caption",
    "figure_text": "figure_text",
    "table_text": "figure_text",
    "abandon": "page_footnote",
}

_CENTER_ROLES = {
    "title",
    "figure_caption",
    "table_caption",
    "formula_caption",
}


def _build_background_pdf(
    source_pdf: Path, pages: list[dict], out_path: Path
) -> None:
    """Build a *vector* background PDF.

    For each translated page we open the matching source page, add redaction
    annotations over the translated-text regions, then ``apply_redactions`` with
    ``text=REMOVE`` but ``graphics=LINE_ART_NONE`` / ``images=IMAGE_NONE`` so
    the original glyphs vanish while vector borders, rules and figures are
    preserved.  The result is saved as a native-vector PDF (small, crisp) --
    identical intent to pdf2zh's mono background, instead of a 150-DPI raster.
    """
    src = pymupdf.open(source_pdf)
    out = pymupdf.open()
    needed = sorted({int(p["number"]) - 1 for p in pages})
    for idx in needed:
        W = float(src[idx].rect.width)
        H = float(src[idx].rect.height)
        sp = src[idx]
        redacted_any = False
        # Find the IL page that maps to this source page to get block bboxes.
        il_page = next(
            (p for p in pages if int(p["number"]) - 1 == idx), None
        )
        if il_page is not None:
            for b in il_page.get("blocks", []):
                if not b.get("translate"):
                    continue
                pad = 0.35
                r = pymupdf.Rect(*[float(v) for v in b["bbox"]])
                r = pymupdf.Rect(
                    max(0.0, r.x0 - pad),
                    max(0.0, r.y0 - pad),
                    min(W, r.x1 + pad),
                    min(H, r.y1 + pad),
                )
                sp.add_redact_annot(r, fill=False)
                redacted_any = True
        if redacted_any:
            sp.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
        out.insert_pdf(src, from_page=idx, to_page=idx)
    out.save(str(out_path), garbage=4, clean=True)
    out.close()
    src.close()


def _plain_text(block: dict) -> str:
    """Return cleaned translated text, stripping backend markup like <style>."""
    text = (block.get("translated_text") or "").strip()
    # The DeepSeek/LawAI backend occasionally wraps phrases in
    # <style id='N'>...</style>; remove them for the PDF text layer.
    text = re.sub(r"<style[^>]*>|</style>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _block_style(block: dict) -> tuple[float, float, float, str, bool, bool]:
    """Return (font_size, line_height, letter_spacing, text-align, bold, single_line)."""
    text = _plain_text(block)
    x0, y0, x1, y1 = [float(v) for v in block["bbox"]]
    width_pt = max(1.0, x1 - x0)
    height_pt = max(1.0, y1 - y0)
    role = block.get("role", "body")
    profile = _PROFILE_BY_ROLE.get(role, "text")
    fit = fit_text_style(text, width_pt, height_pt, region_type=profile)
    source_size = float(block.get("source_font_size") or fit.font_size)

    if role in {"table_text", "figure_text"}:
        # pdf2zh native renders translated table/figure text *in place* at the
        # source font size -- CJK glyphs overflow narrow cells by design. Match
        # that pixel-for-pixel instead of reflowing/shrinking (which is what the
        # height-constrained typography profile would otherwise force).
        fs = round(source_size, 2)
        est = estimate_lines(text, width_pt, fs, letter_spacing=0.0)
        single = est <= 1
    else:
        cap = 1.08 if role == "title" else 1.04
        fs = round(min(fit.font_size, source_size * cap), 2)
        single = (fit.estimated_lines or 1) <= 1 or role in {"title"}
        # For genuine single-line body/title spans, force the text to fit
        # horizontally so it doesn't spill out of the block.
        if single and text:
            pad = 1.0
            max_w = max(1.0, width_pt - 2 * pad)
            while fs > 4.0 and _CJK_FONT.text_length(text, fontsize=fs) > max_w:
                fs = round(fs - 0.5, 2)
    align = "center" if role in _CENTER_ROLES else "left"
    bold = role in {"title", "heading"}
    return fs, fit.line_height, fit.letter_spacing, align, bold, single


def _block_story(
    block: dict, page_width: float, page_height: float, base_css: str
) -> tuple[str | None, pymupdf.Rect]:
    """Return (html_src, placement_rect) for one translated block.

    The document ``bbox`` is already in PDF y-up coordinates:
    ``[x_left, y_top, x_right, y_bottom]``.  No flip is needed.

    The returned rect deliberately gives Story more room than the bare cell:
    Noto Serif CJK's vertical metrics are ~1.44x the point size, so a
    line-box-=font-size rect would be *smaller* than the glyphs and Story would
    silently refuse to place the line.  We also widen the rect for single-line
    cells so nowrap text overflows (pdf2zh's natural look) instead of clipping.
    ``render_pdf`` retries placement with progressively larger rects until Story
    commits the content, so exact padding here is not critical.
    """
    text = _plain_text(block)
    if not text:
        return None, pymupdf.Rect(0, 0, 0, 0)
    role = block.get("role", "body")
    fs, lh, ls, align, bold, single = _block_style(block)
    x0, y0, x1, y1 = [float(v) for v in block["bbox"]]
    rect_pdf = pymupdf.Rect(x0, y0, x1, y1)
    line_h = fs * lh
    mid = (rect_pdf.y0 + rect_pdf.y1) / 2.0
    pad_v = fs * 0.5  # comfortably exceed the 1.44x glyph box
    ry0 = mid - line_h / 2.0 - pad_v
    ry1 = mid + line_h / 2.0 + pad_v
    if single:
        text_w = _CJK_FONT.text_length(text, fontsize=fs)
        need_w = text_w + 2.0
        cx0 = (rect_pdf.x0 + rect_pdf.x1) / 2.0
        if need_w > (rect_pdf.x1 - rect_pdf.x0):
            rx0 = cx0 - need_w / 2.0
            rx1 = cx0 + need_w / 2.0
        else:
            rx0 = rect_pdf.x0 - 4.0
            rx1 = rect_pdf.x1 + 4.0
        rx0 = max(0.0, rx0)
        rx1 = min(page_width, rx1)
        wrap = "white-space:nowrap;overflow:visible;"
    else:
        rx0 = rect_pdf.x0
        rx1 = rect_pdf.x1
        wrap = "word-break:break-all;overflow:visible;"
    rect_pdf = pymupdf.Rect(rx0, ry0, rx1, ry1)
    style = (
        f"font-size:{fs}pt;"
        f"line-height:{lh};"
        f"letter-spacing:{ls}em;"
        f"text-align:{align};"
        f"font-weight:{'bold' if bold else 'normal'};"
        f"margin:0;padding:0;{wrap}"
    )
    html_src = f'<p style="{style}">{html.escape(text)}</p>'
    return html_src, rect_pdf


def _place_story(html_src: str, rect: pymupdf.Rect, dev, page_w: float, page_h: float, base_css: str) -> None:
    """Place + draw a Story, enlarging the rect until Story commits it.

    Story's placement is surprisingly finicky: if the area is only slightly
    smaller than the glyph box it returns MORE_DATA and emits nothing.  We
    recreate the Story (placement is stateful) and retry with a larger rect,
    expanding symmetrically around the centre and clamping to the page.
    """
    story = pymupdf.Story(html_src, user_css=base_css)
    more = story.place(rect)
    tries = 0
    while more[0] != 0 and tries < 10:
        cx = (rect.x0 + rect.x1) / 2.0
        cy = (rect.y0 + rect.y1) / 2.0
        hw = (rect.x1 - rect.x0) / 2.0 + 4.0
        hh = (rect.y1 - rect.y0) / 2.0 + 6.0
        rect = pymupdf.Rect(
            max(0.0, cx - hw), max(0.0, cy - hh),
            min(page_w, cx + hw), min(page_h, cy + hh),
        )
        story = pymupdf.Story(html_src, user_css=base_css)
        more = story.place(rect)
        tries += 1
    story.draw(dev)


def render_pdf(
    document: dict,
    source_pdf: Path,
    output_pdf: Path,
    *,
    dpi: int = 150,
) -> dict:
    """Render a pixel-aligned translated PDF from a translated ``document``."""
    source_pdf = Path(source_pdf).resolve()
    output_pdf = Path(output_pdf)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    pages = document.get("pages", [])

    base_css = (
        f'@font-face {{ font-family: CJK; src: url("{CJK_FONT}"); }}\n'
        "p { font-family: CJK, serif; margin: 0; padding: 0; white-space: pre-wrap; }\n"
    )

    tmp_text = output_pdf.with_suffix(".tmp-text.pdf")
    tmp_bg = output_pdf.with_suffix(".tmp-bg.pdf")

    # ---- text layer via Story/DocumentWriter (correct CID CJK embedding) ----
    text_writer = pymupdf.DocumentWriter(str(tmp_text))
    for p in pages:
        W = float(p["width"])
        H = float(p["height"])
        dev = text_writer.begin_page(pymupdf.Rect(0, 0, W, H))
        for b in p.get("blocks", []):
            if not b.get("translate"):
                continue
            html_src, rect = _block_story(b, W, H, base_css)
            if html_src is None:
                continue
            _place_story(html_src, rect, dev, W, H, base_css)
        text_writer.end_page()
    text_writer.close()

    # ---- background layer (redacted source page, kept as VECTOR) ----
    _build_background_pdf(source_pdf, pages, tmp_bg)

    # ---- composite text over background ----
    text_doc = pymupdf.open(str(tmp_text))
    bg_doc = pymupdf.open(str(tmp_bg))
    out = pymupdf.open()
    for i, p in enumerate(pages):
        W = float(p["width"])
        H = float(p["height"])
        page = out.new_page(width=W, height=H)
        page.show_pdf_page(page.rect, bg_doc, i, overlay=False)
        page.show_pdf_page(page.rect, text_doc, i, overlay=True)
    out.subset_fonts(verbose=False)
    out.save(str(output_pdf), garbage=4, clean=True)
    out.close()
    text_doc.close()
    bg_doc.close()

    for tmp in (tmp_text, tmp_bg):
        try:
            os.remove(str(tmp))
        except Exception:
            pass

    return {"pdf": str(output_pdf.resolve()), "pages": len(pages)}
