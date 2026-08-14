"""Exploration-only script for page_013 table geometry.

Loads the diagnostic outputs (geometry.json from PyMuPDF path, babeldoc.json
from BabelDOC path) and dumps the texts/lines inside the detected table ROI.
This is used ONLY to ground the reconstruction algorithm design; nothing here
is hardcoded into the final module.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.normpath(os.path.join(HERE, "..", "..", "runs", "fyresults", "diag_2504_p13"))


def load(name):
    with open(os.path.join(BASE, name), "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    geo = load("page_013_geometry.json")
    bab = load("page_013_babeldoc.json")

    roi = None
    for t in bab.get("detected_tables", []):
        roi = t["bbox"]
        break
    print("ROI (table bbox):", roi)
    print("page size:", geo.get("page"))

    texts = geo.get("texts", [])
    hlines = geo.get("horizontal_lines", [])
    vlines = geo.get("vertical_lines", [])
    rects = geo.get("rectangles", [])

    def inside(b, roi, m=1.0):
        x0, y0, x1, y1 = b
        return (x0 >= roi[0] - m and x1 <= roi[2] + m and
                y0 >= roi[1] - m and y1 <= roi[3] + m)

    ins = [t for t in texts if inside(t["bbox"], roi)]
    print("\n=== texts inside ROI: %d (of %d total) ===" % (len(ins), len(texts)))
    ins.sort(key=lambda t: (round(t["bbox"][1], 2), t["bbox"][0]))
    for t in ins:
        b = t["bbox"]
        cx = (b[0] + b[2]) / 2.0
        txt = t.get("text", "")
        print("y0=%7.2f y1=%7.2f x0=%7.2f x1=%7.2f cx=%7.2f | %r" % (
            b[1], b[3], b[0], b[2], cx, txt))

    print("\n=== horizontal_lines (%d) ===" % len(hlines))
    for l in hlines:
        print("  ", l)
    print("\n=== vertical_lines (%d) ===" % len(vlines))
    for l in vlines:
        print("  ", l)
    print("\n=== rectangles (%d) ===" % len(rects))
    # show only those intersecting ROI
    rin = [r for r in rects if inside(r["bbox"], roi, m=2.0)]
    print("rects intersecting ROI:", len(rin))
    for r in rin[:20]:
        print("  ", r)

    # x-occupancy histogram (row-aggregated) to validate band detection
    print("\n=== x-occupancy (row-aggregated, 4pt bins) ===")
    x0, y0, x1, y1 = roi
    binw = 4.0
    nb = int((x1 - x0) / binw) + 1
    occ = [0] * nb
    for t in ins:
        b = t["bbox"]
        i0 = max(0, int((b[0] - x0) / binw))
        i1 = min(nb - 1, int((b[2] - x0) / binw))
        for i in range(i0, i1 + 1):
            occ[i] += 1
    for i, c in enumerate(occ):
        mark = "#" * min(c, 40)
        print("x=%6.1f %3d %s" % (x0 + i * binw, c, mark))


if __name__ == "__main__":
    main()
