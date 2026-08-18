# -*- coding: utf-8 -*-
"""math_token_sequence -- structural math token sequence extractor/comparator.

Three-stage closure for math formulas (no LaTeX reconstruction, no OCR):

  source    : page_model formula regions -> ordered (formula_id, structural
              signature) where the signature is derived from the region's
              *components* (glyph text + bbox).  A component is classified as
              SUPERSCRIPT (raised above baseline), SUBSCRIPT (lowered below
              baseline), or BASE by its bbox geometry.  This is the structural
              ground truth for each formula and is fully text-derived.

  protected : the translation target's ``{{FORMULA_x}}`` token sequence (the
              protect-and-render contract keeps the formula as a placeholder
              whose rendering is driven by the same source components).

  restored final : the final rendered HTML's ``<img class="formula-seg"
              data-formula="Bx">`` images in document order, plus the formula
              region's *render_segments* (the exact component groups passed to
              the SVG renderer).  Because the final SVG is a raster (no text
              layer), the structural truth of "what actually got drawn" is
              recovered by checking that every source superscript / subscript /
              base component's bbox is *covered* by at least one render_segment
              layout bbox.  A source superscript component (e.g. the ``-1`` in
              ``F^{-1}``) that is NOT covered by any render_segment means the
              renderer dropped it -> a structural token loss.

Hard metrics (all must be 0 for a clean page):
  math_token_loss_count, math_token_duplicate_count,
  math_token_order_inversion_count, math_base_symbol_loss_count,
  math_superscript_loss_count, math_subscript_loss_count,
  math_group_structure_mismatch_count.
"""
from __future__ import annotations

import re
import statistics

from final_visible_translation_qa import is_prose_adopted_formula  # noqa: E402

_FORMULA_TOKEN_RE = re.compile(r"\{\{FORMULA_([A-Za-z0-9_]+)\}\}")


# --------------------------------------------------------------------------
# structural signature from source components
# --------------------------------------------------------------------------
def _component_role(bbox, baseline, tol):
    top, bot = bbox[1], bbox[3]
    if top < baseline - tol:
        return "sup"
    if bot > baseline + tol:
        return "sub"
    return "base"


def formula_structural_signature(payload):
    """Return a structural signature for one formula region payload.

    A component is classified SUPERSCRIPT only if it is (a) SMALLER than the
    median glyph height (superscripts/subscripts are set in a reduced size)
    and (b) clearly raised above the main baseline; likewise SUBSCRIPT if
    small and clearly lowered.  Full-size glyphs are always BASE even when
    they sit on a different text line -- this avoids the false positive where
    an entire display-formula line is misread as "raised".

    signature = {
      "n_components": int,
      "base_symbols": set[str],
      "sup_runs": int, "sub_runs": int,
      "has_fraction": bool, "has_large_symbol": bool,
      "components": [ (role, text, bbox), ... ]   # in x,y order
    }
    """
    comps = [c for c in (payload.get("components") or []) if c.get("bbox")]
    if not comps:
        return None
    heights = [c["bbox"][3] - c["bbox"][1] for c in comps]
    med_h = statistics.median(heights) if heights else 6.0
    # baseline = median bottom of the *base-size* glyphs
    base_bottoms = [c["bbox"][3] for c in comps
                    if (c["bbox"][3] - c["bbox"][1]) >= 0.7 * med_h]
    baseline = (statistics.median(base_bottoms) if base_bottoms
                else statistics.median([c["bbox"][3] for c in comps]))
    size_thr = 0.85 * med_h          # smaller than this => likely sup/sub
    raise_thr = max(1.0, 0.35 * med_h)
    ordered = sorted(comps, key=lambda c: (c["bbox"][0], c["bbox"][1]))
    out = []
    base_symbols = set()
    sup_runs = 0
    sub_runs = 0
    prev_role = None
    for c in ordered:
        h = c["bbox"][3] - c["bbox"][1]
        top, bot = c["bbox"][1], c["bbox"][3]
        if h < size_thr and top < baseline - raise_thr:
            role = "sup"
        elif h < size_thr and bot > baseline + raise_thr:
            role = "sub"
        else:
            role = "base"
        txt = (c.get("text") or "").strip()
        # Skip adjacent-prose contamination: formula regions frequently
        # swallow neighbouring English words ("where", "otherwise",
        # "denote"...).  These are NOT part of the formula's math token
        # sequence and are rendered as body text, not inside the formula SVG,
        # so they must not be treated as a math base symbol (would cause a
        # false base_symbol_loss).  Keep single letters / numbers / symbols.
        if re.fullmatch(r"[A-Za-z]{2,}", txt):
            continue
        out.append((role, txt, list(c["bbox"])))
        if role == "base" and txt:
            base_symbols.add(txt)
        if role == "sup" and prev_role != "sup":
            sup_runs += 1
        elif role == "sub" and prev_role != "sub":
            sub_runs += 1
        prev_role = role
    return {
        "n_components": len(ordered),
        "base_symbols": base_symbols,
        "sup_runs": sup_runs,
        "sub_runs": sub_runs,
        "has_fraction": bool(payload.get("contains_fraction")),
        "has_large_symbol": bool(payload.get("contains_large_symbol")),
        "components": out,
    }


