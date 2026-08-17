# -*- coding: utf-8 -*-
"""ResidualProseTruthQA (visual-v05).

Answers the question that FinalSourceResidualQA (visual-v04) missed:

    is any source prose that REQUIRED translation still VISIBLE
    in the final PDF -- in the TEXT LAYER **or** as VECTOR INK?

visual-v04's residual QA only counted text-like vector paths inside
prose-adopted formula bboxes.  It missed:

  * formula regions whose SVG is STILL rendered while the formula bbox
    also contains real English prose lines (PPAT p005 B2 "form and its
    inverse transformation respectively.", B11/B14 "where ...", PPAT
    p004 B26 "C channels, we can extract its high-frequency
    components") -- those lines are translated neither in the text
    layer (they are vector paths) nor visually (they are English);

  * source paragraph HEAD/TAIL fragments left behind by a partial
    recovery (PAF_SOURCE_HEAD_LEFT_BEHIND / PAF_SOURCE_TAIL_LEFT_BEHIND);

  * source fallback renders (render_source == SOURCE_TEXT_SELECTED) for
    fragments that required translation.

This QA is PROVENANCE-AWARE: it starts from source PDF text lines inside
formula regions (source span evidence), not from a language detector over
the final page.

Detection paths:

  A. formula vector prose:  for every formula region whose SVG is still
     referenced by the final HTML (i.e. NOT recovered/skipped), extract
     the source-PDF text lines inside its bbox.  Lines that are
     translatable prose (>= 3 lowercase English words with sentence
     completeness, no citation-only lines, math-symbol ratio below 50%)
     are VISIBLE English in the final PDF (the SVG renders them as
     text_as_path) -> residual fragment.

  B. paragraph head/tail:  for every translatable paragraph, the
     canonical target must cover the source; any head/tail sentence of
     the source that is still visible in the final PDF text layer while
     the target fragment is absent -> residual fragment.

  C. source fallback:  any flow paragraph rendered from SOURCE (no
     canonical target) counts as source_fallback_for_required_translation.

Legal English stays legal: model names, dataset names, method acronyms,
citations, equations, variables, KEEP route, NON_TRANSLATABLE_MATH,
reference-exempt roles, figure-internal text.  The prose-line test works
on LOWERCASE content words AFTER stripping citation/entity tokens, so
"LSOTB-TIR100 Liu et al. (2023a) 是..." never triggers (CJK present)
and "where SE(·) is the spatial feature extractor..." DOES trigger.

Hard (all must be 0):
    translatable_source_residual_fragment_count
    translatable_source_residual_char_count
    translatable_source_residual_sentence_count
    source_head_residual_count
    source_tail_residual_count
    formula_adjacent_source_residual_count
    untranslated_required_fragment_count
    source_fallback_for_required_translation_count
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

import pymupdf  # noqa: E402

from final_visible_translation_qa import (  # noqa: E402
    EXEMPT_ROLES, TRANSLATABLE_ROLES, _NON_PROSE_RE, _char_seq,
    _prose_word_count, _strip_tokens, is_prose_adopted_formula,
    normalize_visible_text)

# ---- sentence-completeness starters (document-general) -------------------
_SENTENCE_STARTERS = (
    "where", "specifically", "moreover", "therefore", "in", "to", "for",
    "the", "this", "these", "that", "we", "our", "however", "then", "when",
    "given", "using", "as", "a", "an", "consequently", "finally", "first",
    "second", "third", "based", "from", "with", "by", "after", "before",
    "unlike", "like", "such", "because", "although", "since", "while",
    "on", "of", "at", "under", "over", "through", "via", "note", "notably",
    "importantly", "intuitively", "formally", "equivalently", "together",
    "both", "all", "each", "any", "no", "one", "two", "three", "there",
    "here", "also", "still", "thus", "hence", "e.g.", "i.e.", "namely",
)

_MATH_CHARS_RE = re.compile(
    r"[\u0370-\u03ff\U0001d400-\U0001d7ff\u2200-\u22ff\u2190-\u21ff"
    r"\u2300-\u23ff\u00d7\u2212\u221a\u222b\u2264\u2265\u2260\u2248]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CITATION_RE = re.compile(
    r"\([^)]*(?:et al\.|19\d\d|20\d\d|pp\.|eds\.)[^)]*\)|\bet al\.")


def _lower_content_words(text: str) -> List[str]:
    """Lowercase English content words after stripping citations/entities.

    The FIRST token of the line always counts (it may be a capitalized
    sentence starter like "Specifically"); later capitalized tokens are
    treated as named entities and removed.  Function words (where/while/
    the/and/...) are NOT removed -- the sentence-completeness test needs
    them (e.g. "where ... while maintaining differentiability.").
    """
    t = _CITATION_RE.sub(" ", text or "")
    tokens = re.findall(r"[A-Za-z]{3,}", t)
    words: List[str] = []
    for i, w in enumerate(tokens):
        if i == 0:
            words.append(w.lower())
        elif w.isupper() or (w[0].isupper() and w[1:].islower()):
            continue                      # named entity / acronym
        else:
            words.append(w.lower())
    return words


def is_translatable_prose_line(text: str) -> bool:
    """document-general prose-line test (see module docstring)."""
    t = normalize_visible_text(text or "")
    if not t:
        return False
    if _CJK_RE.search(t):
        return False                      # already a target-mixed line
    if _NON_PROSE_RE.match(t.strip()):
        return False                      # footer / page furniture
    if _MATH_CHARS_RE.search(t) and len(_MATH_CHARS_RE.findall(t)) > 0.4 * len(t.strip()):
        return False                      # math-symbol dominated line
    words = _lower_content_words(t)
    if len(words) < 3:
        return False
    stripped = t.strip()
    first = stripped.split()[0].lower().rstrip("(;:,")
    has_period = bool(re.search(r"[.!?](?:['\u201d\u2019])?$", stripped)) or \
        ". " in stripped or ".  " in stripped or ". \u2014" in stripped
    return has_period or first in _SENTENCE_STARTERS


def _source_lines_in_bbox(pdf_path: str, page_idx: int,
                          bbox: List[float]) -> List[Dict[str, Any]]:
    """Source-PDF text lines inside a bbox (non-OCR, PyMuPDF text layer)."""
    out: List[Dict[str, Any]] = []
    try:
        doc = pymupdf.open(str(pdf_path))
        try:
            page = doc[page_idx]
            d = page.get_text("dict", clip=pymupdf.Rect(*bbox))
            for b in d.get("blocks", []):
                if b.get("type") != 0:
                    continue
                for l in b.get("lines", []):
                    txt = "".join(s.get("text") or "" for s in
                                  l.get("spans", [])).strip()
                    if not txt:
                        continue
                    bb = [float(v) for v in l["bbox"]]
                    out.append({
                        "bbox": bb, "text": txt,
                        "size": max((s.get("size") or 0)
                                    for s in l.get("spans", [])),
                        "font": next((s.get("font") or "")
                                     for s in l.get("spans", []) if
                                     (s.get("text") or "").strip()),
                    })
            return out
        finally:
            doc.close()
    except Exception:  # noqa: BLE001
        return out


def _rendered_formula_ids(html_path) -> List[str]:
    """Formula ids whose SVG files are actually referenced by the HTML."""
    ids: List[str] = []
    if not html_path or not Path(html_path).exists():
        return ids
    html = Path(html_path).read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"(?:data-formula|data-inline-formula|src)=[\"']"
                         r"[^\"']*?B(\d+)[^\"']*[\"']", html):
        ids.append("B" + m.group(1))
    return sorted(set(ids))


def _final_text_spans(final_pdf_path) -> List[Dict[str, Any]]:
    """Final PDF text layer spans (with bbox) + normalized text."""
    spans: List[Dict[str, Any]] = []
    if not final_pdf_path or not Path(final_pdf_path).exists():
        return spans
    try:
        doc = pymupdf.open(str(final_pdf_path))
        try:
            page = doc[0]
            d = page.get_text("dict")
            for b in d.get("blocks", []):
                if b.get("type") != 0:
                    continue
                for l in b.get("lines", []):
                    for s in l.get("spans", []):
                        txt = s.get("text") or ""
                        if txt.strip():
                            spans.append({
                                "bbox": [float(v) for v in s["bbox"]],
                                "text": txt,
                                "font": s.get("font", "")})
            return spans
        finally:
            doc.close()
    except Exception:  # noqa: BLE001
        return spans


def residual_prose_truth_qa(
    page_model: Dict[str, Any],
    translations: Dict[str, str],
    flows: List[Dict[str, Any]],
    final_pdf_path=None,
    html_path=None,
    source_pdf_path=None,
    out_dir=None,
    recovered_formulas=None,
    page_idx: int = 0,
) -> Dict[str, Any]:
    """Run ResidualProseTruthQA for one page."""
    recovered = set(recovered_formulas or [])
    rendered_ids = set(_rendered_formula_ids(html_path))
    final_spans = _final_text_spans(final_pdf_path)
    final_text = " ".join(s["text"] for s in final_spans)
    final_norm = _char_seq(final_text)

    details: List[Dict[str, Any]] = []
    m_frag = m_char = m_sent = 0
    m_head = m_tail = m_fa = 0
    m_untranslated = 0
    m_fallback = 0

    # ---- A. formula vector prose (rendered, non-recovered formulas) ------
    # locate page index from flows/page_model (single-page QA: caller passes)
    for r in page_model.get("regions", []):
        if r.get("type") != "formula":
            continue
        p = r.get("payload") or {}
        fid = str(p.get("formula_id"))
        if not fid:
            continue
        if fid in recovered:
            continue                      # SVG excluded from rendering
        if fid not in rendered_ids:
            continue                      # not rendered -> no vector prose
        bb = [float(v) for v in (p.get("layout_bbox") or [])]
        if len(bb) != 4:
            continue
        if source_pdf_path:
            lines = _source_lines_in_bbox(str(source_pdf_path), page_idx, bb)
            prose = [ln for ln in lines if is_translatable_prose_line(ln["text"])]
            if prose:
                src = " ".join(ln["text"] for ln in prose)
                # the formula SVG renders these lines as vector ink
                m_frag += 1
                m_char += len(src)
                m_sent += max(1, len(re.findall(r"[.!?](?:[\"'\u201d\u2019])?\s|$",
                                                src)))
                m_fa += 1
                details.append({
                    "kind": "formula_vector_prose",
                    "fragment_type": "formula_left_context",
                    "paragraph_id": None,
                    "fragment_id": "%s-PROSELINES" % fid,
                    "formula_id": fid,
                    "source_text": src,
                    "expected_target_text": None,
                    "final_visible_text": "vector ink (text_as_path)",
                    "source_bbox": [round(v, 1) for v in bb],
                    "reason": "rendered formula SVG embeds translatable "
                              "English prose lines (vector, not text layer)",
                    "source_line_count": len(prose),
                    "lines": [ln["text"] for ln in prose],
                })

    # ---- B. paragraph head/tail text-layer residual ----------------------
    for r in page_model.get("regions", []):
        if r.get("type") != "text":
            continue
        para = r.get("payload") or {}
        pid = para.get("paragraph_id")
        if not pid:
            continue
        role = para.get("semantic_role") or para.get("style_role") or "body"
        if role in EXEMPT_ROLES:
            continue
        src = para.get("source_text") or ""
        target = translations.get(pid) or ""
        if not src.strip():
            continue
        if not target or not _char_seq(target):
            # required translation missing entirely (QA-1 counts separately)
            if _prose_word_count(src) >= 6:
                m_untranslated += 1
                details.append({
                    "kind": "untranslated_required_fragment",
                    "fragment_type": "paragraph_middle",
                    "paragraph_id": pid, "fragment_id": "%s-ALL" % pid,
                    "source_text": src[:200],
                    "expected_target_text": None,
                    "final_visible_text": "text-layer source run",
                    "source_bbox": [round(v, 1) for v in r.get("bbox", [])],
                    "reason": "translatable paragraph has no canonical target",
                })
            continue
        # head: first sentence of source vs first of target
        src_sents = re.split(r"(?<=[.!?])\s+", src.strip())
        tgt_seq = _char_seq(target)
        if src_sents and len(src_sents[0]) >= 40 and _prose_word_count(src_sents[0]) >= 6:
            head_seq = _char_seq(src_sents[0])
            if head_seq in final_norm and tgt_seq and tgt_seq not in final_norm:
                m_head += 1
                m_frag += 1
                m_char += len(head_seq)
                details.append({
                    "kind": "source_head_residual",
                    "fragment_type": "paragraph_head",
                    "paragraph_id": pid, "fragment_id": "%s-HEAD" % pid,
                    "source_text": src_sents[0][:200],
                    "expected_target_text": None,
                    "final_visible_text": src_sents[0][:80],
                    "source_bbox": [round(v, 1) for v in r.get("bbox", [])],
                    "reason": "source head sentence visible while target absent",
                })
        # tail: last sentence
        if src_sents and len(src_sents) > 1:
            tail = src_sents[-1]
            if len(tail) >= 40 and _prose_word_count(tail) >= 6:
                tail_seq = _char_seq(tail)
                if tail_seq in final_norm and tgt_seq not in final_norm:
                    m_tail += 1
                    m_frag += 1
                    m_char += len(tail_seq)
                    details.append({
                        "kind": "source_tail_residual",
                        "fragment_type": "paragraph_tail",
                        "paragraph_id": pid, "fragment_id": "%s-TAIL" % pid,
                        "source_text": tail[:200],
                        "expected_target_text": None,
                        "final_visible_text": tail[:80],
                        "source_bbox": [round(v, 1) for v in r.get("bbox", [])],
                        "reason": "source tail sentence visible while target absent",
                    })

    # ---- C. source fallback for required translation ---------------------
    # evidence from the final HTML RenderIdentity (data-render-source)
    if html_path and Path(html_path).exists():
        html = Path(html_path).read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(
                r'<div class="paragraph-block" data-para="([^"]*)"[^>]*>'
                r'(.*?)</div>', html, re.S):
            pid, inner = m.groups()
            rs = re.search(r'data-render-source="([^"]*)"', m.group(0))
            reason = re.search(r'data-render-source-reason="([^"]*)"',
                               m.group(0))
            src = rs.group(1) if rs else "canonical_target"
            if src == "SOURCE_TEXT_SELECTED" or "source" in src.lower() or \
                    "fallback" in (reason.group(1) if reason else "").lower():
                text = re.sub(r"<[^>]+>", "", inner)[:200]
                m_fallback += 1
                details.append({
                    "kind": "source_fallback_for_required_translation",
                    "fragment_type": "paragraph_middle",
                    "paragraph_id": pid, "fragment_id": "%s-FB" % pid,
                    "source_text": text,
                    "expected_target_text": None,
                    "final_visible_text": text[:80],
                    "source_bbox": None,
                    "reason": "required-translation fragment rendered from "
                              "source (render_source=%s)" % src,
                })
    for flow in flows or []:
        for it in flow.get("items", []):
            if it.get("kind") != "paragraph":
                continue
            rs = it.get("render_source") or "canonical_target"
            reason = it.get("render_source_reason") or ""
            if rs == "SOURCE_TEXT_SELECTED" or "fallback" in reason.lower() \
                    or "source" in rs.lower():
                pid = it.get("paragraph_id")
                role = "recovered" if str(pid).startswith("PAF_") else "body"
                m_fallback += 1
                details.append({
                    "kind": "source_fallback_for_required_translation",
                    "fragment_type": "paragraph_middle",
                    "paragraph_id": pid, "fragment_id": "%s-FB" % pid,
                    "source_text": (it.get("render_text") or "")[:200],
                    "expected_target_text": None,
                    "final_visible_text": (it.get("render_text") or "")[:80],
                    "source_bbox": None,
                    "reason": "required-translation fragment rendered from "
                              "source (%s)" % reason,
                })

    metrics = {
        "translatable_source_residual_fragment_count": m_frag,
        "translatable_source_residual_char_count": m_char,
        "translatable_source_residual_sentence_count": m_sent,
        "source_head_residual_count": m_head,
        "source_tail_residual_count": m_tail,
        "formula_adjacent_source_residual_count": m_fa,
        "untranslated_required_fragment_count": m_untranslated,
        "source_fallback_for_required_translation_count": m_fallback,
    }
    return {
        "qa": "residual_prose_truth_qa",
        "schema_version": "visual_v05.residual_prose_truth_qa.v1",
        "metrics": metrics,
        "residual_details": details,
        "decision": "pass" if all(v == 0 for v in metrics.values()) else "fail",
    }
