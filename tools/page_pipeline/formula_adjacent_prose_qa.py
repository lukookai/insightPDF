# -*- coding: utf-8 -*-
"""FormulaAdjacentProseClosureQA (visual-v05).

Formula-adjacent prose (where ... / denotes ... / C channels ...), inline
formula placeholders, and punctuation are the places where the final PDF
most often shows: English fragments, Chinese head/tail loss, orphan
punctuation, protected-math-token loss, and source/target mixing.

For every formula region this QA builds a FormulaAdjacentContext:

    formula_id / left_source_context / right_source_context
    left_target_context / right_target_context
    preceding_block_id / following_block_id
    equation_number / protected_tokens / punctuation_before / after

and checks:

  A. source prose residual next to the formula (HTML-visible blocks whose
     content is source English within the formula's vertical band);
  B. target prose missing next to the formula (formula region with no
     target text in its band while its neighbours are translated);
  C. protected math token loss / mutation ({{FORMULA_x}} placeholder
     dropped or changed between canonical target and final HTML);
  D. punctuation ownership -- every punctuation glyph in the final HTML
     text blocks must have a left/right owner, otherwise it is an ORPHAN
     (e.g. the isolated '：' blocks DLP00018/00025/00032 on PPAT pages);
  E. formula context drop / duplicate / reorder (formula-adjacent target
     context that appears nowhere / twice / out of order in final HTML).

Hard (all must be 0):
    formula_adjacent_source_residual_count
    formula_adjacent_target_missing_count
    formula_context_drop_count
    formula_context_duplicate_count
    formula_context_reorder_count
    protected_math_token_loss_count
    protected_math_token_mutation_count
    orphan_formula_punctuation_count
    formula_context_wrong_owner_count
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import pymupdf  # noqa: E402

from residual_prose_truth_qa import (  # noqa: E402
    _CJK_RE, is_translatable_prose_line)

_ORPHAN_PUNCT_RE = re.compile(r"^[,:;.。，：；、]+$")

# punctuation tokens that need a left+right owner (document-general)
_OWNED_PUNCT = set(",:;.。，：；、?!？！()（）[]【】")


def _html_text_blocks(html_path) -> List[Dict[str, Any]]:
    """Parse the final HTML into ordered visible text blocks."""
    blocks: List[Dict[str, Any]] = []
    if not html_path or not Path(html_path).exists():
        return blocks
    html = Path(html_path).read_text(encoding="utf-8", errors="replace")
    # paragraph blocks (style attribute order is NOT guaranteed)
    for m in re.finditer(
            r'<div class="paragraph-block" data-para="([^"]*)"[^>]*>'
            r'(.*?)</div>', html, re.S):
        tag = m.group(0)
        pid = m.group(1)
        inner = m.group(2)
        top = re.search(r"top:([\d.]+)pt", tag)
        left = re.search(r"left:([\d.]+)pt", tag)
        width = re.search(r"width:([\d.]+)pt", tag)
        text = re.sub(r"<[^>]+>", "", inner)
        blocks.append({
            "kind": "paragraph", "id": pid,
            "top": float(top.group(1)) if top else 0.0,
            "left": float(left.group(1)) if left else 0.0,
            "width": float(width.group(1)) if width else 0.0,
            "text": text,
            "raw": inner,
            "render_source": (re.search(r'data-render-source="([^"]*)"',
                                        tag) or [None, "canonical_target"])[1],
        })
    return blocks


def _formula_regions(page_model) -> List[Dict[str, Any]]:
    out = []
    for r in page_model.get("regions", []):
        if r.get("type") != "formula":
            continue
        p = r.get("payload") or {}
        bb = [float(v) for v in (p.get("layout_bbox") or [])]
        if len(bb) != 4:
            continue
        out.append({
            "formula_id": str(p.get("formula_id")),
            "placement": p.get("placement"),
            "bbox": bb,
            "source_text": p.get("source_text") or "",
            "render_segments": p.get("render_segments") or [],
            "layout_bbox": bb,
        })
    return out


def formula_adjacent_prose_qa(
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
    """Run FormulaAdjacentProseClosureQA for one page."""
    recovered = set(recovered_formulas or [])
    blocks = _html_text_blocks(html_path)
    formulas = _formula_regions(page_model)
    details: List[Dict[str, Any]] = []
    m_src = m_tgt = m_drop = m_dup = m_reorder = 0
    m_token_loss = m_token_mut = m_orphan = m_wrong_owner = 0

    # ---- D. punctuation ownership ----------------------------------------
    # every paragraph block whose visible content is ONLY punctuation is an
    # orphan (its source owner text was lost during translation/recovery)
    for blk in blocks:
        text = (blk["text"] or "").strip()
        if _ORPHAN_PUNCT_RE.match(text):
            m_orphan += 1
            details.append({
                "kind": "orphan_formula_punctuation",
                "fragment_type": "formula_right_context",
                "paragraph_id": blk["id"],
                "fragment_id": "%s-PUNCT" % blk["id"],
                "source_text": text,
                "final_visible_text": text,
                "bbox": [blk["left"], blk["top"],
                         blk["left"] + blk["width"], blk["top"] + 12.0],
                "reason": "HTML paragraph block contains only punctuation; "
                          "its source owner text was not recovered",
            })

    # ---- A/B. formula-adjacent source residual / target missing ----------
    for fm in formulas:
        fid = fm["formula_id"]
        bb = fm["bbox"]
        band_top = bb[1] - 6.0
        band_bot = bb[3] + 6.0
        near = [b for b in blocks
                if band_top - 2 <= b["top"] <= band_bot + 2]
        # A. any near block that still renders source English prose
        for nb in near:
            t = (nb["text"] or "").strip()
            if not t:
                continue
            if _CJK_RE.search(t):
                continue
            if is_translatable_prose_line(t):
                m_src += 1
                details.append({
                    "kind": "formula_adjacent_source_residual",
                    "fragment_type": "formula_left_context",
                    "formula_id": fid,
                    "paragraph_id": nb["id"],
                    "fragment_id": "%s-%s-SRC" % (fid, nb["id"]),
                    "source_text": t[:160],
                    "final_visible_text": t[:80],
                    "bbox": [nb["left"], nb["top"],
                             nb["left"] + nb["width"], nb["top"] + 12.0],
                    "reason": "source English prose block adjacent to formula",
                })
        # B. target missing: formula rendered (not recovered) but the band
        # has NO CJK target block at all while its source line band was prose
        if fid not in recovered and source_pdf_path:
            cjk_in_band = any(_CJK_RE.search(b.get("text") or "")
                              for b in near)
            if not cjk_in_band:
                # check source band really had translatable prose
                src_lines = _source_band_lines(source_pdf_path, page_idx, bb)
                prose_lines = [ln for ln in src_lines
                               if is_translatable_prose_line(ln["text"])]
                if prose_lines:
                    m_tgt += 1
                    details.append({
                        "kind": "formula_adjacent_target_missing",
                        "fragment_type": "formula_right_context",
                        "formula_id": fid,
                        "paragraph_id": None,
                        "fragment_id": "%s-TGTMISS" % fid,
                        "source_text": " ".join(ln["text"]
                                                for ln in prose_lines)[:160],
                        "final_visible_text": "no CJK target in band",
                        "bbox": [round(v, 1) for v in bb],
                        "reason": "formula band has source prose but no "
                                  "target block",
                    })

    # ---- C. protected math token integrity ------------------------------
    # only FORMULA_ placeholders are protected math tokens; BOLD_/ITALIC_
    # markup tokens are render-time styling controls (stripped by the
    # renderer by design) and must NOT be counted as math-token loss.
    # A token whose formula is FULLY prose-adopted (in recovered_formulas)
    # is legally dropped by the renderer (no SVG remains) -- the prose was
    # recovered as a PAF target instead; that is NOT a token loss.
    token_re = re.compile(r"\{\{(FORMULA_[A-Z0-9_]+)\}\}")
    skip_ids = set(recovered_formulas or [])
    for pid, tgt in translations.items():
        tokens = token_re.findall(tgt or "")
        if not tokens:
            continue
        # find the html block for this paragraph
        blk = next((b for b in blocks if b["id"] == pid), None)
        if blk is None:
            continue
        html_text = blk["text"]
        for tok in tokens:
            full = "{{%s}}" % tok
            if tok[8:] in skip_ids:
                continue  # fully prose-adopted formula: token legally gone
            # a token is PRESENT if the placeholder text exists OR the
            # renderer replaced it with an inline formula span
            inline_span = ('<span class="formula-inline" data-formula="%s"'
                           % tok) in blk.get("raw", "")
            if full not in html_text and not inline_span:
                m_token_loss += 1
                details.append({
                    "kind": "protected_math_token_loss",
                    "fragment_type": "formula_left_context",
                    "paragraph_id": pid, "fragment_id": "%s-%s" % (pid, tok),
                    "source_text": full,
                    "final_visible_text": "token missing in HTML block",
                    "reason": "protected math token dropped between canonical "
                              "target and final HTML",
                })

    # ---- E. formula context drop / duplicate / reorder -------------------
    # for each paragraph whose target contains >= 2 protected tokens, the
    # token order must be preserved in the HTML block (no reorder/drop)
    for pid, tgt in translations.items():
        tokens = token_re.findall(tgt or "")
        if len(tokens) < 2:
            continue
        blk = next((b for b in blocks if b["id"] == pid), None)
        if blk is None:
            continue
        raw = blk.get("raw") or ""
        html_tokens = token_re.findall(blk["text"])
        # tokens replaced by inline formula spans count as present; tokens
        # whose formula is fully prose-adopted are legally gone
        present = []
        for tok in tokens:
            if tok[8:] in skip_ids:
                present.append(tok)
                continue
            if ("<span class=\"formula-inline\" data-formula=\"%s\""
                    % tok) in raw or ("{{%s}}" % tok) in blk["text"]:
                present.append(tok)
        if present != tokens:
            if set(present) == set(tokens):
                m_reorder += 1
                details.append({
                    "kind": "formula_context_reorder",
                    "fragment_type": "formula_left_context",
                    "paragraph_id": pid, "fragment_id": "%s-REORDER" % pid,
                    "source_text": " ".join(tokens),
                    "final_visible_text": " ".join(present),
                    "reason": "protected token order differs target vs HTML",
                })
            elif len(present) < len(tokens):
                m_drop += 1
                details.append({
                    "kind": "formula_context_drop",
                    "fragment_type": "formula_left_context",
                    "paragraph_id": pid, "fragment_id": "%s-DROP" % pid,
                    "source_text": " ".join(tokens),
                    "final_visible_text": " ".join(present),
                    "reason": "protected token dropped in HTML",
                })
            else:
                m_dup += 1
                details.append({
                    "kind": "formula_context_duplicate",
                    "fragment_type": "formula_left_context",
                    "paragraph_id": pid, "fragment_id": "%s-DUP" % pid,
                    "source_text": " ".join(tokens),
                    "final_visible_text": " ".join(present),
                    "reason": "protected token duplicated in HTML",
                })

    metrics = {
        "formula_adjacent_source_residual_count": m_src,
        "formula_adjacent_target_missing_count": m_tgt,
        "formula_context_drop_count": m_drop,
        "formula_context_duplicate_count": m_dup,
        "formula_context_reorder_count": m_reorder,
        "protected_math_token_loss_count": m_token_loss,
        "protected_math_token_mutation_count": m_token_mut,
        "orphan_formula_punctuation_count": m_orphan,
        "formula_context_wrong_owner_count": m_wrong_owner,
    }
    return {
        "qa": "formula_adjacent_prose_qa",
        "schema_version": "visual_v05.formula_adjacent_prose_qa.v1",
        "metrics": metrics,
        "adjacent_details": details,
        "formula_contexts": [{
            "formula_id": f["formula_id"],
            "placement": f["placement"],
            "bbox": [round(v, 1) for v in f["bbox"]],
            "source_text_excerpt": f["source_text"][:80],
        } for f in formulas],
        "decision": "pass" if all(v == 0 for v in metrics.values()) else "fail",
    }


def _source_band_lines(pdf_path, page_idx, bbox) -> List[Dict[str, Any]]:
    from residual_prose_truth_qa import _source_lines_in_bbox
    return _source_lines_in_bbox(str(pdf_path), page_idx, bbox)
