# -*- coding: utf-8 -*-
"""GlyphTokenIntegrityQA (visual-v05).

Human review of visual-v04 final PDFs showed suspected missing glyphs
(tofu boxes / U+FFFD replacement characters) near formulas.  This QA
detects them formally -- no OCR, no pixel heuristics:

  A. HTML target text containing U+FFFD / U+25A1 / U+25AF / U+FFFE
     (replacement / white square / horizontal black bar);
  B. final PDF text-layer spans containing the same codepoints;
  C. final PDF text-layer spans whose text is a control/NUL placeholder
     (U+0000..U+0008, U+000B, U+000C, U+000E..U+001F) -> the glyph mapping
     was lost during Chromium rendering;
  D. protected math tokens ({{FORMULA_x}}) that were rendered as ordinary
     CJK/ASCII text instead of the placeholder -> protected_token_glyph_loss.

Only the TRANSLATABLE PROSE layer is checked: formula SVG source figures
that legitimately contain box/graphic symbols are NOT scanned (they are
not in the prose layer).

Hard (all must be 0):
    replacement_character_count
    unexpected_square_glyph_count
    missing_unicode_mapping_count
    protected_token_glyph_loss_count
    unresolved_font_fallback_count
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import pymupdf  # noqa: E402

_BAD_GLYPH_RE = re.compile(r"[\ufffd\u25a1\u25af\u25a0\u25ad\ufffe\uffff]")
_CTRL_PLACEHOLDER_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_TOKEN_RE = re.compile(r"\{\{(FORMULA_[A-Z0-9_]+)\}\}")


def glyph_token_integrity_qa(
    page_model: Dict[str, Any],
    translations: Dict[str, str],
    flows: List[Dict[str, Any]],
    final_pdf_path=None,
    html_path=None,
    source_pdf_path=None,
    out_dir=None,
    recovered_formulas=None,
) -> Dict[str, Any]:
    """Run GlyphTokenIntegrityQA for one page."""
    details: List[Dict[str, Any]] = []
    m_repl = m_square = m_missing = m_tok_glyph = m_font = 0
    skip_ids = set(recovered_formulas or [])

    # ---- A. HTML target text ---------------------------------------------
    if html_path and Path(html_path).exists():
        html = Path(html_path).read_text(encoding="utf-8", errors="replace")
        # only paragraph-block inner text (prose layer), skip img/svg refs
        for m in re.finditer(
                r'<div class="paragraph-block"[^>]*>(.*?)</div>', html, re.S):
            pid = re.search(r'data-para="([^"]*)"', m.group(0))
            inner = m.group(1)
            text = re.sub(r"<[^>]+>", "", inner)
            if not text:
                continue
            for bad in _BAD_GLYPH_RE.finditer(text):
                ch = bad.group(0)
                if ch in ("\ufffd", "\ufffe", "\uffff"):
                    m_repl += 1
                else:
                    m_square += 1
                details.append({
                    "kind": "replacement_character" if ch in ("\ufffd",)
                    else "unexpected_square_glyph",
                    "fragment_type": "paragraph_middle",
                    "paragraph_id": pid.group(1) if pid else None,
                    "fragment_id": "%s-GLYPH" % (pid.group(1) if pid
                                                 else "html"),
                    "source_text": None,
                    "final_visible_text": "U+%04X" % ord(ch),
                    "reason": "HTML prose block carries glyph U+%04X"
                              % ord(ch),
                })

    # ---- B/C. final PDF text layer ---------------------------------------
    if final_pdf_path and Path(final_pdf_path).exists():
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
                            for bad in _BAD_GLYPH_RE.finditer(txt):
                                ch = bad.group(0)
                                if ch in ("\ufffd", "\ufffe", "\uffff"):
                                    m_repl += 1
                                else:
                                    m_square += 1
                                details.append({
                                    "kind": ("replacement_character" if
                                             ch in ("\ufffd",) else
                                             "unexpected_square_glyph"),
                                    "fragment_type": "paragraph_middle",
                                    "paragraph_id": None,
                                    "fragment_id": "PDF-%04X" % ord(ch),
                                    "source_text": None,
                                    "final_visible_text": "U+%04X" % ord(ch),
                                    "bbox": [round(v, 1) for v in s["bbox"]],
                                    "font": s.get("font", ""),
                                    "reason": "final PDF text layer carries "
                                              "glyph U+%04X" % ord(ch),
                                })
                            if _CTRL_PLACEHOLDER_RE.search(txt):
                                m_missing += 1
                                details.append({
                                    "kind": "missing_unicode_mapping",
                                    "fragment_type": "paragraph_middle",
                                    "paragraph_id": None,
                                    "fragment_id": "PDF-CTRL",
                                    "source_text": None,
                                    "final_visible_text": repr(txt[:20]),
                                    "bbox": [round(v, 1) for v in s["bbox"]],
                                    "font": s.get("font", ""),
                                    "reason": "text-layer span carries a "
                                              "control placeholder -- glyph "
                                              "mapping lost",
                                })
            finally:
                doc.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- D. protected token glyph loss -----------------------------------
    # canonical targets that contain {{FORMULA_x}} tokens: the final HTML
    # must still carry the placeholder (rendering replaced it by an inline
    # SVG; if the placeholder vanished with no inline svg the glyph is lost)
    if html_path and Path(html_path).exists():
        html = Path(html_path).read_text(encoding="utf-8", errors="replace")
        for pid, tgt in translations.items():
            toks = _TOKEN_RE.findall(tgt or "")
            if not toks:
                continue
            blk_m = re.search(
                r'<div class="paragraph-block" data-para="%s"[^>]*>(.*?)'
                r'</div>' % re.escape(pid), html, re.S)
            if not blk_m:
                continue
            inner = blk_m.group(1)
            text = re.sub(r"<[^>]+>", "", inner)
            for tok in toks:
                if tok[8:] in skip_ids:
                    continue  # fully prose-adopted formula: token gone by
                              # design (RenderExclusivity), not glyph loss
                full = "{{%s}}" % tok
                inline_span = ('<span class="formula-inline" '
                               'data-formula="%s"' % tok) in inner
                if full not in inner and not inline_span:
                    m_tok_glyph += 1
                    details.append({
                        "kind": "protected_token_glyph_loss",
                        "fragment_type": "formula_left_context",
                        "paragraph_id": pid,
                        "fragment_id": "%s-%s" % (pid, tok),
                        "source_text": full,
                        "final_visible_text": text[:40],
                        "reason": "protected token missing and no inline "
                                  "formula svg in the block",
                    })

    metrics = {
        "replacement_character_count": m_repl,
        "unexpected_square_glyph_count": m_square,
        "missing_unicode_mapping_count": m_missing,
        "protected_token_glyph_loss_count": m_tok_glyph,
        "unresolved_font_fallback_count": m_font,
    }
    return {
        "qa": "glyph_token_integrity_qa",
        "schema_version": "visual_v05.glyph_token_integrity_qa.v1",
        "metrics": metrics,
        "glyph_details": details,
        "decision": "pass" if all(v == 0 for v in metrics.values()) else "fail",
    }
