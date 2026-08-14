# -*- coding: utf-8 -*-
"""ProseAdoptedFormulaRecovery (visual-v04).

PPAT-style documents: the formula detector swallows whole prose paragraphs
into formula regions (a "prose-adopted formula": bbox > 30pt tall whose
source_text carries >= 8 English words).  Those regions render as source
SVG vector ink (text_as_path=True) -> English prose visibly remains in the
final PDF while the text layer is empty -> every text-layer QA is blind.

Recovery (document-general, no page / filename / id special cases):

  1. find prose-adopted formula regions;
  2. extract their REAL text lines from the SOURCE PDF (PyMuPDF clip,
     NOT OCR -- the source PDF has a text layer);
  3. exclude lines that belong to real (non-prose-adopted) formula regions
     (those keep their atomic SVG) and lines already covered by a text
     region (already translated);
  4. cluster the remaining lines into logical prose paragraphs
     (column-aware, y-adjacent);
  5. assign stable render ids (PAF_<formula_id>_<n>) and translate via the
     existing DeepSeek batch pipeline (protected tokens are preserved);
  6. emit recovered paragraphs that the visual layout renders as SOFT TEXT
     (target Chinese) in their source region -- the prose-adopted formula
     itself is excluded from formula rendering (RenderExclusivity: never
     source AND target in the same region).

Translation is the ONLY external call; it is a normal pipeline operation
(the swallowed prose was never routed to translation before).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pymupdf  # noqa: E402

from final_visible_translation_qa import is_prose_adopted_formula  # noqa: E402

# page-number / preprint footer markers (never recovered as prose)
_NON_PROSE_RE = re.compile(
    r"^(page\s*\d+\s*of\s*\d+|preprint submitted to .*)$", re.I)

# line-height tolerance for clustering consecutive lines into a paragraph
LINE_GAP_TOL = 3.0
# x split between the two columns (mid-page)
COL_SPLIT_X = 300.0
# max lines merged into one recovered paragraph
MAX_PARA_LINES = 40


def _is_prose_line(text: str) -> bool:
    """document-general: a line is PROSE when it carries real words.

    Formula fragment lines (e.g. "𝑤𝑘=", ",", "(10)", "𝑘=1") are pure
    symbols / single identifiers -- never recovered as prose.
    """
    t = text or ""
    words = re.findall(r"[A-Za-z]{3,}", t)
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", t))
    if has_cjk:
        return True
    return len(words) >= 2


def _text_lines_in_regions(pdf_path: str, page_idx: int,
                           regions: List[List[float]]) -> List[Dict[str, Any]]:
    """Extract text lines (with bbox) inside the union of ``regions``."""
    if not regions:
        return []
    union = [min(r[0] for r in regions), min(r[1] for r in regions),
             max(r[2] for r in regions), max(r[3] for r in regions)]
    doc = pymupdf.open(pdf_path)
    try:
        page = doc[page_idx]
        d = page.get_text("dict", clip=pymupdf.Rect(*union))
        lines = []
        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            for l in b.get("lines", []):
                txt = "".join(s.get("text") or "" for s in
                              l.get("spans", [])).strip()
                if not txt:
                    continue
                if not _is_prose_line(txt):
                    continue  # formula fragment line -> not prose
                bb = [float(v) for v in l["bbox"]]
                # keep only lines inside at least one target region
                if not any(_inside(bb, r) for r in regions):
                    continue
                lines.append({"bbox": bb, "text": txt,
                              "size": max((s.get("size") or 0)
                                          for s in l.get("spans", []))})
        return lines
    finally:
        doc.close()


def _inside(a: List[float], b: List[float], tol: float = 2.0) -> bool:
    """Line a (bbox) is (mostly) inside region b."""
    # overlap of the line's horizontal span with b >= 60% of line width
    xo = min(a[2], b[2]) - max(a[0], b[0])
    if xo < 0.6 * (a[2] - a[0]) - tol:
        return False
    # vertical: line center inside b
    cy = (a[1] + a[3]) / 2.0
    return b[1] - tol <= cy <= b[3] + tol


def _cluster_lines(lines: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Cluster text lines into paragraphs: same column + y-adjacent.

    Gap tolerance is font-relative (<= 0.8 * font size of the previous
    line) so distinct prose paragraphs separated by a display formula /
    blank band are NOT merged (p005 B3 region contains several paragraphs).
    """
    lines = sorted(lines, key=lambda x: (round(x["bbox"][0] / 10.0),
                                         x["bbox"][1]))
    clusters: List[List[Dict[str, Any]]] = []
    for ln in lines:
        placed = False
        for cl in clusters:
            last = cl[-1]
            # same column (x overlap) and vertically adjacent
            x_overlap = (min(ln["bbox"][2], last["bbox"][2])
                         - max(ln["bbox"][0], last["bbox"][0]))
            gap = ln["bbox"][1] - last["bbox"][3]
            last_size = last.get("size") or 10.0
            max_gap = max(0.8 * last_size, 4.0)
            if x_overlap > 10.0 and -2.0 < gap <= max_gap:
                cl.append(ln)
                placed = True
                break
            # same column, slightly wider gap, very similar x0 -> same
            # paragraph continuation after an inline formula
            if (x_overlap > 10.0 and max_gap < gap <= max_gap * 3.0
                    and abs(ln["bbox"][0] - last["bbox"][0]) < 20.0):
                cl.append(ln)
                placed = True
                break
        if not placed:
            clusters.append([ln])
    merged: List[List[Dict[str, Any]]] = []
    for cl in clusters:
        if merged and len(cl) == 1 and len(merged[-1]) >= 2:
            last = merged[-1][-1]
            ln = cl[0]
            gap = ln["bbox"][1] - last["bbox"][3]
            last_size = last.get("size") or 10.0
            if 0.0 < gap <= max(0.8 * last_size, 4.0):
                merged[-1].append(ln)
                continue
        merged.append(cl)
    return [c for c in merged if len(c) <= MAX_PARA_LINES]


