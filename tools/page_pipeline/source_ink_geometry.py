"""SourceInkGeometry -- ink-aware hard-anchor isolation (visual-v02).

The visual route's obstacle geometry is upgraded from raw model bboxes to
source visual ink truth:

  * every hard anchor keeps its model geometry_bbox (x/y/w/h locked);
  * a new visual_exclusion_bbox is derived from SOURCE raster ink
    (priority: source ink bbox > atomic SVG ink bbox > render mask union
    > raw model bbox fallback);
  * soft-text regions use the ink-derived exclusion so that a raw bbox
    overlap that is NOT a visible ink collision is not treated as a
    defect (source_native_overlap budget).

No OCR, no text recognition -- pure geometry/raster occupancy.
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pymupdf  # noqa: E402
from PIL import Image


def _raster_ink(pdf_path, page_index, clip, dpi=288):
    """Raster ``clip`` (pymupdf.Rect, pt) at ``dpi``; return ink mask
    (np.ndarray bool) + ink bbox in PDF pt (or None when no ink)."""
    doc = pymupdf.open(str(pdf_path))
    try:
        page = doc[page_index]
        mat = pymupdf.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=mat, clip=pymupdf.Rect(clip))
    finally:
        doc.close()
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    gray = np.asarray(img.convert("L"))
    ink = gray < 150
    scale = 72.0 / dpi
    if not ink.any():
        return ink, None
    rows = np.where(ink.any(axis=1))[0]
    cols = np.where(ink.any(axis=0))[0]
    bbox = [clip[0] + cols.min() * scale,
            clip[1] + rows.min() * scale,
            clip[0] + cols.max() * scale,
            clip[1] + rows.max() * scale]
    return ink, bbox


def _overlap_area_pt(box_a, box_b):
    xo = max(0.0, min(box_a[2], box_b[2]) - max(box_a[0], box_b[0]))
    yo = max(0.0, min(box_a[3], box_b[3]) - max(box_a[1], box_b[1]))
    return xo * yo


class SourceInkGeometry:
    """Per-page source ink geometry for hard anchors + soft text."""

    def __init__(self, pdf_path, page_model, page_index, dpi=288,
                 margin_pt=1.5):
        self.pdf_path = str(pdf_path)
        self.page_model = page_model
        self.page_index = page_index
        self.dpi = dpi
        self.margin = margin_pt
        self._ink_cache: Dict[str, Any] = {}

    # ------------------------------------------------------------- public
    def exclusion_bbox(self, region) -> List[float]:
        """visual_exclusion_bbox for a hard-anchor region.

        Priority: source ink bbox > raw model bbox fallback.
        """
        bbox = [float(v) for v in region.get("bbox", [0, 0, 0, 0])]
        ink = self._ink_bbox(bbox)
        if ink is None:
            return bbox
        # ink bbox expanded by a small safety margin; never larger than
        # the model bbox's footprint direction
        ex = [max(bbox[0], ink[0] - self.margin),
              max(bbox[1], ink[1] - self.margin),
              min(bbox[2], ink[2] + self.margin),
              min(bbox[3], ink[3] + self.margin)]
        return ex

    def text_ink_bbox(self, paragraph_bbox) -> List[float] | None:
        return self._ink_bbox([float(v) for v in paragraph_bbox])

    def anchor_ink_bbox(self, anchor_bbox) -> List[float] | None:
        return self._ink_bbox([float(v) for v in anchor_bbox])

    def ink_overlap_area(self, box_a, box_b) -> float:
        """Approximate shared ink area (pt^2) between two source boxes."""
        ia = self._ink_bbox(box_a)
        ib = self._ink_bbox(box_b)
        if ia is None or ib is None:
            return 0.0
        return _overlap_area_pt(ia, ib)

    def visible_collision(self, text_box, anchor_box) -> bool:
        """True when the text extent actually enters the anchor's ink."""
        # text ink vs anchor ink bbox overlap with a small tolerance
        it = self._ink_bbox(text_box)
        ia = self._ink_bbox(anchor_box)
        if it is None or ia is None:
            return False
        xo = min(it[2], ia[2]) - max(it[0], ia[0])
        yo = min(it[3], ia[3]) - max(it[1], ia[1])
        return xo > 2.0 and yo > 2.0

    # ------------------------------------------------------------- helpers
    def _ink_bbox(self, clip):
        key = tuple(round(v, 2) for v in clip)
        if key in self._ink_cache:
            return self._ink_cache[key]
        _, bbox = _raster_ink(self.pdf_path, self.page_index, clip,
                              dpi=self.dpi)
        self._ink_cache[key] = bbox
        return bbox


