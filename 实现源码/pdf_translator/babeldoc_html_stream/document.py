from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any


def _box_values(box) -> list[float]:
    return [float(box.x), float(box.y), float(box.x2), float(box.y2)]


def _top_left_box(box, page_height: float) -> list[float]:
    x1, y1, x2, y2 = _box_values(box)
    return [round(x1, 3), round(page_height - y2, 3), round(x2, 3), round(page_height - y1, 3)]


def _characters_text(characters) -> str:
    return "".join((char.char_unicode or "") for char in (characters or []))


def _paragraph_by_id(page) -> dict[str, Any]:
    return {
        paragraph.debug_id or f"index-{index}": paragraph
        for index, paragraph in enumerate(page.pdf_paragraph)
    }


def _formulae(paragraph) -> list[Any]:
    result = []
    for composition in paragraph.pdf_paragraph_composition or []:
        if composition.pdf_formula is not None:
            result.append(composition.pdf_formula)
    return result


def _coverage(box, region_box) -> float:
    left = _box_values(box)
    right = _box_values(region_box)
    width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    area = max(1.0, (left[2] - left[0]) * (left[3] - left[1]))
    return width * height / area


def _paragraph_role(
    paragraph, page_width: float, page_height: float, page_layouts
) -> str:
    label = (paragraph.layout_label or "plain text").lower()
    for region in page_layouts:
        region_label = (region.class_name or "").lower()
        if region_label in {"figure", "table"} and _coverage(
            paragraph.box, region.box
        ) >= 0.55:
            return "figure_text" if region_label == "figure" else "table_text"
    if label == "title":
        box = _top_left_box(paragraph.box, page_height)
        size = float(paragraph.pdf_style.font_size or 9.0)
        if box[1] < page_height * 0.3 and (
            box[2] - box[0] > page_width * 0.55 or size >= 14
        ):
            return "title"
        return "heading"
    return {
        "plain text": "body",
        "text": "body",
        "figure": "figure_text",
        "table": "table_text",
        "figure_caption": "figure_caption",
        "table_caption": "table_caption",
        "formula_caption": "formula_caption",
        "isolate_formula": "formula",
        "abandon": "abandon",
    }.get(label, "body")


def _style_attributes(style, base_size: float, fonts: dict[str, Any]) -> str:
    if style is None:
        return ""
    values = []
    size = float(style.font_size or base_size)
    if base_size > 0 and abs(size - base_size) > 0.05:
        values.append(f"font-size:{max(0.55, min(1.8, size / base_size)):.3f}em")
    font = fonts.get(style.font_id)
    if font is not None:
        if bool(font.bold):
            values.append("font-weight:700")
        if bool(font.italic):
            values.append("font-style:italic")
        if bool(font.monospace):
            values.append("font-family:monospace")
    return ";".join(values)


def _composition_html(
    paragraph,
    formula_assets: list[dict[str, Any]],
    fonts: dict[str, Any],
) -> str:
    paragraph_style = paragraph.pdf_style
    base_size = float(
        (paragraph_style.font_size if paragraph_style is not None else None) or 9.0
    )
    formula_index = 0
    parts: list[str] = []
    for composition in paragraph.pdf_paragraph_composition or []:
        text = ""
        style = None
        if composition.pdf_same_style_unicode_characters is not None:
            item = composition.pdf_same_style_unicode_characters
            text = item.unicode or ""
            style = item.pdf_style
        elif composition.pdf_same_style_characters is not None:
            item = composition.pdf_same_style_characters
            text = _characters_text(item.pdf_character)
            style = item.pdf_style
        elif composition.pdf_line is not None:
            item = composition.pdf_line
            text = _characters_text(item.pdf_character)
        elif composition.pdf_character is not None:
            item = composition.pdf_character
            text = item.char_unicode or ""
            style = item.pdf_style
        elif composition.pdf_formula is not None:
            formula = composition.pdf_formula
            if formula_index < len(formula_assets):
                asset = formula_assets[formula_index]
                width_em = max(0.35, asset["width"] / max(base_size, 1.0))
                height_em = max(0.5, asset["height"] / max(base_size, 1.0))
                parts.append(
                    '<img class="inline-formula" src="{}" style="width:{:.3f}em;height:{:.3f}em" alt="{}">'.format(
                        html.escape(Path(asset["path"]).resolve().as_uri(), quote=True),
                        width_em,
                        height_em,
                        html.escape(_characters_text(formula.pdf_character), quote=True),
                    )
                )
                formula_index += 1
                continue
            text = _characters_text(formula.pdf_character)
        if not text:
            continue
        attributes = _style_attributes(style, base_size, fonts)
        escaped = html.escape(text)
        parts.append(f'<span style="{attributes}">{escaped}</span>' if attributes else escaped)
    return "".join(parts) or html.escape(paragraph.unicode or "")


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _is_identity_band(page_index: int, box: list[float], source_text: str) -> bool:
    return bool(
        page_index == 1
        and 80 <= box[1] <= 225
        and (
            "@" in source_text
            or len(re.findall(r"[,†∗*]", source_text)) >= 3
            or re.search(
                r"\b(?:university|institute|department|laborator(?:y|ies)|college|school)\b",
                source_text,
                flags=re.IGNORECASE,
            )
        )
    )


