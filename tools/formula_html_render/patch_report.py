# -*- coding: utf-8 -*-
"""Append per-formula verdicts + conclusions to formula_ab_report.json."""
import json

p = "runs/fyresults/formula_p11/formula_ab_report.json"
r = json.load(open(p, encoding="utf-8"))

for f in r["formulas"]:
    s1 = f["strategy1_glyph_preserving"]
    s2 = f["strategy2_atomic_image"]
    if s2["formula_bbox_max_error_pt"] <= 1.0 and s1["formula_bbox_max_error_pt"] > 1.0:
        v = "direct_visual_only"
    elif s1["formula_bbox_max_error_pt"] <= 1.0 and s1["glyph_match_ratio"] >= 0.99:
        v = "direct_safe"
    else:
        v = "babeldoc_preferred" if f["original_span_count"] > 20 else "unsupported"
    f["verdict"] = {
        "label": v,
        "why": ("BabelDOC provides: precise bbox for atomic-image protection; "
                "glyph HTML rebuild degrades because Computer-Modern math fonts "
                "(CMMI/CMSY/CMEX) have no browser metric match")
        if v == "direct_visual_only" else
        ("plain text-glyph formula fully rebuildable without BabelDOC")
        if v == "direct_safe" else "needs BabelDOC-style detection for this structure",
    }

r["conclusion"] = {
    "question_1_babeldoc_irreplaceable_info": (
        "For the HTML route BabelDOC adds almost nothing irreplaceable: its "
        "detection is character-rule heuristics (reimplementable), its bbox is a "
        "span union (PyMuPDF gives the same), and x/y_offset are only consumed by "
        "BabelDOC's own typesetting backend. The one genuinely useful thing is a "
        "curated formula region bbox to drive atomic-image protection."),
    "question_2_pymupdf_recovery": (
        "PyMuPDF recovers every formula as text_glyphs (CMMI10/CMSY10/CMR10/CMEX10, "
        "0 images, 0-1 vector fraction bars on p5). source_mode: text_glyphs (p11), "
        "mixed (p5 fraction = text + 1 vector bar). Nothing is rasterized in the "
        "source PDF."),
    "question_3_formulas_without_babeldoc": (
        "Simple inline text-glyph formulas (single font, no CMEX/CMSY big symbols) "
        "can be fully protected without BabelDOC (strategy 1 keeps the text layer; "
        "strategy 2 keeps geometry)."),
    "question_4_visual_preservation_only": (
        "Complex formulas (fractions, CMEX big delimiters, mixed superscript/"
        "subscript stacks) can only be reliably preserved VISUALLY (strategy 2 "
        "atomic image, geometry error <=0.36pt, zero text layer). Glyph rebuild "
        "degraded: bbox error 11-16pt, glyph match 0.10-0.69."),
    "question_5_offset_useful": (
        "No. x_offset/y_offset are consumed only by BabelDOC's typesetting backend. "
        "The HTML route locks the formula at its original bbox (strategy 2: "
        "0.25-0.36pt error) and never needs the offset."),
    "question_6_fallback_trigger": (
        "Fall back to BabelDOC formula detection only when: (a) a page contains "
        "CMEX/CMSY big-symbol or fraction structures and (b) the custom detector's "
        "bbox disagrees with the span-union bbox by >1pt. For everything else the "
        "custom detector is equivalent."),
    "question_7_removal_loss": (
        "Removing BabelDOC from the formula chain loses: curated inline/display "
        "composition structure (recoverable via line-occupancy heuristic) and "
        "formula-curve collection (rare, recoverable via get_drawings). It does "
        "NOT lose offset/placeholder/translation-exclusion for the HTML route. "
        "Remaining system-wide reliance is the non-formula chain (paragraph/"
        "reading-order/scanned/input-repair)."),
}
json.dump(r, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("updated", p)
for f in r["formulas"]:
    print(f["formula_id"], "->", f["verdict"]["label"])
