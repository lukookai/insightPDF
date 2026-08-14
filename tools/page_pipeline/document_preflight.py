# -*- coding: utf-8 -*-
"""DocumentPreflight - run BEFORE the full translation pipeline.

Reads the source PDF only and produces ``preflight.json``:

* openability / page_count / per-page media_box / crop_box / rotation
* encrypted / likely_scanned (image-only ratio + text density)
* non_embedded_fonts (font-embedding risk for CJK/symbol rendering)
* normalization_required (rotation != 0 or crop != media)

If normalization_required is true the pipeline must NOT run a production
delivery run (rotation != 0 or crop != media is out of scope in 4C.1).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for path in (HERE, REPO / "实现源码" / "pdf_translator"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

SCANNED_PAGE_RATIO = 0.5      # >=50% image-only pages -> likely_scanned
SCANNED_MIN_CHARS = 50        # <50 chars/page -> low text density


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _page_text_len(page):
    try:
        return len((page.get_text("text") or "").strip())
    except Exception:  # noqa: BLE001
        return 0


def _page_image_only(page):
    """True when the page has no meaningful text layer (scanned/fax/slides)."""
    text = _page_text_len(page)
    images = page.get_images(full=True)
    if not images:
        return False
    if text >= SCANNED_MIN_CHARS:
        return False
    try:
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1, 1))
        ink = sum(1 for i in range(0, len(pix.samples), 3)
                  if pix.samples[i] < 235)
        ratio = ink / max(pix.width * pix.height, 1)
        return ratio > 0.01  # visible raster content, no text
    except Exception:  # noqa: BLE001
        return True  # cannot prove text -> treat as image-only (conservative)


def run_preflight(pdf_path, out_dir):
    """Build preflight.json; return (preflight_dict, allowed_to_run)."""
    pdf_path = Path(pdf_path).resolve()
    out_dir = Path(out_dir)
    result = {
        "source_pdf": str(pdf_path),
        "openability": "ok",
        "open_error": "",
        "page_count": 0,
        "pages": [],
        "encrypted": False,
        "likely_scanned": False,
        "scanned_page_count": 0,
        "text_density_min_chars_per_page": None,
        "non_embedded_fonts": [],
        "non_embedded_font_details": [],
        "normalization_required": False,
        "normalization_reasons": [],
        "coordinate_space": "cropbox_rotated",
    }
    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:  # noqa: BLE001
        result["openability"] = "failed"
        result["open_error"] = str(exc)
        _dump(out_dir / "preflight.json", result)
        return result, False

    result["encrypted"] = bool(doc.is_encrypted)
    result["page_count"] = doc.page_count

    fonts = set()
    font_details = []
    min_density = None
    scanned = 0
    for page_index in range(doc.page_count):
        page = doc[page_index]
        media = [round(v, 3) for v in page.mediabox]
        crop = [round(v, 3) for v in page.cropbox]
        rotation = int(page.rotation or 0)
        entry = {
            "page": page_index + 1,
            "media_box": media,
            "crop_box": crop,
            "rotation": rotation,
            "width": round(page.rect.width, 3),
            "height": round(page.rect.height, 3),
        }
        result["pages"].append(entry)
        text_len = _page_text_len(page)
        min_density = text_len if min_density is None \
            else min(min_density, text_len)
        if _page_image_only(page):
            scanned += 1
        for f in page.get_fonts(full=True):
            # get_fonts row: (xref, ext, type, basefont, name, encoding,
            #                 emb, ...)
            basefont = f[3] or f[4] or ""
            emb = f[6] if len(f) > 6 else 0
            if not emb:
                fonts.add(basefont)
                font_details.append({
                    "page": page_index + 1, "font": basefont,
                    "type": f[2] if len(f) > 2 else "",
                })
    doc.close()

    result["scanned_page_count"] = scanned
    result["text_density_min_chars_per_page"] = min_density
    result["likely_scanned"] = (
        result["page_count"] > 0
        and (scanned / result["page_count"] >= SCANNED_PAGE_RATIO
             or (min_density is not None and min_density < SCANNED_MIN_CHARS)))
    result["non_embedded_fonts"] = sorted(fonts)
    result["non_embedded_font_details"] = font_details

    # normalization: rotation != 0 or crop != media
    for entry in result["pages"]:
        if entry["rotation"] != 0:
            result["normalization_required"] = True
            result["normalization_reasons"].append(
                "page %d rotation=%d" % (entry["page"], entry["rotation"]))
        if entry["crop_box"] != entry["media_box"]:
            result["normalization_required"] = True
            result["normalization_reasons"].append(
                "page %d crop_box != media_box" % entry["page"])
    if result["encrypted"]:
        result["normalization_required"] = True
        result["normalization_reasons"].append("encrypted")

    allowed = result["openability"] == "ok" and not result["normalization_required"]
    _dump(out_dir / "preflight.json", result)
    return result, allowed


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result, allowed = run_preflight(args.pdf, args.out)
    print(json.dumps({**result, "production_run_allowed": allowed},
                     ensure_ascii=False, indent=2))
    return 0 if allowed else 2


if __name__ == "__main__":
    sys.exit(main())
