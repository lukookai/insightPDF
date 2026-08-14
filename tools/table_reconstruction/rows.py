"""Row reconstruction from text y-clustering.

Spans that share the same baseline / center-y belong to the same logical row.
This is what lets a "Title" cell that PyMuPDF split into two spans (because it
wrapped) be re-merged into one row / one cell instead of two table rows.

Header detection: if there are >= 2 horizontal rules inside the ROI, the row
whose center-y falls between the first and second rule is marked as the header
row.
"""

from __future__ import annotations

import statistics


def _roi_texts(texts, roi, margin=0.5):
    rx0, ry0, rx1, ry1 = roi
    out = []
    for t in texts:
        b = t["bbox"]
        if (b[1] >= ry0 - margin and b[3] <= ry1 + margin and
                b[0] >= rx0 - margin and b[2] <= rx1 + margin):
            out.append(t)
    return out


def _cluster_rows(spans_sorted, tol):
    """Greedy 1-D clustering of spans (sorted by center-y) into rows."""
    clusters = []
    cur = [spans_sorted[0]]
    cur_center = (spans_sorted[0]["bbox"][1] + spans_sorted[0]["bbox"][3]) / 2.0
    for t in spans_sorted[1:]:
        cy = (t["bbox"][1] + t["bbox"][3]) / 2.0
        if cy - cur_center <= tol:
            cur.append(t)
            cur_center = sum((s["bbox"][1] + s["bbox"][3]) / 2.0
                             for s in cur) / len(cur)
        else:
            clusters.append(cur)
            cur = [t]
            cur_center = cy
    clusters.append(cur)
    return clusters


def _header_row_index(rows, h_lines_in, roi):
    if not h_lines_in or len(h_lines_in) < 2:
        return None
    hs = sorted(h_lines_in, key=lambda l: (l["y0"] + l["y1"]) / 2.0)
    band0 = (hs[0]["y0"] + hs[0]["y1"]) / 2.0
    band1 = (hs[1]["y0"] + hs[1]["y1"]) / 2.0
    for r in rows:
        cy = (r["y0"] + r["y1"]) / 2.0
        if band0 - 1.0 <= cy <= band1 + 1.0:
            return r["index"]
    return None


def reconstruct_rows(roi, texts_in, h_lines_in=None):
    rx0, ry0, rx1, ry1 = roi
    spans = _roi_texts(texts_in, roi)
    if not spans:
        return {"rows": [], "spans_by_row": {}, "header_row_index": None,
                "row_centers": [], "row_bands": []}

    heights = [t["bbox"][3] - t["bbox"][1] for t in spans]
    med_h = statistics.median(heights) if heights else 6.0
    tol = max(2.0, 0.7 * med_h)

    spans_sorted = sorted(
        spans, key=lambda t: (t["bbox"][1] + t["bbox"][3]) / 2.0)
    clusters = _cluster_rows(spans_sorted, tol)

    rows = []
    spans_by_row = {}
    row_centers = []
    for idx, cl in enumerate(clusters):
        y0 = min(t["bbox"][1] for t in cl)
        y1 = max(t["bbox"][3] for t in cl)
        cy = (y0 + y1) / 2.0
        rows.append({
            "index": idx,
            "y0": round(y0, 3),
            "y1": round(y1, 3),
            "x0": round(rx0, 3),
            "x1": round(rx1, 3),
        })
        spans_by_row[idx] = cl
        row_centers.append(round(cy, 3))

    header_idx = _header_row_index(rows, h_lines_in, roi)

    return {
        "rows": rows,
        "spans_by_row": spans_by_row,
        "header_row_index": header_idx,
        "row_centers": row_centers,
        "row_bands": [[r["y0"], r["y1"]] for r in rows],
    }
