# -*- coding: utf-8 -*-
"""Isolated CSS sandbox for Phase 4D.2A balanced mock pages."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pymupdf
from playwright.sync_api import sync_playwright

try:
    from .classify_text_role import classify_paragraph
    from .extract_typography import capture_dom_typography
except ImportError:  # direct script execution
    from classify_text_role import classify_paragraph  # type: ignore
    from extract_typography import capture_dom_typography  # type: ignore


FONT_URL = Path(r"C:\Windows\Fonts\NotoSerifSC-VF.ttf").as_uri()


def _text_payloads(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [region["payload"] for region in model.get("regions", [])
            if region.get("type") == "text"]


def balanced_css(page_number: int, model: dict[str, Any],
                 profile: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Build page-specific CSS using only isolated mock selectors.

    Paragraphs containing inline formulas retain the accepted 4D.1C font
    metrics so every inline formula outer bbox stays frozen.  This is an
    intentional sandbox limitation recorded in the manifest, not an implicit
    production integration.
    """
    roles: dict[str, list[str]] = {}
    formula_paragraphs = set()
    for payload in _text_payloads(model):
        pid = payload.get("paragraph_id")
        if not pid:
            continue
        role = classify_paragraph(page_number, payload)
        roles.setdefault(role, []).append(pid)
        translated = payload.get("translated_text") or payload.get(
            "translation_source_text") or ""
        if "{{FORMULA_" in translated:
            formula_paragraphs.add(pid)

    body_size = profile["body"]["font_size_pt"]["value"]
    line_ratio = profile["body"]["line_height_ratio"]["value"]
    section_size = body_size * profile["section_heading"]["font_size_ratio_to_body"]["value"]
    subsection_size = body_size * profile["subsection_heading"]["font_size_ratio_to_body"]["value"]
    subsub_size = body_size * profile["subsubsection_heading"]["font_size_ratio_to_body"]["value"]
    title_size = body_size * profile["title"]["font_size_ratio_to_body"]["value"]
    caption_size = body_size * profile["caption"]["font_size_ratio_to_body"]["value"]
    footnote_size = body_size * profile["footnote"]["font_size_ratio_to_body"]["value"]

    def selectors(role: str, *, exclude_formula: bool = False) -> str:
        pids = roles.get(role, [])
        if exclude_formula:
            pids = [pid for pid in pids if pid not in formula_paragraphs]
        return ",\n".join(f'[data-para="{pid}"]' for pid in pids)

    body_selector = ",\n".join(filter(None, [
        selectors("body", exclude_formula=True),
        selectors("body_bold_lead", exclude_formula=True),
        selectors("abstract_body", exclude_formula=True),
        selectors("reference", exclude_formula=True),
    ]))
    list_selector = selectors("list_item", exclude_formula=True)
    body_rule = None
    heading_rules = []
    for role, size, weight in (
        ("section_heading", section_size, profile["section_heading"]["font_weight"]["value"]),
        ("subsection_heading", subsection_size, profile["subsection_heading"]["font_weight"]["value"]),
        ("subsubsection_heading", subsub_size, profile["subsubsection_heading"]["font_weight"]["value"]),
    ):
        selector = selectors(role, exclude_formula=True)
        if selector:
            heading_rules.append(
                f"{selector}{{font-family:'Balanced Latin','Balanced CJK',serif !important;"
                f"font-size:{size:.3f}pt !important;line-height:{size*1.24:.3f}pt !important;"
                f"font-weight:{weight} !important;letter-spacing:.005em !important;}}"
            )

    rules = [f"""
@font-face{{font-family:'Balanced CJK';src:url('{FONT_URL}') format('truetype');
  font-weight:100 900;font-style:normal;
  unicode-range:U+2E80-2EFF,U+3000-303F,U+31C0-31EF,U+3400-4DBF,U+4E00-9FFF,U+F900-FAFF;}}
@font-face{{font-family:'Balanced Latin';src:local('Times New Roman');
  unicode-range:U+0000-024F,U+1E00-1EFF;}}
html,body{{word-spacing:normal !important;transform:none !important;zoom:1 !important;}}
.formula-inline,.formula-seg,.figure-region,.translated-cell{{transform:none !important;}}
"""]
    if body_selector:
        body_rule = (
            f"{body_selector}{{font-family:'Balanced Latin','Balanced CJK',serif !important;"
            f"font-size:{body_size:.3f}pt !important;line-height:{body_size*line_ratio:.3f}pt !important;"
            "font-kerning:normal !important;font-variant-east-asian:proportional-width !important;}"
        )
        rules.append(body_rule)
    if list_selector:
        indent = profile["list"]["hanging_indent_em"]["value"]
        rules.append(
            f"{list_selector}{{font-family:'Balanced Latin','Balanced CJK',serif !important;"
            f"font-size:{body_size:.3f}pt !important;line-height:{body_size*line_ratio:.3f}pt !important;"
            f"padding-left:{indent:.3f}em !important;text-indent:-{indent:.3f}em !important;}}"
        )
    rules.extend(heading_rules)
    if roles.get("document_title"):
        rules.append(
            f".title-block{{font-family:'Balanced Latin','Balanced CJK',serif !important;"
            f"font-size:{title_size:.3f}pt !important;line-height:{title_size*profile['title']['line_height_ratio']['value']:.3f}pt !important;"
            f"font-weight:{profile['title']['font_weight']['value']} !important;letter-spacing:.005em !important;}}"
        )
    # Author/Affiliation blocks contain frozen superscript FormulaGroups on
    # p001 and therefore retain their current metrics in this sandbox.
    if roles.get("caption"):
        rules.append(
            f".caption-block,{selectors('caption', exclude_formula=True)}{{"
            "font-family:'Balanced Latin','Balanced CJK',serif !important;"
            f"font-size:{caption_size:.3f}pt !important;"
            f"line-height:{caption_size*profile['caption']['line_height_ratio']['value']:.3f}pt !important;}}"
        )
    if roles.get("footnote"):
        rules.append(
            f".footnote-block{{font-size:{footnote_size:.3f}pt !important;"
            f"line-height:{footnote_size*profile['footnote']['line_height_ratio']['value']:.3f}pt !important;}}"
        )
    rules.append(".inline-bold{font-weight:650 !important}.code-run{font-family:Consolas,'Courier New',monospace !important;letter-spacing:0 !important;}")
    css = "\n".join(rules)
    manifest = {
        "page": page_number,
        "sandbox_only": True,
        "profile_name": profile.get("profile_name"),
        "applied_paragraphs": sorted(pid for pids in roles.values() for pid in pids
                                     if pid not in formula_paragraphs),
        "formula_geometry_locked_paragraphs": sorted(formula_paragraphs),
        "word_spacing_used": False,
        "fit_to_page_used": False,
        "global_scale_used": False,
        "production_renderer_modified": False,
    }
    return css, manifest


