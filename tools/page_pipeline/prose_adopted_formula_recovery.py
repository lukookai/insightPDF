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


# math-heavy characters: a line containing these is formula notation,
# not recoverable prose (even when it embeds English words)
_MATH_CHAR_RE = re.compile(
    r"[\u2200-\u22ff\U0001d400-\U0001d7ff"
    r"\u0370-\u03ff\u2190-\u21ff\u2b00-\u2bff]")


def _is_plain_prose_line(text: str) -> bool:
    """A line is RECOVERABLE prose when it has >= 2 English words AND no
    math notation (formula glyphs).  Lines that carry formula symbols are
    formula annotations, not swallowed prose."""
    t = text or ""
    if _MATH_CHAR_RE.search(t):
        return False
    return len(re.findall(r"[A-Za-z]{3,}", t)) >= 2


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
    """Line a (bbox) is inside region b: BOTH axes overlap >= 60%.

    A line whose center is inside b but which extends well beyond b (a
    body line beside an inline formula bbox) is NOT excluded -- it is
    recoverable prose, not swallowed formula content."""
    if len(a) != 4 or len(b) != 4:
        return False
    # horizontal overlap >= 60% of the LINE width
    xo = min(a[2], b[2]) - max(a[0], b[0])
    if xo < 0.6 * (a[2] - a[0]) - tol:
        return False
    # vertical overlap >= 60% of the LINE height
    yo = min(a[3], b[3]) - max(a[1], b[1])
    if yo < 0.6 * (a[3] - a[1]) - tol:
        return False
    return True


