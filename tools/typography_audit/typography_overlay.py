# -*- coding: utf-8 -*-
"""Typography overlays, comparison board and required detail boards."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageDraw, ImageFont


DETAIL_CROPS = {
    1: (50, 55, 545, 610),
    3: (55, 55, 540, 790),
    6: (55, 55, 540, 790),
    13: (55, 55, 540, 745),
    14: (55, 325, 540, 790),
    16: (55, 55, 540, 790),
}

DETAIL_NAMES = {
    1: "p001_frontmatter_detail.png",
    3: "p003_formula_text_detail.png",
    6: "p006_heading_list_detail.png",
    13: "p013_table_detail.png",
    14: "p014_formula_spacing_detail.png",
    16: "p016_dense_layout_detail.png",
}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path(r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _fit_page(image: Image.Image, width: int) -> Image.Image:
    ratio = width / image.width
    return image.resize((width, round(image.height * ratio)), Image.Resampling.LANCZOS)


def make_comparison_board(current_pngs: dict[int, str | Path],
                          mock_pngs: dict[int, str | Path],
                          output_path: str | Path) -> Path:
    page_width, label_height, row_gap, margin = 330, 38, 28, 28
    current = {p: _fit_page(Image.open(path).convert("RGB"), page_width)
               for p, path in current_pngs.items()}
    mocks = {p: _fit_page(Image.open(path).convert("RGB"), page_width)
             for p, path in mock_pngs.items()}
    row_height = max(image.height for image in current.values()) + label_height
    board = Image.new("RGB", (margin * 2 + page_width * 2 + 24,
                              margin * 2 + len(current) * row_height
                              + (len(current) - 1) * row_gap + 52), "#eef1f5")
    draw = ImageDraw.Draw(board)
    title_font, label_font, small_font = _font(24, True), _font(18, True), _font(13)
    draw.text((margin, 16), "Phase 4D.2A 中文排版比较板（同尺度整页）", fill="#16243a", font=title_font)
    y = margin + 48
    for pno in sorted(current):
        draw.rounded_rectangle((margin - 8, y - 5, board.width - margin + 8,
                                y + row_height + 5), radius=10, fill="white", outline="#c5ced9")
        draw.text((margin, y), f"p{pno:03d} · current", fill="#26364d", font=label_font)
        draw.text((margin + page_width + 24, y), f"p{pno:03d} · balanced mock",
                  fill="#0a5a43", font=label_font)
        board.paste(current[pno], (margin, y + label_height))
        board.paste(mocks[pno], (margin + page_width + 24, y + label_height))
        y += row_height + row_gap
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    board.save(target)
    return target


def make_detail_boards(current_pngs: dict[int, str | Path],
                       mock_pngs: dict[int, str | Path], out_dir: str | Path) -> list[Path]:
    out_dir = Path(out_dir)
    outputs = []
    scale = 2.0  # baseline PNGs are 2x PDF points
    label = _font(18, True)
    for pno, crop_pt in DETAIL_CROPS.items():
        crop_px = tuple(round(value * scale) for value in crop_pt)
        left = Image.open(current_pngs[pno]).convert("RGB").crop(crop_px)
        right = Image.open(mock_pngs[pno]).convert("RGB").crop(crop_px)
        width = 560
        left, right = _fit_page(left, width), _fit_page(right, width)
        height = max(left.height, right.height)
        board = Image.new("RGB", (width * 2 + 42, height + 62), "#f0f3f7")
        draw = ImageDraw.Draw(board)
        draw.text((12, 13), f"p{pno:03d} current", fill="#26364d", font=label)
        draw.text((width + 30, 13), f"p{pno:03d} balanced mock", fill="#0a5a43", font=label)
        board.paste(left, (12, 50))
        board.paste(right, (width + 30, 50))
        target = out_dir / DETAIL_NAMES[pno]
        board.save(target)
        outputs.append(target)
    return outputs


def make_typography_overlay(pdf_path: str | Path, analysis: dict[str, Any],
                            output_path: str | Path) -> Path:
    doc = pymupdf.open(str(pdf_path))
    page = doc[0]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    draw = ImageDraw.Draw(image, "RGBA")
    colors = {
        "document_title": (128, 0, 180, 180), "author": (20, 90, 210, 160),
        "affiliation": (0, 130, 150, 160), "section_heading": (220, 45, 30, 180),
        "subsection_heading": (230, 100, 20, 180), "subsubsection_heading": (220, 150, 10, 180),
        "caption": (0, 135, 65, 180), "list_item": (150, 65, 180, 160),
        "table_header": (20, 110, 110, 160), "table_body": (20, 150, 110, 120),
        "body": (60, 80, 100, 80), "body_bold_lead": (50, 50, 50, 110),
    }
    for line in analysis["current_zh"]["lines"]:
        bbox = line.get("bbox")
        if not bbox:
            continue
        role = line.get("role") or "body"
        color = colors.get(role, (80, 80, 80, 90))
        draw.rectangle(tuple(round(v * 2) for v in bbox), outline=color, width=2)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    return target
