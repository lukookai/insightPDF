# -*- coding: utf-8 -*-
"""FinalFootnoteCollisionQA - final Chromium PDF is the geometry truth."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf

from bottom_reserved_region import footnote_paragraph_ids


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _intersection(a, b):
    x = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    y = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return x * y


def _pdf_lines(pdf_path):
    doc = pymupdf.open(str(pdf_path))
    page = doc[0]
    lines = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if (s.get("text") or "").strip()]
            if not spans:
                continue
            text = "".join(s.get("text") or "" for s in spans).strip()
            lines.append({"text": text,
                          "bbox": [float(v) for v in line["bbox"]]})
    page_box = [float(v) for v in page.rect]
    count = len(doc)
    doc.close()
    return lines, page_box, count


def _looks_like_footnote_start(text):
    t = re.sub(r"\s+", "", text or "")
    return (t.startswith("*") and any(x in t for x in (
        "同等贡献", "Equalcontribution", "通讯作者", "Correspondingauthor")))


def final_footnote_collision_qa(page_model, pdf_path, *, frontmatter=None,
                                bottom_reserved_regions=None, out_dir=None):
    """Measure body/footnote line bboxes extracted from the final PDF."""
    lines, page_box, physical_pages = _pdf_lines(pdf_path)
    owners = footnote_paragraph_ids(page_model, frontmatter)
    reserved = (bottom_reserved_regions or [])
    expected = reserved[0] if reserved else None

    starts = [i for i, row in enumerate(lines) if _looks_like_footnote_start(row["text"])]
    foot_lines = []
    if starts:
        start = starts[0]
        first = lines[start]
        foot_lines.append(first)
        # Continuation lines stay full-width/left-aligned and within three
        # footnote line heights of the detected first line.
        for row in lines:
            if row is first:
                continue
            if (row["bbox"][1] >= first["bbox"][1] - 0.5
                    and row["bbox"][1] <= first["bbox"][3] + 30.0
                    and row["bbox"][0] < page_model.get("width", 0) * 0.55):
                foot_lines.append(row)
    footnote_bbox = _union([r["bbox"] for r in foot_lines]) if foot_lines else None

    columns = []
    if page_model.get("page_layout_grid"):
        columns = page_model["page_layout_grid"].get("columns") or []
    if not columns:
        # Final 4D tracks are stable; infer them from paragraph column data
        # without using the footnote's full-width rendered bbox.
        for ci in (0, 1):
            ps = [r["payload"] for r in page_model.get("regions", [])
                  if r.get("type") == "text" and r["payload"].get("column") == ci
                  and r["payload"].get("paragraph_id") not in owners]
            if ps:
                columns.append({"column_id": ci,
                                "x0": min(p.get("col_x0", p["bbox"][0]) for p in ps),
                                "x1": max(p.get("col_x1", p["bbox"][2]) for p in ps)})

    foot_ids = {id(x) for x in foot_lines}
    body_by_col = {0: [], 1: []}
    for row in lines:
        if id(row) in foot_ids:
            continue
        cx = (row["bbox"][0] + row["bbox"][2]) / 2.0
        for ci, col in enumerate(columns[:2]):
            if float(col["x0"]) - 2 <= cx <= float(col["x1"]) + 2:
                body_by_col[ci].append(row)
                break
    bottoms = {ci: (max((r["bbox"][3] for r in rows), default=None))
               for ci, rows in body_by_col.items()}
    overlaps = []
    if footnote_bbox:
        for ci, rows in body_by_col.items():
            for row in rows:
                area = _intersection(row["bbox"], footnote_bbox)
                if area > 0.01:
                    overlaps.append({"column": ci, "text": row["text"][:120],
                                     "bbox": [round(v, 3) for v in row["bbox"]],
                                     "overlap_area": round(area, 3)})
    body_bottom = max((b for b in bottoms.values() if b is not None), default=None)
    gap = (footnote_bbox[1] - body_bottom
           if footnote_bbox and body_bottom is not None else None)
    clip = 0
    if footnote_bbox and (footnote_bbox[0] < page_box[0] - .1
                          or footnote_bbox[1] < page_box[1] - .1
                          or footnote_bbox[2] > page_box[2] + .1
                          or footnote_bbox[3] > page_box[3] + .1):
        clip = 1
    result = {
        "footnote_bbox": ([round(v, 3) for v in footnote_bbox]
                          if footnote_bbox else None),
        "left_body_bottom": round(bottoms[0], 3) if bottoms[0] is not None else None,
        "right_body_bottom": round(bottoms[1], 3) if bottoms[1] is not None else None,
        "body_footnote_overlap_count": len(overlaps),
        "body_footnote_overlap_area": round(sum(x["overlap_area"] for x in overlaps), 3),
        "minimum_body_footnote_gap": round(gap, 3) if gap is not None else None,
        "footnote_clipping_count": clip,
        "physical_page_count": physical_pages,
        "bottom_reserved_region": expected,
        "overlap_details": overlaps[:16],
        "footnote_collision_clean": bool(
            footnote_bbox and not overlaps and gap is not None and gap > 0
            and not clip and physical_pages == 1),
    }
    if out_dir:
        path = Path(out_dir) / "footnote_collision_qa.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))
    result = final_footnote_collision_qa(model, args.pdf, out_dir=args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["footnote_collision_clean"] else 2


if __name__ == "__main__":
    sys.exit(main())
