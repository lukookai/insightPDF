# -*- coding: utf-8 -*-
"""FinalSourceResidualQA (visual-v04).

Answers the SECOND render-truth question:

    was source prose that REQUIRED translation
    still visible in the FINAL PDF?

Two independent detection paths (no language-ratio guessing, no OCR):

  * text-layer residual: the FINAL PDF text layer contains a substantial
    normalised run of a translatable SOURCE paragraph while the canonical
    TARGET is not fully present -> SOURCE_TEXT_SELECTED / fallback.

  * vector-ink residual: a page-model FORMULA region whose source_text
    carries substantial English prose (a prose-adopted formula -- the
    detector swallowed a prose paragraph) renders its source SVG paths
    (text_as_path=True) into the final PDF.  Those paths ARE the source
    prose, visible but invisible to the text layer.  We detect them by
    counting text-like vector paths inside the formula bbox.

Role-aware exemptions (allowed to stay English): model names, dataset
names, code, URLs, citation keys, references, formula symbols, pure math,
author names, KEEP route, NON_TRANSLATABLE_MATH.

Hard:
    translatable_source_residual_count == 0
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import pymupdf  # noqa: E402

from final_visible_translation_qa import (  # noqa: E402
    EXEMPT_ROLES, TRANSLATABLE_ROLES, _NON_PROSE_RE, _char_seq,
    _prose_word_count, _strip_tokens, expand_visible_text,
    is_prose_adopted_formula, normalize_visible_text,
    recover_target_in_lines)

TOKEN_RE = re.compile(r"\{\{[A-Z_0-9]+(?:_[A-Z0-9]+)?\}\}")

# prose-adopted formula threshold: shared with FinalVisibleTranslationQA
PROSE_ADOPTED_WORD_MIN = 8
PROSE_ADOPTED_HEIGHT_PT = 30.0


def _text_like_paths(page, bbox, max_paths=10_000) -> List[List[float]]:
    """Count small filled vector paths (glyph-shaped) inside ``bbox``."""
    x0, y0, x1, y1 = bbox
    out = []
    try:
        for dr in page.get_drawings()[:max_paths]:
            r = dr["rect"]
            if (r.x1 < x0 or r.x0 > x1 or r.y1 < y0 or r.y0 > y1):
                continue
            if dr.get("fill") is None:
                continue
            w, h = r.width, r.height
            # glyph-like: small box with ink
            if w < 24.0 and h < 18.0 and w > 0.2 and h > 0.2:
                out.append([r.x0, r.y0, r.x1, r.y1])
    except Exception:  # noqa: BLE001
        pass
    return out


def _runs(y_centers, gap=3.0):
    """Cluster y-centers into line bands; returns number of distinct bands."""
    if not y_centers:
        return 0
    ys = sorted(y_centers)
    bands = 1
    prev = ys[0]
    for y in ys[1:]:
        if y - prev > gap:
            bands += 1
        prev = y
    return bands


def _inside_rect(a, b, tol=2.0):
    """Path bbox a is (mostly) inside region b (center inside b)."""
    cx = (a[0] + a[2]) / 2.0
    cy = (a[1] + a[3]) / 2.0
    return (b[0] - tol <= cx <= b[2] + tol
            and b[1] - tol <= cy <= b[3] + tol)


def final_source_residual_qa(
    page_model: Dict[str, Any],
    translations: Dict[str, str],
    flows: List[Dict[str, Any]],
    final_pdf_path=None,
    source_pdf_path=None,
    out_dir=None,
    recovered_formulas=None,
    excluded_segments=None,
) -> Dict[str, Any]:
    """Run FinalSourceResidualQA for one page.

    ``recovered_formulas`` (visual-v04): formula ids whose prose was
    recovered by the production pipeline; residual is only reported when
    their source vector ink actually leaked into the final PDF.
    """
    # ---- final PDF: text layer + vector page ------------------------------
    final_chars = ""
    line_chars: List[str] = []
    page = None
    if final_pdf_path and Path(final_pdf_path).exists():
        try:
            doc = pymupdf.open(str(final_pdf_path))
            page = doc[0]
            d = page.get_text("dict")
            for b in d.get("blocks", []):
                if b.get("type") != 0:
                    continue
                for l in b.get("lines", []):
                    txt = "".join(s.get("text") or ""
                                  for s in l.get("spans", []))
                    if txt.strip():
                        final_chars += " " + normalize_visible_text(txt)
                        line_chars.append(_char_seq(txt))
            final_chars = _char_seq(final_chars)
        except Exception:  # noqa: BLE001
            page = None

    residuals: List[Dict[str, Any]] = []
    # protected token -> rendered visible text, PER PARAGRAPH
    para_protected: Dict[str, Dict[str, str]] = {}
    for r in page_model.get("regions", []):
        if r.get("type") != "text":
            continue
        p = r.get("payload") or {}
        pid = p.get("paragraph_id")
        if pid:
            para_protected[pid] = p.get("protected_runs") or {}

    # ---- path 1: text-layer residual for translatable paragraphs ----------
    for r in page_model.get("regions", []):
        if r.get("type") != "text":
            continue
        p = r["payload"]
        pid = p.get("paragraph_id")
        if not pid:
            continue
        role = (p.get("semantic_role") or p.get("style_role") or "body")
        if role.lower() in EXEMPT_ROLES:
            continue
        if role.lower() not in TRANSLATABLE_ROLES:
            continue
        source = p.get("source_text") or ""
        src_wo = _strip_tokens(source).strip()
        if not src_wo or not re.search(r"[A-Za-z\u4e00-\u9fff]", src_wo):
            continue
        if _NON_PROSE_RE.match(normalize_visible_text(src_wo)):
            continue
        target = translations.get(pid)
        prot = para_protected.get(pid, {})
        if target is None or not _char_seq(target, prot):
            # no canonical target -> missing, already counted by QA-1;
            # if the source prose is still visible -> residual
            status = "target_missing"
            cov = 0.0
        else:
            status, cov = recover_target_in_lines(
                target, line_chars, prot)
        if status != "full":
            # is the SOURCE itself substantially present in the text layer?
            src_seq = _char_seq(source, prot)
            if len(src_seq) >= 20:
                window = max(20, len(src_seq) // 2)
                hit = src_seq[:window] in final_chars \
                    or src_seq[-window:] in final_chars
                if hit:
                    residuals.append({
                        "kind": "text_layer_source_residual",
                        "paragraph_id": pid,
                        "semantic_role": role,
                        "source_excerpt": normalize_visible_text(
                            src_wo)[:100],
                        "target_status": status,
                        "detail": "source prose still in final PDF text layer",
                    })

    # ---- path 2: vector-ink residual for prose-adopted formulas -----------
    # visual-v04: when the production pipeline recovered this formula's
    # prose (recovered_formulas), the source SVG must NOT be rendered.  A
    # residual is real ONLY when glyph-like vector paths remain inside the
    # formula's OWN region excluding:
    #   * figure/table/image regions (legally vector content)
    #   * REAL (non-prose-adopted) formula regions nested inside the
    #     prose-adopted bbox (their atomic SVGs are legal)
    anchor_regions = [[float(v) for v in r.get("bbox", [])]
                      for r in page_model.get("regions", [])
                      if r.get("type") in ("figure", "table", "image")
                      and len(r.get("bbox", [])) == 4]
    for r in page_model.get("regions", []):
        if r.get("type") == "formula":
            p = r.get("payload") or {}
            if not is_prose_adopted_formula(p):
                bb = p.get("layout_bbox") or []
                if len(bb) == 4:
                    anchor_regions.append([float(v) for v in bb])
    recovered_formulas = set(recovered_formulas or [])
    excl_seg = excluded_segments or {}
    for r in page_model.get("regions", []):
        if r.get("type") != "formula":
            continue
        p = r.get("payload") or {}
        if not is_prose_adopted_formula(p):
            continue
        bb = p.get("layout_bbox") or []
        fid = p.get("formula_id")
        # visual-v05: a MIXED formula (math rows + annotation prose) only
        # renders its math segments -- the remaining glyph paths are the
        # legal equation, NOT residual source prose.
        if excl_seg.get(str(fid)):
            continue
        tok = "{{FORMULA_%s}}" % fid
        has_target = any(TOKEN_RE.sub("", v or "").strip()
                         for v in translations.values())
        paths = _text_like_paths(page, [float(v) for v in bb]) if page else []
        # exclude legal anchor (figure/table/image) paths from the count
        legal = [pa for pa in paths
                 if not any(_inside_rect(pa, ar) for ar in anchor_regions)]
        y_centers = [(pa[1] + pa[3]) / 2.0 for pa in legal]
        bands = _runs(y_centers)
        if len(legal) >= 40 and bands >= 3:
            residuals.append({
                "kind": "vector_ink_source_residual",
                "formula_id": fid,
                "placement": p.get("placement"),
                "bbox": [round(float(v), 1) for v in bb],
                "source_word_count": _prose_word_count(
                    p.get("source_text") or ""),
                "text_like_path_count": len(legal),
                "line_band_count": bands,
                "recovered_by_pipeline": fid in recovered_formulas,
                "has_formula_translation": has_target,
                "detail": "prose-adopted formula rendered as source SVG "
                          "vector ink in final PDF",
            })

    translatable_source_residual_chars = sum(
        len(_char_seq(p.get("source_text") or ""))
        for r in page_model.get("regions", [])
        if r.get("type") == "text" and _char_seq(
            (r.get("payload") or {}).get("source_text") or ""))
    # only count residuals that are TRANSLATABLE (role-aware)
    metrics = {
        "translatable_source_residual_count": len(residuals),
        "translatable_source_residual_chars":
            translatable_source_residual_chars,
        "source_residual_ratio":
            round(len(residuals) / max(1, len([
                r for r in page_model.get("regions", [])
                if r.get("type") == "text"])), 6),
        "prose_adopted_formula_count": sum(
            1 for r in page_model.get("regions", [])
            if r.get("type") == "formula"
            and is_prose_adopted_formula(r.get("payload") or {})),
    }
    decision = "pass" if metrics["translatable_source_residual_count"] == 0 \
        else "fail"
    return {
        "schema_version": "visual_v04.final_source_residual_qa.v1",
        "metrics": metrics,
        "decision": decision,
        "residual_details": residuals,
    }