def _exclusion_regions(page_model) -> List[List[float]]:
    """Real (non-prose-adopted) formula region boxes + table/figure/image."""
    boxes = []
    for r in page_model.get("regions", []):
        t = r.get("type")
        if t in ("table", "figure", "image"):
            boxes.append([float(v) for v in r.get("bbox", [])])
        elif t == "formula":
            p = r.get("payload") or {}
            if not is_prose_adopted_formula(p):
                bb = p.get("layout_bbox") or []
                if len(bb) == 4:
                    boxes.append([float(v) for v in bb])
    return boxes


def _text_region_boxes(page_model) -> List[List[float]]:
    """Text regions already handled by the normal translation path."""
    return [[float(v) for v in r.get("bbox", [])]
            for r in page_model.get("regions", [])
            if r.get("type") == "text" and len(r.get("bbox", [])) == 4]


def _assign_paragraph_ids(clusters, fid, existing):
    """Assign stable PAF ids; never collide with existing paragraph ids."""
    out = []
    for i, cl in enumerate(clusters):
        pid = "PAF_%s_%02d" % (fid, i)
        n = 2
        while pid in existing:
            pid = "PAF_%s_%02d_%d" % (fid, i, n)
            n += 1
        existing.add(pid)
        out.append(pid)
    return out

