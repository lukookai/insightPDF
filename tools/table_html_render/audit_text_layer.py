"""Phase 2C text-layer audit: does the rendered PDF recover full cell text?

Reads a rendered translated PDF, extracts its text layer three ways
(get_text("text") / "dict" / "words") and answers, per logical cell:

  * how many PDF text spans make up the cell
  * the reconstructed (joined) text
  * whether the model's exact translated_text can be recovered
  * whether fragmentation is at character level (n_spans == n_chars)

Also prints aggregate metrics:
  pdf_cell_text_exact_match_ratio
  fragmented_character_ratio
  cells_recoverable_from_pdf_text
  cells_with_character_level_fragmentation

Usage:
    python audit_text_layer.py --model <translated_model.json> \
        --pdf <rendered.pdf> [--per-cell] [--json <out.json>]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def cell_text_of(model):
    out = {}
    for c in model["cells"]:
        st = c["translation_status"]
        text = (c.get("translated_text") if st == "translated"
                and c.get("translated_text") else c.get("source_text") or "")
        out[c["cell_id"]] = text
    return out


def extract_spans(pdf_path):
    import pymupdf
    doc = pymupdf.open(pdf_path)
    pg = doc[0]
    spans = []
    for block in pg.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "")
                if not t.strip():
                    continue
                spans.append({"text": t, "font": span.get("font"),
                              "bbox": [float(v) for v in span["bbox"]]})
    doc.close()
    return spans


def _cell_lookup(model):
    """Build span->cell lookup using the model's frozen geometry."""
    cols = sorted(model["columns"], key=lambda c: c["x0"])
    bounds = model.get("row_layout_boundaries") or []
    cellmap = {}
    for c in model["cells"]:
        cellmap[c["cell_id"]] = c
    return cols, bounds, cellmap


def _pin(cols, bounds, cx, cy):
    """(cell_id or None) for a span centre using model column/row bands."""
    cid = None
    for c in cols:
        if c["x0"] <= cx < c["x1"]:
            cid = "R?C%d" % c["index"]
            break
    if cid is None:
        return None
    if len(bounds) >= 2:
        for i in range(len(bounds) - 1):
            if bounds[i] <= cy < bounds[i + 1]:
                cid = "R%dC%s" % (i, cid.split("C")[1])
                break
    return cid


def audit(model, spans):
    cell_text = cell_text_of(model)
    cols, bounds, cellmap = _cell_lookup(model)
    by_cell = defaultdict(list)
    for s in spans:
        cx = (s["bbox"][0] + s["bbox"][2]) / 2.0
        cy = (s["bbox"][1] + s["bbox"][3]) / 2.0
        cid = _pin(cols, bounds, cx, cy)
        if cid and cid in cellmap:
            by_cell[cid].append(s)

    per_cell = {}
    exact = 0
    recoverable = 0
    char_frag_cells = 0
    frag_chars = 0
    total_chars = 0
    total_spans = 0
    for cid, ct in sorted(cell_text.items()):
        total_chars += len(ct)
        ss = sorted(by_cell.get(cid, []), key=lambda s: s["bbox"][0])
        if not ss:
            per_cell[cid] = {"found": False, "n_spans": 0,
                             "reconstructed": "", "exact_match": False,
                             "recoverable": False}
            continue
        total_spans += len(ss)
        joined = "".join(s["text"] for s in ss)
        joined_sp = " ".join(s["text"] for s in ss)
        norm = lambda t: "".join(t.split())  # noqa: E731
        is_exact = norm(joined) == norm(ct)
        is_recoverable = (is_exact or (ct and (norm(ct) in norm(joined)
                                               or norm(joined) in norm(ct))))
        char_frag = len(ss) == len(ct) and len(ct) > 1
        if is_exact:
            exact += 1
        if is_recoverable:
            recoverable += 1
        if char_frag:
            char_frag_cells += 1
            frag_chars += len(ct)
        per_cell[cid] = {
            "found": True,
            "n_spans": len(ss),
            "reconstructed": joined_sp.strip(),
            "exact_match": is_exact,
            "recoverable": is_recoverable,
            "character_level_fragmentation": char_frag,
            "fonts": sorted({s["font"] for s in ss}),
        }

    n = len(cell_text)
    return {
        "pdf_cell_text_exact_match_ratio": round(exact / n, 4) if n else 0.0,
        "fragmented_character_ratio": round(frag_chars / total_chars, 4)
        if total_chars else 0.0,
        "cells_recoverable_from_pdf_text": recoverable,
        "cells_with_character_level_fragmentation": char_frag_cells,
        "total_cells": n,
        "total_text_spans": total_spans,
        "per_cell": per_cell,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    model = json.load(open(args.model, "r", encoding="utf-8"))
    spans = extract_spans(args.pdf)
    result = audit(model, spans)
    print("total spans      :", result["total_text_spans"])
    print("exact match ratio:", result["pdf_cell_text_exact_match_ratio"])
    print("frag char ratio  :", result["fragmented_character_ratio"])
    print("recoverable cells:", "%d/%d" % (result["cells_recoverable_from_pdf_text"],
                                           result["total_cells"]))
    print("char-frag cells  :", result["cells_with_character_level_fragmentation"])
    if args.json:
        Path(args.json).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