def _bbox_covered(bbox, seg_bboxes):
    """True if `bbox` is contained in at least one segment layout bbox."""
    x0, y0, x1, y1 = bbox
    for sb in seg_bboxes:
        if (sb[0] - 0.5) <= x0 and x1 <= (sb[2] + 0.5) and \
           (sb[1] - 0.5) <= y0 and y1 <= (sb[3] + 0.5):
            return True
    return False


def _segment_bboxes(payload):
    segs = payload.get("render_segments") or []
    return [s.get("layout_bbox") for s in segs if s.get("layout_bbox")]


# --------------------------------------------------------------------------
# sequence extraction
# --------------------------------------------------------------------------
def _inside(bbox, outer):
    if not (bbox and outer):
        return False
    return (outer[0] - 1 <= bbox[0] and bbox[2] <= outer[2] + 1 and
            outer[1] - 1 <= bbox[1] and bbox[3] <= outer[3] + 1)


def _center_inside(bbox, outer):
    if not (bbox and outer):
        return False
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    return (outer[0] <= cx <= outer[2]) and (outer[1] <= cy <= outer[3])


def extract_source_formula_sequence(model):
    """Ordered list of formula-region descriptors from the page model.

    Each entry:
      {formula_id, reading_order, bbox, adj_prose(bool), in_figure(bool),
       in_text(bool), absorbed(bool), standalone(bool), signature,
       seg_bboxes}
    `standalone` = a genuine standalone formula the pipeline is expected to
    render as its own formula-seg image (atomic_svg, math_formula, NOT
    formula+adjacent-prose, NOT swallowed inside a text region, NOT inside a
    figure region).  `absorbed` formulas live inside a text region (their
    detector box overlaps a paragraph) and are rendered as part of that text
    block's SVG -- they are intentionally NOT standalone formula-segs, so
    neither the loss metric nor the structural coverage metric applies to them.
    """
    regions = model.get("regions", [])
    figures = [r.get("bbox") for r in regions if r.get("type") == "figure"]
    text_bboxes = [r["payload"].get("bbox") or r["payload"].get("ink_bbox")
                   for r in regions if r.get("type") == "text"]
    text_bboxes = [b for b in text_bboxes if b]
    out = []
    raw = [r for r in regions if r.get("type") == "formula"]
    raw.sort(key=lambda r: (r.get("reading_order", 999),
                            (r["payload"].get("ink_bbox") or
                             r["payload"].get("bbox") or
                             r["payload"].get("layout_bbox") or
                             [0, 0, 0, 0])[1]))
    for r in raw:
        pl = r.get("payload", {})
        fid = pl.get("formula_id")
        bbox = (pl.get("ink_bbox") or pl.get("bbox") or
                pl.get("layout_bbox"))
        adj_prose = bool(pl.get("formula_adjacent_prose"))
        in_figure = any(_inside(bbox, f) for f in figures)
        in_text = any(_center_inside(bbox, t) for t in text_bboxes)
        absorbed = adj_prose or in_text
        render_policy = pl.get("render_policy")
        kind = pl.get("kind")
        standalone = (render_policy == "atomic_svg" and
                      kind in ("math_formula", "inline_formula", None) and
                      not absorbed and not in_figure)
        # prose-adopted formula: the detector swallowed a whole prose
        # paragraph (>= 8 English words) into a "formula" region.  Such a
        # region is intentionally REPLACED by recovered, translated prose
        # (its math is preserved inside the translation via token
        # protection), so it is NOT a standalone math token sequence to
        # preserve -- counting it as a "loss" would be a false positive.
        # Only GENUINE standalone formulas (not prose-adopted) are checked.
        prose_adopted = is_prose_adopted_formula(pl)
        out.append({
            "formula_id": fid,
            "reading_order": r.get("reading_order", 999),
            "bbox": bbox,
            "adj_prose": adj_prose,
            "in_figure": in_figure,
            "in_text": in_text,
            "absorbed": absorbed,
            "standalone": standalone and not prose_adopted,
            "prose_adopted": prose_adopted,
            "signature": formula_structural_signature(pl),
            "seg_bboxes": _segment_bboxes(pl),
        })
    return out


