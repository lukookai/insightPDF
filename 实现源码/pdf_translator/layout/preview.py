from __future__ import annotations

import html
import json
from pathlib import Path


def _box_style(box: list[float]) -> str:
    x1, y1, x2, y2 = box
    return (
        f"left:{x1}px;top:{y1}px;width:{max(1, x2-x1)}px;"
        f"height:{max(1, y2-y1)}px"
    )


def build_alignment_preview(
    document: dict,
    *,
    background_pattern: str,
    output: Path,
) -> None:
    page_html = []
    for page in document.get("pages", []):
        number = page["number"]
        translations = []
        for item in page.get("translations", []):
            if not item.get("bbox"):
                continue
            translations.append(
                '<div class="translation" style="{}" title="{}">'
                '<span class="tag">{}</span><span class="text">{}</span></div>'.format(
                    _box_style(item["bbox"]),
                    html.escape(item.get("source_text", ""), quote=True),
                    html.escape(item["id"]),
                    html.escape(item.get("translated_text", "")),
                )
            )
        ocr_boxes = []
        for detection in (page.get("ocr") or {}).get("detections", []):
            for box in detection.get("pdf_bboxes", []):
                ocr_boxes.append(
                    '<div class="ocr" style="{}"><span class="tag">OCR:{}</span></div>'.format(
                        _box_style(box), html.escape(detection.get("label", ""))
                    )
                )
        background = background_pattern.format(page=number)
        page_html.append(
            f'<section class="page" style="width:{page["width"]}px;'
            f'height:{page["height"]}px;background-image:url(\'{html.escape(background)}\')">'
            f'<span class="page-number">Page {number}</span>'
            + "".join(translations)
            + "".join(ocr_boxes)
            + "</section>"
        )

    source = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>PDF alignment preview</title>
<style>
@page{size:612px 792px;margin:0}
body{margin:0;background:#555;font-family:sans-serif}
.toolbar{position:fixed;z-index:99;top:10px;left:10px;background:#fff;padding:8px 12px;border-radius:5px;box-shadow:0 2px 8px #0008}
.page{position:relative;margin:16px auto;background-size:100% 100%;background-color:#fff;box-shadow:0 2px 10px #000;overflow:hidden}
.page-number{position:absolute;right:4px;top:2px;background:#111;color:#fff;font-size:10px;padding:2px 4px;z-index:20}
.translation,.ocr{position:absolute;box-sizing:border-box;pointer-events:auto}
.translation{border:1.5px solid #0077ff;background:rgba(255,255,255,.74);overflow:hidden}
.translation .tag,.ocr .tag{position:absolute;left:0;top:0;font-size:8px;line-height:10px;color:white;padding:0 2px;white-space:nowrap}
.translation .tag{background:#0077ff}.ocr .tag{background:#e00078}
.translation .text{display:block;padding:11px 2px 1px;color:#003b7a;font-family:"Noto Serif CJK SC",serif;font-size:8px;line-height:1.25}
.ocr{border:1.5px dashed #e00078;background:rgba(224,0,120,.06)}
body.hide-translation .translation{display:none}body.hide-ocr .ocr{display:none}
@media print{.toolbar{display:none}.page{margin:0;box-shadow:none;break-after:page}.page:last-of-type{break-after:auto}}
</style></head><body>
<div class="toolbar"><label><input id="translation" type="checkbox" checked> 译文框</label> <label><input id="ocr" type="checkbox" checked> OCR框</label></div>
""" + "\n".join(page_html) + """
<script>
document.querySelector('#translation').onchange=e=>document.body.classList.toggle('hide-translation',!e.target.checked);
document.querySelector('#ocr').onchange=e=>document.body.classList.toggle('hide-ocr',!e.target.checked);
</script></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(source, encoding="utf-8")


def load_and_build(document_path: Path, background_pattern: str, output: Path) -> None:
    document = json.loads(document_path.read_text(encoding="utf-8"))
    build_alignment_preview(
        document,
        background_pattern=background_pattern,
        output=output,
    )
