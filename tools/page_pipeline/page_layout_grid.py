# -*- coding: utf-8 -*-
"""PageLayoutGrid inference (Phase 4D.1) -- x-projection based.

The final layout constraint for a page comes from a PageLayoutGrid, NOT
from per-paragraph source bboxes.  Source geometry is evidence only.

Grid inference (page-local evidence + document-level prior):

1. collect body-ish text spans (size within [body*0.85, body*1.35],
   excluding tiny superscripts/footnotes and the oversized title);
2. build an x-occupancy histogram over the page width (1 pt bins,
   smoothed with a 5 pt window);
3. content frame = [first occupied x, last occupied x];
4. two-column detection = the widest low-occupancy valley between the
   left/right occupied bands (stable text bands on both sides);
5. single-column pages fall back to one column = content frame;
6. full-width regions = blocks spanning the whole content frame.

The DocumentLayoutProfile aggregates dominant margins / column count /
median column width / median gutter across pages so ordinary two-column
pages do not drift page-to-page.  Page 1 front matter must NOT pollute
pages 2..19 (front-matter spans are excluded by the size filter when they
are notably larger than the body size).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

BIN_PT = 1.0          # histogram bin width (pt)
SMOOTH_PT = 5.0       # smoothing window (pt)
OCCUPANCY_MIN = 1     # a bin is "occupied" when >= this many spans cover it
VALLEY_MIN_PT = 6.0   # gutter must be at least this wide to count
EDGE_PAD = 2.0        # content frame padding from extreme occupied bins


def _body_size_estimate(spans):
    """Dominant (mode) size of ordinary spans.

    The median is biased by front-matter rows on title pages (authors 12 pt,
    title 14.3 pt) and section headings; the MODE of the size distribution
    rounds to the body size (10-11 pt here), which lets the occupancy filter
    exclude title/authors/affiliations/headings from the column histogram.
    """
    from collections import Counter
    counts = Counter(round(s["size"], 1) for s in spans if s["size"] > 0)
    if not counts:
        return 10.0
    mode = counts.most_common(1)[0][0]
    return float(mode)


def _collect_body_spans(page, body_size, size_lo=0.85, size_hi=1.15,
                        min_len=2):
    """Text spans in the body-size band; excludes superscripts (size*0.5),
    footnotes (size*0.65), title/authors/affiliations and section headings
    (size >= mode*1.15) -- front-matter rows must not fill the gutter in
    the occupancy histogram."""
    out = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "").strip()
                sz = float(span.get("size") or 0)
                if not t or sz <= 0:
                    continue
                if not (body_size * size_lo <= sz <= body_size * size_hi):
                    continue
                b = [float(v) for v in span["bbox"]]
                out.append({"text": t, "bbox": b, "size": sz,
                            "font": span.get("font") or ""})
    return out


def _occupancy(spans, width):
    """x occupancy histogram: count spans covering each 1 pt bin."""
    n = int(width // BIN_PT) + 1
    occ = [0] * n
    for s in spans:
        x0 = max(0, int(s["bbox"][0] // BIN_PT))
        x1 = min(n - 1, int((s["bbox"][2] - 0.001) // BIN_PT))
        for i in range(x0, x1 + 1):
            occ[i] += 1
    # smooth
    win = max(1, int(SMOOTH_PT // BIN_PT))
    out = []
    for i in range(n):
        lo = max(0, i - win)
        hi = min(n, i + win + 1)
        out.append(sum(occ[lo:hi]) / (hi - lo))
    return out


def _occupancy_threshold(occ):
    peak = max(occ) or 1
    return max(2, peak * 0.2)


def _find_bands(occ, width, thresh):
    """Occupied bands [x0, x1] separated by below-threshold valleys."""
    n = len(occ)
    bands = []
    start = None
    for i, v in enumerate(occ):
        if v >= thresh:
            if start is None:
                start = i
        else:
            if start is not None:
                bands.append([start * BIN_PT, (i - 1) * BIN_PT + BIN_PT])
                start = None
    if start is not None:
        bands.append([start * BIN_PT, (n - 1) * BIN_PT + BIN_PT])
    # merge bands separated by tiny gaps (< 4 pt)
    merged = []
    for b in bands:
        if merged and b[0] - merged[-1][1] < 4.0:
            merged[-1][1] = b[1]
        else:
            merged.append(list(b))
    return merged


def _valleys(occ, width, thresh):
    """Low-occupancy runs [x0, x1] strictly inside the occupied area.

    A valley bin must be far below the page's peak occupancy: full-width
    front-matter rows (centered affiliations) still cover the gutter
    region with a few spans, so "zero occupancy" is too strict.  p001:
    gutter bins carry 4-6 spans vs 12-44 in the columns.
    """
    n = len(occ)
    inside = False
    valleys = []
    start = None
    for i, v in enumerate(occ):
        if v >= thresh:
            if start is not None:
                valleys.append([start * BIN_PT, i * BIN_PT])
                start = None
            inside = True
        else:
            if inside and start is None:
                start = i
    return valleys


def infer_page_grid(pdf_path, page_index, body_size=None):
    """Infer PageLayoutGrid for one page from its text-span x-projection."""
    doc = pymupdf.open(str(pdf_path))
    page = doc[page_index]
    width = page.rect.width
    height = page.rect.height
    # collect all spans first to estimate body size, then filter
    all_spans = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "").strip()
                sz = float(span.get("size") or 0)
                if t and sz > 0:
                    all_spans.append({"text": t,
                                      "bbox": [float(v) for v in span["bbox"]],
                                      "size": sz,
                                      "font": span.get("font") or ""})
    body = body_size or _body_size_estimate(all_spans)
    body_spans = [s for s in all_spans
                  if body * 0.85 <= s["size"] <= body * 1.15
                  and len(s["text"]) >= 2]
    doc.close()

    occ = _occupancy(body_spans, width)
    thresh = _occupancy_threshold(occ)
    bands = _find_bands(occ, width, thresh)
    valleys = _valleys(occ, width, thresh)

    # content frame from the extreme occupied bins of body spans
    if body_spans:
        cx0 = min(s["bbox"][0] for s in body_spans)
        cx1 = max(s["bbox"][2] for s in body_spans)
        cy0 = min(s["bbox"][1] for s in body_spans)
        cy1 = max(s["bbox"][3] for s in body_spans)
    else:
        cx0, cx1, cy0, cy1 = 0.0, width, 0.0, height
    content = [cx0, cx1]
    content_y = [cy0, cy1]

    # two-column: widest valley strictly inside [content_x0, content_x1]
    gutter = None
    columns = None
    two_column_evidence = False
    for v in sorted(valleys, key=lambda v: -(v[1] - v[0])):
        gx0, gx1 = v
        gx0 = max(gx0, content[0])
        gx1 = min(gx1, content[1])
        if gx1 - gx0 < VALLEY_MIN_PT:
            continue
        # require stable occupied bands on both sides
        left_band = any(b[1] <= gx0 + 1 and b[0] < gx0 for b in bands)
        right_band = any(b[0] >= gx1 - 1 and b[1] > gx1 for b in bands)
        if left_band and right_band:
            gutter = [gx0, gx1]
            columns = [
                {"column_id": 0,
                 "x0": round(content[0], 2),
                 "x1": round(gx0, 2),
                 "width": round(gx0 - content[0], 2)},
                {"column_id": 1,
                 "x0": round(gx1, 2),
                 "x1": round(content[1], 2),
                 "width": round(content[1] - gx1, 2)},
            ]
            two_column_evidence = True
            break

    if columns is None:
        columns = [{"column_id": 0, "x0": round(content[0], 2),
                    "x1": round(content[1], 2),
                    "width": round(content[1] - content[0], 2)}]
        gutter = None

    # Phase 4D.1 refinement: snap column edges to the DOMINANT text
    # alignment inside each band (the histogram gives the band, the
    # aligned spans give the exact margin).  p001 left column must snap to
    # x0=70.9 / x1=289.1, not the padded histogram extremes.
    if len(columns) == 2 and body_spans:
        from collections import Counter
        left = [s for s in body_spans if s["bbox"][0] < gutter[0] - 2]
        right = [s for s in body_spans if s["bbox"][2] > gutter[1] + 2]
        if left:
            x0s = Counter(round(s["bbox"][0], 1) for s in left
                          if s["bbox"][0] < gutter[0] - 2 + 8)
            x1s = Counter(round(s["bbox"][2], 1) for s in left
                          if s["bbox"][2] > columns[0]["x1"] - 8)
            if x0s:
                columns[0]["x0"] = x0s.most_common(1)[0][0]
            if x1s:
                columns[0]["x1"] = x1s.most_common(1)[0][0]
            columns[0]["width"] = round(columns[0]["x1"] - columns[0]["x0"], 2)
        if right:
            x0s = Counter(round(s["bbox"][0], 1) for s in right
                          if s["bbox"][0] < columns[1]["x0"] + 8)
            x1s = Counter(round(s["bbox"][2], 1) for s in right
                          if s["bbox"][2] > gutter[1] + 2 - 8)
            if x0s:
                columns[1]["x0"] = x0s.most_common(1)[0][0]
            if x1s:
                columns[1]["x1"] = x1s.most_common(1)[0][0]
            columns[1]["width"] = round(columns[1]["x1"] - columns[1]["x0"], 2)
        if columns[0]["x1"] < columns[1]["x0"]:
            gutter = [columns[0]["x1"], columns[1]["x0"]]
        else:
            gutter = None
            columns = [{"column_id": 0, "x0": round(content[0], 2),
                        "x1": round(content[1], 2),
                        "width": round(content[1] - content[0], 2)}]
            two_column_evidence = False

    # full-width regions: rows (any non-tiny size) spanning the content
    # frame -- title / authors / affiliations / cross-column captions.
    full_width_regions = []
    all_rows = {}
    for s in all_spans:
        if s["size"] < 7.0:
            continue
        key = round((s["bbox"][1] + s["bbox"][3]) / 2 / 2.0) * 2
        all_rows.setdefault(key, []).append(s)
    cf_x0, cf_x1 = content[0], content[1]
    cf_w = max(cf_x1 - cf_x0, 1.0)
    for ykey, row in sorted(all_rows.items()):
        rx0 = min(s["bbox"][0] for s in row)
        rx1 = max(s["bbox"][2] for s in row)
        if (rx1 - rx0) / cf_w >= 0.88:
            full_width_regions.append({
                "y_center": round(ykey, 1),
                "bbox": [round(rx0, 1),
                         round(min(s["bbox"][1] for s in row), 1),
                         round(rx1, 1),
                         round(max(s["bbox"][3] for s in row), 1)]})

    confidence = 1.0
    if not two_column_evidence:
        confidence = 0.6
    elif len(columns) == 2:
        valley_depth = max((occ[int(gutter[0])], occ[int(gutter[1]) - 1]))
        max_occ = max(occ) or 1
        if valley_depth > max_occ * 0.15:
            confidence = 0.8
        if abs(columns[0]["width"] - columns[1]["width"]) / max(
                columns[0]["width"], columns[1]["width"], 1.0) > 0.25:
            confidence = 0.85

    grid = {
        "page": page_index + 1,
        "page_width": round(width, 2),
        "page_height": round(height, 2),
        "body_size_estimated": round(body, 2),
        "content_frame": {"x0": round(content[0], 2), "y0": round(content_y[0], 2),
                          "x1": round(content[1], 2),
                          "y1": round(content_y[1], 2)},
        "columns": columns,
        "gutter": ({"x0": gutter[0], "x1": gutter[1],
                    "width": round(gutter[1] - gutter[0], 2)}
                   if gutter else None),
        "full_width_regions": full_width_regions[:24],
        "confidence": round(confidence, 2),
        "_bands": [[round(b[0], 1), round(b[1], 1)] for b in bands],
        "_valleys": [[round(v[0], 1), round(v[1], 1)] for v in valleys],
        "_occupancy_n": len(occ),
    }
    return grid


class DocumentLayoutProfile:
    """Document-level priors from all pages (median margins/gutter/columns)."""

    def __init__(self, pdf_path):
        self.pdf_path = str(pdf_path)
        self.grids = []
        self.profile = {}

    def build(self):
        doc = pymupdf.open(self.pdf_path)
        n = doc.page_count
        doc.close()
        grids = []
        for i in range(n):
            try:
                g = infer_page_grid(self.pdf_path, i)
                grids.append(g)
            except Exception:  # noqa: BLE001
                continue
        self.grids = grids
        two_col = [g for g in grids if g["gutter"]]
        gutters = [g["gutter"]["width"] for g in two_col]
        left_w = [g["columns"][0]["width"] for g in two_col]
        right_w = [g["columns"][1]["width"] for g in two_col]
        margins = []
        for g in grids:
            cf = g["content_frame"]
            margins.append(cf["x0"])
            margins.append(g["page_width"] - cf["x1"])
        self.profile = {
            "page_count": n,
            "grid_inferred": len(grids),
            "two_column_pages": len(two_col),
            "single_column_pages": len(grids) - len(two_col),
            "median_gutter_width": round(_median(gutters), 2) if gutters else None,
            "median_column_width_left": round(_median(left_w), 2) if left_w else None,
            "median_column_width_right": round(_median(right_w), 2) if right_w else None,
            "median_left_margin": round(_median([m for m in margins[::2]]), 2) if margins else None,
            "median_right_margin": round(_median([m for m in margins[1::2]]), 2) if margins else None,
            "gutter_pages": {g["page"]: round(g["gutter"]["width"], 2) for g in two_col},
        }
        # Phase 4D.1: median column tracks for the document-level prior
        # (snap target for ordinary two-column pages).
        if two_col:
            def med(vals):
                s = sorted(vals)
                m = len(s) // 2
                return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0
            self.profile["median_tracks"] = {
                "left": [round(med(c["columns"][0]["x0"] for c in two_col), 2),
                         round(med(c["columns"][0]["x1"] for c in two_col), 2)],
                "right": [round(med(c["columns"][1]["x0"] for c in two_col), 2),
                          round(med(c["columns"][1]["x1"] for c in two_col), 2)],
            }
        return self.profile


def _median(xs):
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    grid = infer_page_grid(args.pdf, args.page - 1)
    print(json.dumps(grid, ensure_ascii=False, indent=1))
    if args.out:
        Path(args.out).write_text(
            json.dumps(grid, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
