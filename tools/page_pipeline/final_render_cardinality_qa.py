# -*- coding: utf-8 -*-
"""FinalRenderCardinalityQA (visual-v04).

Answers the FOURTH render-truth question:

    did every object that MUST render render EXACTLY ONCE,
    with the CORRECT payload, in the CORRECT region?

Render objects: logical-paragraph visual fragments, caption groups,
headings, visual-group render blocks.  RenderIdentity travels
paragraph -> visual block -> DOM -> QA trace via data-render-* attributes;
when the HTML does not yet carry them (visual-v03 frozen output), the QA
re-derives identity from the flows + translations + final PDF text layer.

Metrics:
    missing_render_count
    duplicate_render_count
    wrong_region_render_count
    source_and_target_double_render_count

Hard (all == 0):
    missing_render_count
    duplicate_render_count
    wrong_region_render_count
    source_and_target_double_render_count
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import pymupdf  # noqa: E402

from final_visible_translation_qa import (  # noqa: E402
    EXEMPT_ROLES, TRANSLATABLE_ROLES, _NON_PROSE_RE, _char_seq,
    _strip_tokens, normalize_visible_text, recover_target_in_lines)
from final_source_residual_qa import (  # noqa: E402
    is_prose_adopted_formula, _text_like_paths, _inside_rect)

TOKEN_RE = re.compile(r"\{\{[A-Z_0-9]+(?:_[A-Z0-9]+)?\}\}")


def _paragraph_blocks_from_html(html: str) -> Dict[str, Dict[str, Any]]:
    """data-para -> {render_source_guess, bbox, role} from the HTML DOM.

    RenderIdentity (visual-v04): when the renderer emits data-render-source,
    we trust it; otherwise we fall back to "target" when the block's text
    contains CJK, else "source" (fallback render).
    """
    out: Dict[str, Dict[str, Any]] = {}
    pat = re.compile(
        r'<div class="paragraph-block"[^>]*data-para="([^"]+)"[^>]*'
        r'data-role="([^"]*)"[^>]*style="[^"]*left:([\d.]+)pt;top:([\d.]+)pt;'
        r'width:([\d.]+)pt[^"]*"[^>]*>(.*?)</div>', re.S)
    for m in pat.finditer(html or ""):
        pid, role, left, top, width, inner = m.groups()
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", inner))
        rs = "target" if has_cjk else "source"
        m2 = re.search(r'data-render-source="([^"]*)"', m.group(0))
        if m2:
            rs = m2.group(1)
        out[pid] = {"render_source": rs, "role": role,
                    "bbox": [float(left), float(top),
                             float(left) + float(width), float(top) + 200.0],
                    "has_cjk": has_cjk}
    return out


def _target_char_in_final(target: str, final_chars: str) -> bool:
    return _char_seq(target) in final_chars


def final_render_cardinality_qa(
    page_model: Dict[str, Any],
    translations: Dict[str, str],
    flows: List[Dict[str, Any]],
    final_pdf_path=None,
    html_path=None,
    out_dir=None,
    recovered_formulas=None,
    excluded_segments=None,
) -> Dict[str, Any]:
    """Run FinalRenderCardinalityQA for one page.

    ``recovered_formulas`` (visual-v04): formula ids whose prose was
    recovered; their source SVG is excluded from rendering, so they never
    count as source+target double renders.
    """
    final_chars = ""
    line_chars: List[str] = []
    line_boxes: List[List[float]] = []
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
                        line_boxes.append([float(v) for v in l["bbox"]])
            final_chars = _char_seq(final_chars)
        except Exception:  # noqa: BLE001
            page = None

    html = ""
    if html_path and Path(html_path).exists():
        html = Path(html_path).read_text(encoding="utf-8")
    html_blocks = _paragraph_blocks_from_html(html)
    # protected token -> rendered visible text, PER PARAGRAPH ({{CODE_n}}
    # tokens are paragraph-local; a global map would cross-contaminate)
    para_protected: Dict[str, Dict[str, str]] = {}
    for r in page_model.get("regions", []):
        if r.get("type") != "text":
            continue
        p = r.get("payload") or {}
        pid = p.get("paragraph_id")
        if pid:
            para_protected[pid] = p.get("protected_runs") or {}

    missing = 0
    duplicate = 0
    wrong_region = 0
    double_render = 0
    details: List[Dict[str, Any]] = []

    # ---- 1. translatable paragraphs: exact-once target render ------------
    flow_paras = [it for flow in flows or []
                  for it in flow.get("items", [])
                  if it.get("kind") == "paragraph"]
    rendered_ids = set()
    for it in flow_paras:
        pid = it.get("paragraph_id")
        if not pid:
            continue
        role = it.get("style_role") or "body"
        if role.lower() in EXEMPT_ROLES:
            continue
        prot = para_protected.get(pid, {})
        render_text = it.get("render_text") or ""
        if not _char_seq(render_text, prot):
            continue
        if _NON_PROSE_RE.match(normalize_visible_text(
                _strip_tokens(render_text))):
            continue
        if pid in rendered_ids:
            continue
        rendered_ids.add(pid)
        # render occurrence check: the payload must appear CONTIGUOUSLY
        # inside one text layer line OR across consecutive lines of the
        # same column (block reading order is column-major; a global concat
        # would split a paragraph across columns)
        tseq = _char_seq(render_text, prot)
        status, _cov = recover_target_in_lines(
            render_text, line_chars, prot, line_boxes)
        if status != "full":
            # fallback source render check (HTML DOM evidence)
            hb = html_blocks.get(pid)
            rs = hb.get("render_source") if hb else None
            missing += 1
            details.append({
                "kind": "missing_render", "paragraph_id": pid,
                "role": role, "target_excerpt": tseq[:60],
                "html_render_source": rs or "unknown",
                "final_pdf_occurrences": 0})
        elif len(tseq) >= 8:
            # count duplicate occurrences across distinct lines
            n = sum(1 for lc in line_chars if tseq in lc)
            if n > 1:
                duplicate += n - 1
                details.append({
                    "kind": "duplicate_render", "paragraph_id": pid,
                    "role": role, "final_pdf_occurrences": n})

    # ---- 2. wrong region: target present but far from its flow_y ---------
    for it in flow_paras:
        pid = it.get("paragraph_id")
        if not pid:
            continue
        prot = para_protected.get(pid, {})
        render_text = it.get("render_text") or ""
        tseq = _char_seq(render_text, prot)
        if not tseq or not any(tseq in lc for lc in line_chars):
            continue
        # locate the target span y in the final PDF: find the line whose
        # text contains the LONGEST prefix of the target (first line of the
        # paragraph), not just any matching sub-line.
        expect_y = it.get("flow_y") or 0.0
        found_y = None
        best_len = 0
        if page is not None:
            for b in page.get_text("dict").get("blocks", []):
                if b.get("type") != 0:
                    continue
                for l in b.get("lines", []):
                    txt = "".join(s.get("text") or "" for s in
                                  l.get("spans", []))
                    seq = _char_seq(txt, prot)
                    if not seq:
                        continue
                    # longest common prefix between this line and target
                    m = min(len(seq), len(tseq))
                    k = 0
                    while k < m and seq[k] == tseq[k]:
                        k += 1
                    if k > best_len:
                        best_len = k
                        found_y = l["bbox"][1]
        if found_y is not None and best_len >= 6 \
                and abs(found_y - expect_y) > 30.0:
            wrong_region += 1
            details.append({
                "kind": "wrong_region_render", "paragraph_id": pid,
                "expected_flow_y": round(expect_y, 2),
                "final_pdf_y": round(found_y, 2),
                "match_chars": best_len})

    # ---- 3. source AND target double render (same visual region) ---------
    # visual-v04: a RECOVERED prose-adopted formula renders its target via
    # the PAF soft-text paragraph (the source SVG is excluded from HTML).
    # Double render is real ONLY when (a) the formula was NOT recovered
    # (its source SVG still rendered) AND (b) a CJK text layer appears in
    # the same band.  Path counts exclude figure/table/image regions (their
    # vector content is legal).
    recovered = set(recovered_formulas or [])
    anchor_regions = [[float(v) for v in r.get("bbox", [])]
                      for r in page_model.get("regions", [])
                      if r.get("type") in ("figure", "table", "image")
                      and len(r.get("bbox", [])) == 4]
    # real (non-prose-adopted) formulas render atomic SVG legally
    for r in page_model.get("regions", []):
        if r.get("type") == "formula":
            p = r.get("payload") or {}
            if not is_prose_adopted_formula(p):
                bb = p.get("layout_bbox") or []
                if len(bb) == 4:
                    anchor_regions.append([float(v) for v in bb])
    for r in page_model.get("regions", []):
        if r.get("type") != "formula":
            continue
        p = r.get("payload") or {}
        if not is_prose_adopted_formula(p):
            continue
        fid = p.get("formula_id")
        if fid in recovered:
            continue  # target renders via PAF; source SVG excluded
        if (excluded_segments or {}).get(str(fid)):
            # visual-v05: mixed formula renders ONLY its math segments --
            # the remaining glyph paths are the legal equation, no source
            # prose leaks; skip the double-render check
            continue
        bb = [float(v) for v in (p.get("layout_bbox") or [])]
        if len(bb) != 4:
            continue
        paths = _text_like_paths(page, bb) if page else []
        paths = [pa for pa in paths
                 if not any(_inside_rect(pa, ar) for ar in anchor_regions)]
        if len(paths) < 40:
            continue
        # is there a CJK text layer inside the same band?
        cjk_in_band = False
        if page is not None:
            for b in page.get_text("dict").get("blocks", []):
                if b.get("type") != 0:
                    continue
                for l in b.get("lines", []):
                    lbb = l["bbox"]
                    if (lbb[1] < bb[3] and lbb[3] > bb[1]
                            and lbb[0] < bb[2] and lbb[2] > bb[0]):
                        txt = "".join(s.get("text") or ""
                                      for s in l.get("spans", []))
                        if re.search(r"[\u4e00-\u9fff]", txt):
                            cjk_in_band = True
                            break
                if cjk_in_band:
                    break
        if cjk_in_band:
            double_render += 1
            details.append({
                "kind": "source_and_target_double_render",
                "formula_id": fid,
                "bbox": [round(v, 1) for v in bb],
                "vector_ink_paths": len(paths),
                "target_text_layer_in_band": True})

    metrics = {
        "missing_render_count": missing,
        "duplicate_render_count": duplicate,
        "wrong_region_render_count": wrong_region,
        "source_and_target_double_render_count": double_render,
        "render_object_count": len(rendered_ids),
    }
    decision = "pass" if all(v == 0 for v in metrics.values()) else "fail"
    return {
        "schema_version": "visual_v04.final_render_cardinality_qa.v1",
        "metrics": metrics,
        "decision": decision,
        "cardinality_details": details,
    }
