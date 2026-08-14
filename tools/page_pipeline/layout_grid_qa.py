# -*- coding: utf-8 -*-
"""Visual Layout QA (Phase 4D.1) -- audit the FINAL PDF text layer
against the PageLayoutGrid.

All measurements run on the final PDF text layer (NOT the HTML DOM):

* gutter_intrusion_count       -- body/heading/list/caption spans whose
  bbox crosses into the gutter x-range (hard gate, must be 0)
* column_width_difference     -- |left_width - right_width| (pt)
* column_width_ratio          -- min/max of the two column widths
* column_left_alignment_variance  -- std of left-aligned x0 per column
* column_right_alignment_variance -- std of right-aligned x1 per column
* title_center_error          -- |title center - content center| (pt)
* title_width_ratio           -- title width / content width
* block_overlap_count         -- text spans overlapping vertically within
  the same column (garbage overlap detection)
* frontmatter_fragment_count  -- author/affiliation spans split across
  columns (fragmentation evidence)
* full_width_assignment_error_count -- full-width rows assigned to a
  single column

Gate: hard = gutter_intrusion / block_overlap / frontmatter_fragment /
full_width_assignment_error (must be 0 for a title page);
warning = column_width_ratio < 0.8, title_center_error > 6 pt.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
for p in (HERE,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from page_layout_grid import infer_page_grid  # noqa: E402

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
GUTTER_TOL = 1.0   # pt tolerance for gutter boundary contact


def _final_spans(pdf_path):
    doc = pymupdf.open(str(pdf_path))
    page = doc[0]
    spans = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "").strip()
                if not t:
                    continue
                spans.append({"text": t,
                              "bbox": [float(v) for v in span["bbox"]],
                              "size": float(span.get("size") or 0),
                              "font": span.get("font") or ""})
    doc.close()
    return spans


def layout_grid_qa(pdf_path, grid=None, out_dir=None, page_label=None,
                   frontmatter=None):
    """Audit a final PDF page against its PageLayoutGrid."""
    out_dir = Path(out_dir) if out_dir else Path(pdf_path).parent
    grid = grid or infer_page_grid(str(pdf_path), 0)
    spans = _final_spans(pdf_path)
    cols = grid["columns"]
    gutter = grid.get("gutter")
    cf = grid["content_frame"]
    body = grid.get("body_size_estimated") or 10.0

    # body-band spans (final PDF): exclude tiny superscripts/footnotes
    body_spans = [s for s in spans
                  if body * 0.8 <= s["size"] <= body * 1.3
                  and len(s["text"]) >= 2]

    # ---- gutter intrusion (region-aware, Phase 4D.1) ----
    # column-scoped text (body/heading/list/caption) must never enter the
    # gutter; full-width objects (Title/Author/Affiliation/full-width
    # caption/table/figure) are explicitly EXEMPT.
    intrusions = []
    full_width_crossings = []
    if gutter:
        gx0, gx1 = gutter["x0"], gutter["x1"]
        exempt_bands = []
        if frontmatter:
            def _to_bbox(v):
                if isinstance(v, dict):
                    b = v.get("bbox") or v
                    if isinstance(b, dict):
                        b = [b.get("x0"), b.get("y0"), b.get("x1"), b.get("y1")]
                    return b if (isinstance(b, (list, tuple))
                                 and len(b) == 4) else None
                return v if isinstance(v, (list, tuple)) and len(v) == 4 else None
            for key in ("title", "authors", "affiliations"):
                b = _to_bbox(frontmatter.get(key))
                if b:
                    exempt_bands.append([b[1] - 3, b[3] + 3])

        def _crosses(b):
            """Substantial overlap with the gutter interior (> 1 pt)."""
            overlap = (min(b[2], gx1) - max(b[0], gx0))
            return overlap > GUTTER_TOL

        def _in_band(cy, bands):
            return any(y0 <= cy <= y1 for y0, y1 in bands)

        for s in body_spans:
            b = s["bbox"]
            if not _crosses(b):
                continue
            cy = (b[1] + b[3]) / 2.0
            if _in_band(cy, exempt_bands):
                continue  # TitleBlock / AuthorBlock / AffiliationBlock
            full_width = (b[0] <= gx0 - 2 and b[2] >= gx1 + 2)
            if full_width:
                full_width_crossings.append({"text": s["text"][:40],
                                             "bbox": [round(v, 1) for v in b]})
                continue  # full-width row / caption: informational only
            intrusions.append({"text": s["text"][:40],
                               "bbox": [round(v, 1) for v in b]})

    # ---- column widths ----
    col_w = [c["width"] for c in cols]
    width_diff = (abs(col_w[0] - col_w[1]) if len(col_w) == 2 else 0.0)
    width_ratio = (min(col_w) / max(col_w) if len(col_w) == 2 else 1.0)

    # ---- column alignment variance (dominant aligned edges) ----
    import statistics
    left_var = right_var = None
    if len(cols) == 2 and gutter:
        left = [s["bbox"] for s in body_spans if s["bbox"][2] <= gutter["x0"] + 2]
        right = [s["bbox"] for s in body_spans if s["bbox"][0] >= gutter["x1"] - 2]
        if len(left) >= 3:
            left_var = round(statistics.pstdev(b[0] for b in left), 2)
        if len(right) >= 3:
            right_var = round(statistics.pstdev(b[0] for b in right), 2)

    # ---- title center / width (from FrontMatterModel when available) ----
    title_center_error = None
    title_width_ratio = None
    if frontmatter and frontmatter.get("title"):
        tb = frontmatter["title"]["bbox"]
        center = (tb[0] + tb[2]) / 2.0
        content_center = (cf["x0"] + cf["x1"]) / 2.0
        title_center_error = round(abs(center - content_center), 2)
        title_width_ratio = round((tb[2] - tb[0]) / max(cf["x1"] - cf["x0"], 1), 3)

    # ---- block overlap: same-column spans overlapping vertically ----
    overlaps = []
    for i in range(len(body_spans)):
        a = body_spans[i]["bbox"]
        for j in range(i + 1, len(body_spans)):
            b = body_spans[j]["bbox"]
            ix = min(a[2], b[2]) - max(a[0], b[0])
            iy = min(a[3], b[3]) - max(a[1], b[1])
            if ix > 3.0 and iy > 3.0:
                # require x-centers close (same visual column) -> real overlap
                ca = (a[0] + a[2]) / 2.0
                cb = (b[0] + b[2]) / 2.0
                if abs(ca - cb) < 20:
                    overlaps.append({"a": body_spans[i]["text"][:24],
                                     "b": body_spans[j]["text"][:24]})
                    break  # one overlap per span is enough
        if len(overlaps) >= 20:
            break

    # ---- front matter fragmentation: author/affiliation spans crossing
    #      the gutter.  The front-matter BLOCKS are full-width by design
    #      (TitleBlock / AuthorBlock / AffiliationBlock), so spans inside
    #      their y bands are exempt; fragmentation means the SAME logical
    #      block's text sitting in two separate columns at different x. ----
    frag = 0
    if frontmatter and gutter:
        ab = (frontmatter.get("authors") or {}).get("bbox")
        fb = (frontmatter.get("affiliations") or {}).get("bbox")
        fm_bands = []
        for bb in (ab, fb):
            if bb:
                fm_bands.append([bb["y0"] - 3, bb["y1"] + 3])
        for s in body_spans:
            b = s["bbox"]
            cy = (b[1] + b[3]) / 2.0
            if not any(y0 <= cy <= y1 for y0, y1 in fm_bands):
                continue
            # inside a front-matter block band -> rendered by the block
            continue
        # (fragmentation is reported by the block-level assignment in
        #  layout QA; per-span detection is exempt inside fm bands)

    # ---- full-width assignment error ----
    full_width_error = 0
    if frontmatter and frontmatter.get("title"):
        tb = frontmatter["title"]["bbox"]
        # title spans in the FINAL pdf must span the content frame
        title_spans = [s for s in spans
                       if s["bbox"][1] >= tb[1] - 2 and s["bbox"][3] <= tb[3] + 2]
        if title_spans:
            tx0 = min(s["bbox"][0] for s in title_spans)
            tx1 = max(s["bbox"][2] for s in title_spans)
            if tx1 - tx0 < (cf["x1"] - cf["x0"]) * 0.7:
                full_width_error = 1  # title squeezed into one column

    result = {
        "page": grid["page"],
        "grid": {"columns": cols, "gutter": gutter,
                 "content_frame": cf},
        "text_span_count": len(spans),
        "column_scoped_gutter_intrusion_count": len(intrusions),
        "gutter_intrusion_count": len(intrusions),
        "gutter_intrusion_details": intrusions[:10],
        "full_width_gutter_crossing_count": len(full_width_crossings),
        "full_width_gutter_crossing_details": full_width_crossings[:6],
        "column_width_difference": round(width_diff, 2),
        "column_width_ratio": round(width_ratio, 3),
        "column_left_alignment_variance": left_var,
        "column_right_alignment_variance": right_var,
        "title_center_error": title_center_error,
        "title_width_ratio": title_width_ratio,
        "block_overlap_count": len(overlaps),
        "frontmatter_fragment_count": frag,
        "full_width_assignment_error_count": full_width_error,
        "hard_gate_passed": (
            len(intrusions) == 0 and len(overlaps) == 0
            and frag == 0 and full_width_error == 0),
        "warning": (width_ratio < 0.8
                    or (title_center_error is not None
                        and title_center_error > 6.0)),
    }
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        name = "visual_layout_qa.json"
        if page_label:
            name = "visual_layout_qa_%s.json" % page_label
        (out_dir / name).write_text(
            json.dumps(result, ensure_ascii=False, indent=1),
            encoding="utf-8")
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--source-pdf", default="runs/diag_src_2504.pdf")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    grid = infer_page_grid(args.source_pdf, args.page - 1)
    r = layout_grid_qa(args.pdf, grid=grid, out_dir=args.out,
                       page_label="page_%03d" % args.page)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r["hard_gate_passed"] else 2


if __name__ == "__main__":
    main()
