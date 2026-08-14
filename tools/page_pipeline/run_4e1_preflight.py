# -*- coding: utf-8 -*-
"""Phase 4E.1 -- preflight + source document profiling (read-only).

Runs against the NEW unseen Ding CVPR 2026 PDF using only PyMuPDF (read-only)
plus the production raw PageModel already built by the pipeline.  NO
adaptation, NO tuning -- this profile is observation only.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

PDF = (r"C:\Users\74496\Desktop\Ding_SynthRGB-T_Language-Vision_Guided_Image_"
       r"Translation_for_Diversity_Synthesis_CVPR_2026_paper.pdf")
OUT = REPO / "outputs" / "phase4e1_ding_generalization"

MATH_FONT_HINTS = ("cmr", "cmmi", "cmsy", "cmex", "cmbx", "msam", "msbm")
MONO_HINTS = ("nimbusmon", "courier", "mono")
CAPTION_RE = re.compile(r"^(?:Fig\.?|Figure|Table|Tab\.?)\s*\d", re.I)
SECTION_RE = re.compile(r"^\d+(?:\.\d+)*\s+[A-Z]|^[IVX]+\.\s+[A-Z]")
REF_HEAD_RE = re.compile(r"^(?:References|Bibliography)\s*$")


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _span_bbox(s):
    b = s.get("bbox") or s.get("origin") or []
    return [float(b[0]), float(b[1]), float(b[2]), float(b[3])]


def preflight():
    doc = pymupdf.open(PDF)
    fonts = set()
    images = 0
    drawings = 0
    spans = 0
    for pno in range(doc.page_count):
        page = doc[pno]
        for f in page.get_fonts():
            fonts.add(f[3])
        images += len(page.get_images(full=True))
        drawings += len(page.get_drawings())
        d = page.get_text("dict")
        spans += sum(len(l.get("spans", []))
                     for b in d.get("blocks", []) for l in b.get("lines", []))
    sha = hashlib.sha256(Path(PDF).read_bytes()).hexdigest()
    p0 = doc[0]
    result = {
        "source_path": PDF,
        "sha256": sha,
        "physical_page_count": doc.page_count,
        "pdf_version": (doc.metadata or {}).get("format"),
        "MediaBox": [float(v) for v in p0.mediabox],
        "CropBox": [float(v) for v in p0.cropbox],
        "rotation": p0.rotation,
        "encrypted": doc.is_encrypted,
        "embedded_fonts": sorted(fonts),
        "image_xobject_count": images,
        "vector_drawing_count": drawings,
        "text_span_count": spans,
        "produced_by": (doc.metadata or {}).get("producer", ""),
        "creator": (doc.metadata or {}).get("creator", ""),
    }
    doc.close()
    _dump(OUT / "preflight.json", result)
    return result


def _page_source_stats(page_index, model):
    """Combine PyMuPDF raw stats with the production raw PageModel."""
    doc = pymupdf.open(PDF)
    page = doc[page_index]
    rect = page.rect
    d = page.get_text("dict")
    spans = []
    math_spans = 0
    mono_spans = 0
    rotated = 0
    all_bbox = []
    lines = []
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            line_spans = [s for s in line.get("spans", []) if (s.get("text") or "").strip()]
            if not line_spans:
                continue
            text = "".join(s["text"] for s in line_spans)
            lines.append({"text": text, "bbox": [float(v) for v in line["bbox"]]})
            for s in line_spans:
                font = (s.get("font") or "").lower()
                spans.append(s)
                all_bbox.append(_span_bbox(s))
                if any(h in font for h in MATH_FONT_HINTS):
                    math_spans += 1
                if any(h in font for h in MONO_HINTS):
                    mono_spans += 1
                if s.get("dir") and s["dir"] not in ((0, 1), (1, 0)):
                    rotated += 1
    # column detection via the production paragraphs.detect_columns
    from paragraphs import detect_columns
    cols = detect_columns([{"bbox": _span_bbox(s)} for s in spans],
                          page_w=rect.width)
    ncols = len(cols)
    gutter = None
    if ncols >= 2:
        gutter = round(cols[0][1] - cols[0][0], 2) if False else None
        # gap between column[0].x1 and column[1].x0
        gutter = round(cols[1][0] - cols[0][1], 2)
    # content frame from all text bboxes
    x0 = min(b[0] for b in all_bbox) if all_bbox else 0
    y0 = min(b[1] for b in all_bbox) if all_bbox else 0
    x1 = max(b[2] for b in all_bbox) if all_bbox else rect.width
    y1 = max(b[3] for b in all_bbox) if all_bbox else rect.height

    images = page.get_images(full=True)
    img_area = 0.0
    for img in images:
        try:
            r = page.get_image_bbox(img)
            img_area += abs(r.width * r.height)
        except Exception:  # noqa: BLE001
            pass
    drawings = page.get_drawings()
    # captions / headings / refs from text lines
    captions = [l for l in lines if CAPTION_RE.match(l["text"].strip())]
    headings = [l for l in lines if SECTION_RE.match(l["text"].strip())]
    list_items = [l for l in lines if re.match(r"^\s*(?:[•·\-–]|\d+[.)])\s", l["text"])]
    refs = [l for l in lines if REF_HEAD_RE.match(l["text"].strip())]

    types = Counter(r["type"] for r in model.get("regions", []))
    roles = Counter((r.get("payload") or {}).get("style_role", "?")
                    for r in model.get("regions", []) if r.get("type") == "text")
    page_area = rect.width * rect.height
    doc.close()
    return {
        "page": page_index + 1,
        "page_size": [round(rect.width, 2), round(rect.height, 2)],
        "detected_column_count": ncols,
        "column_bounds": [[round(a, 2), round(b, 2)] for a, b in cols],
        "gutter": gutter,
        "inferred_content_frame": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
        "text_span_count": len(spans),
        "math_span_count": math_spans,
        "mono_span_count": mono_spans,
        "rotated_span_count": rotated,
        "image_xobject_count": len(images),
        "raster_image_coverage_ratio": round(img_area / page_area, 4),
        "vector_drawing_count": len(drawings),
        "caption_count": len(captions),
        "heading_line_count": len(headings),
        "list_item_line_count": len(list_items),
        "reference_head_present": bool(refs),
        "region_counts": dict(types),
        "paragraph_roles": dict(roles),
    }


def build_source_profile():
    stats = []
    for p in range(1, 12):
        model = json.loads(
            (OUT / "pages" / ("p%03d" % p) / "raw_page_model.json").read_text(
                encoding="utf-8"))
        stats.append(_page_source_stats(p - 1, model))
    single = [s for s in stats if s["detected_column_count"] == 1]
    double = [s for s in stats if s["detected_column_count"] == 2]
    mixed = [s for s in stats if s["detected_column_count"] > 2]
    formula_dense = [s["page"] for s in stats if s["region_counts"].get("formula", 0) >= 8]
    fig_dense = [s["page"] for s in stats if s["region_counts"].get("figure", 0) >= 2
                 or s["image_xobject_count"] >= 40]
    table_dense = [s["page"] for s in stats if s["region_counts"].get("table", 0) >= 2]
    doc = {
        "document_level": {
            "single_column_pages": [s["page"] for s in single],
            "double_column_pages": [s["page"] for s in double],
            "mixed_column_pages": [s["page"] for s in mixed],
            "unusual_layout_pages": [],
            "formula_dense_pages": formula_dense,
            "figure_dense_pages": fig_dense,
            "table_dense_pages": table_dense,
            "front_matter_complexity": "single-column title + author block (CVPR style)",
            "references_pages": [s["page"] for s in stats if s["reference_head_present"]],
        },
        "per_page": stats,
    }
    _dump(OUT / "document_source_profile.json", doc)
    return doc


def overview_board():
    """Thumbnail board of all source pages (detected regions overlaid)."""
    from PIL import ImageDraw
    cell_w = 170
    cell_h = int(cell_w * 792 / 612)
    board = Image.new("RGB", (cell_w * 4, cell_h * 3), (250, 250, 250))
    for i in range(11):
        page_idx = i
        doc = pymupdf.open(PDF)
        pix = doc[page_idx].get_pixmap(matrix=pymupdf.Matrix(cell_w / 612, cell_w / 612))
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        doc.close()
        col, row = i % 4, i // 4
        board.paste(img, (col * cell_w, row * cell_h))
    path = OUT / "source_layout_overview.png"
    board.save(path)
    return path


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pf = preflight()
    print("preflight: pages=%d images=%d vectors=%d spans=%d"
          % (pf["physical_page_count"], pf["image_xobject_count"],
             pf["vector_drawing_count"], pf["text_span_count"]))
    prof = build_source_profile()
    dl = prof["document_level"]
    print("columns: single=%d double=%d mixed=%d"
          % (len(dl["single_column_pages"]), len(dl["double_column_pages"]),
             len(dl["mixed_column_pages"])))
    print("formula_dense=%s figure_dense=%s table_dense=%s"
          % (dl["formula_dense_pages"], dl["figure_dense_pages"],
             dl["table_dense_pages"]))
    board = overview_board()
    print("overview board:", board)
    return 0


if __name__ == "__main__":
    sys.exit(main())
