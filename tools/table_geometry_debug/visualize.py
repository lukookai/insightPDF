"""Phase 3: render the original page plus two diagnostic overlays.

All overlays are drawn in the same pixel space as the rendered page, which is
the PDF pt space scaled by ``ZOOM``.  Because both Path A and Path B emit
top-left pt coordinates, the same scaling maps both onto the page image.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

ZOOM = 2.0

COLORS = {
    "text": (0, 170, 0),
    "hline": (0, 90, 255),
    "vline": (230, 0, 0),
    "rect": (255, 150, 0),
    "table": (200, 0, 200),
}


def _scale(bbox, z):
    return [c * z for c in bbox]


def _draw_layer(base, data, z):
    img = base.copy()
    d = ImageDraw.Draw(img)

    for t in data.get("texts", []):
        x0, y0, x1, y1 = _scale(t["bbox"], z)
        d.rectangle([x0, y0, x1, y1], outline=COLORS["text"], width=1)

    for l in data.get("horizontal_lines", []):
        x0, y0, x1, y1 = _scale([l["x0"], l["y0"], l["x1"], l["y1"]], z)
        d.line([x0, y0, x1, y1], fill=COLORS["hline"], width=2)

    for l in data.get("vertical_lines", []):
        x0, y0, x1, y1 = _scale([l["x0"], l["y0"], l["x1"], l["y1"]], z)
        d.line([x0, y0, x1, y1], fill=COLORS["vline"], width=2)

    for r in data.get("rectangles", []):
        x0, y0, x1, y1 = _scale(r["bbox"] if "bbox" in r else [r["x0"], r["y0"], r["x1"], r["y1"]], z)
        d.rectangle([x0, y0, x1, y1], outline=COLORS["rect"], width=1)

    for tb in data.get("detected_tables", []):
        x0, y0, x1, y1 = _scale(tb["bbox"], z)
        d.rectangle([x0, y0, x1, y1], outline=COLORS["table"], width=3)

    _draw_legend(d, data)
    return img


def _draw_legend(d, data):
    lines = [
        ("text bbox", COLORS["text"], len(data.get("texts", []))),
        ("horizontal line", COLORS["hline"], len(data.get("horizontal_lines", []))),
        ("vertical line", COLORS["vline"], len(data.get("vertical_lines", []))),
        ("rectangle", COLORS["rect"], len(data.get("rectangles", []))),
        ("detected table", COLORS["table"], len(data.get("detected_tables", []))),
    ]
    pad = 6
    lh = 16
    box_h = pad * 2 + lh * len(lines)
    box_w = 200
    d.rectangle([4, 4, 4 + box_w, 4 + box_h], fill=(255, 255, 255), outline=(0, 0, 0))
    y = 4 + pad
    for label, color, count in lines:
        d.rectangle([12, y + 3, 12 + 12, y + 3 + 12], outline=color, width=2)
        d.text((30, y), f"{label}: {count}", fill=(0, 0, 0))
        y += lh


def make_overlays(pdf: Path, page: int, babeldoc_json: dict, geometry_json: dict, out_dir: Path) -> dict:
    pdf = Path(pdf).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    nn = f"{page:03d}"

    doc = pymupdf.open(pdf)
    pg = doc[page - 1]
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM))
    base = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()

    original_path = out_dir / f"page_{nn}_original.png"
    base.save(original_path)

    babel_path = out_dir / f"page_{nn}_babeldoc_overlay.png"
    _draw_layer(base, babeldoc_json, ZOOM).save(babel_path)

    geom_path = out_dir / f"page_{nn}_geometry_overlay.png"
    _draw_layer(base, geometry_json, ZOOM).save(geom_path)

    return {
        "original": str(original_path),
        "babeldoc_overlay": str(babel_path),
        "geometry_overlay": str(geom_path),
        "zoom": ZOOM,
        "image_size": [pix.width, pix.height],
    }
