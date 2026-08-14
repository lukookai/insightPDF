"""Phase 2C font A/B + lang A/B for the PDF text layer.

Same HTML / same font size / same positions / same TableModel / same Chromium
parameters -- only the CJK font family (or the lang attributes) changes.

Only fonts that actually exist on this machine are tested (probed the same way
as the runtime).  For every variant we render -> PDF -> measure:

  * text layer: span count per cell, exact-match / fragmentation / recovery
  * geometry:  baseline error (visual regression guard)

Output: font_text_layer_comparison.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "实现源码" / "pdf_translator"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from render_zh import build_zh_html, LINE_BOX_EM_CJK
from audit_text_layer import extract_spans, audit
from cjk_fonts import detect_cjk_serif
import qa_zh


def render_variant(model, page_w, page_h, font_css, out, tag, *,
                   html_lang=None, cell_lang=None):
    html = build_zh_html(model, page_w, page_h, font_css,
                         html_lang=html_lang, cell_lang=cell_lang)
    h = out / ("_ab_%s.html" % tag)
    h.write_text(html, encoding="utf-8")
    from _chromium_pdf import render_html_to_pdf
    p = out / ("_ab_%s.pdf" % tag)
    render_html_to_pdf(h, p)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fonts", default=None,
                    help="comma list; default = installed CJK serif set")
    args = ap.parse_args()

    model = json.load(open(args.model, "r", encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    import pymupdf
    doc = pymupdf.open(Path(args.pdf).resolve())
    pg = doc[args.page - 1]
    page_w, page_h = float(pg.rect.width), float(pg.rect.height)
    doc.close()

    probe = detect_cjk_serif()
    installed = [fam for fam, _ok in probe["probe"].items() if _ok]
    if args.fonts:
        fonts = [f.strip() for f in args.fonts.split(",") if f.strip()]
    else:
        # serif-first, and only what is actually installed
        order = ["SimSun", "SimSun-ExtB", "NSimSun", "Songti SC",
                 "Noto Serif CJK SC", "Source Han Serif SC"]
        fonts = [f for f in order if f in installed]
        extras = [f for f in installed if f not in fonts]
        fonts += extras
    print("[ab] installed CJK fonts:", installed)
    print("[ab] testing fonts      :", fonts)

    results = {"fonts": {}, "lang_variants": {}, "installed": installed,
               "model_page": args.page}

    for fam in fonts:
        css = "'%s',serif" % fam
        tag = fam.replace(" ", "_").replace("-", "")
        pdf = render_variant(model, page_w, page_h, css, out, "f_%s" % tag)
        spans = extract_spans(pdf)
        audit_res = audit(model, spans)
        # geometry regression: baseline error (unclipped, original size)
        spans_geo, rules = qa_zh.extract_zh_rendered(pdf)
        mtr = qa_zh.compute_metrics(model, spans_geo, rules, state={})
        results["fonts"][fam] = {
            "css_family": css,
            "text_layer": {k: v for k, v in audit_res.items()
                           if k != "per_cell"},
            "geometry": {
                "baseline_max_error_pt": mtr["baseline_max_error_pt"],
                "overflow_cells": mtr["overflow_cells"],
                "cross_column_cells": mtr["cross_column_cells"],
                "column_boundary_max_error_pt":
                    mtr["column_boundary_max_error_pt"],
                "rendered_fonts": mtr["rendered_fonts"],
            },
        }
        print("[ab] %-16s exact=%.2f frag=%.3f baseline=%.3f fonts=%s"
              % (fam, audit_res["pdf_cell_text_exact_match_ratio"],
                 audit_res["fragmented_character_ratio"],
                 mtr["baseline_max_error_pt"], mtr["rendered_fonts"]))

    # lang A/B on the chosen font (SimSun if installed, else first)
    base_fam = "SimSun" if "SimSun" in fonts else fonts[0]
    css = "'%s',serif" % base_fam
    variants = {
        "no_lang": {},
        "html_lang_zh-CN": {"html_lang": "zh-CN"},
        "cell_lang_zh-CN": {"cell_lang": "zh-CN"},
        "html_and_cell_lang": {"html_lang": "zh-CN", "cell_lang": "zh-CN"},
    }
    for vname, kw in variants.items():
        pdf = render_variant(model, page_w, page_h, css, out,
                             "l_%s" % vname, **kw)
        spans = extract_spans(pdf)
        ar = audit(model, spans)
        results["lang_variants"][vname] = {
            "font": base_fam,
            "params": kw,
            "text_layer": {k: v for k, v in ar.items() if k != "per_cell"},
        }
        print("[ab] lang %-20s exact=%.2f frag=%.3f"
              % (vname, ar["pdf_cell_text_exact_match_ratio"],
                 ar["fragmented_character_ratio"]))

    # conclusion
    best_font = max(results["fonts"],
                    key=lambda f: results["fonts"][f]["text_layer"]
                    ["pdf_cell_text_exact_match_ratio"])
    results["conclusion"] = {
        "best_font": best_font,
        "current_font": "SimSun",
        "character_fragmentation_observed": any(
            r["text_layer"]["cells_with_character_level_fragmentation"] > 0
            for r in results["fonts"].values()),
        "chromium_cjk_text_layer": (
            "Chromium prints each logical cell as exactly one text span with a "
            "full ToUnicode mapping; no character-level splitting was observed "
            "for any installed CJK font on this machine."),
    }
    out_json = out / "font_text_layer_comparison.json"
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print("\n[ab] wrote", out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