def extract_protected_formula_sequence(trans, model):
    """Ordered list of {{FORMULA_x}} token ids found in translation targets.

    `trans` maps paragraph_id/DLP_id -> target string.  The order follows the
    text regions' reading order (paragraph_id parity preserved).  Returns
    (ordered_ids, referenced_set).
    """
    para_order = {}
    for r in model.get("regions", []):
        if r.get("type") == "text":
            pl = r.get("payload", {})
            pid = pl.get("paragraph_id")
            if pid is not None:
                para_order[pid] = (pl.get("reading_order", 999),
                                   pl.get("anchor_y", 0))
    items = []
    for pid in sorted(trans, key=lambda k: para_order.get(k, (999, 0))):
        t = trans[pid]
        if not isinstance(t, str):
            t = t.get("target") or "" if isinstance(t, dict) else ""
        for m in _FORMULA_TOKEN_RE.findall(t):
            items.append(m)
    referenced = set(items)
    return items, referenced


def _norm_formula_id(raw):
    """Normalize a formula id: strip a leading ``FORMULA_`` prefix and a
    possible ``inline_`` prefix so source ``B7`` matches final ``FORMULA_B7``
    / ``inline_FORMULA_B3`` etc."""
    if not raw:
        return raw
    r = raw.strip()
    if r.startswith("inline_"):
        r = r[len("inline_"):]
    if r.startswith("FORMULA_"):
        r = r[len("FORMULA_"):]
    return r


