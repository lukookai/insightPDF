# -*- coding: utf-8 -*-
"""FinalVisibleTranslationQA (visual-v04).

Answers the FIRST render-truth question:

    canonical target translation
    -> did it actually appear in the FINAL PDF?

It never trusts ``translation_status == translated``.  The FINAL PDF text
layer is the only delivery truth.  Inputs:

  * canonical translation map  (page translation.json)
  * page model text regions    (semantic role + source text + bbox)
  * visual flows               (render payload + render_source selection)
  * final PDF path             (PyMuPDF text layer)

Outputs (per page):

    translatable_target_count
    target_visible_count
    translatable_target_missing_count
    translatable_target_partial_count
    translatable_target_duplicate_count
    visible_translation_recovery_ratio
    per_paragraph_target_recovery

Hard:
    translatable_target_missing_count == 0
    translatable_target_duplicate_count == 0
    visible_translation_recovery_ratio == 1.0

Design rules
------------
* ``normalize_visible_text()`` only normalises whitespace / line breaks /
  zero-width / script-gap / unicode NFC / known PDF-extraction punctuation.
  It NEVER rewrites words, never fuzzy-matches, never substitutes source
  for target.
* Protected / KEEP content is excluded via the existing translation_route
  + semantic_role + protected token map: formula / pure math / code / URL /
  reference citation / model names / dataset names / author names / KEEP /
  NON_TRANSLATABLE_MATH are NOT required to be translated.
* A paragraph whose canonical target is missing or empty while the source
  required translation -> translatable_target_missing (not silently pass).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pymupdf  # noqa: E402

TOKEN_RE = re.compile(r"\{\{[A-Z_0-9]+(?:_[A-Z0-9]+)?\}\}")

# a formula region whose source_text carries this many English words is a
# *prose-adopted formula*: the layout detector swallowed a prose paragraph.
# Its body REQUIRES translation -> a missing canonical target counts as
# translatable_target_missing (the render-truth must not silently pass it).
PROSE_ADOPTED_WORD_MIN = 8
PROSE_ADOPTED_HEIGHT_PT = 30.0


def _prose_word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]{3,}", _strip_tokens(text or "")))


def is_prose_adopted_formula(payload: Dict[str, Any]) -> bool:
    """document-general prose-adopted formula test (no page/id specials)."""
    if not isinstance(payload, dict):
        return False
    bb = payload.get("layout_bbox") or []
    if len(bb) != 4:
        return False
    height = bb[3] - bb[1]
    if height < PROSE_ADOPTED_HEIGHT_PT:
        return False
    return _prose_word_count(payload.get("source_text") or "") \
        >= PROSE_ADOPTED_WORD_MIN

# semantic roles that are exempt from translation (already target language
# or not-to-translate content)
EXEMPT_ROLES = {
    "references", "reference", "acknowledgement", "appendix_reference",
    "abstract_heading", "page_number", "header_footer", "marginal",
}
# page-number / preprint footer markers
_NON_PROSE_RE = re.compile(
    r"^(page\s*\d+\s*of\s*\d+|preprint submitted to .*)$", re.I)

# roles that must be translated when they carry prose
TRANSLATABLE_ROLES = {
    "body", "body_bold_lead", "abstract_body", "list_item", "heading",
    "subsection_heading", "caption", "footnote", "title", "author",
    "affiliation",
}


def normalize_visible_text(text: str) -> str:
    """Deterministic visible-text normalisation for PDF text-layer matching.

    Allowed only:
      * Unicode NFC
      * zero-width / script-gap removal (U+200B..U+200F, U+2060, U+FEFF,
        U+200D)
      * whitespace runs (incl. line breaks) -> single space
      * known PDF-extraction punctuation normalisation: soft hyphen
        (U+00AD), curly quotes/dashes -> ASCII, full-width space
      * remove inline mark-up tokens ({{BOLD_n}} / {{END_BOLD_n}} /
        {{ITALIC_n}} / {{END_ITALIC_n}})
    Never: word rewriting, fuzzy semantic rewrite, source substitution,
    auto-completing missing target.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    # mark-up control tokens are render-only, not visible
    t = re.sub(r"\{\{(?:END_)?(?:BOLD|ITALIC)_\d+\}\}", "", t)
    t = re.sub(r"[\u200b-\u200f\u2060\ufeff\u200d\u00ad]", "", t)
    t = t.replace("\u3000", " ")
    # curly quotes / dashes -> ASCII (common PDF-extraction variation)
    t = (t.replace("\u2018", "'").replace("\u2019", "'")
         .replace("\u201c", "\"").replace("\u201d", "\"")
         .replace("\u2013", "-").replace("\u2014", "-"))
    # whitespace runs -> single space
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _strip_tokens(text: str) -> str:
    """Remove formula / protected placeholders for character accounting."""
    return TOKEN_RE.sub("", text or "")