def build_html_document(
    source_pdf: Path,
    source_docs,
    translated_docs,
    assets_dir: Path,
) -> dict[str, Any]:
    """Convert pre/post-translation BabelDOC IL into the HTML engine contract."""

    import pymupdf

    source_pdf = Path(source_pdf).resolve()
    assets_dir = Path(assets_dir).resolve()
    assets_dir.mkdir(parents=True, exist_ok=True)
    source = pymupdf.open(source_pdf)
    pages = []

    for page_index, (source_page_il, translated_page_il) in enumerate(
        zip(source_docs.page, translated_docs.page), start=1
    ):
        pdf_page = source[page_index - 1]
        page_height = float(pdf_page.rect.height)
        page_width = float(pdf_page.rect.width)
        source_map = _paragraph_by_id(source_page_il)
        font_map = {font.font_id: font for font in translated_page_il.pdf_font}
        blocks = []

        for paragraph_index, translated in enumerate(
            translated_page_il.pdf_paragraph, start=1
        ):
            key = translated.debug_id or f"index-{paragraph_index - 1}"
            original = source_map.get(key)
            if original is None:
                continue
            source_text = original.unicode or ""
            translated_text = translated.unicode or source_text
            box = _top_left_box(original.box, page_height)
            changed = _normalized_text(source_text) != _normalized_text(translated_text)
            identity_protected = _is_identity_band(page_index, box, source_text)
            if identity_protected:
                changed = False
                translated_text = source_text
            formula_assets = []
            if changed:
                for formula_index, formula in enumerate(_formulae(original), start=1):
                    formula_box = _top_left_box(formula.box, page_height)
                    clip = pymupdf.Rect(*formula_box) & pdf_page.rect
                    if clip.is_empty or clip.width <= 0 or clip.height <= 0:
                        continue
                    formula_path = assets_dir / (
                        f"formula-p{page_index:03d}-{paragraph_index:03d}-{formula_index:02d}.png"
                    )
                    pdf_page.get_pixmap(
                        matrix=pymupdf.Matrix(3, 3), clip=clip, alpha=False
                    ).save(formula_path)
                    formula_assets.append(
                        {
                            "path": str(formula_path),
                            "bbox": formula_box,
                            "width": float(clip.width),
                            "height": float(clip.height),
                        }
                    )

            role = _paragraph_role(
                original,
                page_width,
                page_height,
                source_page_il.page_layout,
            )
            block = {
                "id": f"p{page_index}-{key}",
                "debug_id": translated.debug_id,
                "page": page_index,
                "bbox": box,
                "source_text": source_text,
                "translated_text": translated_text,
                "translated_html": (
                    html.escape(source_text)
                    if identity_protected
                    else _composition_html(translated, formula_assets, font_map)
                ),
                "role": role,
                "layout_label": original.layout_label,
                "layout_id": original.layout_id,
                "translate": changed,
                "source_font_size": round(
                    float(
                        (
                            original.pdf_style.font_size
                            if original.pdf_style is not None
                            else None
                        )
                        or 9.0
                    ),
                    3,
                ),
                "first_line_indent": bool(original.first_line_indent),
                "vertical": bool(original.vertical),
                "protected_identity": identity_protected,
                "formula_assets": formula_assets,
                "column": (
                    "full"
                    if box[2] - box[0] >= page_width * 0.62
                    else "left"
                    if (box[0] + box[2]) / 2 < page_width / 2
                    else "right"
                ),
            }
            blocks.append(block)

        for block in blocks:
            if block["translate"]:
                pad = 0.35
                rect = pymupdf.Rect(*block["bbox"])
                rect = pymupdf.Rect(
                    max(0, rect.x0 - pad),
                    max(0, rect.y0 - pad),
                    min(page_width, rect.x1 + pad),
                    min(page_height, rect.y1 + pad),
                )
                pdf_page.add_redact_annot(rect, fill=False)
        if any(block["translate"] for block in blocks):
            pdf_page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
        background = assets_dir / f"page-{page_index:03d}.png"
        pdf_page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).save(background)

        layout_regions = []
        for region in translated_page_il.page_layout:
            layout_regions.append(
                {
                    "id": region.id,
                    "label": region.class_name,
                    "confidence": region.conf,
                    "bbox": _top_left_box(region.box, page_height),
                }
            )
        pages.append(
            {
                "number": page_index,
                "width": page_width,
                "height": page_height,
                "background_image": str(background),
                "background_text_removed": True,
                "doclayout_regions": layout_regions,
                "blocks": blocks,
            }
        )
    source.close()
    return {
        "schema_version": "3.0",
        "source_pdf": str(source_pdf),
        "layout_frontend": "babeldoc-doclayout",
        "final_layout_engine": "html-css-weasyprint",
        "pages": pages,
    }
