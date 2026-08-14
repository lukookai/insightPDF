"""Phase 2C: semantic HTML layer QA + PDF text recovery QA.

Regenerates page_013_table_zh.html with the two-layer structure (locked visual
layer + display:none semantic <table>), renders it, then verifies:

  HTML layer
    * table=1, thead=1, tbody=1, tr=21, th=5 (header), td=100 (body)
    * every semantic cell carries data-cell-id / source / translated matching
      the TableModel 1:1 (105 cells)
    * no duplicate visible text (semantic layer is display:none)

  PDF layer
    * text recovery metrics (exact match / fragmentation / recoverable)
    * geometry regression: column/row/rule/anchor/baseline unchanged

Outputs: semantic_html_report.json (+ regenerated page_013_table_zh.{html,pdf,png})
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "实现源码" / "pdf_translator"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from render_zh import build_zh_html, _display_text
from audit_text_layer import extract_spans, audit
from cjk_fonts import detect_cjk_serif
import qa_zh


class TableCounter(HTMLParser):
    """Count tags and collect semantic cells inside the hidden table."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = 0
        self.theads = 0
        self.tbodies = 0
        self.trs = 0
        self.cells = []          # (tag, attrs dict, text)
        self.stack = []
        self.in_hidden = False
        self.in_cell = False
        self.cur_cell = None
        self.textbuf = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "div" and d.get("style") and "display:none" in d["style"]:
            self.in_hidden = True
        if not self.in_hidden:
            return
        if tag == "table":
            self.tables += 1
        elif tag == "thead":
            self.theads += 1
        elif tag == "tbody":
            self.tbodies += 1
        elif tag == "tr":
            self.trs += 1
        elif tag in ("td", "th"):
            self.in_cell = True
            self.cur_cell = {"tag": tag, "attrs": d, "text": ""}
            self.textbuf = []
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cur_cell is not None:
            self.cur_cell["text"] = "".join(self.textbuf).strip()
            self.cells.append(self.cur_cell)
            self.cur_cell = None
            self.in_cell = False
            self.textbuf = []
        if self.stack:
            self.stack.pop()
        if tag == "div" and self.in_hidden:
            self.in_hidden = False

    def handle_data(self, data):
        if self.in_cell:
            self.textbuf.append(data)


def html_structure_qa(html_path, model):
    parser = TableCounter()
    parser.feed(open(html_path, encoding="utf-8").read())

    cells = parser.cells
    header_cells = [c for c in cells if c["tag"] == "th"]
    body_cells = [c for c in cells if c["tag"] == "td"]

    # TableModel ground truth
    model_cells = {c["cell_id"]: c for c in model["cells"]}
    mismatches = []
    matched = 0
    for c in cells:
        cid = c["attrs"].get("data-cell-id")
        mc = model_cells.get(cid)
        if mc is None:
            mismatches.append({"cell_id": cid, "error": "not in model"})
            continue
        expected = _display_text(mc)
        if c["text"] != expected:
            mismatches.append({"cell_id": cid,
                               "expected": expected, "got": c["text"]})
        else:
            matched += 1

    return {
        "table": parser.tables,
        "thead": parser.theads,
        "tbody": parser.tbodies,
        "tr": parser.trs,
        "th": len(header_cells),
        "td": len(body_cells),
        "semantic_cells_total": len(cells),
        "cells_matching_model_text": matched,
        "cell_id_mismatches": mismatches[:10],
        "cell_id_mismatch_count": len(mismatches),
        "expected_header_cells": 5,
        "expected_body_cells": 100,
        "expected_total_cells": 105,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = "page_%03d" % args.page
    model = json.load(open(args.model, "r", encoding="utf-8"))

    import pymupdf
    doc = pymupdf.open(Path(args.pdf).resolve())
    pg = doc[args.page - 1]
    page_w, page_h = float(pg.rect.width), float(pg.rect.height)
    doc.close()

    cjk = detect_cjk_serif()
    print("[semantic] CJK font:", cjk["chosen"])

    # apply the persisted Phase 2B baseline calibration so the semantic-layer
    # render keeps the exact same geometry (baseline error ~0.4 pt)
    import render_zh as rz
    fit = model.get("fit") or {}
    if fit.get("line_box_em_cjk"):
        rz.LINE_BOX_EM_CJK = float(fit["line_box_em_cjk"])
    if fit.get("line_box_em_latin"):
        rz.LINE_BOX_EM_LATIN = float(fit["line_box_em_latin"])
    print("[semantic] line-box factors: cjk=%.3f latin=%.3f"
          % (rz.LINE_BOX_EM_CJK, rz.LINE_BOX_EM_LATIN))

    # ---- regenerate two-layer HTML + PDF + PNG ----
    html = build_zh_html(model, page_w, page_h, cjk["css_family"],
                         semantic=True)
    html_path = out / (base + "_table_zh.html")
    html_path.write_text(html, encoding="utf-8")
    from _chromium_pdf import render_html_to_pdf
    pdf_path = out / (base + "_table_zh.pdf")
    render_html_to_pdf(html_path, pdf_path)

    rdoc = pymupdf.open(pdf_path)
    pix = rdoc[0].get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0))
    png_path = out / (base + "_table_zh.png")
    pix.save(str(png_path))
    rdoc.close()

    # ---- HTML structure QA ----
    html_qa = html_structure_qa(html_path, model)
    print("[semantic] table=%d thead=%d tbody=%d tr=%d th=%d td=%d"
          % (html_qa["table"], html_qa["thead"], html_qa["tbody"],
             html_qa["tr"], html_qa["th"], html_qa["td"]))
    print("[semantic] cells matching model text: %d/%d"
          % (html_qa["cells_matching_model_text"],
             html_qa["semantic_cells_total"]))

    # ---- PDF text recovery QA ----
    spans = extract_spans(pdf_path)
    text_qa = audit(model, spans)
    print("[pdf-text] spans=%d exact=%.3f frag=%.3f recoverable=%d/%d"
          % (text_qa["total_text_spans"],
             text_qa["pdf_cell_text_exact_match_ratio"],
             text_qa["fragmented_character_ratio"],
             text_qa["cells_recoverable_from_pdf_text"],
             text_qa["total_cells"]))

    # ---- geometry regression (must be unchanged vs Phase 2B) ----
    spans_geo, rules = qa_zh.extract_zh_rendered(pdf_path)
    geo = qa_zh.compute_metrics(model, spans_geo, rules, state={})
    print("[geometry] col=%.3f row=%.3f rule=%.3f th=%.3f "
          "left=%.3f center=%.3f baseline=%.3f overflow=%s"
          % (geo["column_boundary_max_error_pt"],
             geo["row_boundary_max_error_pt"],
             geo["rule_position_max_error_pt"],
             geo["rule_thickness_max_error_pt"],
             geo["left_anchor_max_error_pt"],
             geo["center_anchor_max_error_pt"],
             geo["baseline_max_error_pt"],
             geo["overflow_cells"] or "none"))

    report = {
        "page": args.page,
        "html_layer": html_qa,
        "pdf_text_layer": {k: v for k, v in text_qa.items()
                           if k != "per_cell"},
        "geometry_regression": {k: v for k, v in geo.items()
                                if k != "per_cell"},
        "double_text_check": {
            "visual_spans_in_pdf": text_qa["total_text_spans"],
            "expected_visual_spans": 105,
            "duplicate_text_rendered": text_qa["total_text_spans"] != 105,
        },
        "cjk_font": cjk["chosen"],
    }
    report_path = out / "semantic_html_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print("\n[semantic] wrote", report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