def expand_visible_text(text: str, protected_runs: Dict[str, str] | None = None
                        ) -> str:
    """Expand protected tokens to their RENDERED visible text.

    ``{{CODE_x}}`` / ``{{FORMULA_x}}`` map to real visible text (code runs,
    protected model names) via ``protected_runs``; ``{{BOLD_n}}`` /
    ``{{ITALIC_n}}`` / ``{{END_*}}`` are mark-up-only and become empty.
    Unknown tokens become empty (they render as SVG/images, not text).
    """
    if not text:
        return ""
    prot = protected_runs or {}

    def _repl(m):
        tok = m.group(0)
        if tok in prot and prot[tok]:
            return prot[tok]
        if tok.startswith("{{FORMULA_"):
            return ""
        return ""

    return re.sub(r"\{\{[A-Z_0-9]+(?:_[A-Z0-9]+)?\}\}", _repl, text)


def _char_seq(text: str, protected_runs: Dict[str, str] | None = None) -> str:
    """Visible characters only: protected tokens expanded to their rendered
    text, mark-up tokens dropped, whitespace removed."""
    return re.sub(r"\s+", "", normalize_visible_text(
        expand_visible_text(text, protected_runs)))


def recover_target_in_final(target: str, final_chars: str,
                            protected_runs: Dict[str, str] | None = None
                            ) -> Tuple[str, float]:
    """Exact character-sequence recovery of the canonical target.

    Returns (status, coverage) where status in
    {'full', 'partial', 'missing'} and coverage in [0,1] is the fraction of
    target characters that appear as a contiguous run in ``final_chars``.
    """
    t = _char_seq(target, protected_runs)
    if not t:
        return "full", 1.0  # nothing required -> nothing missing
    if t in final_chars:
        return "full", 1.0
    # sliding-window longest contiguous run (O(n*m) on char level, fine for
    # pages; exact matching only -- no fuzzy rewriting)
    best = 0
    n = len(t)
    # windowed search: the target must appear as a *contiguous* run inside
    # the final text to count; partial credit is the longest run
    for i in range(n, 0, -1):
        if t[:i] in final_chars:
            best = i
            break
    cov = best / n
    if cov >= 0.9:
        return "partial", cov
    return "missing", cov


def _duplicate_target_count(target: str, final_chars: str,
                            protected_runs: Dict[str, str] | None = None
                            ) -> int:
    """Number of extra (beyond 1) non-overlapping full occurrences."""
    t = _char_seq(target, protected_runs)
    if not t or len(t) < 8:
        return 0
    count = final_chars.count(t)
    return max(count - 1, 0)


