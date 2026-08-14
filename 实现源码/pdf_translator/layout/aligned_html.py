from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .typography import fit_text_style


def _classify(source: str, region_type: str = "") -> str:
    if region_type in {"title", "image_caption", "page_footnote"}:
        return region_type.replace("_", "-")
    stripped = source.strip()
    if re.match(r"^\d+\.\s+[A-Z]", stripped):
        return "clause"
    if len(stripped) < 80 and stripped.endswith(":"):
        return "label"
    if len(stripped) < 80:
        return "short"
    return "paragraph"


def _intersection_ratio(first: list[float], second: list[float]) -> float:
    x1 = max(first[0], second[0])
    y1 = max(first[1], second[1])
    x2 = min(first[2], second[2])
    y2 = min(first[3], second[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area = max(1.0, (first[2] - first[0]) * (first[3] - first[1]))
    return intersection / area


def build_aligned_html(
    document: dict,
    *,
    output: Path,
    fallback_background_pattern: str | None = None,
) -> None:
    pages_html = []
    page_rules = []
    for page in document.get("pages", []):
        page_name = f"sheet-{page['number']}"
        page_rules.append(
            f"@page {page_name}{{size:{page['width']}pt {page['height']}pt;margin:0}}"
            f".page-{page['number']}{{page:{page_name}}}"
        )
        blocks = []
        translations = page.get("translations", [])
        ocr = page.get("ocr") or {}
        page_image = ocr.get("page_image")
        translation_boxes = [item["bbox"] for item in translations if item.get("bbox")]
        if page_image:
            image_url = Path(page_image).resolve().as_uri()
            for native_image in page.get("native_images", []):
                ix1, iy1, ix2, iy2 = native_image["bbox"]
                blocks.append(
                    '<div class="original-region native-image" '
                    'style="left:{x}pt;top:{y}pt;width:{w}pt;height:{h}pt">'
                    '<img src="{src}" style="left:-{x}pt;top:-{y}pt;width:{pw}pt;height:{ph}pt"></div>'.format(
                        x=ix1,
                        y=iy1,
                        w=ix2 - ix1,
                        h=iy2 - iy1,
                        src=html.escape(image_url, quote=True),
                        pw=page["width"],
                        ph=page["height"],
                    )
                )
            for detection in ocr.get("detections", []):
                for image_box in detection.get("pdf_bboxes", []):
                    covered = any(
                        _intersection_ratio(image_box, box) >= 0.65
                        for box in translation_boxes
                    )
                    # Preserve figures, formulas, tables, references and all
                    # other OCR regions that were deliberately not translated.
                    if covered:
                        continue
                    ix1, iy1, ix2, iy2 = image_box
                    blocks.append(
                        '<div class="original-region {label}" style="left:{x}pt;top:{y}pt;width:{w}pt;height:{h}pt">'
                        '<img src="{src}" style="left:-{x}pt;top:-{y}pt;width:{pw}pt;height:{ph}pt"></div>'.format(
                            label=html.escape(detection.get("label", "region"), quote=True),
                            x=ix1,
                            y=iy1,
                            w=ix2 - ix1,
                            h=iy2 - iy1,
                            src=html.escape(image_url, quote=True),
                            pw=page["width"],
                            ph=page["height"],
                        )
                    )
        for item in translations:
            box = item.get("bbox")
            if not box:
                continue
            x1, y1, x2, y2 = box
            width, height = x2 - x1, y2 - y1
            translated = item.get("translated_text", "")
            region_type = item.get("region_type", "")
            fit = fit_text_style(
                translated,
                width,
                height,
                region_type=region_type or "text",
            )
            css = (
                f"left:{x1}pt;top:{y1}pt;width:{width}pt;height:{height}pt;"
                f"font-size:{fit.font_size}pt;line-height:{fit.line_height};"
                f"letter-spacing:{fit.letter_spacing}em;text-indent:{fit.text_indent}em"
            )
            blocks.append(
                '<article class="block {}" data-id="{}" data-fit-lines="{}" '
                'data-fit-occupancy="{}" data-fit-overflow="{}" style="{}" title="{}">{}</article>'.format(
                    _classify(item.get("source_text", ""), region_type),
                    html.escape(item["id"], quote=True),
                    fit.estimated_lines,
                    fit.occupancy,
                    str(fit.overflow).lower(),
                    css,
                    html.escape(item.get("source_text", ""), quote=True),
                    html.escape(translated),
                )
            )
        background = ""
        if not translations and fallback_background_pattern:
            background_url = fallback_background_pattern.format(page=page["number"])
            background = f"background-image:url('{html.escape(background_url)}');background-size:100% 100%;"
        has_detected_page_number = any(
            item.get("label") == "page_number" for item in ocr.get("detections", [])
        )
        footer = (
            f'<span class="footer">{page["number"]}</span>'
            if page["number"] <= 8 and not has_detected_page_number
            else ""
        )
        pages_html.append(
            f'<section class="page page-{page["number"]}" '
            f'style="width:{page["width"]}pt;height:{page["height"]}pt;{background}">'
            + "".join(blocks)
            + footer
            + "</section>"
        )

    source = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>Aligned Chinese PDF draft</title><style>
""" + "\n".join(page_rules) + """
*{box-sizing:border-box}html,body{margin:0;padding:0;background:#ddd}
body{font-family:"Noto Serif CJK SC","Source Han Serif SC",serif;color:#111}
.page{position:relative;margin:0 auto;background-color:white;overflow:hidden;break-after:page}
.page:last-child{break-after:auto}
.block{position:absolute;z-index:2;overflow:hidden;line-height:1.38;text-align:justify;word-break:break-all;background:#fff}
.original-region{position:absolute;z-index:1;overflow:hidden}.original-region img{position:absolute;max-width:none}
.block.paragraph,.block.clause{text-indent:2em}.block.clause{font-weight:400}
.block.short,.block.label{text-align:left}.block.label{font-weight:600}
.block.title{text-align:center;font-weight:600;line-height:1.25}
.block.image-caption{font-size:8pt;text-align:justify;line-height:1.25}
.block.page-footnote{font-size:7pt;text-align:left;line-height:1.2}
.footer{position:absolute;bottom:18pt;left:0;right:0;text-align:center;font:8pt serif}
@media screen{.page{margin:16px auto;box-shadow:0 2px 12px #777}.block:hover{outline:1px solid #1683ff;background:#eaf4ff}}
@media print{html,body{background:white}.page{margin:0;box-shadow:none}}
</style></head><body>""" + "\n".join(pages_html) + "</body></html>"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(source, encoding="utf-8")


def load_and_build(
    document_path: Path,
    output: Path,
    fallback_background_pattern: str | None = None,
) -> None:
    document = json.loads(document_path.read_text(encoding="utf-8"))
    build_aligned_html(
        document,
        output=output,
        fallback_background_pattern=fallback_background_pattern,
    )
