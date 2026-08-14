"""Path A: BabelDOC native parse, *without* translation.

This reuses the exact pre-translation stages the production pipeline runs
(``babeldoc_html/frontend.py::_run_babeldoc_stages``) but stops before
``ILTranslator`` so that **no DeepSeek request is ever made**.  We only need the
intermediate layer (IL) that BabelDOC builds from the PDF, because that is where
table geometry either is or is not preserved.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

try:  # package import (python -m tools.table_geometry_debug.run_diagnose)
    from .common import classify_primitives, il_box_to_topleft
except ImportError:  # direct script run
    from common import classify_primitives, il_box_to_topleft


def _ensure_babeldoc_importable(cache_home: Path) -> None:
    """Mirror the env setup ``run_babeldoc_frontend`` does before importing babeldoc.

    babeldoc resolves its model/font cache from ``HOME`` at import time, so we
    point it at the caller-supplied cache home.
    """
    cache_home = Path(cache_home).expanduser().resolve()
    cache_home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(cache_home)
    os.environ["XDG_CACHE_HOME"] = str(cache_home / ".cache")
    os.environ["HF_HOME"] = str(cache_home / ".hf-home")


def _dummy_translator():
    """The pre-translation stages never touch ``config.translator`` (verified by
    grep).  A namespace is enough; we avoid constructing a real OpenAITranslator
    which would require an API key."""
    return types.SimpleNamespace(
        lang_in="en", lang_out="zh", name="diagnostic-noop"
    )


def extract_babeldoc(
    pdf: Path,
    page: int,
    *,
    babeldoc_cache_home: Path,
    doclayout_model: Path | None = None,
    work_dir: Path,
) -> dict:
    """Run BabelDOC native parse for a single page and emit the unified schema.

    Returns the same dict shape as :func:`path_geometry.extract_geometry`.
    """
    import pymupdf

    from babeldoc.docvision.base_doclayout import DocLayoutModel
    from babeldoc.docvision.doclayout import OnnxModel
    from babeldoc.format.pdf.document_il.midend.layout_parser import LayoutParser
    from babeldoc.format.pdf.document_il.midend.paragraph_finder import (
        ParagraphFinder,
    )
    from babeldoc.format.pdf.document_il.midend.styles_and_formulas import (
        StylesAndFormulas,
    )
    from babeldoc.format.pdf.high_level import (
        get_translation_stage,
        safe_save,
    )
    from babeldoc.format.pdf.new_parser.native_parse import (
        parse_prepared_pdf_with_new_parser_to_legacy_ir,
    )
    from babeldoc.format.pdf.translation_config import TranslationConfig
    from babeldoc.progress_monitor import ProgressMonitor

    _ensure_babeldoc_importable(babeldoc_cache_home)

    import pymupdf

    pdf = Path(pdf).resolve()
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    # Build a single-page physical subset so BabelDOC parses exactly one page.
    # (BabelDOC's own ``pages=`` filter mis-behaves on this document -- it
    # returns 0 paragraphs/rectangles -- whereas the production pipeline parses
    # the whole file.  A 1-page subset reproduces the proven path cheaply.)
    subset_pdf = work_dir / "source_subset.pdf"
    src_doc = pymupdf.open(pdf)
    sub_doc = pymupdf.open()
    sub_doc.insert_pdf(src_doc, from_page=page - 1, to_page=page - 1)
    sub_doc.save(subset_pdf)
    sub_doc.close()
    src_doc.close()

    resolved_model = Path(doclayout_model).expanduser() if doclayout_model else None
    if resolved_model and resolved_model.is_file():
        doc_layout_model = OnnxModel(str(resolved_model.resolve()))
    else:
        doc_layout_model = DocLayoutModel.load_onnx()

    config = TranslationConfig(
        translator=_dummy_translator(),
        input_file=str(subset_pdf),
        lang_in="en",
        lang_out="zh",
        doc_layout_model=doc_layout_model,
        output_dir=str(work_dir / "unused_pdf_output"),
        working_dir=str(work_dir / "babeldoc_work"),
        debug=True,
        no_dual=True,
        no_mono=True,
        qps=1,
        pool_max_workers=1,
        term_pool_max_workers=1,
        skip_clean=True,
        min_text_length=5,
        enable_graphic_element_process=True,  # keep graphic rects/curves
        remove_non_formula_lines=False,  # DO NOT delete lines for diagnosis
        non_formula_line_iou_threshold=0.9,
        figure_table_protection_threshold=0.9,
        skip_formula_offset_calculation=False,
        disable_same_text_fallback=True,
    )
    config.progress_monitor = ProgressMonitor(get_translation_stage(config))

    from pymupdf import Document

    config.working_dir = Path(config.working_dir)
    config.working_dir.mkdir(parents=True, exist_ok=True)
    prepared_pdf = config.get_working_file_path("input.pdf")
    prepared_new = config.get_working_file_path("input.new.pdf")
    doc_pdf = Document(config.input_file)
    safe_save(doc_pdf, prepared_new)
    prepared_new.replace(prepared_pdf)

    # --- pre-translation stages only; NO ILTranslator -> no DeepSeek call ---
    docs = parse_prepared_pdf_with_new_parser_to_legacy_ir(
        prepared_pdf, config=config, doc_pdf=doc_pdf
    )
    LayoutParser(config).process(docs, doc_pdf)
    ParagraphFinder(config).process(docs)
    StylesAndFormulas(config).process(docs)

    page_height = float(doc_pdf[0].rect.height)
    page_width = float(doc_pdf[0].rect.width)
    doc_pdf.close()

    # The subset contains exactly one page -> index 0.
    il_page = docs.page[0]

    # ---- detected tables (DocLayout class_name == "table") ----
    detected_tables = []
    for region in il_page.page_layout:
        if (region.class_name or "").lower() == "table":
            detected_tables.append(
                {
                    "bbox": il_box_to_topleft(region.box, page_height),
                    "label": region.class_name,
                    "confidence": round(float(region.conf), 4) if region.conf is not None else None,
                    "id": region.id,
                }
            )

    # ---- graphic primitives (rectangles AND curves) -> lines / rectangles ----
    # BabelDOC stores vector strokes either as pdf_rectangle (axis-aligned
    # rects) or pdf_curve (path-based strokes, incl. table rules).  Merge both
    # so the diagnostic reveals wherever the lines actually landed.
    rect_bboxes = []
    for r in il_page.pdf_rectangle:
        if r.box is not None:
            rect_bboxes.append(tuple(il_box_to_topleft(r.box, page_height)))
    for c in il_page.pdf_curve:
        if c.box is not None:
            rect_bboxes.append(tuple(il_box_to_topleft(c.box, page_height)))
    h_lines, v_lines, rectangles = classify_primitives(rect_bboxes)

    # ---- texts (paragraph level) with font + size ----
    font_by_id = {f.font_id: f for f in il_page.pdf_font}
    texts = []
    for para in il_page.pdf_paragraph:
        if not (para.unicode or "").strip():
            continue
        style = para.pdf_style
        font_name = None
        font_size = None
        if style is not None:
            font_size = round(float(style.font_size or 9.0), 3) if style.font_size else None
            fnt = font_by_id.get(style.font_id)
            if fnt is not None:
                font_name = fnt.name
        texts.append(
            {
                "text": para.unicode,
                "bbox": il_box_to_topleft(para.box, page_height),
                "font": font_name,
                "font_size": font_size,
            }
        )

    result = {
        "page": {
            "width": round(page_width, 3),
            "height": round(page_height, 3),
            "coordinate_unit": "pt",
        },
        "texts": texts,
        "horizontal_lines": h_lines,
        "vertical_lines": v_lines,
        "rectangles": rectangles,
        "detected_tables": detected_tables,
    }
    result["_meta"] = {
        "parser": "babeldoc-native",
        "graphic_primitive_count": len(rect_bboxes),
        "pdf_rectangle_count": len(il_page.pdf_rectangle),
        "pdf_curve_count": len(il_page.pdf_curve),
        "pdf_character_count": len(il_page.pdf_character),
        "pdf_paragraph_count": len(il_page.pdf_paragraph),
        "page_layout_count": len(il_page.page_layout),
    }
    return result
