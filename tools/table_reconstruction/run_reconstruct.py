"""CLI: PDF -> TableModel for a single detected table.

Usage:
    python run_reconstruct.py --pdf <pdf> --page <n> --out <dir> \
        [--babeldoc-json <path>] [--babeldoc-cache-home <dir>] \
        [--doclayout-model <path>]

Stage 1 of Table Reconstruction.  Independent of the translation pipeline:
no DeepSeek, no HTML, no WeasyPrint, no OCR / vision models.

Table detection normally comes from BabelDOC (Path A).  Pass ``--babeldoc-json``
to reuse a previously saved ``*_babeldoc.json`` (e.g. from the geometry
diagnostic tool) and skip the BabelDOC parse entirely.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "tools" / "table_geometry_debug"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from path_geometry import extract_geometry
from path_babeldoc import extract_babeldoc
from reconstruct import build_table_model
from recon_visualize import render_reconstruction


def _n_inside(roi, texts):
    b = roi
    n = 0
    for t in texts:
        bb = t["bbox"]
        if (bb[0] >= b[0] - 1 and bb[2] <= b[2] + 1 and
                bb[1] >= b[1] - 1 and bb[3] <= b[3] + 1):
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description="PDF -> TableModel (one table).")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--babeldoc-json", default=None,
                    help="reuse a saved *_babeldoc.json; skip BabelDOC parse")
    ap.add_argument("--babeldoc-cache-home", default=None)
    ap.add_argument("--doclayout-model", default=None)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("[1/4] geometry (PyMuPDF) ...")
    geo = extract_geometry(Path(args.pdf), args.page)

    print("[2/4] table detection ...")
    if args.babeldoc_json:
        print("  reuse:", args.babeldoc_json)
        bab = json.load(open(args.babeldoc_json, "r", encoding="utf-8"))
    else:
        cache_home = args.babeldoc_cache_home or str(out / "_babeldoc_cache")
        work_dir = out / "_babeldoc_work"
        bab = extract_babeldoc(
            Path(args.pdf), args.page,
            babeldoc_cache_home=Path(cache_home),
            doclayout_model=(Path(args.doclayout_model)
                             if args.doclayout_model else None),
            work_dir=work_dir)

    tables = bab.get("detected_tables", [])
    if not tables:
        print("[ERROR] no table detected (detected_tables empty)")
        return 1
    table = max(tables, key=lambda tb: _n_inside(tb["bbox"], geo["texts"]))
    roi = table["bbox"]
    print("  selected table bbox=%s label=%s conf=%s"
          % (roi, table.get("label"), table.get("confidence")))

    input_data = {
        "bbox": roi,
        "texts": geo["texts"],
        "horizontal_lines": geo["horizontal_lines"],
        "vertical_lines": geo["vertical_lines"],
        "rectangles": geo["rectangles"],
        "has_image": False,
    }

    print("[3/4] reconstruct TableModel ...")
    model = build_table_model(input_data)

    base = "page_%03d" % args.page
    model_path = out / (base + "_table_model.json")
    with open(model_path, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)

    print("[4/4] visualization ...")
    png_path = out / (base + "_table_reconstructed.png")
    render_reconstruction(Path(args.pdf), args.page, model, png_path)

    qa = model["qa"]
    print("\n================ Table Reconstruction QA ================")
    print("table_type            :", qa["table_type"])
    print("geometry_source       :", model["geometry_source"])
    print("row_count             :", qa["row_count"])
    print("column_count          :", qa["column_count"])
    print("assigned_text_ratio   :", qa["assigned_text_ratio"])
    print("unassigned_text_count :", qa["unassigned_text_count"])
    print("column_align_variance :", qa["column_alignment_variance"])
    print("row_alignment_variance :", qa["row_alignment_variance"])
    print("cells_multi_span      :", qa["cells_with_multiple_spans"])
    print("recon_confidence      :", qa["reconstruction_confidence"])
    print("\ncolumn x-ranges:")
    for c in model["columns"]:
        print("  C%d : [%.2f, %.2f]  conf=%.2f"
              % (c["index"], c["x0"], c["x1"],
                 model["column_analysis"]["column_confidence"][c["index"]]))
    print("\nheader row index      :", model["row_analysis"]["header_row_index"])
    print("\noutputs:")
    print("  model :", model_path)
    print("  image :", png_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
