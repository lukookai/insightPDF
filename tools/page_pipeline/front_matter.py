# -*- coding: utf-8 -*-
"""FrontMatterModel (Phase 4D.1) -- title / authors / affiliations /
abstract / figure / caption / footnotes classification.

Only enabled for page 1 or any page where a title-like block is detected
at the top.  Classification is geometric + typographic (size hierarchy,
y order, x span), driven by the PageLayoutGrid:

* title        -- topmost rows whose size >= body*1.2 and whose x span
                  covers most of the content frame (full-width, centered)
* authors      -- rows below the title with size in [body*1.0, body*1.3]
                  (12 pt authors vs 10.1 body), spanning a wide centered
                  band (not a single column)
* affiliations -- rows below the authors (12 pt), often containing
                  University / Institute / College keywords or preceded
                  by a numeric superscript marker row
* abstract     -- the "Abstract" heading row + following body rows until
                  the first full-width row / section heading / figure
* figure       -- vector drawings cluster (Figure 1 is vector, not an
                  embedded image on p001) with its caption ("Figure N:")
* footnotes    -- small rows (size <= body*0.75) starting with * / † / a
                  numeric marker, located below the abstract/figure area

Outputs a dict with bboxes in source-PDF coordinates (evidence for
4D.1B; the final layout is produced by the grid + FrontMatterModel).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
for p in (HERE,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from page_layout_grid import infer_page_grid  # noqa: E402

INSTITUTION_RE = re.compile(
    r"\b(?:University|Institute|College|School|Laboratory|Lab|Academy|"
    r"Corporation|Company|Center|Centre|Group|Team)\b", re.I)
FIGURE_CAPTION_RE = re.compile(r"^\s*Figure\s*\d+\s*:", re.I)
ABSTRACT_HEADING_RE = re.compile(r"^\s*Abstract\s*$", re.I)


def _collect_rows(page, body):
    """y-clustered text rows: {y_center, y0, y1, x0, x1, size, text, spans}."""
    rows = {}
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            spans = []
            for s in line.get("spans", []):
                t = (s.get("text") or "").strip()
                if not t:
                    continue
                spans.append({"text": t,
                              "bbox": [float(v) for v in s["bbox"]],
                              "size": float(s.get("size") or 0),
                              "font": s.get("font") or ""})
            if not spans:
                continue
            bbox = line.get("bbox")
            yc = round((bbox[1] + bbox[3]) / 2 / 2.0) * 2
            size = max(s["size"] for s in spans)
            text = "".join(s["text"] for s in spans).strip()
            rows.setdefault(yc, []).append({
                "y": yc, "y0": bbox[1], "y1": bbox[3],
                "x0": min(s["bbox"][0] for s in spans),
                "x1": max(s["bbox"][2] for s in spans),
                "size": size, "text": text, "spans": spans})
    out = []
    for yc in sorted(rows):
        group = rows[yc]
        x0 = min(r["x0"] for r in group)
        x1 = max(r["x1"] for r in group)
        y0 = min(r["y0"] for r in group)
        y1 = max(r["y1"] for r in group)
        text = " ".join(r["text"] for r in sorted(group, key=lambda r: r["x0"]))
        out.append({"y": yc, "y0": y0, "y1": y1, "x0": x0, "x1": x1,
                    "size": max(r["size"] for r in group),
                    "text": text})
    return out


def _row_bbox(row):
    return [round(v, 2) for v in (row["x0"], row["y0"], row["x1"], row["y1"])]


def classify_front_matter(pdf_path, page_index, grid=None):
    """FrontMatterModel for one page (returns None when no title)."""
    doc = pymupdf.open(str(pdf_path))
    page = doc[page_index]
    width, height = page.rect.width, page.rect.height
    grid = grid or infer_page_grid(pdf_path, page_index)
    body = grid.get("body_size_estimated") or 10.5
    rows = _collect_rows(page, body)
    doc.close()

    cf = grid["content_frame"]
    cf_w = max(cf["x1"] - cf["x0"], 1.0)
    gutter = grid.get("gutter")

    # ---- 1. title: topmost full-width large rows ----
    title_rows = []
    title_end = None
    for r in rows:
        if r["size"] < body * 1.2:
            break  # rows are y-ordered; title is at the very top
        if (r["x1"] - r["x0"]) / cf_w >= 0.6:
            title_rows.append(r)
            title_end = r["y1"]
        else:
            break
    if not title_rows:
        return None  # no front matter detected
    title = {"text": " ".join(r["text"] for r in title_rows)[:200],
             "bbox": _row_bbox({"x0": min(r["x0"] for r in title_rows),
                                "y0": min(r["y0"] for r in title_rows),
                                "x1": max(r["x1"] for r in title_rows),
                                "y1": max(r["y1"] for r in title_rows)}),
             "rows": len(title_rows),
             "width_ratio": round((max(r["x1"] for r in title_rows)
                                   - min(r["x0"] for r in title_rows)) / cf_w, 2)}

    # ---- 2/3. authors + affiliations (rows after title) ----
    author_rows = []
    affiliation_rows = []
    abstract_heading = None
    for r in rows:
        if title_end and r["y0"] <= title_end + 2:
            continue
        if abstract_heading is None and ABSTRACT_HEADING_RE.match(r["text"]):
            abstract_heading = r
            continue
        if abstract_heading is not None:
            break  # everything after the Abstract heading is abstract/figure
        if body * 0.95 <= r["size"] <= body * 1.35:
            if INSTITUTION_RE.search(r["text"]):
                affiliation_rows.append(r)
            else:
                author_rows.append(r)
    authors = {"names": [r["text"] for r in author_rows],
               "bbox": ({"x0": min(r["x0"] for r in author_rows),
                         "y0": min(r["y0"] for r in author_rows),
                         "x1": max(r["x1"] for r in author_rows),
                         "y1": max(r["y1"] for r in author_rows)}
                        if author_rows else None)}
    affiliations = {"names": [r["text"] for r in affiliation_rows],
                    "bbox": ({"x0": min(r["x0"] for r in affiliation_rows),
                              "y0": min(r["y0"] for r in affiliation_rows),
                              "x1": max(r["x1"] for r in affiliation_rows),
                              "y1": max(r["y1"] for r in affiliation_rows)}
                             if affiliation_rows else None)}

    # ---- 4. abstract body: left-column rows after the Abstract heading,
    #        size in the body band, until the first section heading ----
    abstract_rows = []
    for r in rows:
        if abstract_heading is None:
            break
        if r["y0"] <= abstract_heading["y1"]:
            continue
        if not (body * 0.85 <= r["size"] <= body * 1.15):
            continue  # arXiv stamp (20 pt) / footnotes (9 pt) excluded
        if not (cf["x0"] - 2 <= r["x0"] <= (gutter["x0"] if gutter else
                                            (cf["x0"] + cf["x1"]) / 2) + 2):
            continue  # right column (figure caption) / outside content
        if r["x1"] > (gutter["x0"] if gutter else cf["x1"]) + 2:
            continue
        # section heading ("1 Introduction"): stop
        if (r["size"] >= body * 1.05
                and re.match(r"^\s*\d+(?:\.\d+)*\s+[A-Z]", r["text"])):
            break
        if FIGURE_CAPTION_RE.match(r["text"]):
            break
        abstract_rows.append(r)
    abstract = {
        "heading": abstract_heading["text"] if abstract_heading else None,
        "heading_bbox": _row_bbox(abstract_heading) if abstract_heading else None,
        "body_bbox": ({"x0": min(r["x0"] for r in abstract_rows),
                       "y0": min(r["y0"] for r in abstract_rows),
                       "x1": max(r["x1"] for r in abstract_rows),
                       "y1": max(r["y1"] for r in abstract_rows)}
                      if abstract_rows else None),
        "rows": len(abstract_rows),
        "column": ("left" if abstract_rows
                   and abstract_rows[0]["x0"] < (cf["x0"] + cf["x1"]) / 2
                   else "full_width"),
    }

    # ---- 5. figure: drawings cluster in a column (right side on p001) ----
    doc = pymupdf.open(str(pdf_path))
    page = doc[page_index]
    drawings = [d for d in page.get_drawings()
                if d["rect"].width > 5 and d["rect"].height > 5]
    doc.close()
    figure_bbox = None
    if drawings:
        gutter_x0 = (gutter["x0"] if gutter else (cf["x0"] + cf["x1"]) / 2)
        right = [d for d in drawings if d["rect"].x0 > gutter_x0]
        pool = right if len(right) >= 3 else drawings
        # y-cluster the pool; take the largest cluster
        pool.sort(key=lambda d: d["rect"].y0)
        clusters = []
        cur = None
        for d in pool:
            r = d["rect"]
            if cur is None or r.y0 - cur["y1"] > 15:
                if cur:
                    clusters.append(cur)
                cur = {"y0": r.y0, "y1": r.y1, "x0": r.x0, "x1": r.x1, "n": 1}
            else:
                cur["y1"] = max(cur["y1"], r.y1)
                cur["x0"] = min(cur["x0"], r.x0)
                cur["x1"] = max(cur["x1"], r.x1)
                cur["n"] += 1
        if cur:
            clusters.append(cur)
        clusters = [c for c in clusters if c["n"] >= 3]
        if clusters:
            main = max(clusters, key=lambda c: c["n"])
            if main["x1"] - main["x0"] > 20 and main["y1"] - main["y0"] > 20:
                figure_bbox = [round(main["x0"], 1), round(main["y0"], 1),
                               round(main["x1"], 1), round(main["y1"], 1)]
    figure_caption = None
    for r in rows:
        if FIGURE_CAPTION_RE.match(r["text"]):
            figure_caption = {"text": r["text"][:160],
                              "bbox": _row_bbox(r)}
            break

    # ---- 6. footnotes: small rows in the bottom band of the page ----
    footnotes = []
    for r in rows:
        if r["size"] > body * 0.9:
            continue
        if r["y0"] < height * 0.75:
            continue  # only the bottom of the page
        if re.match(r"^\s*[∗*†‡1-9]", r["text"]) or "contribution" in r["text"].lower():
            footnotes.append({"text": r["text"][:160], "bbox": _row_bbox(r)})

    model = {
        "page": page_index + 1,
        "title": title,
        "authors": authors,
        "affiliations": affiliations,
        "abstract_heading": abstract["heading"],
        "abstract_heading_bbox": abstract["heading_bbox"],
        "abstract_body": {"text": "",
                          "bbox": abstract["body_bbox"],
                          "rows": abstract["rows"],
                          "column": abstract["column"]},
        "figure": {"bbox": figure_bbox, "vector": bool(figure_bbox)},
        "figure_caption": figure_caption,
        "footnotes": footnotes,
        "grid": {"content_frame": cf,
                 "columns": grid["columns"],
                 "gutter": grid["gutter"]},
        "confidence": 0.9 if (authors.get("bbox") and affiliations.get("bbox")
                              and abstract_heading) else 0.7,
    }
    return model


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    m = classify_front_matter(args.pdf, args.page - 1)
    print(json.dumps(m, ensure_ascii=False, indent=1) if m else "null")
    if args.out and m:
        Path(args.out).write_text(
            json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
