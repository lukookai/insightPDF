# -*- coding: utf-8 -*-
"""Path B: PyMuPDF raw formula analysis + custom formula candidate detection.

For each page we dump, purely from PyMuPDF:

  * every span (text / font / size / bbox / baseline / superscript-ish flags)
  * drawings (vector) and image blocks in math-looking regions
  * a custom formula-candidate detector that mimics BabelDOC's character rules
    (math fonts, Sm/Mn/Sk unicodes, Greek, digits, CID) but operates on
    PyMuPDF spans, so Path A vs Path B can be compared on equal footing.

source_mode classification per region: text_glyphs | vector | image | mixed.

Output: formula_geometry_raw.json
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pymupdf

MATH_FONT_HINTS = ("cmmi", "cmsy", "cmr", "cmm", "msam", "msbm", "mtsy",
                   "eufm", "math", "cambria math", "stix", "xits", "latinmodern math",
                   "asana math", "tex gyre", "nimbus math", "lmsyn", "lmr", "lmmi")


def is_math_font(font):
    low = (font or "").lower()
    return any(h in low for h in MATH_FONT_HINTS)


def is_math_char(ch):
    if not ch or ch.isspace():
        return False
    cat = unicodedata.category(ch[0])
    if cat in ("Sm", "Mn", "Sk", "Zl", "Zp", "Zs", "Co"):
        return True
    if 0x370 <= ord(ch[0]) < 0x400:  # Greek
        return True
    if "(cid:" in ch:
        return True
    return False


def is_math_digit(ch):
    return ch and ch[0].isdigit() or (ch and ch[0] in "[]\u2022")


def collect_page_raw(pdf: Path, page_index: int) -> dict:
    doc = pymupdf.open(str(pdf))
    pg = doc[page_index]
    page_w, page_h = float(pg.rect.width), float(pg.rect.height)

    spans = []
    for block in pg.get_text("dict").get("blocks", []):
        if block.get("type") == 1:  # image
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = (span.get("text") or "")
                if not txt.strip():
                    continue
                b = [round(v, 3) for v in span["bbox"]]
                spans.append({
                    "text": txt,
                    "font": span.get("font"),
                    "font_size": round(float(span.get("size") or 0), 3),
                    "bbox": b,
                    "baseline": round(b[3], 3),
                    "flags": span.get("flags", 0),
                    "superscript": bool(span.get("flags", 0) & 1),
                    "italic": bool(span.get("flags", 0) & 2),
                    "serifed": bool(span.get("flags", 0) & 4),
                    "monospaced": bool(span.get("flags", 0) & 8),
                    "bold": bool(span.get("flags", 0) & 16),
                    "y_line": round((b[1] + b[3]) / 2.0, 3),
                })
    # drawings (vector) + images
    drawings = []
    for d in pg.get_drawings():
        r = d.get("rect")
        if r is None:
            continue
        x0, y0, x1, y1 = [round(float(v), 3) for v in r]
        if x1 - x0 < 0.5 and y1 - y0 < 0.5:
            continue
        drawings.append({"type": d["type"], "bbox": [x0, y0, x1, y1],
                         "width": round(float(d.get("width") or 0), 3)})
    images = []
    for img in pg.get_images(full=True):
        xref = img[0]
        try:
            rects = pg.get_image_rects(xref)
        except Exception:
            rects = []
        for r in rects:
            images.append({"xref": xref, "bbox": [round(float(v), 3) for v in r]})
    doc.close()
    return {"page": page_index + 1, "page_size": [page_w, page_h],
            "spans": spans, "drawings": drawings, "images": images}


def custom_formula_detection(raw: dict, *, include_digits: bool = True) -> list[dict]:
    """Span-level formula candidates (BabelDOC-style character rules)."""
    # cluster spans into lines
    lines = {}
    for s in raw["spans"]:
        key = round(s["y_line"] * 2) / 2
        lines.setdefault(key, []).append(s)

    cands = []
    for key in sorted(lines):
        ss = sorted(lines[key], key=lambda s: s["bbox"][0])
        # a span is math if font is math OR any char is math
        run = []
        for s in ss:
            text = s["text"] or ""
            is_math = (is_math_font(s["font"])
                       or any(is_math_char(c) for c in text)
                       or (include_digits and any(is_math_digit(c) for c in text)))
            if is_math:
                run.append(s)
            else:
                if run:
                    cands.append(_run_to_candidate(run))
                    run = []
        if run:
            cands.append(_run_to_candidate(run))
    # merge overlapping candidates per line (consecutive runs close together)
    merged = []
    for c in cands:
        if merged and c["y_line"] == merged[-1]["y_line"] \
                and c["bbox"][0] - merged[-1]["bbox"][2] < 4.0:
            merged[-1] = _merge(merged[-1], c)
        else:
            merged.append(c)
    return merged


def _run_to_candidate(run):
    x0 = min(s["bbox"][0] for s in run)
    y0 = min(s["bbox"][1] for s in run)
    x1 = max(s["bbox"][2] for s in run)
    y1 = max(s["bbox"][3] for s in run)
    return {"bbox": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)],
            "y_line": run[0]["y_line"],
            "text": "".join(s["text"] for s in sorted(run, key=lambda s: s["bbox"][0])),
            "fonts": sorted({s["font"] for s in run}),
            "n_spans": len(run)}


def _merge(a, b):
    return {"bbox": [min(a["bbox"][0], b["bbox"][0]),
                     min(a["bbox"][1], b["bbox"][1]),
                     max(a["bbox"][2], b["bbox"][2]),
                     max(a["bbox"][3], b["bbox"][3])],
            "y_line": a["y_line"],
            "text": a["text"] + b["text"],
            "fonts": sorted(set(a["fonts"] + b["fonts"])),
            "n_spans": a["n_spans"] + b["n_spans"]}


def classify_source_mode(raw: dict, bbox) -> str:
    x0, y0, x1, y1 = bbox
    inner = [s for s in raw["spans"]
             if s["bbox"][2] > x0 + 1 and s["bbox"][0] < x1 - 1
             and s["bbox"][3] > y0 + 1 and s["bbox"][1] < y1 - 1]
    inner_draw = [d for d in raw["drawings"]
                  if d["bbox"][2] > x0 + 1 and d["bbox"][0] < x1 - 1
                  and d["bbox"][3] > y0 + 1 and d["bbox"][1] < y1 - 1]
    inner_img = [i for i in raw["images"]
                 if i["bbox"][2] > x0 + 1 and i["bbox"][0] < x1 - 1
                 and i["bbox"][3] > y0 + 1 and i["bbox"][1] < y1 - 1]
    has_text = bool(inner)
    has_vec = bool(inner_draw)
    has_img = bool(inner_img)
    if has_text and (has_vec or has_img):
        return "mixed"
    if has_img:
        return "image"
    if has_vec:
        return "vector"
    if has_text:
        return "text_glyphs"
    return "empty"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    raw = collect_page_raw(Path(args.pdf), args.page - 1)
    cands = custom_formula_detection(raw)
    for c in cands:
        c["source_mode"] = classify_source_mode(raw, c["bbox"])

    result = {
        "page": args.page,
        "page_size": raw["page_size"],
        "span_count": len(raw["spans"]),
        "drawing_count": len(raw["drawings"]),
        "image_count": len(raw["images"]),
        "custom_formula_candidates": cands,
        "all_spans": raw["spans"],
        "all_drawings": raw["drawings"],
        "all_images": raw["images"],
    }
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print("page %d: %d spans, %d drawings, %d images, %d custom formula cands"
          % (args.page, len(raw["spans"]), len(raw["drawings"]),
             len(raw["images"]), len(cands)))
    for c in cands[:12]:
        print("  %-12s bbox=%s %r %s" % (c["source_mode"], c["bbox"],
                                         c["text"][:40], c["fonts"]))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