def final_visible_translation_qa(
    page_model: Dict[str, Any],
    translations: Dict[str, str],
    flows: List[Dict[str, Any]],
    final_pdf_path=None,
    out_dir=None,
    html_trace: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run FinalVisibleTranslationQA for one page.

    ``flows``: visual flows (render payload = render_source selection).
    ``html_trace``: optional {paragraph_id: {render_source, html_bbox}}
    extracted from the rendered HTML DOM (visual-v04 RenderIdentity).
    """
    # ---- 1. final PDF text layer (the only delivery truth) ---------------
    final_chars = ""
    spans = []
    if final_pdf_path and Path(final_pdf_path).exists():
        try:
            doc = pymupdf.open(str(final_pdf_path))
            page = doc[0]
            d = page.get_text("dict")
            for b in d.get("blocks", []):
                if b.get("type") != 0:
                    continue
                for l in b.get("lines", []):
                    for s in l.get("spans", []):
                        txt = s.get("text") or ""
                        if txt.strip():
                            spans.append({"bbox": [float(v) for v in s["bbox"]],
                                          "text": txt})
            final_chars = _char_seq(" ".join(s["text"] for s in spans))
            doc.close()
        except Exception:  # noqa: BLE001
            final_chars = ""

    # ---- 2. translatable targets (from the VISUAL FLOWS) -----------------
    # The render-truth unit is the flow paragraph item: its render_text is
    # the payload THIS PAGE was asked to render (a cross-page continuation
    # paragraph contributes only its page-local fragment here, so matching
    # against this single-page final PDF is exact).
    flow_items = [it for flow in flows or []
                  for it in flow.get("items", [])
                  if it.get("kind") == "paragraph"]
    # protected token -> rendered visible text, PER PARAGRAPH
    # ({{CODE_n}} tokens are paragraph-local: the same token id may map to
    # different code runs on different paragraphs, so a global map would
    # cross-contaminate.  We resolve by paragraph_id.)
    para_protected: Dict[str, Dict[str, str]] = {}
    for r in page_model.get("regions", []):
        if r.get("type") != "text":
            continue
        p = r.get("payload") or {}
        pid = p.get("paragraph_id")
        if pid:
            para_protected[pid] = p.get("protected_runs") or {}
    records: List[Dict[str, Any]] = []
    seen_pids = set()
    for it in flow_items:
        pid = it.get("paragraph_id")
        if not pid or pid in seen_pids:
            continue
        seen_pids.add(pid)
        role = it.get("style_role") or "body"
        render_text = it.get("render_text") or ""
        source = it.get("source_text") or ""
        target = translations.get(pid)
        prot = para_protected.get(pid, {})
        rt_wo = _strip_tokens(render_text).strip()
        # role exempt -> not translatable
        if role.lower() in EXEMPT_ROLES:
            continue
        if role.lower() not in TRANSLATABLE_ROLES:
            continue
        # non-prose (page number / preprint footer / pure punctuation)
        if _NON_PROSE_RE.match(normalize_visible_text(rt_wo)):
            continue
        if not rt_wo or not re.search(r"[A-Za-z\u4e00-\u9fff]", rt_wo):
            continue  # placeholder-only / punctuation-only render
        # required visible payload = render_text (page-local target)
        if not _char_seq(render_text, prot):
            status = "missing"
            cov = 0.0
            dup = 0
            reason = "render_payload_empty"
        else:
            status, cov = recover_target_in_final(
                render_text, final_chars, prot)
            dup = _duplicate_target_count(render_text, final_chars, prot)
            reason = "ok" if status == "full" else "final_pdf_missing"
            if status != "full" and target is None:
                reason = "canonical_target_missing"
        records.append({
            "paragraph_id": pid,
            "semantic_role": role,
            "flow_fragment_id": it.get("flow_fragment_id"),
            "source_excerpt": normalize_visible_text(
                _strip_tokens(source))[:80],
            "render_text_excerpt": normalize_visible_text(
                _strip_tokens(render_text))[:80],
            "canonical_target_excerpt": normalize_visible_text(
                _strip_tokens(target or ""))[:80],
            "target_status": status,
            "target_coverage": round(cov, 4),
            "duplicate_count": dup,
            "reason": reason,
        })

    # ---- 2b. prose-adopted formulas: swallowed prose REQUIRES translation
    # The layout detector merged a prose body into a formula region.  On the
    # visual route those regions render as source SVG vector ink (English
    # visible in the final PDF).  A translatable body with NO canonical
    # target in the translation map is a hard missing-translation defect.
    for r in page_model.get("regions", []):
        if r.get("type") != "formula":
            continue
        p = r.get("payload") or {}
        if not is_prose_adopted_formula(p):
            continue
        fid = p.get("formula_id")
        token = "{{FORMULA_%s}}" % fid
        # canonical target exists if ANY translation entry covers this
        # formula's prose (documents translate prose under paragraph ids,
        # so a formula-region entry is rare -> missing)
        has_any_translation = any(
            _char_seq(v) for v in translations.values())
        target = None
        for k, v in translations.items():
            if token in (k or "") or token in (v or ""):
                target = v
                break
        if target is None or not _char_seq(target):
            status = "missing"
            cov = 0.0
            dup = 0
            reason = "prose_adopted_formula_no_canonical_target"
        else:
            status, cov = recover_target_in_final(target, final_chars)
            dup = _duplicate_target_count(target, final_chars)
            reason = "ok" if status == "full" else "final_pdf_missing"
        records.append({
            "paragraph_id": "FORMULA_%s" % fid,
            "semantic_role": "prose_adopted_formula",
            "source_excerpt": normalize_visible_text(
                _strip_tokens(p.get("source_text") or ""))[:80],
            "canonical_target_excerpt": normalize_visible_text(
                _strip_tokens(target or ""))[:80],
            "target_status": status,
            "target_coverage": round(cov, 4),
            "duplicate_count": dup,
            "reason": reason,
            "formula_bbox": [round(float(v), 1)
                             for v in (p.get("layout_bbox") or [])],
            "translation_required": True,
        })

    # ---- 3. aggregate metrics ---------------------------------------------
    translatable = len(records)
    visible = sum(1 for r in records if r["target_status"] == "full")
    missing = sum(1 for r in records if r["target_status"] == "missing")
    partial = sum(1 for r in records if r["target_status"] == "partial")
    duplicate = sum(r["duplicate_count"] for r in records)
    coverage = (visible / translatable) if translatable else 1.0

    metrics = {
        "translatable_target_count": translatable,
        "target_visible_count": visible,
        "translatable_target_missing_count": missing,
        "translatable_target_partial_count": partial,
        "translatable_target_duplicate_count": duplicate,
        "visible_translation_recovery_ratio": round(coverage, 6),
    }
    decision = "pass" if (missing == 0 and duplicate == 0
                          and coverage >= 1.0) else "fail"
    return {
        "schema_version": "visual_v04.final_visible_translation_qa.v1",
        "metrics": metrics,
        "decision": decision,
        "final_text_chars": len(final_chars),
        "per_paragraph_target_recovery": records,
    }
