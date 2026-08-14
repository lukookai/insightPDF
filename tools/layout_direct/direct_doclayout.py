"""Direct DocLayout adapter -- BabelDOC-free layout detection.

Reuses *exactly* the model BabelDOC runs:

  * the same ONNX weights  (``doclayout_yolo_docstructbench_imgsz1024.onnx``
    via ``OnnxModel.from_pretrained``),
  * the same preprocessing (``OnnxModel.predict``: 1024x1024 resize+pad,
    CHW float32 /255, conf > 0.25 filter),
  * the same class mapping (``names`` stored in the ONNX metadata),
  * the same coordinate conversion (``LayoutParser`` formula: image pixels at
    72 dpi == PDF pt, y flip, +/-1 box inflation, top-left y-down output).

Only BabelDOC's orchestration (new-parser -> LayoutParser -> ParagraphFinder
-> StylesAndFormulas) is bypassed.  No translation, no OCR, no new table
model.

Unified output per page::

    {
      "page": {"width": pt, "height": pt, "coordinate_unit": "pt"},
      "regions": [
        {"id": 1, "class_name": "table", "confidence": 0.957,
         "bbox": [x0, y0, x1, y1]}   # top-left, y-down, pt
      ]
    }
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import numpy as np

try:  # package import
    from ._env import ensure_babeldoc_importable
except ImportError:  # direct script run
    from _env import ensure_babeldoc_importable


def _topleft(box, page_height):
    """IL Box (PDF y-up pt) -> top-left y-down pt list (mirrors
    ``tools.table_geometry_debug.common.il_box_to_topleft``)."""
    x0, y0, x1, y1 = float(box.x), float(box.y), float(box.x2), float(box.y2)
    return [round(x0, 3), round(page_height - y1, 3),
            round(x1, 3), round(page_height - y0, 3)]


class DirectDocLayout:
    def __init__(self, cache_home: Path | None = None,
                 model_path: str | None = None):
        """Load the same ONNX DocLayout model BabelDOC uses."""
        if cache_home is not None:
            ensure_babeldoc_importable(cache_home)
        from babeldoc.docvision.doclayout import OnnxModel
        if model_path:
            self.model = OnnxModel(model_path)
        else:
            self.model = OnnxModel.from_pretrained()

    def detect_page(self, pdf: Path, page_index: int) -> dict:
        """Detect layout regions of one page (0-based index).

        Same pixels / same conversion as BabelDOC's ``LayoutParser.process``.
        """
        import pymupdf
        from babeldoc.format.pdf.document_il.utils.mupdf_helper import (
            get_no_rotation_img,
        )

        doc = pymupdf.open(str(Path(pdf).resolve()))
        pg = doc[page_index]
        page_w = float(pg.rect.width)
        page_h = float(pg.rect.height)

        pix = get_no_rotation_img(pg)  # dpi=72 -> 1 px = 1 pt
        image = np.frombuffer(pix.samples, np.uint8).reshape(
            pix.height, pix.width, 3)[:, :, ::-1]  # RGB->BGR

        result = self.model.predict(image)[0]

        mediabox = pg.mediabox_size
        b_h = math.ceil(float(mediabox.y))
        b_w = math.ceil(float(mediabox.x))
        h, w = b_h, b_w

        regions = []
        for i, box in enumerate(result.boxes):
            x0, y0, x1, y1 = box.xyxy
            # same conversion as BabelDOC's LayoutParser.process (tuple
            # assignment: right-hand side uses the ORIGINAL values)
            x0n = int(np.clip(int(x0 - 1), 0, w - 1))
            y0n = int(np.clip(int(h - y1 - 1), 0, h - 1))
            x1n = int(np.clip(int(x1 + 1), 0, w - 1))
            y1n = int(np.clip(int(h - y0 + 1), 0, h - 1))
            cls_name = result.names[box.cls]
            conf = float(box.conf)
            regions.append({
                "id": i + 1,
                "class_name": cls_name,
                "confidence": round(conf, 4),
                "bbox_tl_yup_il": [x0n, y0n, x1n, y1n],  # LayoutParser IL coords
                "bbox": [round(float(x0n), 3),
                         round(page_h - float(y1n), 3),
                         round(float(x1n), 3),
                         round(page_h - float(y0n), 3)],
            })
        doc.close()
        return {
            "page": {
                "width": round(page_w, 3),
                "height": round(page_h, 3),
                "coordinate_unit": "pt",
            },
            "regions": regions,
            "_meta": {
                "detector": "doclayout_yolo_docstructbench_imgsz1024.onnx",
                "direct": True,
                "page_index": page_index,
            },
        }

    def detect_tables(self, pdf: Path, page_index: int) -> list[dict]:
        """Table regions only (class_name == 'table'), unified bbox schema."""
        out = self.detect_page(pdf, page_index)
        return [r for r in out["regions"] if (r["class_name"] or "").lower() == "table"]


def main():
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True, help="1-based")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache-home", default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    det = DirectDocLayout(
        cache_home=Path(args.cache_home) if args.cache_home else None,
        model_path=args.model,
    )
    res = det.detect_page(Path(args.pdf), args.page - 1)
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
