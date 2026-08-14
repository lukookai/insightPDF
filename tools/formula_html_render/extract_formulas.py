# -*- coding: utf-8 -*-
"""Path A: extract BabelDOC formula ground truth for one page.

Reuses the exact BabelDOC parse (new parser -> LayoutParser -> ParagraphFinder
-> StylesAndFormulas) and dumps every PdfFormula with characters / fonts /
curves / forms / offsets, plus per-paragraph inline vs display classification.

Output: formula_reference.json (per page) as specified in Phase 3B part 3.
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, "tools/table_geometry_debug")


def _ensure_home(cache_home: Path):
    cache_home = Path(cache_home).resolve()
    cache_home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(cache_home)
    os.environ["XDG_CACHE_HOME"] = str(cache_home / ".cache")
    os.environ["HF_HOME"] = str(cache_home / ".hf-home")


def _topleft(box, page_height):
    if box is None:
        return None
    x0, y0, x1, y1 = float(box.x), float(box.y), float(box.x2), float(box.y2)
    return [round(x0, 3), round(page_height - y1, 3),
            round(x1, 3), round(page_height - y0, 3)]


def extract_formulas(pdf: Path, page: int, cache_home: Path, work_dir: Path):
    import pymupdf
    from babeldoc.docvision.base_doclayout import DocLayoutModel
    from babeldoc.format.pdf.document_il.midend.layout_parser import LayoutParser
    from babeldoc.format.pdf.document_il.midend.paragraph_finder import (
        ParagraphFinder,
    )
    from babeldoc.format.pdf.document_il.midend.styles_and_formulas import (
        StylesAndFormulas,
    )
    from babeldoc.format.pdf.high_level import get_translation_stage, safe_save
    from babeldoc.format.pdf.new_parser.native_parse import (
        parse_prepared_pdf_with_new_parser_to_legacy_ir,
    )
    from babeldoc.format.pdf.translation_config import TranslationConfig
    from babeldoc.progress_monitor import ProgressMonitor

    _ensure_home(cache_home)
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    subset = work_dir / "source_subset.pdf"
    src = pymupdf.open(pdf)
    sub = pymupdf.open()
    sub.insert_pdf(src, from_page=page - 1, to_page=page - 1)
    sub.save(subset)
    sub.close()
    src.close()

    config = TranslationConfig(
        translator=types.SimpleNamespace(lang_in="en", lang_out="zh"),
        input_file=str(subset),
        lang_in="en", lang_out="zh",
        doc_layout_model=DocLayoutModel.load_onnx(),
        output_dir=str(work_dir / "out"),
        working_dir=str(work_dir / "work"),
        debug=True, no_dual=True, no_mono=True, qps=1,
        pool_max_workers=1, term_pool_max_workers=1, skip_clean=True,
        min_text_length=5, enable_graphic_element_process=True,
        remove_non_formula_lines=False, non_formula_line_iou_threshold=0.9,
        figure_table_protection_threshold=0.9,
        skip_formula_offset_calculation=False,
        disable_same_text_fallback=True,
    )
    config.progress_monitor = ProgressMonitor(get_translation_stage(config))
    config.working_dir = Path(config.working_dir)
    config.working_dir.mkdir(parents=True, exist_ok=True)
    prepared = config.get_working_file_path("input.pdf")
    prepared_new = config.get_working_file_path("input.new.pdf")
    doc_pdf = pymupdf.Document(config.input_file)
    safe_save(doc_pdf, prepared_new)
    prepared_new.replace(prepared)
    docs = parse_prepared_pdf_with_new_parser_to_legacy_ir(
        prepared, config=config, doc_pdf=doc_pdf)
    LayoutParser(config).process(docs, doc_pdf)
    ParagraphFinder(config).process(docs)
    StylesAndFormulas(config).process(docs)

    page_h = float(doc_pdf[0].rect.height)
    page_w = float(doc_pdf[0].rect.width)
    il_page = docs.page[0]
    doc_pdf.close()

    fonts = {f.font_id: f.name for f in il_page.pdf_font}

    formulas = []
    fid = 0
    for pid, para in enumerate(il_page.pdf_paragraph):
        if not para.pdf_paragraph_composition:
            continue
        for comp in para.pdf_paragraph_composition:
            if comp.pdf_formula is None:
                continue
            f = comp.pdf_formula
            fid += 1
            chars = []
            for ch in f.pdf_character:
                chars.append({
                    "char": ch.char_unicode,
                    "bbox_visual": _topleft(ch.visual_bbox.box if ch.visual_bbox else None, page_h),
                    "bbox": _topleft(ch.box, page_h),
                    "font_id": ch.pdf_style.font_id if ch.pdf_style else None,
                    "font": fonts.get(ch.pdf_style.font_id) if ch.pdf_style else None,
                    "font_size": round(float(ch.pdf_style.font_size or 0), 3)
                    if ch.pdf_style and ch.pdf_style.font_size else None,
                })
            formulas.append({
                "formula_id": "F%d" % fid,
                "type": "unknown",
                "bbox": _topleft(f.box, page_h),
                "text_or_repr": "".join(c["char"] or "" for c in chars),
                "characters": chars,
                "fonts": sorted({c["font"] for c in chars if c["font"]}),
                "font_sizes": sorted({c["font_size"] for c in chars
                                      if c["font_size"] is not None}),
                "offset": {
                    "x_offset": f.x_offset,
                    "y_offset": f.y_offset,
                },
                "curve_count": len(f.pdf_curve),
                "form_count": len(f.pdf_form),
                "metadata": {"paragraph_index": pid},
            })

    # classify inline vs display: a display formula's paragraph has one
    # composition only, or formula is the only content of its line
    for fa in formulas:
        pid = fa["metadata"]["paragraph_index"]
        para = il_page.pdf_paragraph[pid] if pid < len(il_page.pdf_paragraph) else None
        if para is not None:
            comps = para.pdf_paragraph_composition or []
            if len(comps) <= 1:
                fa["type"] = "display"
            else:
                fa["type"] = "inline"
        fa.pop("metadata", None)

    return {
        "page": page,
        "page_size": [round(page_w, 3), round(page_h, 3)],
        "formula_count": len(formulas),
        "formulas": formulas,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache-home", required=True)
    ap.add_argument("--work-dir", default=None)
    args = ap.parse_args()
    res = extract_formulas(Path(args.pdf), args.page,
                           Path(args.cache_home),
                           Path(args.work_dir) if args.work_dir
                           else Path(args.out).parent / "_formula_work")
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print("page %d: %d formulas" % (res["page"], res["formula_count"]))
    for f in res["formulas"]:
        print("  %s %-7s bbox=%s chars=%d fonts=%s sizes=%s offset=%s"
              % (f["formula_id"], f["type"], f["bbox"], len(f["characters"]),
                 f["fonts"], f["font_sizes"], f["offset"]))
