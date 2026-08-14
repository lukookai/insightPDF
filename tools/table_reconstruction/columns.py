"""Column reconstruction from PDF text x-projection.

This does NOT require vertical rules.  Columns are recovered by projecting the
real PDF text objects inside the ROI onto the x-axis and finding whitespace
gaps between occupied bands:

  * Build a 1-D occupancy histogram over the ROI x-range (bin = 1 pt).
  * Each text span marks the bins it covers; occupancy is aggregated across all
    rows, so a wide column (e.g. a "Title" column whose text length varies per
    row) stays a single contiguous band.
  * Whitespace runs wider than ``GAP_MIN_PT`` become column boundaries.  The
    first / last column are anchored to the table bbox edges so the rebuilt grid
    lines up with the visible table rules.

This naturally handles:
  * right-aligned numeric columns (stable right edges -> stable occupied band),
  * a wide left-aligned text column (inferred from the other bands + bbox).

Optional ``v_lines_in`` seeds exact column boundaries for full-grid tables.
"""

from __future__ import annotations

import statistics

BIN_PT = 1.0
GAP_MIN_PT = 4.0  # a whitespace run wider than this splits columns


def _roi_texts(texts, roi, margin=0.5):
    rx0, ry0, rx1, ry1 = roi
    out = []
    for t in texts:
        b = t["bbox"]
        if (b[1] >= ry0 - margin and b[3] <= ry1 + margin and
                b[0] >= rx0 - margin and b[2] <= rx1 + margin):
            out.append(t)
    return out


def reconstruct_columns(roi, texts_in, h_lines_in=None, v_lines_in=None):
    rx0, ry0, rx1, ry1 = roi
    spans = _roi_texts(texts_in, roi)

    xL, xR = rx0, rx1
    nb = int((xR - xL) / BIN_PT) + 1
    occ = [0] * nb
    for t in spans:
        b = t["bbox"]
        i0 = max(0, int((b[0] - xL) / BIN_PT))
        i1 = min(nb - 1, int((b[2] - xL) / BIN_PT))
        for i in range(i0, i1 + 1):
            occ[i] += 1

    # Only gaps *between* occupied bands split columns.  The leading / trailing
    # empty margin (between the ROI edge and the first/last text) is absorbed
    # into the first / last column and must NOT create phantom columns.
    if not any(occ):
        gaps = []
    else:
        first_occ = next(i for i in range(nb) if occ[i] > 0)
        last_occ = nb - 1 - next(i for i in range(nb) if occ[nb - 1 - i] > 0)
        gaps = []
        i = first_occ
        while i <= last_occ:
            if occ[i] == 0:
                j = i
                while j <= last_occ and occ[j] == 0:
                    j += 1
                g0 = xL + i * BIN_PT
                g1 = xL + j * BIN_PT
                if (g1 - g0) >= GAP_MIN_PT:
                    gaps.append((g0, g1))
                i = j
            else:
                i += 1

    # boundary x positions: ROI edges + gap midpoints + internal v-line x
    extra = []
    for l in (v_lines_in or []):
        x = (l["x0"] + l["x1"]) / 2.0
        if rx0 + 1.0 < x < rx1 - 1.0:
            extra.append(x)

    bounds = [xL]
    for (g0, g1) in gaps:
        bounds.append((g0 + g1) / 2.0)
    bounds.extend(extra)
    bounds.append(xR)
    bounds = sorted(set(round(b, 3) for b in bounds))

    columns = []
    centers = []
    confidences = []
    for k in range(len(bounds) - 1):
        c0, c1 = bounds[k], bounds[k + 1]
        cx = (c0 + c1) / 2.0
        assigned = [t for t in spans
                    if c0 <= (t["bbox"][0] + t["bbox"][2]) / 2.0 < c1]
        if assigned:
            cxs = [(t["bbox"][0] + t["bbox"][2]) / 2.0 for t in assigned]
            mean = sum(cxs) / len(cxs)
            std = statistics.pstdev(cxs) if len(cxs) > 1 else 0.0
        else:
            std = 0.0
        width = c1 - c0
        # tighter center cluster -> higher confidence
        conf = 1.0 - min(1.0, std / max(8.0, 0.4 * width))
        conf = round(max(0.3, min(1.0, conf)), 3)
        columns.append({"index": k, "x0": round(c0, 3), "x1": round(c1, 3)})
        centers.append(round(cx, 3))
        confidences.append(conf)

    return {
        "columns": columns,
        "column_centers": centers,
        "column_boundaries": [
            [round(bounds[k], 3), round(bounds[k + 1], 3)]
            for k in range(len(bounds) - 1)
        ],
        "column_confidence": confidences,
        "gaps": [list(g) for g in gaps],
    }