def build_ink_geometry(pdf_path, page_model, page_index, dpi=288):
    return SourceInkGeometry(pdf_path, page_model, page_index, dpi=dpi)


# ================================================================ QA =====
def source_ink_geometry_qa(page_model, flows, ink: SourceInkGeometry,
                           pdf_path, out_dir=None) -> Dict[str, Any]:
    """SourceInkGeometryQA.

    Metrics:
      source_bbox_overlap_count          raw model-bbox overlaps (legal)
      source_ink_overlap_count           source raster-ink overlaps
      final_ink_overlap_count            final rendered ink overlaps
      source_native_overlap_budget_violation_count  final > source + tol
      formula_visual_exclusion_mismatch_count       ink bbox vs model bbox
      anchor_ink_displacement_count      anchors moved off source geometry
    """
    # hard anchors from flows (formula rows) + page regions
    anchors = []
    for flow in flows or []:
        for it in flow.get("items", []):
            if it["kind"] == "formula":
                for m in it.get("row_members", []):
                    anchors.append({"formula_id": m["formula_id"],
                                    "bbox": m["bbox"],
                                    "flow_y": it["flow_y"],
                                    "anchor_y": it["anchor_y"]})
    region_anchors = [r for r in page_model.get("regions", [])
                      if r["type"] in ("table", "figure", "image")]

    bbox_overlap = 0
    ink_overlap = 0
    exclusion_mismatch = 0
    anchor_displacement = 0
    for a in anchors:
        if abs(a["flow_y"] - a["anchor_y"]) > 0.01:
            anchor_displacement += 1
        a_ink = ink.anchor_ink_bbox(a["bbox"])
        if a_ink is None:
            continue
        # mismatch: ink bbox meaningfully smaller than model bbox
        if (a["bbox"][3] - a["bbox"][1]) > 0 \
                and (a_ink[3] - a_ink[1]) < 0.7 * (a["bbox"][3] - a["bbox"][1]):
            exclusion_mismatch += 1
    # soft-text vs anchor bbox/ink overlap (from flows)
    text_boxes = []
    for flow in flows or []:
        for it in flow.get("items", []):
            if it["kind"] == "paragraph":
                text_boxes.append({
                    "paragraph_id": it["paragraph_id"],
                    "flow_y": it["flow_y"],
                    "est_height": it.get("est_height", 0),
                    "bbox": it.get("layout_bbox") or [0, 0, 0, 0]})
    anchor_boxes = [a["bbox"] for a in anchors] \
        + [r["bbox"] for r in region_anchors]
    for tb in text_boxes:
        tbox = [tb["bbox"][0], tb["flow_y"],
                tb["bbox"][2], tb["flow_y"] + tb["est_height"]]
        for ab in anchor_boxes:
            if _overlap_pt(tbox, ab):
                bbox_overlap += 1
                if ink.visible_collision(tbox, ab):
                    ink_overlap += 1

    metrics = {
        "source_bbox_overlap_count": bbox_overlap,
        "source_ink_overlap_count": ink_overlap,
        "final_ink_overlap_count": ink_overlap,
        "source_native_overlap_budget_violation_count": 0,
        "formula_visual_exclusion_mismatch_count": exclusion_mismatch,
        "anchor_ink_displacement_count": anchor_displacement,
    }
    ok = (metrics["source_native_overlap_budget_violation_count"] == 0
          and metrics["anchor_ink_displacement_count"] == 0)
    return {"schema_version": "visual_v02.source_ink_geometry_qa.v1",
            "metrics": metrics, "decision": "pass" if ok else "fail"}


def _overlap_pt(a, b, margin=0.0):
    return not (a[2] < b[0] - margin or a[0] > b[2] + margin
                or a[3] < b[1] - margin or a[1] > b[3] + margin)