def recover_prose_adopted_formulas(
    page_model: Dict[str, Any],
    translations: Dict[str, str],
    pdf_path: str,
    page_idx: int,
    translator_fn=None,
    existing_ids: set | None = None,
) -> Dict[str, Any]:
    """Recover prose swallowed into formula regions.

    Returns:
        {"recovered": [paragraph_dict...], "skipped_formulas": [fid...],
         "trace": {...}, "api_calls": int}

    Each recovered paragraph has the same schema as a normal text-region
    payload (paragraph_id / source_text / target_text / bbox / column /
    anchor_y / base_font_size / semantic_role=body / style_role=body /
    protected_runs={}) so the visual layout renders it as soft text.
    """
    pafs = [r for r in page_model.get("regions", [])
            if r.get("type") == "formula"
            and is_prose_adopted_formula(r.get("payload") or {})]
    # visual-v04 extension: a formula whose bbox contains >= 2 source-PDF
    # lines of real English prose (>= 2 words each) has swallowed prose even
    # when its static height/word-count heuristic misses it (PPAT detector
    # also labels single body lines as formulas, e.g. "to a Gaussian
    # low-pass process in the frequency domain.").
    paf_ids = {str((r.get("payload") or {}).get("formula_id"))
               for r in pafs}
    for r in page_model.get("regions", []):
        if r.get("type") != "formula":
            continue
        p = r.get("payload") or {}
        bb = p.get("layout_bbox") or []
        if len(bb) != 4:
            continue
        fid = str(p.get("formula_id"))
        if fid in paf_ids or is_prose_adopted_formula(p):
            continue
        region_lines = _text_lines_in_regions(
            str(pdf_path), page_idx, [[float(v) for v in bb]])
        # >= 1 real English prose line (>= 3 words) inside the region means
        # the detector swallowed body text (PPAT labels single body lines
        # as formulas, e.g. "to a Gaussian low-pass process...").  Page
        # footers ("Preprint submitted to Elsevier") are exempt.
        prose_lines = [ln for ln in region_lines
                       if len(re.findall(r"[A-Za-z]{3,}", ln["text"])) >= 3
                       and not _NON_PROSE_RE.match(ln["text"].strip())]
        if len(prose_lines) >= 1:
            pafs.append(r)
            paf_ids.add(fid)
    if not pafs:
        return {"recovered": [], "skipped_formulas": [],
                "trace": {"prose_adopted_formula_count": 0},
                "api_calls": 0}

    fid_list = [(r.get("payload") or {}).get("formula_id")
                for r in pafs]
    paf_boxes = [[float(v) for v in (r.get("payload") or {})
                  .get("layout_bbox", [])] for r in pafs]
    # exclude real formulas / tables / figures / existing text regions
    excl = _exclusion_regions(page_model) + _text_region_boxes(page_model)

    lines = _text_lines_in_regions(str(pdf_path), page_idx, paf_boxes)
    # drop lines inside exclusion regions (real formulas keep their SVG)
    kept = [ln for ln in lines
            if not any(_inside(ln["bbox"], ex) for ex in excl)]
    clusters = _cluster_lines(kept)

    existing = set(existing_ids or set())
    existing.update(translations.keys())
    # stable short id prefix: first prose-adopted formula id (or "PAF")
    prefix = str(fid_list[0]) if fid_list and fid_list[0] else "PAF"
    pids = _assign_paragraph_ids(clusters, prefix, existing)

    recovered = []
    for cl, pid in zip(clusters, pids):
        # page-local source text joined with spaces (PDF lines split words)
        src = " ".join(ln["text"] for ln in cl).strip()
        bb = [min(ln["bbox"][0] for ln in cl),
              min(ln["bbox"][1] for ln in cl),
              max(ln["bbox"][2] for ln in cl),
              max(ln["bbox"][3] for ln in cl)]
        col = 0 if (bb[0] + bb[2]) / 2.0 < COL_SPLIT_X else 1
        size = max((ln["size"] or 10.0) for ln in cl) or 10.0
        # visual-v04: recovered prose renders with the standard BODY font
        # (the detector's per-line size mixes in formula scales; a CJK body
        # at 10.96pt inflates the packed height and overflows the region).
        # The bbox is clipped to its own column so the packed block never
        # leaks into the gutter / other column.
        body_size = 10.0
        if col == 0:
            bb[2] = min(bb[2], COL_SPLIT_X - 2.0)
        else:
            bb[0] = max(bb[0], COL_SPLIT_X + 2.0)
        recovered.append({
            "paragraph_id": pid,
            "logical_paragraph_id": pid,
            "source_text": src,
            "target_text": None,          # filled after translation
            "bbox": [round(v, 2) for v in bb],
            "anchor_y": round(bb[1], 2),
            "column": col,
            "col_x0": round(bb[0], 2),
            "col_x1": round(bb[2], 2),
            "col_width": round(bb[2] - bb[0], 2),
            "base_font_size": body_size,
            "semantic_role": "body",
            "style_role": "body",
            "protected_runs": {},
            "source_fragments": [{
                "flow_fragment_id": pid + "-F0",
                "column": col,
                "anchor_y": round(bb[1], 2),
                "bbox": [round(v, 2) for v in bb],
                "col_x0": round(bb[0], 2),
                "col_x1": round(bb[2], 2),
                "col_width": round(bb[2] - bb[0], 2),
                "base_font_size": body_size,
                "source_text": src,
                "continuation": False,
            }],
            "render_source": "canonical_target",
            "render_source_reason": "prose_adopted_formula_recovery",
            "_recovered_prose": True,
        })

    # ---- translate (only the recovered prose; no formula tokens) --------
    api_calls = 0
    if translator_fn is not None and recovered:
        items = [{"item_id": p["paragraph_id"], "type": "paragraph",
                  "source_text": p["source_text"]} for p in recovered]
        zh_map = translator_fn(items)  # {paragraph_id: zh}
        api_calls += max(1, (len(items) + 29) // 30)
        for p in recovered:
            zh = zh_map.get(p["paragraph_id"], "")
            # visual-v04: a translation that kept the source ENTIRELY in
            # English (no CJK at all) is a failed translation -- the prose
            # was not translated, so rendering it would just re-render the
            # English.  Mark it BLOCK (never silently render source).
            if zh and not re.search(r"[\u4e00-\u9fff]", zh) \
                    and re.search(r"[A-Za-z]{6,}", zh):
                zh = None
                p["render_source"] = "BLOCK"
                p["render_source_reason"] = "translation_not_chinese"
            p["target_text"] = zh if zh else None
            if p["target_text"] is None and p.get("render_source") != "BLOCK":
                p["render_source"] = "BLOCK"
                p["render_source_reason"] = "target_missing_block"
    else:
        # dry-run / no API token: keep the source as a placeholder payload so
        # the layout/render pipeline can be exercised; render_source marks it
        # as source (the QA keeps flagging it -> RED is preserved)
        for p in recovered:
            p["target_text"] = p.get("source_text", "")
            p["render_source"] = "source"
            p["render_source_reason"] = "dry_run_no_translation"

    trace = {
        "prose_adopted_formula_count": len(pafs),
        "formula_ids": fid_list,
        "extracted_lines": len(lines),
        "kept_prose_lines": len(kept),
        "clusters": len(clusters),
        "recovered_paragraphs": len(recovered),
        "api_calls": api_calls,
    }
    return {"recovered": recovered,
            "skipped_formulas": [str(f) for f in fid_list],
            "trace": trace, "api_calls": api_calls}