def extract_final_formula_sequence(html_path):
    """Ordered list of (normalized_id, y0, x0) from the final rendered HTML.

    The final HTML emits two kinds of formula images:

    * **Block formulas** -- ``<img class="formula-seg" data-formula=".."
      data-layout="x0,y0,x1,y1">`` (absolute-positioned).  These carry a real
      layout bbox, so they participate in the visual-order comparison.
    * **Inline formulas** -- ``<span class="formula-inline"
      data-formula="FORMULA_Bx">`` or ``<img src="inline_FORMULA_Bx_N.svg">``
      (flowed within a text run, no ``data-layout``).  They are genuinely
      rendered, so they must count as *present* (no false loss), but they have
      no layout bbox and their order is governed by prose flow -- they must
      NOT participate in the formula-chain order comparison.

    The renderer emits either ``data-formula="B7"`` or
    ``data-formula="FORMULA_B7"``; both normalize to ``B7``.
    """
    try:
        html = open(html_path, "r", encoding="utf-8", errors="replace").read()
    except Exception:
        return []
    out = []
    seen = set()
    # 1) block formulas (have a real layout bbox -> carry position)
    for m in re.finditer(
            r'data-formula="([^"]+)"[^>]*data-layout="([^"]+)"', html):
        fid = _norm_formula_id(m.group(1))
        lay = m.group(2).split(",")
        try:
            x0, y0 = float(lay[0]), float(lay[1])
        except (ValueError, IndexError):
            x0 = y0 = 0.0
        out.append((fid, y0, x0))
        seen.add(fid)
    # 2) inline formulas: data-formula without data-layout (already-normalized
    #    value may be "FORMULA_Bx" or "Bx"); no layout -> position (0,0).
    for m in re.finditer(r'data-formula="([^"]+)"', html):
        fid = _norm_formula_id(m.group(1))
        if fid in seen:
            continue
        out.append((fid, 0.0, 0.0))
        seen.add(fid)
    # 3) inline image src fallback: src="inline_FORMULA_Bx_N.svg"
    for m in re.finditer(
            r'inline_FORMULA_([A-Za-z0-9_]+?)(?:_\d+)?\.svg', html):
        fid = _norm_formula_id(m.group(1))
        if fid in seen:
            continue
        out.append((fid, 0.0, 0.0))
        seen.add(fid)
    if not out:
        # last-resort fallback: just ids in emission order
        for fid in re.findall(r'data-formula="([^"]+)"', html):
            out.append((_norm_formula_id(fid), 0.0, 0.0))
    return out


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------
def compare_math_sequences(source_seq, protected_seq, final_seq,
                           skipped_fids=None):
    """Return the hard-metric dict for one page.

    source_seq   : output of extract_source_formula_sequence
    protected_seq : (ordered_ids, referenced_set) from extract_protected_...
    final_seq    : ordered data-formula ids from extract_final_formula_sequence
    skipped_fids : formula ids the pipeline intentionally did NOT render as
                   standalone (they were replaced by recovered, translated
                   prose via the PAF recovery).  Counting them as a "loss"
                   would be a false positive -- they are not math-token
                   sequences to preserve.
    """
    prot_ids, prot_ref = protected_seq
    src_by_id = {s["formula_id"]: s for s in source_seq}
    src_ids = [s["formula_id"] for s in source_seq]
    # final_seq is list of (id, y0, x0); keep ids and visual order
    fin_items = final_seq
    fin_ids = [it[0] for it in fin_items]

    final_set = set(fin_ids)
    src_set = set(src_ids)

    metrics = {
        "math_token_loss_count": 0,
        "math_token_duplicate_count": 0,
        "math_token_order_inversion_count": 0,
        "math_base_symbol_loss_count": 0,
        "math_superscript_loss_count": 0,
        "math_subscript_loss_count": 0,
        "math_group_structure_mismatch_count": 0,
    }
    detail = {
        "loss": [], "duplicate": [], "order_inversion": [],
        "base_loss": [], "sup_loss": [], "sub_loss": [], "group_mismatch": [],
    }

    # --- token loss: standalone source formula missing from final ----------
    skipped = set(skipped_fids or [])
    for s in source_seq:
        if not s["standalone"]:
            continue
        if s["formula_id"] in skipped:
            # replaced by recovered prose (PAF) -- not a math-token loss
            continue
        if s["formula_id"] not in final_set:
            metrics["math_token_loss_count"] += 1
            detail["loss"].append(s["formula_id"])

    # --- token duplicate: final formula_id with no source region ----------
    for fid in fin_ids:
        if fid not in src_set:
            metrics["math_token_duplicate_count"] += 1
            detail["duplicate"].append(fid)

    # --- order inversion: distinct formula_id order by VISUAL position ----
    # Only meaningful when the final HTML exposes real positions (data-layout
    # on block formulas).  Inline formulas flow in emission order and carry no
    # layout bbox, so comparing emission orders across generators is
    # unreliable -> skip (do not flag) when final positions are unavailable.
    final_has_pos = any((it[1] != 0.0 or it[2] != 0.0) for it in fin_items)
    if final_has_pos:
        # Only GENUINE block formulas participate in the order check:
        #   * in the SOURCE: the formula must carry a real render-segment
        #     layout bbox (seg_bboxes non-empty) -- i.e. the pipeline planned
        #     to render it as a standalone block image;
        #   * in the FINAL: the rendered image must carry a real layout bbox
        #     (y0,x0 != 0) -- i.e. it is a block formula-seg, not an inline
        #     image flowed inside prose.
        # Prose-adopted (PAF) formulas, absorbed formulas, and inline formulas
        # are governed by prose flow, not the formula chain, so including them
        # produces false inversions (their rendered position does not follow
        # the source region bbox).  Restricting to block-formula-in-both keeps
        # the order check meaningful without false positives.
        standalone_ids = {s["formula_id"] for s in source_seq
                          if s["standalone"]}
        src_vis = sorted([s for s in source_seq
                          if s["standalone"] and s["formula_id"] in final_set
                          and (s.get("seg_bboxes") or [])],
                         key=lambda s: ((s["bbox"] or [0, 0, 0, 0])[1],
                                        (s["bbox"] or [0, 0, 0, 0])[0]))
        # final visual order: by (y0, x0) from data-layout, distinct ids,
        # only formulas that actually carry a layout position (blocks).
        fin_vis = sorted([it for it in fin_items
                          if it[0] in standalone_ids
                          and (it[1] != 0.0 or it[2] != 0.0)],
                         key=lambda it: (it[1], it[2]))

        def _dedup_seq(ids):
            out = []
            for x in ids:
                if x not in out:
                    out.append(x)
            return out

        src_vis_ids = _dedup_seq([s["formula_id"] for s in src_vis])
        fin_vis_ids = _dedup_seq([it[0] for it in fin_vis])
        # compare only the ids that are block formulas in BOTH source and
        # final -- avoids KeyError and ignores formulas that are block on one
        # side only (those cannot have a consistent chain order).
        common = [x for x in src_vis_ids if x in set(fin_vis_ids)]
        src_order = [x for x in src_vis_ids if x in set(common)]
        fin_order = [x for x in fin_vis_ids if x in set(common)]
        if src_order and fin_order and src_order != fin_order:
            pos = {x: i for i, x in enumerate(src_order)}
            inv = 0
            for i in range(len(fin_order)):
                for j in range(i + 1, len(fin_order)):
                    if pos[fin_order[i]] > pos[fin_order[j]]:
                        inv += 1
            if inv > 0:
                metrics["math_token_order_inversion_count"] = inv
                detail["order_inversion"] = {
                    "source_visual": src_order,
                    "final_visual": fin_order,
                }

    # --- structural closure (source components vs rendered segments) -------
    for s in source_seq:
        sig = s["signature"]
        if not sig:
            continue
        # absorbed formulas are rendered as part of a text-region SVG, not as
        # a standalone formula-seg, so the segment-coverage test does not
        # apply (would be a false positive).  Prose-adopted formulas are
        # replaced by recovered, translated prose, so their component
        # coverage is likewise not a math-token loss.
        if s.get("absorbed") or s.get("prose_adopted"):
            continue
        segs = s["seg_bboxes"]
        # base symbol loss: a base component dropped from all segments
        for role, txt, bbox in sig["components"]:
            if role == "base":
                if not _bbox_covered(bbox, segs):
                    metrics["math_base_symbol_loss_count"] += 1
                    detail["base_loss"].append((s["formula_id"], txt))
            elif role == "sup":
                if not _bbox_covered(bbox, segs):
                    metrics["math_superscript_loss_count"] += 1
                    detail["sup_loss"].append((s["formula_id"], txt))
            elif role == "sub":
                if not _bbox_covered(bbox, segs):
                    metrics["math_subscript_loss_count"] += 1
                    detail["sub_loss"].append((s["formula_id"], txt))
        # group structure mismatch: source has superscript/subscript runs but
        # the rendered segments cover NONE of the raised/lowered components.
        rendered_sup = any(_bbox_covered(b, segs)
                           for role, _, b in sig["components"]
                           if role == "sup")
        rendered_sub = any(_bbox_covered(b, segs)
                           for role, _, b in sig["components"]
                           if role == "sub")
        if sig["sup_runs"] > 0 and not rendered_sup:
            metrics["math_group_structure_mismatch_count"] += 1
            detail["group_mismatch"].append((s["formula_id"], "sup"))
        if sig["sub_runs"] > 0 and not rendered_sub:
            metrics["math_group_structure_mismatch_count"] += 1
            detail["group_mismatch"].append((s["formula_id"], "sub"))

    return metrics, detail


def decision(metrics):
    return "pass" if all(v == 0 for v in metrics.values()) else "fail"
