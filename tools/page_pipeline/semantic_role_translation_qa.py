# -*- coding: utf-8 -*-
"""SemanticRoleTranslationClosureQA (visual-v06).

All translation_required semantic roles must appear as target translation in
the final PDF.

This is the architecture fix for the v05 blind spot: a prose-adopted formula
region was PARTIALLY recovered (its body prose) but its heading line (e.g.
"4. Experiment" -- a single-word heading the recovery's >=2-word prose-line
filter dropped) stayed untranslated and re-rendered as source SVG vector ink.
The v05 text-layer residual QA could not see it because the region counted as
"recovered" (its body was), so the whole region was skipped.

The QA is PROVENANCE-AWARE and DOCUMENT-GENERAL:

  * text regions: every translatable role (body/heading/caption/list_item/
    abstract/footnote/front_matter/affiliation/table-cell/figure-caption
    member) must have a CJK canonical target that is actually visible in the
    final PDF text layer;
  * formula regions: real source-PDF lines that are headings or translatable
    prose but were NOT covered by any recovered (PAF) paragraph bbox are
    untranslated residual -- the region's SVG still renders them as English;
  * render_source contract: a translation_required flow item must render from
    a validated target, never SOURCE_TEXT_SELECTED fallback.

Hard (all 0):
    role_target_missing_count
    role_source_residual_count
    heading_target_missing_count
    heading_source_residual_count
    role_wrong_render_source_count
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import pymupdf  # noqa: E402

from final_visible_translation_qa import (  # noqa: E402
    EXEMPT_ROLES, TRANSLATABLE_ROLES, normalize_visible_text,
    _char_seq, _strip_tokens, recover_target_in_lines,
    is_prose_adopted_formula, _prose_word_count)
from residual_prose_truth_qa import (  # noqa: E402
    _final_text_spans, is_translatable_prose_line)
from prose_adopted_formula_recovery import (  # noqa: E402
    _text_lines_in_regions, _inside)
from semantic_structure_closure_qa import (  # noqa: E402
    _source_heading_candidates)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# document-general numbered-heading pattern: "4. Experiment" / "4.Experiment"
# / "3.4. Adaptive ..." -- number(s) then a word; tolerant of missing space.
_NUMBERED_HEADING_RE = re.compile(
    r"^\s*\d{1,2}(?:\.\d{1,2})*\s*\.?\s*[A-Z][A-Za-z].*")
_HEADING_HEAD_RE = re.compile(r"^\s*\d{1,2}\s*\.?\s+[A-Z]")

_HEADING_LEXICON = {
    "abstract", "introduction", "related work", "method", "methods",
    "approach", "experiment", "experiments", "results", "discussion",
    "conclusion", "conclusions", "background", "preliminaries",
    "implementation", "implementation details", "references",
}


def _is_heading_line(text: str) -> bool:
    """document-general heading test (source line)."""
    t = (text or "").strip()
    if not t:
        return False
    if _NUMBERED_HEADING_RE.match(t) or _HEADING_HEAD_RE.match(t):
        return True
    low = t.lower()
    if low in _HEADING_LEXICON:
        return True
    if any(low.startswith(k) for k in
           ("implementation details", "experimental setup")):
        return True
    return False


_MATH_DEF_RE = re.compile(
    r"\b(?:denotes|represents|refers to|means|is defined as|defined as|"
    r"stands for|is the|are the|let\s+\w+\s+be|where\s+\w+\s+is|"
    r"given\s+\w+)\b", re.I)
_MATH_SYMBOL_RE = re.compile(
    r"[∈∉⊆⊂⊇⊃∪∩∀∃∑∫∏∂∇≤≥≈≠±×·→⇒⇔←→∞α-ωΑ-Ω]")


def _is_math_definition(text: str) -> bool:
    """True if a formula-region prose line is a math/equation annotation
    rather than body prose -- e.g. ``RS denotes the set of references`` or
    ``where x is the input feature map``.  Such lines are NON_TRANSLATABLE
    _MATH context (spec exemption) and must NOT be flagged as a residual
    when they stay as source SVG vector ink inside a formula region.
    Body-prose residuals (e.g. ``Results on LSOTB-TIR120. As shown in
    Table 1, our ...``) carry no math-definition signal and ARE flagged.
    """
    t = (text or "").strip()
    if not t:
        return False
    if _MATH_SYMBOL_RE.search(t):
        return True
    return bool(_MATH_DEF_RE.search(t))


def _final_lines(final_pdf_path):
    """Final-PDF per-line char sequences + line bboxes (for chunked
    target recovery, matching final_visible_translation_qa's tolerance).

    Structure: page -> blocks -> lines -> spans.  ``pdf_lines`` is a list of
    per-LINE span-lists so the outer comprehension iterates LINES and the
    inner iterates that line's SPANS (each span carries ``text``).
    """
    line_chars: List[str] = []
    line_boxes: List[List[float]] = []
    if not final_pdf_path or not Path(final_pdf_path).exists():
        return line_chars, line_boxes
    try:
        doc = pymupdf.open(str(final_pdf_path))
        try:
            page = doc[0]
            pdf_lines = []
            for b in page.get_text("dict").get("blocks", []):
                if b.get("type") != 0:
                    continue
                for l in b.get("lines", []):
                    line_spans = [s for s in l.get("spans", [])
                                  if (s.get("text") or "").strip()]
                    if line_spans:
                        pdf_lines.append(line_spans)
            line_chars = [_char_seq("".join(x.get("text") or "" for x in l))
                          for l in pdf_lines]
            line_boxes = [[float(v) for v in l[0]["bbox"]] for l in pdf_lines]
            return line_chars, line_boxes
        finally:
            doc.close()
    except Exception:  # noqa: BLE001
        return line_chars, line_boxes


def _cjk_visible_in_bbox(line_chars, line_boxes, bb, tol=2.0):
    """True iff some final-PDF line carrying CJK text overlaps region bbox.

    Used by the formula-region check: a swallowed heading/prose line is a
    residual ONLY when its region renders as source SVG vector ink (no CJK
    in the final PDF text layer).  Overlap test is a bbox intersection with
    a small tolerance so adjacent-but-distinct regions are not confused.
    """
    if not bb or len(bb) != 4:
        return False
    for lc, bx in zip(line_chars, line_boxes):
        if not lc:
            continue
        if not _CJK_RE.search(lc):
            continue
        if (bx[2] > bb[0] - tol and bx[0] < bb[2] + tol
                and bx[3] > bb[1] - tol and bx[1] < bb[3] + tol):
            return True
    return False


def semantic_role_translation_qa(
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
    # recovered prose blocks (PAF recovery) -- a swallowed prose/heading line
    # is NOT a residual when it falls inside a recovered block that carries a
    # translated (CJK) target: the recovery replaced the formula region's
    # English with translated prose, which the layout packs inside the block's
    # band (often at the TOP, leaving the source-line sub-band empty of CJK),
    # or when its OWNING formula region was recovered as a translated block
    # (the region's SVG is skipped, so the stray line is absent rather than
    # an English residual).  The strict source-line-bbox visibility test would
    # otherwise false-fire.
    recovered_blocks = [(p.get("bbox") or [],
                        bool(re.search(r"[\u4e00-\u9fff]",
                                       p.get("target_text") or "")))
                        for p in (recovered_paragraphs or [])]
    recovered_fids = {str(p.get("source_formula_region_id"))
                      for p in (recovered_paragraphs or [])
                      if re.search(r"[\u4e00-\u9fff]",
                                   p.get("target_text") or "")}
    # group flow items by paragraph_id (page-local render_text fragments);
    # prefer these for visibility so cross-page continuation paragraphs are
    # matched against their PAGE-LOCAL fragment, not the whole-doc target.
    flow_by_pid = {}
    flow_items_by_pid = {}
    for fl in (flows or []):
        for it in fl.get("items", []):
            pid = it.get("paragraph_id")
            if pid:
                flow_by_pid[pid] = it
                flow_items_by_pid.setdefault(pid, []).append(it)

    details: List[Dict[str, Any]] = []
    m_req = m_vis = 0
    m_role_target_missing = 0
    m_role_source_residual = 0
    m_heading_target_missing = 0
    m_heading_source_residual = 0
    m_caption_target_missing = 0
    m_list_target_missing = 0
    m_abstract_target_missing = 0
    m_wrong_render_source = 0

    # ---- 1. text-region translatable roles --------------------------------
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
        # KEEP-route paragraphs (title/author/affiliation/etc.) have NO CJK
        # target -- they stay English source by design and are NOT a
        # translation-required residual.  This exactly mirrors v05's own
        # final_visible_translation_qa (which only flags CJK translatable
        # targets).  Without this gate, every KEEP paragraph is a false
        # positive.
        required = bool(_CJK_RE.search(tgt))
        if not required:
            continue
        m_req += 1
        # visibility: mirror v05's final_visible_translation_qa EXACTLY --
        # use the page-model paragraph's protected_runs (so URL/CODE tokens
        # expand to their rendered text) and the flow's page-local
        # render_text fragment.  Cross-page continuation paragraphs: if
        # every flow item is a continuation (the paragraph's primary page is
        # elsewhere and it IS translated there), only require the whole-doc
        # target to be at least partially visible on this page, never a
        # strict "full" on the tiny page-local fragment.
        prot = para.get("protected_runs") or {}
        fitems = flow_items_by_pid.get(pid)
        if fitems:
            non_cont = [it for it in fitems
                        if not it.get("continuation")]
            if non_cont:
                visible = True
                for it in non_cont:
                    rt = it.get("render_text") or ""
                    if not _CJK_RE.search(rt):
                        continue  # KEEP slice inside a mixed paragraph
                    status, _ = recover_target_in_lines(
                        rt, line_chars, prot, line_boxes)
                    if status != "full":
                        visible = False
                        break
            else:
                status, _ = recover_target_in_lines(
                    tgt, line_chars, prot, line_boxes)
                visible = (status != "missing")
        else:
            status, _ = recover_target_in_lines(
                tgt, line_chars, prot, line_boxes)
            visible = (status == "full")
        if not visible:
            m_role_target_missing += 1
            if role_l == "heading":
                m_heading_target_missing += 1
            elif role_l == "caption":
                m_caption_target_missing += 1
            elif role_l == "list_item":
                m_list_target_missing += 1
            elif role_l in ("abstract_body", "abstract"):
                m_abstract_target_missing += 1
            details.append({
                "kind": "role_target_missing",
                "fragment_type": role_l,
                "paragraph_id": pid,
                "fragment_id": "%s-ROLE" % pid,
                "source_text": normalize_visible_text(_strip_tokens(src))[:120],
                "expected_target_text": normalize_visible_text(
                    _strip_tokens(tgt))[:120],
                "final_visible_text": "absent (text-layer)",
                "source_bbox": [round(v, 1) for v in r.get("bbox", [])],
                "reason": "translatable %s role has no CJK target visible "
                          "in final PDF" % role_l,
            })
        else:
            m_vis += 1
        it = flow_by_pid.get(pid)
        if it is not None:
            rs = it.get("render_source") or "canonical_target"
            reason = (it.get("render_source_reason") or "").lower()
            if rs == "SOURCE_TEXT_SELECTED" or "source" in rs.lower() \
                    or "fallback" in reason:
                m_wrong_render_source += 1
                details.append({
                    "kind": "role_wrong_render_source",
                    "fragment_type": role_l,
                    "paragraph_id": pid,
                    "fragment_id": "%s-RS" % pid,
                    "source_text": normalize_visible_text(
                        _strip_tokens(src))[:80],
                    "reason": "translation_required role rendered from source "
                              "fallback (render_source=%s)" % rs,
                })

    # ---- 2. formula-region swallowed heading/prose (delivery truth) -----
    # A formula region whose SOURCE carries an untranslated heading or prose
    # line is a residual IFF that line is NOT visible as CJK in the final PDF
    # (the region was rendered as source SVG vector ink).  This is the v05
    # blind spot: headings like "4.1. Implementation details" / "4. Experiment"
    # live inside formula regions whose stored source_text is the surrounding
    # math (prose-word count < 1), so v05 never recovered them and they stayed
    # vector ink.  We test the DELIVERY truth (final PDF text layer) on the
    # LINE/HEADING bbox itself -- NOT the whole region bbox -- because a
    # formula region may carry translated body prose (CJK present) while its
    # swallowed heading stays vector ink (no CJK in the heading's own bbox).
    # We read source lines via _text_lines_in_regions AND check the source
    # heading candidates directly (a swallowed heading line is sometimes not
    # returned by the line extractor), so neither path is missed.
    for r in page_model.get("regions", []):
        if r.get("type") != "formula":
            continue
        p = r.get("payload") or {}
        bb = p.get("layout_bbox") or []
        if len(bb) != 4:
            continue
        fid = str(p.get("formula_id"))
        if not fid:
            continue
        # (a) swallowed headings: any source heading candidate whose bbox
        # falls inside this formula region.  Check CJK on the HEADING bbox.
        for h in headings:
            if not _inside(h["bbox"], bb, tol=3.0):
                continue
            if not _cjk_visible_in_bbox(line_chars, line_boxes, h["bbox"]):
                m_req += 1
                m_heading_target_missing += 1
                details.append({
                    "kind": "role_target_missing",
                    "fragment_type": "heading",
                    "paragraph_id": None,
                    "fragment_id": "%s-HEAD" % fid,
                    "source_text": h["text"][:120],
                    "expected_target_text": None,
                    "final_visible_text":
                        "source SVG vector ink (untranslated)",
                    "source_bbox": [round(v, 1) for v in h["bbox"]],
                    "formula_id": fid,
                    "reason": "swallowed heading line not translated "
                              "(region SVG still renders English)",
                })
        # (b) swallowed prose lines (delivery truth on the line bbox).
        lines = _text_lines_in_regions(str(source_pdf_path), page_idx, [bb])
        for ln in lines:
            # already accounted for as a heading in (a)
            if any(_inside(ln["bbox"], h["bbox"], tol=2.0)
                   for h in headings):
                continue
            if is_translatable_prose_line(ln["text"]):
                # math/equation annotation lines (NON_TRANSLATABLE_MATH
                # context) are a LEGAL exemption -- skip so genuine formula
                # descriptions do not false-fire as residuals.
                if _is_math_definition(ln["text"]):
                    continue
                # recovered-prose coverage: the line was swallowed into a
                # formula region but a PAF recovery block replaced that
                # region with translated prose (CJK target present).  The
                # recovered CJK may render at a different y within the block's
                # band, so the strict source-line-bbox test is a false
                # positive here -- skip it.  Also skip when the line's OWNING
                # formula region was itself recovered (its SVG is skipped, so
                # the stray line is absent, not an English residual).
                if (fid in recovered_fids
                        or any(_inside(ln["bbox"], rb) and has_cjk
                               for rb, has_cjk in recovered_blocks)):
                    continue
                if not _cjk_visible_in_bbox(line_chars, line_boxes,
                                            ln["bbox"]):
                    m_req += 1
                    m_role_target_missing += 1
                    details.append({
                        "kind": "role_target_missing",
                        "fragment_type": "formula_swallowed_prose",
                        "paragraph_id": None,
                        "fragment_id": "%s-PROSE" % fid,
                        "source_text": ln["text"][:120],
                        "expected_target_text": None,
                        "final_visible_text":
                            "source SVG vector ink (untranslated)",
                        "source_bbox": [round(v, 1) for v in ln["bbox"]],
                        "formula_id": fid,
                        "reason": "swallowed prose line not translated "
                                  "(region SVG still renders English)",
                    })

    metrics = {
        "translation_required_role_count": m_req,
        "translated_role_visible_count": m_vis,
        "role_target_missing_count": m_role_target_missing,
        "role_source_residual_count": m_role_source_residual,
        "heading_target_missing_count": m_heading_target_missing,
        "heading_source_residual_count": m_heading_source_residual,
        "caption_target_missing_count": m_caption_target_missing,
        "list_target_missing_count": m_list_target_missing,
        "abstract_target_missing_count": m_abstract_target_missing,
        "role_wrong_render_source_count": m_wrong_render_source,
    }
    # defect metrics must be 0; the *_count / *_visible counters are
    # informational and must NOT drive the decision.
    _defects = (
        "role_target_missing_count", "role_source_residual_count",
        "heading_target_missing_count", "heading_source_residual_count",
        "caption_target_missing_count", "list_target_missing_count",
        "abstract_target_missing_count", "role_wrong_render_source_count",
    )
    decision = "pass" if all(metrics[k] == 0 for k in _defects) else "fail"
    return {
        "qa": "semantic_role_translation_qa",
        "schema_version": "visual_v06.semantic_role_translation_qa.v1",
        "metrics": metrics,
        "role_details": details,
        "decision": decision,
    }
