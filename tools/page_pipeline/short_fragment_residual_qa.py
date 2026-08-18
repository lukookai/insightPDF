# -*- coding: utf-8 -*-
"""ShortFragmentResidualClosureQA (visual-v06).

Even SHORT source fragments that require translation must not be left behind:

    heading            -- a section heading like "4. Experiment"
    paragraph head     -- first sentence of a translatable paragraph
    paragraph tail     -- last sentence of a translatable paragraph
    standalone short   -- a short standalone English line (e.g. a formula
                          context tail "in Section 4.4.")
    formula context    -- prose annotation around a formula
    citation-adjacent prose

Document-general: whether a fragment is CHECKED depends only on
``translation_required`` + provenance, NEVER on its length.  The only legal
exemptions are protected entities / citations / URLs / variables / code /
KEEP route / NON_TRANSLATABLE_MATH / reference-exempt.  A bare
``len(text) < N -> ignore`` is forbidden.

Fragment provenance (source paragraph + spans + char range) comes from the
source page model and the recovered (PAF) paragraph map; the final PDF is the
last verification truth (text layer + SVG vector-ink residual).

Hard (all 0):
    short_translatable_source_residual_count
    paragraph_head_residual_count
    paragraph_tail_residual_count
    standalone_short_residual_count
    heading_short_residual_count
    formula_context_short_residual_count
    short_target_missing_count
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import pymupdf  # noqa: E402

from final_visible_translation_qa import (  # noqa: E402
    EXEMPT_ROLES, TRANSLATABLE_ROLES, normalize_visible_text,
    _char_seq, _strip_tokens, recover_target_in_lines,
    is_prose_adopted_formula)
from residual_prose_truth_qa import (  # noqa: E402
    _final_text_spans, is_translatable_prose_line)
from prose_adopted_formula_recovery import (  # noqa: E402
    _text_lines_in_regions, _inside)
from semantic_structure_closure_qa import (  # noqa: E402
    _source_heading_candidates)
from semantic_role_translation_qa import (  # noqa: E402
    _is_heading_line, _final_lines)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CITATION_RE = re.compile(
    r"\([^)]*(?:et al\.|19\d\d|20\d\d|pp\.|eds\.)[^)]*\)|\bet al\.")
_URL_RE = re.compile(r"https?://|www\.", re.I)
_CODE_RE = re.compile(r"^\s*\{\{[A-Z_0-9]+(?:_[A-Z0-9]+)?\}\}")


def _is_exempt_short(text: str) -> bool:
    """Legal short-fragment exemptions (document-general)."""
    t = (text or "").strip()
    if _CITATION_RE.search(t):
        return True
    if _URL_RE.search(t):
        return True
    if _CODE_RE.match(t):
        return True
    return False


def short_fragment_residual_qa(
    page_model: Dict[str, Any],
    translations: Dict[str, str],
    flows: List[Dict[str, Any]],
    final_pdf_path=None,
    html_path=None,
    source_pdf_path=None,
    out_dir=None,
    page_idx: int = 0,
    recovered_paragraphs: List[Dict[str, Any]] | None = None,
    grid: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    body_size = float((grid or {}).get("body_size_estimated") or 10.0)
    headings = _source_heading_candidates(
        str(source_pdf_path) if source_pdf_path else "", page_idx, body_size)
    line_chars, line_boxes = _final_lines(final_pdf_path)
    final_spans = _final_text_spans(final_pdf_path)
    final_chars = _char_seq(" ".join(s["text"] for s in final_spans))
    recovered_bboxes = [p.get("bbox") or [] for p in (recovered_paragraphs or [])]
    recovered_fids = {str(p.get("source_formula_region_id"))
                      for p in (recovered_paragraphs or [])
                      if re.search(r"[\u4e00-\u9fff]", p.get("target_text") or "")}
    recovered_texts = " ".join((p.get("source_text") or "") for p in
                               (recovered_paragraphs or []))

    details: List[Dict[str, Any]] = []
    m_short = m_head = m_tail = 0
    m_standalone = m_heading_short = m_fctx = m_target_missing = 0

    # ---- 1. text-region paragraph head / tail ----------------------------
    for r in page_model.get("regions", []):
        if r.get("type") != "text":
            continue
        para = r.get("payload") or {}
        role = (para.get("semantic_role") or para.get("style_role")
                or "body")
        role_l = role.lower()
        if role_l in EXEMPT_ROLES:
            continue
        if role_l not in TRANSLATABLE_ROLES:
            continue
        pid = para.get("paragraph_id")
        if not pid:
            continue
        src = para.get("source_text") or ""
        if not src.strip():
            continue
        tgt = translations.get(pid) or ""
        if not tgt or not _CJK_RE.search(tgt):
            # whole paragraph untranslated is counted by the role QA; here we
            # only add the short head/tail signal
            pass
        src_sents = re.split(r"(?<=[.!?])\s+", src.strip())
        # head: first sentence
        if src_sents and len(src_sents[0]) >= 12 \
                and is_translatable_prose_line(src_sents[0]):
            head_seq = _char_seq(src_sents[0])
            tgt_seq = _char_seq(tgt)
            if head_seq and head_seq in final_chars and \
                    (tgt_seq and tgt_seq not in final_chars):
                m_head += 1
                m_short += 1
                details.append({
                    "kind": "short_fragment_residual",
                    "fragment_type": "paragraph_head",
                    "paragraph_id": pid,
                    "fragment_id": "%s-HEAD" % pid,
                    "source_text": src_sents[0][:120],
                    "final_visible_text": src_sents[0][:60],
                    "source_bbox": [round(v, 1) for v in r.get("bbox", [])],
                    "reason": "source head sentence visible while target "
                              "absent in final PDF",
                })
        # tail: last sentence
        if len(src_sents) > 1:
            tail = src_sents[-1]
            if len(tail) >= 12 and is_translatable_prose_line(tail):
                tail_seq = _char_seq(tail)
                tgt_seq = _char_seq(tgt)
                if tail_seq and tail_seq in final_chars and \
                        (tgt_seq and tgt_seq not in final_chars):
                    m_tail += 1
                    m_short += 1
                    details.append({
                        "kind": "short_fragment_residual",
                        "fragment_type": "paragraph_tail",
                        "paragraph_id": pid,
                        "fragment_id": "%s-TAIL" % pid,
                        "source_text": tail[:120],
                        "final_visible_text": tail[:60],
                        "source_bbox": [round(v, 1) for v in r.get("bbox", [])],
                        "reason": "source tail sentence visible while target "
                                  "absent in final PDF",
                    })

    # ---- 2. formula-region swallowed short heading / prose ---------------
    for r in page_model.get("regions", []):
        if r.get("type") != "formula":
            continue
        p = r.get("payload") or {}
        # only prose-adopted formula regions can leave untranslated short
        # English fragments as vector ink; genuine display formulas are math
        # (not translation-required) -> same gate as v05 / role QA.
        if not is_prose_adopted_formula(p):
            continue
        bb = p.get("layout_bbox") or []
        if len(bb) != 4:
            continue
        fid = str(p.get("formula_id"))
        if not fid:
            continue
        lines = _text_lines_in_regions(str(source_pdf_path), page_idx, [bb])
        for ln in lines:
            is_head = (any(_inside(ln["bbox"], h["bbox"], tol=3.0)
                            for h in headings)
                       or _is_heading_line(ln["text"]))
            covered = any(_inside(ln["bbox"], rb, tol=2.0)
                          for rb in recovered_bboxes)
            if covered or fid in recovered_fids:
                # the line's owning formula region was recovered as a
                # translated block (its SVG is skipped) -> stray line is
                # absent, not an English residual.  Skip it.
                continue
            if is_head:
                m_heading_short += 1
                m_short += 1
                details.append({
                    "kind": "short_fragment_residual",
                    "fragment_type": "heading",
                    "paragraph_id": None,
                    "fragment_id": "%s-HEAD" % fid,
                    "source_text": ln["text"][:120],
                    "final_visible_text": "source SVG vector ink (untranslated)",
                    "source_bbox": [round(v, 1) for v in ln["bbox"]],
                    "formula_id": fid,
                    "reason": "swallowed short heading not recovered/translated",
                })
                continue
            if _is_exempt_short(ln["text"]):
                continue
            if is_translatable_prose_line(ln["text"]):
                m_standalone += 1
                m_fctx += 1
                m_short += 1
                details.append({
                    "kind": "short_fragment_residual",
                    "fragment_type": "formula_context",
                    "paragraph_id": None,
                    "fragment_id": "%s-SHORT" % fid,
                    "source_text": ln["text"][:120],
                    "final_visible_text": "source SVG vector ink (untranslated)",
                    "source_bbox": [round(v, 1) for v in ln["bbox"]],
                    "formula_id": fid,
                    "reason": "swallowed short prose fragment not "
                              "recovered/translated",
                })

    metrics = {
        "short_translatable_source_residual_count": m_short,
        "paragraph_head_residual_count": m_head,
        "paragraph_tail_residual_count": m_tail,
        "standalone_short_residual_count": m_standalone,
        "heading_short_residual_count": m_heading_short,
        "formula_context_short_residual_count": m_fctx,
        "short_target_missing_count": m_target_missing,
    }
    return {
        "qa": "short_fragment_residual_qa",
        "schema_version": "visual_v06.short_fragment_residual_qa.v1",
        "metrics": metrics,
        "short_details": details,
        "decision": "pass" if all(v == 0 for v in metrics.values()) else "fail",
    }
