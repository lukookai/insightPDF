# -*- coding: utf-8 -*-
"""Unified PageModel for Phase 4A.

PDF page -> regions (text / table / formula / figure / image) with a single
owner per source object.

* source objects: text spans (each tagged with a unique ``source_object_id``),
  vector drawings and raster images of the page.
* region payloads are ADAPTERS only - they reuse the existing TableModel
  (tools/table_reconstruction + tools/table_html_render) and FormulaModel
  (tools/formula_html_render) without copying their logic.
* ownership priority: table (DocLayout ROI) > formula (composed FormulaModels)
  > figure/image > text.  A span owned by exactly one owner.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "tools" / "table_html_render",
          REPO / "tools" / "formula_html_render",
          REPO / "tools" / "layout_direct",
          REPO / "tools" / "table_reconstruction",
          REPO / "tools" / "table_geometry_debug",
          REPO / "实现源码" / "pdf_translator"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from formula_compose import compose_blocks  # noqa: E402

# Phase 4C.2R.1: formula SVG crop safety.  A segment's render_viewbox must
# cover ALL of its ink (components, including adopted condition text and
# equation numbers) plus a safety margin ONLY where ink approaches the crop
# edge.  B12's adopted "otherwise" extended 6.8pt past the layout bbox and
# its tail was cropped out of the final PDF; p003 B10's condition rows and
# p011's "(15)" equation numbers sat entirely outside their segment boxes.
CROP_SAFETY_PT = 1.5   # pt safety margin applied at ink-near-edge sides
CROP_NEAR_PT = 0.05    # float tolerance


def _union_boxes(boxes):
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    return [x0, y0, x1, y1]


def _formula_crop_viewboxes(formulas):
    """Fill seg['render_viewbox'] for every formula with components.

    Each component bbox is assigned to the segment with the largest
    intersection (orphans -- condition rows, equation numbers outside the
    segment union -- go to the nearest segment centre).  The viewBox is the
    union of the segment layout box and its assigned ink, expanded by
    CROP_SAFETY_PT on every side where ink sits at/near the box edge.
    Segments whose ink is fully inside keep their layout box untouched.
    """
    for fm in formulas:
        if fm.get("placement") == "inline":
            continue  # inline crops stay inside the text flow (no reflow)
        segs = fm.get("render_segments") or []
        comps = fm.get("components") or []
        if not segs or not comps:
            continue
        bases = [[float(v) for v in (seg.get("layout_bbox") or [0, 0, 0, 0])]
                 for seg in segs]
        extra = [[] for _ in segs]
        for c in comps:
            cb = [float(v) for v in c.get("bbox", [])]
            if len(cb) != 4 or cb[2] <= cb[0] or cb[3] <= cb[1]:
                continue
            best = None
            best_area = 0.0
            best_d = None
            for i, sb in enumerate(bases):
                ix = min(sb[2], cb[2]) - max(sb[0], cb[0])
                iy = min(sb[3], cb[3]) - max(sb[1], cb[1])
                area = max(ix, 0.0) * max(iy, 0.0)
                if area > 0.0 and area > best_area:
                    best, best_area = i, area
            if best is None:
                # orphan (no intersection): prefer the segment on the SAME
                # visual row (y-range overlap) closest in x -- an equation
                # number "(15)" at the bottom-right of a multi-row formula
                # must join its own row (B1-S6), not a taller-but-farther
                # centre (nearest-centre wrongly picked S4's centre).
                ccx = (cb[0] + cb[2]) / 2.0
                ccy = (cb[1] + cb[3]) / 2.0
                row = [i for i, sb in enumerate(bases)
                       if min(sb[3], cb[3]) - max(sb[1], cb[1]) > 1.0]
                pool = row if row else list(range(len(bases)))
                best_d = None
                for i in pool:
                    scx = (bases[i][0] + bases[i][2]) / 2.0
                    d = abs(ccx - scx)
                    if best_d is None or d < best_d:
                        best, best_d = i, d
                if best is None:
                    continue
            if best is not None:
                extra[best].append(cb)
        for seg, base, extras in zip(segs, bases, extra):
            if not extras:
                continue
            ex0 = min(b[0] for b in extras)
            ey0 = min(b[1] for b in extras)
            ex1 = max(b[2] for b in extras)
            ey1 = max(b[3] for b in extras)
            rv = [base[0], base[1], base[2], base[3]]
            if ex0 - base[0] < CROP_SAFETY_PT:
                rv[0] = ex0 - CROP_SAFETY_PT
            if ey0 - base[1] < CROP_SAFETY_PT:
                rv[1] = ey0 - CROP_SAFETY_PT
            if base[2] - ex1 < CROP_SAFETY_PT:
                rv[2] = ex1 + CROP_SAFETY_PT
            if base[3] - ey1 < CROP_SAFETY_PT:
                rv[3] = ey1 + CROP_SAFETY_PT
            seg["render_viewbox"] = [round(v, 3) for v in rv]


_DOCLAYOUT_DETECTOR = None


def extract_source_objects(pdf, page_idx):
    """spans (with unique id), drawings, images of one page."""
    doc = pymupdf.open(pdf)
    pg = doc[page_idx]
    spans = []
    for block in pg.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "")
                if not t.strip():
                    continue
                bbox = [float(v) for v in span["bbox"]]
                w = max(bbox[2] - bbox[0], 1e-6)
                h = max(bbox[3] - bbox[1], 1e-6)
                # vertical (rotated) text: arXiv sidebars etc. are reported
                # by PyMuPDF as one tall span.  Its huge bbox would blow up
                # cluster_lines tolerance and pollute body paragraphs
                # (p001 DLP00005 531pt).  Tag it so paragraph reconstruction
                # keeps it as its own paragraph.
                vertical = h > 30.0 and h > 2.5 * w
                spans.append({
                    "id": "S%d" % len(spans),
                    "text": t,
                    "bbox": bbox,
                    "font": span.get("font"),
                    "size": float(span.get("size") or 0),
                    "vertical": vertical,
                })
    drawings = []
    for d in pg.get_drawings():
        r = d.get("rect")
        if r is None:
            continue
        drawings.append({"id": "D%d" % len(drawings), "type": d["type"],
                         "bbox": [float(v) for v in r],
                         "width": float(d.get("width") or 0)})
    images = []
    for iref in pg.get_images(full=True):
        for r in pg.get_image_rects(iref[0]):
            images.append({"xref": iref[0],
                           "bbox": [float(v) for v in r]})
    page_w, page_h = float(pg.rect.width), float(pg.rect.height)
    media_box = [float(v) for v in pg.mediabox]
    crop_box = [float(v) for v in pg.cropbox]
    rotation = int(pg.rotation or 0)
    doc.close()
    return spans, drawings, images, page_w, page_h, \
        media_box, crop_box, rotation


def _bbox_contains(big, small, margin=1.0):
    return (big[0] - margin <= small[0] and big[2] + margin >= small[2]
            and big[1] - margin <= small[1] and big[3] + margin >= small[3])


def _formula_multi_row(fm, enc_h, main_size):
    """True when the formula is visually multi-row (cases / piecewise /
    stacked equations) even if the composer mislabelled it inline.

    Evidence: enclosure height clearly exceeds one text line, or the
    component y-centres cluster into two or more distinct rows.
    """
    if main_size > 0 and enc_h > main_size * 2.2:
        return True
    comps = fm.get("components") or []
    if len(comps) < 4:
        return False
    cys = sorted((c.get("bbox") or [0, 0, 0, 0])[1]
                 + (c.get("bbox") or [0, 0, 0, 0])[3]
                 for c in comps)
    if not cys:
        return False
    mid = sum(cys) / len(cys)
    above = [y for y in cys if y < mid - main_size * 0.4]
    below = [y for y in cys if y > mid + main_size * 0.4]
    return bool(above) and bool(below)


def _bbox_intersect(a, b, margin=0.0):
    return not (a[2] < b[0] - margin or a[0] > b[2] + margin
                or a[3] < b[1] - margin or a[1] > b[3] + margin)


def _span_matches_component(span, comp):
    sb = span["bbox"]
    cb = comp["bbox"]
    return all(abs(sb[i] - cb[i]) <= 0.05 for i in range(4))


def _doclayout_regions(pdf, page_idx, out_dir):
    """Run Direct DocLayout; returns list of region dicts or [] on failure."""
    try:
        from direct_doclayout import DirectDocLayout
        # reuse a warmed model cache if present (avoids re-download)
        out_dir = Path(out_dir)
        cache = out_dir / "_babeldoc_cache"
        warm = REPO / "runs" / "fyresults" / "recon_2504_p13" / "_babeldoc_cache"
        if warm.exists() and not cache.exists():
            cache = warm
        global _DOCLAYOUT_DETECTOR
        if _DOCLAYOUT_DETECTOR is None:
            _DOCLAYOUT_DETECTOR = DirectDocLayout(cache_home=cache)
        det = _DOCLAYOUT_DETECTOR
        data = det.detect_page(Path(pdf).resolve(), page_idx)
        return data.get("regions", [])
    except Exception as exc:  # noqa: BLE001
        print("  [page_model] DocLayout skipped:", type(exc).__name__, exc)
        return []


def _compose_formulas(spans, drawings, images, page_w, page_h):
    """FormulaModels via the existing composer (adapter)."""
    try:
        return compose_blocks(spans, drawings, images, page_w, page_h)
    except Exception as exc:  # noqa: BLE001
        print("  [page_model] formula compose failed:", exc)
        return []


_MATH_FONT_HINTS = ("cmmi", "cmsy", "cmr", "cmex", "msam", "msbm",
                    "eufm", "mtsy", "stix", "xits", "math", "latinmodern")


def _is_math_font(font):
    low = (font or "").lower()
    return any(h in low for h in _MATH_FONT_HINTS)


CODE_EXPR_RE = re.compile(
    r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*\d+([,.]\d+)?"
    r"(\s*[,;]\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*\d+([,.]\d+)?)*\s*$")
CODE_ASSIGN_SEARCH_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:\s+_[A-Za-z0-9_]+)*\s*=\s*\d+(?:\.\d+)?\b")


def classify_code_expressions(formulas):
    """Adapter-level context filter: math_formula vs code_expression.

    A composed formula whose components use NO math font is a CODE expression
    or an ordinary text line that the detector over-captured (e.g. p13 bullet
    params ``convolution_layer = 6, as Digest-Based Feed-`` in NimbusRomNo9L),
    not a formula to protect as SVG.  Real TeX formulas always use CM/STIX
    math fonts (CMMI/CMSY/CMEX/...), so "no math font -> text flow" is a safe
    adapter-level demotion.  Returns the set of formula_ids to demote.
    """
    code_ids = set()
    for fm in formulas:
        comps = fm.get("components", [])
        if not comps:
            continue
        fonts = [c.get("font") or "" for c in comps]
        if any(_is_math_font(f) for f in fonts):
            continue  # real math (CM/STIX glyphs)
        raw = (fm.get("raw_component_text")
               or fm.get("source_text") or "").strip()
        mono = any("mono" in f.lower() or "courier" in f.lower() for f in fonts)
        # Only positive code evidence demotes to CodeRun.  Punctuation inside
        # method names (Vanilla+Skeleton, LLM×MapReduce-V2) is not evidence.
        if mono or CODE_ASSIGN_SEARCH_RE.search(raw) or re.search(
                r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b", raw):
            code_ids.add(fm["formula_id"])
    return code_ids


def _load_table_payload(page_idx, out_dir, table_model_path=None):
    """Reuse an existing translated TableModel if available (adapter)."""
    if table_model_path and Path(table_model_path).exists():
        try:
            return json.loads(Path(table_model_path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    out_dir = Path(out_dir)
    cands = sorted(out_dir.glob("page_%03d_table_translated_model.json"
                                 % (page_idx + 1)))
    cands += sorted(out_dir.glob("page_%03d_table_model*.json"
                                 % (page_idx + 1)))
    # fall back to the known Phase 2B experiment dir
    # The document batch intentionally does not depend on a manually named
    # validation-page cache.  Callers may still pass table_model_path for the
    # frozen single-page Phase 4B.1 runner.
    for p in cands:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
    return None


def _auto_table_payload(pdf, page_idx, roi, out_dir, table_index=0):
    """DocLayout ROI + existing reconstruction -> extended TableModel.

    This is orchestration only: geometry extraction, reconstruction and the
    TableModel schema are delegated to the frozen table pipeline.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / ("page_%03d_table_%02d_model.json"
                            % (page_idx + 1, table_index + 1))
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    from path_geometry import extract_geometry
    from reconstruct import build_table_model
    from run_render_zh import extend_model
    geo = extract_geometry(Path(pdf), page_idx + 1)
    input_data = {
        "bbox": [float(v) for v in roi],
        "texts": geo["texts"],
        "horizontal_lines": geo["horizontal_lines"],
        "vertical_lines": geo["vertical_lines"],
        "rectangles": [({"bbox": [r["x0"], r["y0"], r["x1"], r["y1"]]}
                        if "bbox" not in r else r)
                       for r in geo["rectangles"]],
        "has_image": False,
    }
    model = extend_model(build_table_model(input_data))
    prefix = "P%03d_T%02d_" % (page_idx + 1, table_index + 1)
    for cell in model.get("cells", []):
        cell["cell_id"] = prefix + cell["cell_id"]
    cache_path.write_text(json.dumps(model, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return model


def build_page_model(pdf, page_idx, out_dir, run_doclayout=True,
                     reuse_table_model=True, table_model_path=None,
                     auto_reconstruct_tables=False):
    """Build the Unified PageModel for one page.

    Returns dict(page, width, height, regions, ownership, page_objects).
    """
    spans, drawings, images, page_w, page_h, media_box, crop_box, rotation = \
        extract_source_objects(pdf, page_idx)

    # ---- DocLayout regions (for table ROI + figure + reading hints) ----
    layout_regions = (_doclayout_regions(pdf, page_idx, out_dir)
                      if run_doclayout else [])
    table_rois = [r["bbox"] for r in layout_regions
                  if r.get("class_name") == "table"]
    figure_defs = [r for r in layout_regions if r.get("class_name") == "figure"]
    figure_rois = [r["bbox"] for r in figure_defs]
    caption_rois = [r["bbox"] for r in layout_regions
                    if r.get("class_name") == "figure_caption"]

    # ---- FormulaModels (whole page; empty list on non-formula pages) ----
    # Table ROI spans are NOT formula candidates: table cells often contain
    # numbers / short labels (p007 '+ Skeleton', '98.95') that a math-font
    # composer would otherwise capture and fight the table for (11
    # formula_vs_table conflicts).  Table content is owned by TableModel.
    table_roi_span_ids = set()
    for roi in table_rois:
        for s in spans:
            if _bbox_contains(roi, s["bbox"]):
                table_roi_span_ids.add(s["id"])
    compose_spans = [s for s in spans if s["id"] not in table_roi_span_ids]
    formulas = _compose_formulas(compose_spans, drawings, images, page_w,
                                 page_h)
    code_fids = classify_code_expressions(formulas)
    false_positive_fids = set()
    for fm in formulas:
        if fm["formula_id"] in code_fids:
            fm["kind"] = "code_expression"
        elif any(_is_math_font(c.get("font")) for c in fm.get("components", [])):
            fm["kind"] = "math_formula"
        else:
            fm["kind"] = "text"
            false_positive_fids.add(fm["formula_id"])

    # ---- ownership ----
    # 1. table ROI spans -> table owner
    table_span_ids = set()
    for roi in table_rois:
        for s in spans:
            if s["id"] in table_span_ids:
                continue
            if _bbox_contains(roi, s["bbox"]):
                table_span_ids.add(s["id"])
    # 2. formula components -> formula owner (bbox match)
    formula_span_ids = set()
    formula_by_span = {}
    for fm in formulas:
        if fm.get("kind") != "math_formula":
            continue  # code expressions and detector false positives stay in text flow
        for comp in fm.get("components", []):
            for s in spans:
                if s["id"] in table_span_ids or s["id"] in formula_span_ids:
                    continue
                if _span_matches_component(s, comp):
                    formula_span_ids.add(s["id"])
                    formula_by_span[s["id"]] = fm["formula_id"]

    # 2b. formula enclosure adoption (Phase 4C.2): display / complex
    #     formulas own EVERY span whose centre lies inside their enclosure,
    #     including normal-Roman condition text ("if", "otherwise",
    #     "correctly supports", "where", "for") that the math composer does
    #     not capture because it is not a math font.  Without this,
    #     cases/piecewise condition words fall through to a paragraph, get
    #     translated to Chinese and double-render on top of the formula SVG
    #     (p14/p16).  Inline formulas are only expanded when they are clearly
    #     multi-row (cases / piecewise / stacked equations mislabelled as
    #     inline by the composer): a single-row inline formula must NOT
    #     swallow surrounding body text.
    adopted_fids = set()
    for fm in formulas:
        if fm.get("kind") != "math_formula":
            continue
        b = fm.get("layout_bbox")
        if not b or len(b) != 4:
            continue
        bbox = [float(v) for v in b]
        enc_h = bbox[3] - bbox[1]
        main_size = float(fm.get("baseline_size") or 0) or 10.0
        is_inline = fm.get("placement") == "inline"
        multi_row = _formula_multi_row(fm, enc_h, main_size)
        # Formula-adjacent ordinary prose must remain text-owned and carry
        # an inline formula placeholder through translation.  A coarse
        # multi-row enclosure often contains prose on the same source lines
        # (p003 B10); it is not a reason to protect that prose as SVG.
        adjacent_prose = []
        _formula_condition_phrases = {
            "if", "iff", "otherwise", "where", "when", "whenever",
            "for all", "for any", "correctly supports",
            "does not support", "and the survey",
        }
        for _s in spans:
            if _s["id"] in table_span_ids or _s["id"] in formula_span_ids:
                continue
            _text = re.sub(r"\s+", " ", (_s.get("text") or "").strip())
            _words = re.findall(r"[A-Za-z][A-Za-z'\-]{1,}", _text)
            _low = _text.lower().strip(" ,.;:")
            _sb = _s["bbox"]
            _y_overlap = min(_sb[3], bbox[3] + 8.0) - max(_sb[1], bbox[1] - 8.0)
            if (len(_words) >= 3 and _low not in _formula_condition_phrases
                    and _y_overlap > 1.0
                    and _sb[2] >= bbox[0] - 3.0 and _sb[0] <= bbox[2] + 3.0):
                adjacent_prose.append(_s)
        if adjacent_prose:
            fm["formula_adjacent_prose"] = True
            fm["formula_adjacent_prose_span_ids"] = [s["id"] for s in adjacent_prose]
            fm["placement"] = "inline"
            fm["type"] = "complex_inline"
            is_inline = True
        if is_inline and not multi_row:
            continue  # single-row inline: keep tight, do not eat body text
        # expand slightly to catch baseline-aligned condition rows
        pad_x, pad_y = 3.0, 8.0  # pad_y generous: condition tails ("and
        # the survey.") sit on the formula's last row, slightly below the
        # math-glyph union bbox
        enc = [bbox[0] - pad_x, bbox[1] - pad_y,
               bbox[2] + pad_x, bbox[3] + pad_y]
        enc_strict = [bbox[0] - pad_x, bbox[1] - 2.0,
                      bbox[2] + pad_x, bbox[3] + 2.0]
        fid = fm["formula_id"]
        adopted = []
        for s in spans:
            if s["id"] in table_span_ids or s["id"] in formula_span_ids:
                continue
            # Phase 4C.2R.1: mono-font spans are CodeRuns (protected code /
            # model identifiers in their paragraph, e.g. p005 "self-refinement"
            # in NimbusMonL) -- they must NOT be absorbed into a formula SVG
            # or the same token double-renders (SVG + body CodeRun).
            _low_font = (s.get("font") or "").lower()
            if _is_math_font(s.get("font")) or "mono" in _low_font \
                    or "courier" in _low_font:
                continue
            sb = s["bbox"]
            cx = (sb[0] + sb[2]) / 2.0
            cy = (sb[1] + sb[3]) / 2.0
            t = (s.get("text") or "").strip()
            # Only genuine formula-condition Roman text may be adopted.
            # Length alone is unsafe: p003's ordinary prose ("are first
            # grouped into clusters ... For each cluster") sits inside the
            # coarse B10 enclosure and used to be swallowed wholesale.
            low_t = re.sub(r"\s+", " ", t.lower()).strip(" ,.;:")
            condition_words = {
                "if", "iff", "otherwise", "where", "when", "whenever",
                "for all", "for any", "correctly supports",
                "does not support", "and the survey",
            }
            is_short_cond = low_t in condition_words
            # A bare "for" is formula semantics only when it is not the
            # start of an ordinary continuation on the next source line.
            if low_t == "for":
                is_short_cond = False
            is_eqnum = bool(re.fullmatch(r"\(\d{1,3}\)", t))
            # small epsilon: PyMuPDF span centres can sit 0.01pt outside the
            # box edge while the span itself overlaps it; equation numbers
            # sit on the formula baseline, slightly below the math union
            eps = 3.0 if is_eqnum else 1.0
            in_enc = (enc[0] - eps <= cx <= enc[2] + eps
                      and enc[1] - eps <= cy <= enc[3] + eps)
            in_strict = (enc_strict[0] - eps <= cx <= enc_strict[2] + eps
                         and enc_strict[1] - eps <= cy <= enc_strict[3] + eps)
            # wide-pad region: only short condition-like tokens qualify
            # (prevents absorbing the paragraph line below the formula)
            in_wide = (not in_strict and is_short_cond
                       and enc[0] - eps <= cx <= enc[2] + eps
                       and enc[1] - eps <= cy <= enc[3] + eps)
            # Phase 4C.2R: baseline-adjacent condition tokens -- a short
            # Roman word ("where", "if", "for", "such that") sitting on the
            # SAME row as the formula, horizontally just outside its
            # enclosure (gap < 6pt), is formula semantics too (p15: "where"
            # floats directly left of B3).  Only short condition-like words
            # qualify; long body text never does.
            y_overlap = (min(sb[3], enc_strict[3]) - max(sb[1], enc_strict[1]))
            if sb[2] <= enc_strict[0]:
                gap_x = enc_strict[0] - sb[2]      # to the left
            elif sb[0] >= enc_strict[2]:
                gap_x = sb[0] - enc_strict[2]      # to the right
            else:
                gap_x = 0.0                         # x-overlapping / adjacent
            adj = (not (in_strict or in_wide)
                   and is_short_cond and y_overlap > 1.0 and gap_x < 6.0)
            # Ordinary-font enclosure content is never adopted merely
            # because it is geometrically inside the formula.  It must be
            # an equation number or a recognised formula condition.
            if not ((is_eqnum and in_enc)
                    or (is_short_cond and (in_strict or in_wide or adj))):
                continue
            if not t:
                continue
            formula_span_ids.add(s["id"])
            formula_by_span[s["id"]] = fid
            adopted.append(s)
        if adopted:
            adopted_fids.add(fid)
            known = {(round(c.get("bbox", [0])[0], 2),
                      round(c.get("bbox", [0])[1], 2))
                     for c in fm.get("components", [])}
            for s in adopted:
                key = (round(s["bbox"][0], 2), round(s["bbox"][1], 2))
                if key in known:
                    continue
                fm.setdefault("components", []).append({
                    "text": s.get("text", ""),
                    "font": s.get("font", ""),
                    "size": s.get("size", 0),
                    "bbox": [round(v, 3) for v in s["bbox"]],
                    "adopted": True,
                })
                fm.setdefault("adopted_span_ids", []).append(s["id"])
    if adopted_fids:
        # multi-row formulas that were mislabelled inline become display
        # blocks: their SVG (already whole-page) renders at the original
        # bbox instead of as an inline wrapper inside paragraph text.
        for fm in formulas:
            if (fm.get("formula_id") in adopted_fids
                    and not fm.get("formula_adjacent_prose")
                    and fm.get("placement") == "inline"):
                fm["placement"] = "block"
                fm["type"] = ("complex_display" if fm.get("type")
                              in ("complex_inline",) else "display")
        print("  [page_model] formula enclosure adoption: %s"
              % sorted(adopted_fids))
    # Phase 4C.2R.1: crop windows must cover adopted condition ink and
    # equation numbers (the SVG viewBox = render_viewbox or layout_bbox).
    _formula_crop_viewboxes(formulas)
    # 3. figure / image regions
    figure_span_ids = set()
    for roi in figure_rois:
        for s in spans:
            if s["id"] in table_span_ids or s["id"] in formula_span_ids:
                continue
            if s["id"] in figure_span_ids:
                continue
            if _bbox_contains(roi, s["bbox"], margin=3.0):
                figure_span_ids.add(s["id"])
    image_owners = set()
    for img in images:
        for roi in figure_rois + table_rois:
            if _bbox_intersect(img["bbox"], roi):
                image_owners.add(img["xref"])
                break
    # 4. remaining -> text owner
    text_span_ids = {s["id"] for s in spans} - table_span_ids \
        - formula_span_ids - figure_span_ids

    # ---- conflicts / unowned ----
    conflicts = []
    for fm in formulas:
        for comp in fm.get("components", []):
            for s in spans:
                if _span_matches_component(s, comp) \
                        and s["id"] in table_span_ids:
                    conflicts.append(("formula_vs_table", s["id"],
                                      fm["formula_id"]))
    ownership = {
        "table_span_ids": sorted(table_span_ids),
        "formula_span_ids": sorted(formula_span_ids),
        "figure_span_ids": sorted(figure_span_ids),
        "text_span_ids": sorted(text_span_ids),
        "image_owner_xrefs": sorted(image_owners),
        "conflicts": conflicts,
    }

    # ---- regions ----
    regions = []

    # table region (payload = existing TableModel adapter)
    for table_index, roi in enumerate(table_rois):
        payload = None
        if auto_reconstruct_tables:
            payload = _auto_table_payload(pdf, page_idx, roi, out_dir,
                                          table_index=table_index)
        elif reuse_table_model:
            payload = _load_table_payload(page_idx, Path(out_dir),
                                          table_model_path)
        regions.append({
            "region_id": "TBL%d" % (len(regions) + 1),
            "type": "table",
            "bbox": [round(v, 3) for v in roi],
            "reading_order": None,
            "owner": "table",
            "payload": payload,
        })

    # formula regions (payload = FormulaModel adapter); code expressions are
    # NOT formula regions - they flow as protected text in their paragraph
    for fm in formulas:
        if fm.get("kind") != "math_formula":
            continue
        regions.append({
            "region_id": "FML" + fm["formula_id"],
            "type": "formula",
            "bbox": [round(v, 3) for v in fm["layout_bbox"]],
            "reading_order": None,
            "owner": "formula",
            "payload": fm,
        })

    # figure / image regions
    for i, img in enumerate(images):
        # A FigureRegion is rendered from one geometry-locked page-SVG crop.
        # Do not also emit the embedded raster as an ImageRegion underneath it.
        inside_figure = any(_bbox_intersect(img["bbox"], roi)
                            for roi in figure_rois)
        if img["xref"] in image_owners and not inside_figure:
            regions.append({
                "region_id": "IMG%d" % (i + 1),
                "type": "image",
                "bbox": [round(v, 3) for v in img["bbox"]],
                "reading_order": None,
                "owner": "figure/image",
                "payload": {"xref": img["xref"]},
            })

    # Vector/raster-agnostic FigureRegion closure.  The renderer crops the
    # single page SVG, so original vector ink and in-figure text are retained.
    for i, roi_def in enumerate(figure_defs):
        roi = roi_def["bbox"]
        span_ids = [s["id"] for s in spans if s["id"] in figure_span_ids
                    and _bbox_contains(roi, s["bbox"], margin=3.0)]
        drawing_ids = [d["id"] for d in drawings if _bbox_intersect(d["bbox"], roi)]
        regions.append({
            "region_id": "FIG%d" % (i + 1),
            "type": "figure",
            "bbox": [round(v, 3) for v in roi],
            "reading_order": None,
            "owner": "figure",
            "payload": {
                "figure_id": "FIG%d" % (i + 1),
                "bbox": [round(v, 3) for v in roi],
                "source_span_ids": span_ids,
                "source_drawing_ids": drawing_ids,
                "render_policy": "geometry_locked",
            },
        })

    # ---- text regions: paragraphs built from text + INLINE formula spans ----
    from paragraphs import build_paragraphs
    inline_fids = {fm["formula_id"] for fm in formulas
                   if fm.get("placement") == "inline"
                   and fm.get("kind") == "math_formula"}
    formula_span_map = {}  # span_id -> {{FORMULA_Bn}}
    for fm in formulas:
        if fm["formula_id"] not in inline_fids:
            continue
        # Phase 4E.1B-C2.1 (Render Exclusivity): an inline formula renders
        # EXACTLY ONCE via a single canonical anchor fragment.  Mapping every
        # matching span would insert the placeholder once per source line (or
        # per paragraph), duplicating the formula SVG.  The anchor is the
        # top-left-most component; the remaining formula-owned spans stay
        # formula-owned and are dropped from the paragraph text layer because
        # the SVG already renders their ink.
        anchor_comps = sorted(fm.get("components", []),
                              key=lambda c: ((c.get("bbox") or [0, 0, 0, 0])[1],
                                             (c.get("bbox") or [0, 0, 0, 0])[0]))
        for comp in anchor_comps:
            matched = None
            for s in spans:
                if s["id"] in table_span_ids:
                    continue
                if _span_matches_component(s, comp):
                    matched = s
                    break
            if matched is not None:
                formula_span_map[matched["id"]] = \
                    "{{FORMULA_%s}}" % fm["formula_id"]
                break
    para_spans = [s for s in spans
                  if s["id"] in text_span_ids or s["id"] in formula_span_map]
    for s in para_spans:
        s["is_formula"] = s["id"] in formula_span_map
    paragraphs, ncols = build_paragraphs(para_spans, page_w, page_h,
                                         formula_by_span_id=formula_span_map)
    # Link translated figure captions to their geometry-locked figure.
    figure_regions = [r for r in regions if r["type"] == "figure"]
    for p in paragraphs:
        if p.get("style_role") != "caption" or not figure_regions:
            continue
        pb = p["bbox"]
        candidates = sorted(
            figure_regions,
            key=lambda fr: (abs(fr["bbox"][1] - pb[3]), abs(fr["bbox"][0] - pb[0])))
        p["caption_for"] = candidates[0]["payload"]["figure_id"]
    for p in paragraphs:
        regions.append({
            "region_id": p["paragraph_id"],
            "type": "text",
            "bbox": [round(v, 3) for v in p["bbox"]],
            "reading_order": list(p["reading_order"]),
            "owner": "text",
            "payload": p,
        })

    model = {
        "page": page_idx + 1,
        "width": round(page_w, 3),
        "height": round(page_h, 3),
        "media_box": [round(v, 3) for v in media_box],
        "crop_box": [round(v, 3) for v in crop_box],
        "rotation": rotation,
        "coordinate_space": "cropbox_rotated",
        "regions": regions,
        "ownership": ownership,
        "page_objects": {
            "span_count": len(spans),
            "drawing_count": len(drawings),
            "image_count": len(images),
        },
        "formulas": formulas,
        "code_expression_count": len(code_fids),
        "code_false_positive_count": len(false_positive_fids),
        "paragraph_count": len(paragraphs),
        "column_count": ncols,
        "formula_span_map": formula_span_map,
        "layout_regions": layout_regions,
    }
    model["regions"] = sorted(regions, key=lambda r: (r.get("reading_order")
                                                      or [999, 0])[0])
    return model


def ownership_stats(model):
    """Unified ownership counts."""
    own = model["ownership"]
    n = model["page_objects"]["span_count"]
    owned = set(own["table_span_ids"]) | set(own["formula_span_ids"]) \
        | set(own["figure_span_ids"]) | set(own["text_span_ids"])
    duplicated = (len(own["table_span_ids"]) + len(own["formula_span_ids"])
                  + len(own["figure_span_ids"]) + len(own["text_span_ids"])
                  - len(owned))
    return {
        "span_count": n,
        "ownership_conflict_count": len(own["conflicts"]),
        "duplicate_owned_object_count": max(0, duplicated),
        "unowned_object_count": n - len(owned),
        "table_spans": len(own["table_span_ids"]),
        "formula_spans": len(own["formula_span_ids"]),
        "figure_spans": len(own["figure_span_ids"]),
        "text_spans": len(own["text_span_ids"]),
        "image_owned": len(own["image_owner_xrefs"]),
    }
