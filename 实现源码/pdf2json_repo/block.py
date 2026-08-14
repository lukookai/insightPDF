#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import fitz  # PyMuPDF
import datetime
import math

def is_bold(flags):
    # 2: bold
    return bool(flags & 2)

def is_italic(flags):
    # 1: italic
    return bool(flags & 1)

def get_angle(span):
    # span["direction"] 是一个向量 (dx, dy)
    dx, dy = span.get("direction", (1, 0))
    angle = math.degrees(math.atan2(dy, dx))
    # 角度范围为 (-180, 180]
    return angle

def get_color(span):
    # span["color"] 是整数RGB
    color = span.get("color", 0)
    r = (color >> 16) & 255
    g = (color >> 8) & 255
    b = color & 255
    return (r, g, b)

def show_dict_spans(page):
    raw = page.get_text("dict", sort=False)
    for block_idx, block in enumerate(raw.get("blocks", []), start=1):
        if "lines" not in block:
            continue
        for line_idx, line in enumerate(block["lines"], start=1):
            for span_idx, span in enumerate(line.get("spans", []), start=1):
                font = span.get("font", "")
                size = span.get("size", "")
                bbox = span.get("bbox", "")
                text = span.get("text", "")
                flags = span.get("flags", 0)
                bold = is_bold(flags)
                italic = is_italic(flags)
                angle = get_angle(span)
                color = get_color(span)
                print(f"\n== Block {block_idx}, Line {line_idx}, Span {span_idx} ==")
                print(f"Font: {font}, Size: {size}, BBox: {bbox}")
                print(f"Bold: {bold}, Italic: {italic}, Angle: {angle:.2f}°")
                print(f"Color: RGB{color}")
                print(f"Text: {repr(text)}")

if __name__ == "__main__":
    b = datetime.datetime.now()
    pdf_path = "股东p1.pdf"  # 替换为你的PDF
    page_number = 1      # 替换为你的页码

    doc = fitz.open(pdf_path)
    page = doc[page_number - 1]

    show_dict_spans(page)

    e = datetime.datetime.now()
    print(f"运行时间: {(e - b).total_seconds()} 秒")
