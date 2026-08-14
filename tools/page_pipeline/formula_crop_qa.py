# -*- coding: utf-8 -*-
"""FormulaCropQA (Phase 4C.2R.1).

Every display / complex formula is rendered as per-segment cropped SVGs
(crop_svg: same glyphs, viewBox = seg render_viewbox or layout_bbox).
The final PDF must show EVERY bit of the formula's ink:

1. per-segment: the segment viewBox must cover the segment's ink
   (component span bboxes from the source PDF -- the glyph truth).  A
   negative right/bottom/left/top margin means glyphs are cropped out
   (p014 B12's adopted condition word "otherwise" extended 6.8pt past
   the layout bbox and its tail vanished from the final PDF).
2. coverage: EVERY component (math glyph, adopted condition text,
   absorbed equation number) must intersect SOME segment viewBox --
   components entirely outside all segment boxes are invisible
   (p003 B10's condition rows "are first grouped into clusters ...
   For each cluster", p011's "(15)" equation numbers).

Safety margin policy: when ink sits at/near a crop edge the viewBox must
carry a safety margin (page_model._formula_crop_viewboxes applies
CROP_SAFETY_PT = 1.5pt on exactly those sides).  This audit records the
crop evidence (ink_bbox / svg_viewbox / right_margin / bottom_margin /
left_margin / top_margin) for every segment.

Formula SVG internal English is allowed -- this QA is purely geometric.

Outputs:
  formula_crop_violation_count
  formula_crop_details[]      (formula_id, segment_id, svg_viewbox,
                               ink_bbox, margins, defect)
  formula_crop_clean          (bool)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "tools" / "formula_html_render"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

EPS = 0.05          # pt float tolerance
SAFETY = 1.5        # pt safety margin policy (page_model.CROP_SAFETY_PT)


def _seg_rv(seg):
    return [float(v) for v in (seg.get("render_viewbox")
                               or seg.get("layout_bbox") or [0, 0, 0, 0])]


def _intersect(a, b):
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    return max(ix, 0.0) * max(iy, 0.0)


def formula_crop_qa(page_model, pdf_path=None, out_dir=None):
    """Audit formula segment crop windows against their component ink."""
    out_dir = Path(out_dir) if out_dir else Path(".")
    details = []
    for region in page_model.get("regions", []):
        if region.get("type") != "formula":
            continue
        fm = region["payload"]
        if fm.get("placement") == "inline":
            continue
        fid = fm.get("formula_id") or region.get("id")
        segs = fm.get("render_segments") or []
        comps = [c for c in (fm.get("components") or [])
                 if isinstance(c.get("bbox"), (list, tuple))
                 and len(c.get("bbox", [])) == 4]
        if not segs or not comps:
            continue
        boxes = [_seg_rv(s) for s in segs]

        # 1+2. assign every component to the segment with the largest
        # intersection (mirrors page_model._formula_crop_viewboxes; a
        # component straddling a segment boundary must not be double-
        # counted into both neighbours -- B2's subscript 'j' row).  A
        # component that intersects NO segment window is uncovered and
        # would be completely invisible in the final PDF.
        assigned = [[] for _ in segs]
        for c in comps:
            cb = [float(v) for v in c["bbox"]]
            best = -1
            best_area = 0.0
            for i, rv in enumerate(boxes):
                area = _intersect(rv, cb)
                if area > best_area:
                    best, best_area = i, area
            if best < 0:
                details.append({
                    "formula_id": fid,
                    "segment_id": None,
                    "defect": "component_uncovered",
                    "component": (c.get("text") or "")[:40],
                    "component_bbox": [round(v, 3) for v in cb],
                    "svg_viewbox": None,
                    "ink_bbox": [round(v, 3) for v in cb],
                    "margins": None,
                    "cropped_sides": ["outside"],
                })
                continue
            assigned[best].append(cb)

        for seg, rv, ink in zip(segs, boxes, assigned):
            if not ink:
                continue
            ix0 = min(b[0] for b in ink)
            iy0 = min(b[1] for b in ink)
            ix1 = max(b[2] for b in ink)
            iy1 = max(b[3] for b in ink)
            ml = round(ix0 - rv[0], 3)
            mt = round(iy0 - rv[1], 3)
            mr = round(rv[2] - ix1, 3)
            mb = round(rv[3] - iy1, 3)
            margins = {"left": ml, "top": mt, "right": mr, "bottom": mb}
            cropped = [k for k, v in margins.items() if v < -EPS]
            if cropped:
                details.append({
                    "formula_id": fid,
                    "segment_id": seg.get("segment_id"),
                    "defect": "ink_cropped",
                    "svg_viewbox": [round(v, 3) for v in rv],
                    "ink_bbox": [round(v, 3) for v in (ix0, iy0, ix1, iy1)],
                    "margins": margins,
                    "cropped_sides": cropped,
                })

    result = {
        "formula_crop_violation_count": len(details),
        "formula_crop_details": details[:24],
        "formula_crop_clean": len(details) == 0,
        "safety_margin_pt": SAFETY,
    }
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (out_dir / "formula_crop_qa.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1),
            encoding="utf-8")
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))
    result = formula_crop_qa(model, out_dir=args.out)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["formula_crop_clean"] else 2


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
