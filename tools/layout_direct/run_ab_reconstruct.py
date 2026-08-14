"""Phase 3A: Path A (BabelDOC) vs Path B (Direct DocLayout) reconstruction A/B.

Feeds each path's detected table bbox into the SAME Table Reconstruction and
compares the resulting TableModels structurally.

Usage:
    python run_ab_reconstruct.py --pdf <pdf> --page 13 \
        --out <dir> \
        [--babeldoc-json <page_XXX_babeldoc.json>] \
        [--direct-json <page_XXX_direct_layout.json>]

Outputs:
    page_013_table_model_babeldoc.json
    page_013_table_model_direct.json
    page_013_ab_structural_qa.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (REPO / "tools" / "table_geometry_debug",
          REPO / "tools" / "table_reconstruction"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from path_geometry import extract_geometry
from reconstruct import build_table_model


def _n_inside(roi, texts):
    b = roi
    n = 0
    for t in texts:
        bb = t["bbox"]
        if (bb[0] >= b[0] - 1 and bb[2] <= b[2] + 1 and
                bb[1] >= b[1] - 1 and bb[3] <= b[3] + 1):
            n += 1
    return n


def build_model_for_roi(geo, roi):
    input_data = {
        "bbox": roi,
        "texts": geo["texts"],
        "horizontal_lines": geo["horizontal_lines"],
        "vertical_lines": geo["vertical_lines"],
        "rectangles": geo["rectangles"],
        "has_image": False,
    }
    return build_table_model(input_data)


def bbox_iou(a, b):
    x0 = max(a[0], b[0]); y0 = max(a[1], b[1])
    x1 = min(a[2], b[2]); y1 = min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union else 0.0


def structural_qa(model_a, model_b):
    def cid(c):
        return "R%dC%d" % (c["row"], c["col"])

    cells_a = {cid(c): c for c in model_a["cells"]}
    cells_b = {cid(c): c for c in model_b["cells"]}

    common = set(cells_a) & set(cells_b)
    text_exact = 0
    col_err, row_err = 0.0, 0.0
    for cid in common:
        ca, cb = cells_a[cid], cells_b[cid]
        if (ca["source_text"] or "").strip() == (cb["source_text"] or "").strip():
            text_exact += 1
        la, lb = ca["layout_bbox"], cb["layout_bbox"]
        col_err = max(col_err, abs(la[0] - lb[0]), abs(la[2] - lb[2]))
        row_err = max(row_err, abs(la[1] - lb[1]), abs(la[3] - lb[3]))

    return {
        "table_bbox_iou": round(bbox_iou(model_a["bbox"], model_b["bbox"]), 4),
        "table_bbox_max_error_pt": round(max(
            abs(model_a["bbox"][i] - model_b["bbox"][i]) for i in range(4)), 3),
        "row_count_match": len(model_a["rows"]) == len(model_b["rows"]),
        "column_count_match": len(model_a["columns"]) == len(model_b["columns"]),
        "cell_count_match": len(model_a["cells"]) == len(model_b["cells"]),
        "cell_text_exact_match_ratio": round(
            text_exact / len(common) if common else 0.0, 4),
        "column_boundary_max_error_pt": round(col_err, 3),
        "row_boundary_max_error_pt": round(row_err, 3),
        "rule_count_match": len(model_a.get("rules", [])) == len(model_b.get("rules", [])),
        "table_type_match": model_a["table_type"] == model_b["table_type"],
        "model_a": {"rows": len(model_a["rows"]),
                    "cols": len(model_a["columns"]),
                    "cells": len(model_a["cells"]),
                    "table_type": model_a["table_type"],
                    "rules": len(model_a.get("rules", [])),
                    "bbox": model_a["bbox"],
                    "confidence": model_a["qa"]["reconstruction_confidence"],
                    "assigned_ratio": model_a["qa"]["assigned_text_ratio"]},
        "model_b": {"rows": len(model_b["rows"]),
                    "cols": len(model_b["columns"]),
                    "cells": len(model_b["cells"]),
                    "table_type": model_b["table_type"],
                    "rules": len(model_b.get("rules", [])),
                    "bbox": model_b["bbox"],
                    "confidence": model_b["qa"]["reconstruction_confidence"],
                    "assigned_ratio": model_b["qa"]["assigned_text_ratio"]},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--babeldoc-json", required=True)
    ap.add_argument("--direct-json", required=True)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = "page_%03d" % args.page

    geo = extract_geometry(Path(args.pdf), args.page)

    # Path A: bbox from BabelDOC detection
    bab = json.load(open(args.babeldoc_json, encoding="utf-8"))
    tables_a = bab.get("detected_tables", [])
    if not tables_a:
        print("[ERROR] Path A: no table detected")
        return 1
    roi_a = max(tables_a, key=lambda t: _n_inside(t["bbox"], geo["texts"]))["bbox"]
    model_a = build_model_for_roi(geo, roi_a)

    # Path B: bbox from Direct DocLayout
    direct = json.load(open(args.direct_json, encoding="utf-8"))
    tables_b = [r for r in direct.get("regions", [])
                if (r["class_name"] or "").lower() == "table"]
    if not tables_b:
        print("[ERROR] Path B: no table detected")
        return 1
    roi_b = max(tables_b, key=lambda t: _n_inside(t["bbox"], geo["texts"]))["bbox"]
    model_b = build_model_for_roi(geo, roi_b)

    pa = out / (base + "_table_model_babeldoc.json")
    pb = out / (base + "_table_model_direct.json")
    pa.write_text(json.dumps(model_a, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    pb.write_text(json.dumps(model_b, ensure_ascii=False, indent=2),
                  encoding="utf-8")

    qa = structural_qa(model_a, model_b)
    qa_path = out / (base + "_ab_structural_qa.json")
    qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2),
                       encoding="utf-8")

    print("Path A bbox:", roi_a, "| Path B bbox:", roi_b)
    print("table_bbox_iou:", qa["table_bbox_iou"])
    print("table_bbox_max_error_pt:", qa["table_bbox_max_error_pt"])
    for k in ("row_count_match", "column_count_match", "cell_count_match",
              "cell_text_exact_match_ratio", "column_boundary_max_error_pt",
              "row_boundary_max_error_pt", "rule_count_match",
              "table_type_match"):
        print("  %-30s: %s" % (k, qa[k]))
    print("model A:", qa["model_a"])
    print("model B:", qa["model_b"])
    print("outputs:", pa, pb, qa_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
