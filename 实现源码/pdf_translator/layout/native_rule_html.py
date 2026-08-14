from __future__ import annotations

import html
from pathlib import Path

from .typography import fit_text_style


_PROFILE_BY_ROLE = {
    "title": "title",
    "heading": "title",
    "figure_caption": "image_caption",
    "table_caption": "image_caption",
    "formula_caption": "image_caption",
    "footnote": "page_footnote",
    "page_header": "page_footnote",
    "page_footer": "page_footnote",
    "figure_text": "figure_text",
    "table_text": "figure_text",
}


def build_native_rule_html(document: dict, output: Path) -> dict:
    """Build the mandatory HTML layer used to print the final PDF."""

    page_rules = []
    page_html = []
    fit_evidence = []
    for page in document.get("pages", []):
        number = int(page["number"])
        width = float(page["width"])
        height = float(page["height"])
        page_name = f"sheet-{number}"
        page_rules.append(
            f"@page {page_name}{{size:{width}pt {height}pt;margin:0}}"
            f".page-{number}{{page:{page_name}}}"
        )
        items = [
            '<img class="source-page" src="{}" alt="">'.format(
                html.escape(Path(page["background_image"]).resolve().as_uri(), quote=True)
            )
        ]
        for block in page.get("blocks", []):
            if not block.get("translate"):
                continue
            translated = block.get("translated_text", "").strip()
            if not translated:
                continue
            x1, y1, x2, y2 = [float(value) for value in block["bbox"]]
            width_pt = max(1.0, x2 - x1)
            height_pt = max(1.0, y2 - y1)
            profile = _PROFILE_BY_ROLE.get(block.get("role", ""), "text")
            fit = fit_text_style(
                translated, width_pt, height_pt, region_type=profile
            )
            source_size = float(block.get("source_font_size") or fit.font_size)
            if block.get("role") == "title":
                size_cap = source_size * 1.08
            elif block.get("role") == "heading":
                size_cap = source_size * 1.04
            else:
                size_cap = source_size * 1.02
            font_size = round(min(fit.font_size, size_cap), 2)
            pad = 0.7
            if not page.get("background_text_removed"):
                items.append(
                    '<div class="erase" style="left:{x}pt;top:{y}pt;width:{w}pt;height:{h}pt"></div>'.format(
                        x=round(x1 - pad, 3),
                        y=round(y1 - pad, 3),
                        w=round(width_pt + pad * 2, 3),
                        h=round(height_pt + pad * 2, 3),
                    )
                )
            css = (
                f"left:{x1}pt;top:{y1}pt;width:{width_pt}pt;height:{height_pt}pt;"
                f"font-size:{font_size}pt;line-height:{fit.line_height};"
                f"letter-spacing:{fit.letter_spacing}em;"
                f"text-indent:{fit.text_indent if block.get('role') == 'body' else 0}em"
            )
            role = html.escape(block.get("role", "body"), quote=True)
            items.append(
                '<article class="translation {role}" data-id="{id}" style="{css}" '
                'title="{source}">{translation}</article>'.format(
                    role=role,
                    id=html.escape(block["id"], quote=True),
                    css=css,
                    source=html.escape(block.get("source_text", ""), quote=True),
                    translation=html.escape(translated),
                )
            )
            fit_evidence.append(
                {
                    "id": block["id"],
                    "page": number,
                    "role": block.get("role"),
                    "font_size": font_size,
                    "line_height": fit.line_height,
                    "letter_spacing": fit.letter_spacing,
                    "estimated_lines": fit.estimated_lines,
                    "occupancy": fit.occupancy,
                    "estimated_overflow": fit.overflow,
                }
            )
        page_html.append(
            f'<section class="page page-{number}" style="width:{width}pt;height:{height}pt">'
            + "".join(items)
            + "</section>"
        )

    source = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>Native-rule PDF translation</title><style>"
        + "\n".join(page_rules)
        + """
*{box-sizing:border-box}html,body{margin:0;padding:0;background:#ddd}
body{font-family:"Noto Serif CJK SC","Noto Serif CJK","Source Han Serif SC",serif;color:#111}
.page{position:relative;margin:0 auto;background:#fff;overflow:hidden;break-after:page}
.page:last-child{break-after:auto}.source-page{position:absolute;inset:0;width:100%;height:100%;z-index:0}
.erase{position:absolute;z-index:1;background:#fff}
.translation{position:absolute;z-index:2;overflow:hidden;word-break:break-all;text-align:justify;background:transparent}
.translation.title{text-align:center;font-weight:700}
.translation.heading{text-align:left;font-weight:700}
.translation.page_header{text-align:center}
.translation.page_footer{text-align:left}
.translation.figure_caption,.translation.table_caption,.translation.formula_caption{text-align:justify}
.translation.figure_text,.translation.table_text{text-align:center}
@media screen{.page{margin:16px auto;box-shadow:0 2px 12px #777}.translation:hover{outline:1px solid #1683ff;background:#eaf4ff}}
@media print{html,body{background:#fff}.page{margin:0;box-shadow:none}}
</style></head><body>"""
        + "\n".join(page_html)
        + "</body></html>"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(source, encoding="utf-8")
    return {"html": str(output.resolve()), "fit_evidence": fit_evidence}
