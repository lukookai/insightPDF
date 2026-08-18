# -*- coding: utf-8 -*-
"""math_token_sequence_qa -- v06 MathTokenSequenceClosure QA.

Verifies that the math formula token sequence is preserved structurally across
the three stages (source -> protected -> restored final).  See
math_token_sequence.py for the methodology.  No LaTeX reconstruction, no OCR.

Hard metrics (all must be 0 for a clean page):
  math_token_loss_count, math_token_duplicate_count,
  math_token_order_inversion_count, math_base_symbol_loss_count,
  math_superscript_loss_count, math_subscript_loss_count,
  math_group_structure_mismatch_count.

Returns {"metrics": {...}, "decision": "pass"|"fail", "detail": {...}}.
"""
from __future__ import annotations

from math_token_sequence import (
    extract_source_formula_sequence,
    extract_protected_formula_sequence,
    extract_final_formula_sequence,
    compare_math_sequences,
    decision as _decide,
)


def math_token_sequence_qa(page_model, translations, flows=None,
                            final_pdf_path=None, html_path=None,
                            source_pdf_path=None, page_idx=0,
                            recovered_paragraphs=None, grid=None,
                            skipped_formulas=None):
    source_seq = extract_source_formula_sequence(page_model)
    protected_seq = extract_protected_formula_sequence(translations, page_model)
    final_seq = extract_final_formula_sequence(html_path) if html_path else []
    metrics, detail = compare_math_sequences(
        source_seq, protected_seq, final_seq,
        skipped_fids=set(skipped_formulas or []))
    dec = _decide(metrics)
    return {
        "qa": "math_token_sequence",
        "page_idx": page_idx,
        "metrics": metrics,
        "decision": dec,
        "detail": detail,
        "source_formula_count": len(source_seq),
        "final_formula_count": len(final_seq),
        "protected_formula_count": len(protected_seq[0]),
    }