def build_mock_html(source_html: str | Path, output_html: str | Path,
                    css: str) -> Path:
    source = Path(source_html).resolve()
    output = Path(output_html).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = source.read_text(encoding="utf-8")
    base = f'<base href="{source.parent.as_uri()}/">'
    raw = raw.replace("<head>", f"<head>{base}", 1)
    raw = raw.replace("</head>", f'<style id="phase4d2a-balanced-sandbox">{css}</style></head>', 1)
    output.write_text(raw, encoding="utf-8")
    return output


def render_html_to_pdf(html_path: str | Path, pdf_path: str | Path) -> None:
    source, target = Path(html_path).resolve(), Path(pdf_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        page = browser.new_page()
        page.goto(source.as_uri(), wait_until="networkidle")
        page.wait_for_function("Array.from(document.images).every(i => i.complete)")
        page.evaluate("document.fonts && document.fonts.ready")
        data = page.pdf(print_background=True, prefer_css_page_size=True)
        target.write_bytes(data)
        browser.close()


def render_pdf_png(pdf_path: str | Path, png_path: str | Path, zoom: float = 2.0) -> None:
    target = Path(png_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf_path))
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    pix.save(str(target))
    doc.close()


def render_balanced_mock(*, page_number: int, source_html: str | Path,
                         model: dict[str, Any], profile: dict[str, Any],
                         mock_dir: str | Path) -> dict[str, Any]:
    mock_dir = Path(mock_dir)
    stem = f"p{page_number:03d}_balanced_mock"
    html_path = mock_dir / f"{stem}.html"
    pdf_path = mock_dir / f"{stem}.pdf"
    png_path = mock_dir / f"{stem}.png"
    css, manifest = balanced_css(page_number, model, profile)
    build_mock_html(source_html, html_path, css)
    render_html_to_pdf(html_path, pdf_path)
    render_pdf_png(pdf_path, png_path)
    manifest.update({
        "html": str(html_path.resolve()), "pdf": str(pdf_path.resolve()),
        "png": str(png_path.resolve()), "dom_snapshot": capture_dom_typography(html_path),
    })
    (mock_dir / f"{stem}_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
