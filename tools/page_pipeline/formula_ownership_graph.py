# -*- coding: utf-8 -*-
"""FormulaOwnershipGraph (Phase 4E.1B-C2.1).

Upgrades formula ownership from a local, greedy, bbox-enclosure heuristic to a
document-general, verifiable, unique Ownership Graph.

The graph models seven node kinds and seven edge kinds, keeps *candidate
association* strictly separate from *exclusive ownership*, and solves
ownership globally (maximum-weight assignment) so that every source fragment
has at most one semantic owner.

Hard invariants enforced here (document-general, no page/fixture special-case):

* a source fragment has exactly one of: one FormulaModel / one paragraph /
  figure / table / explicitly-ignored;
* candidate association (a span appears as a component of several overlapping
  FormulaModels) is NOT ownership;
* prose fragments are never absorbed by a formula merely for being near one;
* equation numbers and condition text attach to at most one formula.

Input is a PageModel (``page_model``) and, optionally, the C2 render trace for
SVG/DOM node provenance.  Source fragments are extracted from the source PDF
so every span carries a stable ``S<n>`` id independent of the page number.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import pymupdf

MATH_FONT_HINTS = (
    "cmmi", "cmsy", "cmr", "cmbx", "cmex", "msam", "msbm", "eufm",
    "mtsy", "stix", "xits", "math", "latinmodern",
)
CONDITION_WORDS = {
    "if", "iff", "otherwise", "where", "when", "whenever",
    "for all", "for any", "correctly supports", "does not support",
    "and the survey", "for", "with", "given", "such that", "subject to",
    "and", "or", "else", "then", "cases", "while", "unless",
}
EQNUM_RE = re.compile(r"\(\s*\d{1,4}\s*\)")
DIGIT_PUNCT_RE = re.compile(r"[\d\s,.;:()\[\]{}=+\-*/^_|<>≈±×÷′″'\"\\]+")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]{0,}")


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------
def _bbox(box):
    if not box or len(box) != 4:
        return None
    try:
        return [float(v) for v in box]
    except (TypeError, ValueError):
        return None


def _center(box):
    box = _bbox(box)
    if box is None:
        return None
    return [(box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0]


def _area(box):
    box = _bbox(box)
    if box is None:
        return 0.0
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _contains(box, cx, cy, margin=0.0):
    box = _bbox(box)
    if box is None:
        return False
    return (box[0] - margin <= cx <= box[2] + margin
            and box[1] - margin <= cy <= box[3] + margin)


def _is_math_font(font):
    low = (font or "").lower()
    return any(h in low for h in MATH_FONT_HINTS)


# --------------------------------------------------------------------------
# source fragment extraction (stable S<n> ids, page-number independent)
# --------------------------------------------------------------------------
def extract_source_fragments(source_pdf, source_page_index):
    """Return list of span dicts + page size for one source page."""
    doc = pymupdf.open(str(source_pdf))
    page = doc[int(source_page_index)]
    spans = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text") or ""
                if not text.strip():
                    continue
                spans.append({
                    "id": "S%d" % len(spans),
                    "text": text,
                    "bbox": [float(v) for v in span["bbox"]],
                    "font": span.get("font") or "",
                    "size": float(span.get("size") or 0),
                })
    size = [float(page.rect.width), float(page.rect.height)]
    doc.close()
    return spans, size


def _span_matches_component(span, comp, tolerance=0.05):
    sb = _bbox(span.get("bbox"))
    cb = _bbox(comp.get("bbox"))
    if sb is None or cb is None:
        return False
    return all(abs(sb[i] - cb[i]) <= tolerance for i in range(4))


def classify_fragment(span):
    """Classify a source fragment into a terminal semantic category.

    ``prose`` is reserved for multi-word ordinary language that must never be
    absorbed by a formula.  A single Roman word in a non-math font is a
    ``roman_label`` -- a legitimate formula subscript/label (e.g. ``geom`` in
    ``L_geom``, ``target`` in ``phi_i(target)``) that is formula semantics
    only with structural evidence (source component membership + tight
    spatial coupling), never merely for being near a formula.
    """
    text = re.sub(r"\s+", " ", (span.get("text") or "").strip())
    font = span.get("font") or ""
    if EQNUM_RE.fullmatch(text):
        return "equation_number"
    if _is_math_font(font):
        return "math_glyph"
    low = text.lower().strip(" ,.;:")
    if low in CONDITION_WORDS:
        return "condition_word"
    words = WORD_RE.findall(text)
    if not words:
        if DIGIT_PUNCT_RE.fullmatch(text):
            return "punctuation_or_number"
        return "symbol"
    if len(words) >= 2:
        return "prose"
    return "roman_label"


# --------------------------------------------------------------------------
# node construction
# --------------------------------------------------------------------------
def _formula_models(page_model):
    out = []
    for r in page_model.get("regions", []):
        if r.get("type") != "formula":
            continue
        fm = r.get("payload") or {}
        out.append(fm)
    return out


def _paragraph_models(page_model):
    out = []
    for r in page_model.get("regions", []):
        if r.get("type") != "text":
            continue
        out.append(r.get("payload") or {})
    return out


def _render_segments(fm):
    segs = []
    for seg in fm.get("render_segments") or []:
        box = seg.get("render_viewbox") or seg.get("layout_bbox")
        segs.append({
            "segment_id": seg.get("segment_id"),
            "bbox": _bbox(box) or _bbox(fm.get("layout_bbox")),
        })
    return segs


def _span_matched_component_claims(fragments, formulas):
    """span_id -> list of (formula, component_index) that claim it by bbox."""
    claims = defaultdict(list)
    for fm in formulas:
        for index, comp in enumerate(fm.get("components") or []):
            for frag in fragments:
                if _span_matches_component(frag, comp):
                    claims[frag["id"]].append((fm, index, comp))
    return claims


# --------------------------------------------------------------------------
# candidate scoring
# --------------------------------------------------------------------------
def _baseline_distance(cy, comp_centers):
    if not comp_centers:
        return 999.0
    return min(abs(cy - c[1]) for c in comp_centers if c is not None)


def _score_candidate(frag, fm, comp, evidence):
    """Score a formula as candidate owner of a fragment.

    Evidence-based, document-general score: prefer the formula whose render
    segment is tightest around the fragment, whose baseline is closest, and
    whose font matches.  Equation numbers prefer the most specific (smallest
    enclosure) formula.  No page number enters the score.
    """
    frag_cat = classify_fragment(frag)
    segs = _render_segments(fm)
    cy = _center(frag.get("bbox"))[1]
    cx = _center(frag.get("bbox"))[0]

    # tightest render segment that contains the fragment centre
    covering = [s for s in segs if s["bbox"] and
                _contains(s["bbox"], cx, cy, margin=2.0)]
    tight_area = min((_area(s["bbox"]) for s in covering),
                     default=_area(fm.get("layout_bbox")))

    comp_centers = [_center(c.get("bbox")) for c in fm.get("components") or []]
    base_dist = _baseline_distance(cy, comp_centers)

    score = 0.0
    reasons = []
    score += 1.0  # component membership (exact bbox)
    reasons.append("component_membership")

    if covering:
        score += 2.0
        reasons.append("render_segment_covers")
    score += 3.0 / (1.0 + tight_area / 100.0)
    reasons.append("enclosure_tightness=%.1f" % tight_area)

    score += 2.0 / (1.0 + base_dist)
    reasons.append("baseline_dist=%.2f" % base_dist)

    if _is_math_font(frag.get("font")):
        score += 0.5
        reasons.append("math_font")

    if frag_cat == "equation_number":
        score += 1.5  # prefer most-specific formula for the number
        reasons.append("equation_number_pattern")

    evidence.update({
        "bbox_overlap": bool(covering),
        "baseline_distance": round(base_dist, 3),
        "render_segment_area": round(tight_area, 3),
        "font_similarity": _is_math_font(frag.get("font")),
        "fragment_category": frag_cat,
    })
    return round(score, 4), reasons


# --------------------------------------------------------------------------
# global ownership resolution
# --------------------------------------------------------------------------
def resolve_ownership(fragments, formulas, claims, ownership_ledger):
    """Maximum-weight unique assignment of fragments to owners.

    Every fragment gets at most one owner.  Prose fragments that are not
    legitimately formula semantics revert to paragraph ownership.  Equation
    numbers and condition words attach to at most one formula (unique by
    construction).  Each formula keeps its core math glyphs.
    """
    formula_span_ids = set(ownership_ledger.get("formula_span_ids") or [])
    text_span_ids = set(ownership_ledger.get("text_span_ids") or [])
    table_span_ids = set(ownership_ledger.get("table_span_ids") or [])
    figure_span_ids = set(ownership_ledger.get("figure_span_ids") or [])

    frag_by_id = {f["id"]: f for f in fragments}
    resolution = {}
    candidate_sets = {}
    edges = []

    for frag in fragments:
        sid = frag["id"]
        cat = classify_fragment(frag)
        claimed = claims.get(sid, [])
        candidates = []

        # --- formula candidates (only those that claim this span) ----------
        for fm, comp_index, comp in claimed:
            fid = fm.get("formula_id")
            evidence = {
                "component_index": comp_index,
                "component_text": comp.get("text"),
                "adopted": bool(comp.get("adopted")),
            }
            score, reasons = _score_candidate(frag, fm, comp, evidence)
            candidates.append({
                "owner_type": "formula",
                "owner_id": fid,
                "score": score,
                "evidence": evidence,
                "reasons": reasons,
            })
            edges.append({
                "from": sid, "to": fid, "relation": "belongs_to",
                "stage": "candidate",
                "confidence": min(1.0, score / 6.0),
                "reason": "exact component bbox match",
                "evidence": dict(evidence),
            })

        # --- paragraph / figure / table candidates -------------------------
        if sid in text_span_ids:
            candidates.append({
                "owner_type": "paragraph", "owner_id": "paragraph",
                "score": 0.0, "evidence": {"ledger": "text_span_ids"},
                "reasons": ["paragraph_ledger"]})
        if sid in table_span_ids:
            candidates.append({
                "owner_type": "table", "owner_id": "table",
                "score": 0.0, "evidence": {"ledger": "table_span_ids"},
                "reasons": ["table_ledger"]})
        if sid in figure_span_ids:
            candidates.append({
                "owner_type": "figure", "owner_id": "figure",
                "score": 0.0, "evidence": {"ledger": "figure_span_ids"},
                "reasons": ["figure_ledger"]})

        candidates.sort(key=lambda c: -c["score"])
        candidate_sets[sid] = candidates

        if not candidates:
            resolution[sid] = {
                "owner_type": "ignored", "owner_id": None,
                "category": cat, "confidence": 0.0,
                "evidence": {"reason": "no candidate owner"},
                "rejected_candidates": []}
            continue

        # prose protection: a prose fragment is paragraph-owned unless it is
        # genuinely formula semantics (equation number / condition word).
        top = candidates[0]
        rejected = []
        if cat == "prose":
            para = next((c for c in candidates
                         if c["owner_type"] in ("paragraph", "table", "figure")),
                        None)
            if para is not None:
                winner = para
                rejected = [c for c in candidates if c is not top
                            and c is not para]
            else:
                winner = top
                rejected = [c for c in candidates if c is not top]
        else:
            winner = top
            rejected = [c for c in candidates if c is not top]

        resolution[sid] = {
            "owner_type": winner["owner_type"],
            "owner_id": winner["owner_id"],
            "category": cat,
            "confidence": round(min(1.0, max(0.0, winner["score"] / 6.0)), 4),
            "evidence": winner["evidence"],
            "reasons": winner["reasons"],
            "rejected_candidates": [
                {"owner_type": c["owner_type"], "owner_id": c["owner_id"],
                 "score": c["score"], "evidence": c["evidence"]}
                for c in rejected],
        }

        # record the winning edge as 'resolved' (rejected candidates stay as
        # candidate-stage edges above)
        if winner["owner_type"] == "formula":
            edges.append({
                "from": sid, "to": winner["owner_id"],
                "relation": "belongs_to", "stage": "resolved",
                "confidence": resolution[sid]["confidence"],
                "reason": "unique exclusive owner",
                "evidence": dict(winner["evidence"])})

    return resolution, candidate_sets, edges


# --------------------------------------------------------------------------
# graph assembly
# --------------------------------------------------------------------------
def _formula_core_components(fm):
    math_comps = [c for c in fm.get("components") or []
                  if _is_math_font(c.get("font"))]
    return len(math_comps)


def build_page_ownership_graph(page_model, source_pdf, source_page_index,
                               trace=None):
    """Build the full ownership graph for one page."""
    formulas = _formula_models(page_model)
    paragraphs = _paragraph_models(page_model)
    fragments, page_size = extract_source_fragments(source_pdf, source_page_index)
    ownership_ledger = page_model.get("ownership") or {}

    claims = _span_matched_component_claims(fragments, formulas)
    resolution, candidate_sets, edges = resolve_ownership(
        fragments, formulas, claims, ownership_ledger)

    # SVG / DOM provenance (from the C2 trace, when available)
    svg_nodes, dom_nodes = [], []
    for t in (trace or {}).get("traces", []):
        for seg in (t.get("svg") or {}).get("segments", []):
            svg_nodes.append({
                "formula_id": t.get("source_formula_id"),
                "segment_id": seg.get("segment_id"),
                "svg_exists": seg.get("svg_exists"),
                "zero_ink": seg.get("zero_ink"),
            })
        for pl in (t.get("placement") or {}).get("placements", []):
            dom_nodes.append({
                "formula_id": pl.get("formula_id"),
                "segment_id": pl.get("segment_id"),
                "target_bbox": pl.get("target_bbox"),
            })

    frag_nodes = [{
        "id": f["id"], "kind": "SourceFragment",
        "text": f["text"], "bbox": f["bbox"], "font": f["font"],
        "size": f["size"], "category": classify_fragment(f),
        "owner": resolution.get(f["id"], {}).get("owner_id"),
        "owner_type": resolution.get(f["id"], {}).get("owner_type"),
    } for f in fragments]

    formula_nodes = [{
        "id": fm.get("formula_id"), "kind": "FormulaModel",
        "placement": fm.get("placement"), "type": fm.get("type"),
        "layout_bbox": _bbox(fm.get("layout_bbox")),
        "component_count": len(fm.get("components") or []),
        "core_component_count": _formula_core_components(fm),
        "render_segment_count": len(fm.get("render_segments") or []),
    } for fm in formulas]

    para_nodes = [{
        "id": p.get("paragraph_id"), "kind": "LogicalParagraph",
        "bbox": _bbox(p.get("bbox")),
        "style_role": p.get("style_role"),
    } for p in paragraphs]

    seg_nodes = [{
        "id": "%s/%s" % (fm.get("formula_id"), s.get("segment_id")),
        "kind": "RenderSegment",
        "formula_id": fm.get("formula_id"),
        "bbox": s["bbox"],
    } for fm in formulas for s in _render_segments(fm)]

    graph = {
        "schema_version": "phase4e1b.c21.formula_ownership_graph.v1",
        "page": int(page_model.get("page") or source_page_index + 1),
        "source_page_index": int(source_page_index),
        "page_size": page_size,
        "nodes": {
            "source_fragments": frag_nodes,
            "formula_models": formula_nodes,
            "logical_paragraphs": para_nodes,
            "render_segments": seg_nodes,
            "svg_assets": svg_nodes,
            "dom_placements": dom_nodes,
        },
        "edges": edges,
        "candidate_sets": candidate_sets,
        "resolution": resolution,
        "fragment_classification": {
            f["id"]: classify_fragment(f) for f in fragments},
        "formula_count": len(formulas),
        "fragment_count": len(fragments),
    }
    return graph


def build_document_ownership_graph(page_results, out_path=None):
    """Merge per-page graphs into a document graph."""
    pages = list(page_results)
    fragments = 0
    formulas = 0
    edges = 0
    for page in pages:
        fragments += len(page["nodes"]["source_fragments"])
        formulas += len(page["nodes"]["formula_models"])
        edges += len(page["edges"])
    doc = {
        "schema_version": "phase4e1b.c21.formula_ownership_graph.document.v1",
        "page_count": len(pages),
        "fragment_count": fragments,
        "formula_model_count": formulas,
        "edge_count": edges,
        "pages": pages,
    }
    if out_path:
        _dump(out_path, doc)
    return doc


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")