def _cluster_lines(lines: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Cluster text lines into paragraphs: same column + y-adjacent.

    Gap tolerance is font-relative (<= 0.8 * font size of the previous
    line) so distinct prose paragraphs separated by a display formula /
    blank band are NOT merged (p005 B3 region contains several paragraphs).

    visual-v05: a line marked ``is_heading`` NEVER merges (heading rows
    must become independent RecoveredProseBlocks so the translated heading
    renders as its own block with the heading semantic role).
    """
    # sort by Y (reading order) -- the old (x-group, y) key stranded a
    # row whose x-group sorted AFTER a later row (PPAT p005 y507.6 with
    # x0=66.3 grouped after y519.5) so it could never merge into the
    # paragraph above it.  Column membership is decided by the absolute
    # COL_SPLIT_X check inside the loop, not by the sort key.
    lines = sorted(lines, key=lambda x: (x["bbox"][1], x["bbox"][0]))
    clusters: List[List[Dict[str, Any]]] = []
    for ln in lines:
        placed = False
        if ln.get("is_heading"):
            clusters.append([ln])
            continue
        for cl in clusters:
            if cl and cl[0].get("is_heading"):
                continue  # a heading cluster never accepts more rows
            last = cl[-1]
            # same-column check: BOTH lines must belong to the SAME column
            # (a left-column line x1~288 and a right-column line x0~306
            # never merge).  Column membership uses the ABSOLUTE x range,
            # NOT the center distance: a short line (x0 51 .. x1 177,
            # center 114) is still a left-column line and must merge with
            # its left-column neighbours (visual-v05, PPAT p005 y495.6
            # "compensation scale end-to-end." was stranded as its own
            # cluster and collided with the row above).
            cx = (ln["bbox"][0] + ln["bbox"][2]) / 2.0
            px = (last["bbox"][0] + last["bbox"][2]) / 2.0
            same_col = ((cx < COL_SPLIT_X and px < COL_SPLIT_X)
                        or (cx >= COL_SPLIT_X and px >= COL_SPLIT_X))
            if not same_col:
                continue
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
            if ln.get("is_heading"):
                merged.append(cl)
                continue
            gap = ln["bbox"][1] - last["bbox"][3]
            last_size = last.get("size") or 10.0
            if 0.0 < gap <= max(0.8 * last_size, 4.0):
                merged[-1].append(ln)
                continue
        merged.append(cl)
    return [c for c in merged if len(c) <= MAX_PARA_LINES]


def _heading_rows_in_lines(pdf_path: str, page_idx: int,
                           line_bboxes: List[List[float]]
                           ) -> List[List[float]]:
    """Source-typography heading rows (bold text face, >= 1.02 x body,
    numbered) whose bbox overlaps the recovered line bboxes.

    document-general: no chapter-number constants; the numbered-heading
    pattern (1. / 3.4. / 4. / ...) is universal for section headings.
    """
    from semantic_structure_closure_qa import _source_heading_candidates
    body_size = 10.0
    try:
        cands = _source_heading_candidates(str(pdf_path), page_idx, body_size)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for c in cands:
        for lb in line_bboxes:
            if _inside(lb, c["bbox"], tol=3.0):
                out.append(c["bbox"])
                break
    return out


def _exclusion_regions(page_model, keep_ids=None) -> List[List[float]]:
    """Real (non-prose-adopted) formula region boxes + table/figure/image.

    ``keep_ids`` (visual-v04): candidate formula ids whose swallowed prose
    must be recovered -- their regions are NOT exclusion boxes.

    visual-v04: short formula boxes (height <= 16pt) are single-line
    pseudo-formulas (an inline math run detected as a formula, e.g. the
    "4 × 10-4" inside a body line).  They never exclude neighbouring prose
    -- a body line that coincides with such a box is still recoverable.
    """
    keep = set(keep_ids or [])
    boxes = []
    for r in page_model.get("regions", []):
        t = r.get("type")
        if t in ("table", "figure", "image"):
            boxes.append([float(v) for v in r.get("bbox", [])])
        elif t == "formula":
            p = r.get("payload") or {}
            if str(p.get("formula_id")) in keep:
                continue  # candidate: recover its prose
            if is_prose_adopted_formula(p):
                continue
            bb = p.get("layout_bbox") or []
            if len(bb) != 4:
                continue
            if bb[3] - bb[1] <= 16.0:
                continue  # single-line pseudo formula: not an obstacle
            boxes.append([float(v) for v in bb])
    return boxes


def _text_region_boxes(page_model) -> List[List[float]]:
    """Text regions already handled by the normal translation path.

    visual-v05: a text region whose source is ONLY formula placeholders
    ("{{FORMULA_B10}} {{FORMULA_B12}}") is an inline-formula CONTAINER, not
    a prose translation carrier -- its bbox must NOT exclude the swallowed
    prose rows inside it (the real English prose lives in the overlapping
    formula region and still needs PAF recovery, e.g. PPAT p006 B10
    "We employ the AdamW optimizer...").
    """
    import re as _re
    out = []
    for r in page_model.get("regions", []):
        if r.get("type") != "text" or len(r.get("bbox", [])) != 4:
            continue
        src = (r.get("payload") or {}).get("source_text") or ""
        stripped = _re.sub(r"\{\{[A-Z_0-9]+(?:_[A-Z0-9]+)?\}\}", "", src)
        if not stripped.strip():
            continue  # placeholder-only container
        out.append([float(v) for v in r.get("bbox", [])])
    return out


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
    # visual-v05 extension: a formula whose bbox contains a translatable
    # English prose line (sentence-completeness test, math glyphs allowed)
    # has swallowed body text -- including formula ANNOTATION lines such as
    # "where F(.) and F^-1(.) denote the 2D Fast Fourier Transform form and
    # its inverse transformation respectively." (PPAT p005 B2/B11/B14) and
    # "Specifically, given a TIR image X in R^{H x W x C} with C channels,
    # we can extract its high-frequency components" (PPAT p004 B26).
    from residual_prose_truth_qa import is_translatable_prose_line  # noqa: E402
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
        # >= 1 translatable prose line -> swallowed prose (document-general;
        # formula-internal annotations like "if rj correctly supports ci"
        # on 2504 pages fail the sentence-completeness test and stay math).
        # Page footers ("Preprint submitted to Elsevier") are exempt.
        prose_lines = [ln for ln in region_lines
                       if is_translatable_prose_line(ln["text"])
                       and not _NON_PROSE_RE.match(ln["text"].strip())]
        if len(prose_lines) >= 1:
            pafs.append(r)
            paf_ids.add(fid)
    if not pafs:
        return {"recovered": [], "skipped_formulas": [],
                "prose_excluded_segments": {},
                "trace": {"prose_adopted_formula_count": 0},
                "api_calls": 0}

    fid_list = [(r.get("payload") or {}).get("formula_id")
                for r in pafs]
    paf_boxes = [[float(v) for v in (r.get("payload") or {})
                  .get("layout_bbox", [])] for r in pafs]
    # exclude REAL formulas / tables / figures / existing text regions.
    # Candidate formulas (pafs) are NOT excluded -- their swallowed prose
    # lines must be recovered.
    excl = _exclusion_regions(page_model, keep_ids=paf_ids) \
        + _text_region_boxes(page_model)

    lines = _text_lines_in_regions(str(pdf_path), page_idx, paf_boxes)
    # drop lines inside exclusion regions (real formulas keep their SVG)
    # -- BUT keep ALL candidate-region lines (including paragraph-middle
    # rows that fail the single-line prose test): prose is decided at the
    # CLUSTER level (a body paragraph's middle rows carry no period and no
    # sentence starter, yet they are prose).
    kept = [ln for ln in lines
            if not any(_inside(ln["bbox"], ex) for ex in excl)]
    # heading rows (source typography: bold text face, >= 1.02 x body,
    # numbered) become INDEPENDENT PAF blocks with semantic_role=heading --
    # never merged into the surrounding body paragraph.
    heading_rows = _heading_rows_in_lines(str(pdf_path), page_idx,
                                          [ln["bbox"] for ln in kept])
    for ln in kept:
        ln["is_heading"] = any(_inside(ln["bbox"], hb, tol=3.0)
                               for hb in heading_rows)
    clusters = _cluster_lines(kept)
    # ---- visual-v05: CLUSTER-level prose decision ------------------------
    # a cluster is recovered PROSE when its joined text carries a real
    # sentence (>= 6 words AND (any row passes the single-line test OR the
    # joined text has a period / a verb core)); otherwise it is a REAL
    # formula cluster and stays SVG.
    from residual_prose_truth_qa import is_translatable_prose_line as _itpl
    from residual_prose_truth_qa import _SENTENCE_CORES as _CORES

    def _cluster_is_prose(cl):
        joined = " ".join(ln["text"] for ln in cl)
        words = re.findall(r"[A-Za-z]{3,}", joined)
        if len(words) < 3:
            return False
        if any(ln.get("is_heading") for ln in cl):
            return True
        if any(_itpl(ln["text"]) for ln in cl):
            return True
        if "." in joined or "!" in joined or "?" in joined:
            return True
        low = [w.lower() for w in words]
        return any(w in _CORES for w in low)

    prose_clusters = [c for c in clusters if _cluster_is_prose(c)]
    clusters = [c for c in clusters if not _cluster_is_prose(c)]
    _formula_clusters = clusters
    clusters = prose_clusters
    # visual-v05: kept already contains ONLY translatable prose lines, so a
    # 1-line cluster (e.g. "where lambda_c > 0 while maintaining
    # differentiability.") is real prose and MUST be recovered.  (2504
    # formula-internal annotations fail the prose test and never appear in
    # kept, so the 2504 regression stays clean.)
    clusters = [c for c in clusters if len(c) >= 1]

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
        # visual-v05: heading rows (source typography evidence) become
        # independent blocks with the heading semantic role -- the semantic
        # structure is restored from SOURCE PROVENANCE, never guessed from
        # the translated text.
        is_heading = any(ln.get("is_heading") for ln in cl)
        role = "heading" if is_heading else "body"
        level = 1 if is_heading else 0
        # source formula region(s) owning this block's lines
        src_fids = []
        for ln in cl:
            for r in pafs:
                p = r.get("payload") or {}
                fbb = p.get("layout_bbox") or []
                if len(fbb) == 4 and _inside(ln["bbox"], fbb, tol=3.0):
                    fid = str(p.get("formula_id"))
                    if fid not in src_fids:
                        src_fids.append(fid)
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
            # visual-v05 RecoveredProseBlock structure
            "semantic_role": role,
            "style_role": role,
            "heading_level": level,
            "reading_order": len(recovered),
            "source_formula_region_id": src_fids[0] if src_fids else None,
            "source_span_ids": ["%s-L%02d" % (src_fids[0] if src_fids
                                              else "PAF", i)
                                for i in range(len(cl))],
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
    # visual-v04: skipped formulas =
    #   (a) candidates that produced ACTUAL recovered paragraphs
    #   (b) ANY formula region overlapping a recovered paragraph bbox --
    #       its content was swallowed prose already rendered as the PAF
    #       target; keeping its SVG would double-render source English
    #       (e.g. PPAT p005 B5 "multi-channel diffusion vector...")
    #   (c) single-line pseudo-formulas (bbox height <= 16pt whose whole
    #       region is plain prose, e.g. PPAT p005 B8 "expert is calculated
    #       by:") -- their content is body text; rendering the SVG would
    #       leak English and truncate the neighbouring recovered region.
    # A candidate that yields zero prose but is a REAL multi-line formula
    # must keep its SVG (skipping it would silently drop real formula
    # content and shift region separators, e.g. 2504 p014 DLP00182
    # boundary).
    rec_boxes = [p.get("bbox") or [] for p in recovered]
    skipped = set()
    for r in page_model.get("regions", []):
        if r.get("type") != "formula":
            continue
        f = str((r.get("payload") or {}).get("formula_id"))
        if not f:
            continue
        bb = (r.get("payload") or {}).get("layout_bbox") or []
        if f in [str(x) for x in fid_list]:
            if any(_bbox_overlap(bb, p.get("bbox") or [])
                   for p in recovered):
                skipped.add(f)
            elif len(bb) == 4 and bb[3] - bb[1] <= 16.0:
                # candidate, no recovered paragraph, but single-line
                # pseudo-formula whose region is plain prose
                rl = _text_lines_in_regions(
                    str(pdf_path), page_idx, [[float(v) for v in bb]])
                if rl and all(_is_plain_prose_line(ln["text"])
                              for ln in rl):
                    skipped.add(f)
                elif rl and all(_NON_PROSE_RE.match(ln["text"].strip())
                                for ln in rl):
                    # page-footer pseudo-formula ("Preprint submitted to
                    # Elsevier") -- renders no body content, skip its SVG
                    skipped.add(f)
            continue
        # (b) non-candidate formulas fully covered by a recovered paragraph
        if any(_bbox_overlap(bb, rb) for rb in rec_boxes):
            skipped.add(f)
            continue
        # (d) page-footer pseudo-formulas (short box, footer-only line):
        # render no body content; keep the footer text via the text layer
        # and drop their SVG (they would otherwise act as a hard separator
        # truncating recovered body prose above them)
        if len(bb) == 4 and bb[3] - bb[1] <= 16.0:
            rl = _text_lines_in_regions(
                str(pdf_path), page_idx, [[float(v) for v in bb]])
            if rl and all(_NON_PROSE_RE.match(ln["text"].strip())
                          for ln in rl):
                skipped.add(f)
    skipped = sorted(skipped)

    # ---- visual-v05: segment-level exclusion -----------------------------
    # a prose-adopted formula often MIXES math rows and annotation prose
    # (e.g. B11: math row "lambda_c = ln(1+exp(theta_c))" + annotation row
    # "where lambda_c > 0 while maintaining differentiability.").  We must
    # render the MATH segments (equation preserved) while excluding the
    # PROSE segments (English not visible).  If EVERY segment is prose the
    # whole formula is skipped (skipped_formulas).
    skip_set = set(skipped)
    prose_excluded_segments: Dict[str, List[str]] = {}
    # segments overlapping RECOVERED prose rows (the PAF clusters) are
    # prose segments; formula rows (non-recovered clusters) stay SVG
    recovered_row_boxes = [ln["bbox"] for cl in prose_clusters
                           for ln in cl]
    for r in page_model.get("regions", []):
        if r.get("type") != "formula":
            continue
        p = r.get("payload") or {}
        fid = str(p.get("formula_id"))
        if fid not in paf_ids:
            continue
        segs = p.get("render_segments", [])
        if not segs:
            # no render segments: the whole region is swallowed prose
            # (e.g. PPAT p004 B26) -> skip entirely
            skip_set.add(fid)
            continue
        excluded = []
        for seg in segs:
            vb = seg.get("render_viewbox") or seg.get("layout_bbox") or []
            if len(vb) != 4:
                continue
            # segment overlaps any recovered PROSE line -> prose segment
            if any(_bbox_overlap(vb, kb) for kb in recovered_row_boxes):
                excluded.append(str(seg.get("segment_id")))
        if excluded and excluded != [str(s.get("segment_id")) for s in segs]:
            # some math segments remain -> keep them rendered
            prose_excluded_segments[fid] = excluded
            skip_set.discard(fid)
        elif excluded:
            # every segment is prose -> whole formula skipped
            skip_set.add(fid)
    skipped = sorted(skip_set)
    return {"recovered": recovered,
            "skipped_formulas": skipped,
            "prose_excluded_segments": prose_excluded_segments,
            "trace": trace, "api_calls": api_calls}


def _bbox_overlap(a, b):
    if len(a) != 4 or len(b) != 4:
        return False
    xo = min(a[2], b[2]) - max(a[0], b[0])
    yo = min(a[3], b[3]) - max(a[1], b[1])
    # yo > 1.0: a segment that only grazes a recovered row (e.g. an inline
    # math glyph whose viewbox crosses the row boundary by ~1.5pt) is still
    # inside the prose row and must be excluded (visual-v05, PPAT B7-S8)
    return xo > 2.0 and yo > 1.0
