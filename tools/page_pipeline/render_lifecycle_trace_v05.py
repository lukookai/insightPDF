# -*- coding: utf-8 -*-
"""render_lifecycle_trace_v05 -- root-cause trace + recovery closure accounting.

For every residual prose fragment flagged by the v05 QAs, trace the full
lifecycle:

    source span -> source paragraph/formula -> semantic role
    -> formula ownership -> PAF adoption/recovery -> translation route
    -> canonical target -> RecoveredProseBlock -> VisualRegion
    -> HTML block -> final PDF visible text

Root-cause classification (document-general, no page constants):
    PAF_PARTIAL_RECOVERY
    PAF_SOURCE_TAIL_LEFT_BEHIND
    PAF_SOURCE_HEAD_LEFT_BEHIND
    SEMANTIC_ROLE_LOST
    HEADING_BOUNDARY_LOST
    FORMULA_CONTEXT_UNOWNED
    FORMULA_CONTEXT_WRONG_OWNER
    TARGET_FRAGMENT_MISSING
    SOURCE_FALLBACK_SELECTED
    PROTECTED_TOKEN_LOST
    GLYPH_NOTDEF
    ORPHAN_PUNCTUATION

Also emits the recovery-closure accounting:

    adopted_prose_span_count      (source prose lines inside prose-bearing
                                   formula regions)
    recovered_prose_span_count    (lines covered by PAF recovered blocks)
    legitimate_formula_span_count (lines inside REAL formulas that stay
                                   SVG: non-prose lines)
    unaccounted_span_count        (adopted - recovered - legitimate; hard = 0)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))

from residual_prose_truth_qa import (  # noqa: E402
    _source_lines_in_bbox, is_translatable_prose_line)
from semantic_structure_closure_qa import (  # noqa: E402
    _source_heading_candidates)

OUT = REPO / "outputs" / "visual_v05_checkpoint"
V04 = REPO / "outputs" / "visual_v05_checkpoint"

DOCS = {
    "ppat": {"src": REPO / "outputs" / "phase4e2a_qa_recovery" / "doc2",
             "pdf": "C:/Users/74496/Desktop/PPAT.pdf",
             "pages": [4, 5, 6]},
}


def _load(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default if default is not None else {}


def _bbox_overlap(a, b):
    if len(a) != 4 or len(b) != 4:
        return False
    xo = min(a[2], b[2]) - max(a[0], b[0])
    yo = min(a[3], b[3]) - max(a[1], b[1])
    return xo > 2.0 and yo > 2.0


def _line_inside(line_bbox, region_bbox):
    """Line bbox (mostly) inside region bbox."""
    xo = min(line_bbox[2], region_bbox[2]) - max(line_bbox[0], region_bbox[0])
    if xo < 0.6 * (line_bbox[2] - line_bbox[0]) - 2:
        return False
    cy = (line_bbox[1] + line_bbox[3]) / 2.0
    return region_bbox[1] - 2 <= cy <= region_bbox[3] + 2


def trace_page(page: int):
    info = DOCS["ppat"]
    src_dir = info["src"] / "pages" / ("p%03d" % page)
    model = _load(src_dir / "stitched_page_model.json")
    translations = _load(src_dir / "translation.json")
    grid = _load(src_dir / "page_grid.json") or _load(
        src_dir / "qa.json", {}).get("page_grid", {})
    pdf = info["pdf"]
    pidx = page - 1
    red = _load(OUT / "old_red_evidence" / ("ppat_p%03d_v04_red.json" % page))
    html = (V04 / ("ppat_p%03d" % page) / "zh_visual.html").read_text(
        encoding="utf-8", errors="replace")

    # formula regions (with rendered status from the HTML)
    formulas = []
    for r in model.get("regions", []):
        if r.get("type") != "formula":
            continue
        p = r.get("payload") or {}
        bb = [float(v) for v in (p.get("layout_bbox") or [])]
        if len(bb) != 4:
            continue
        rendered = ('data-formula="%s"' % p.get("formula_id")) in html
        formulas.append({
            "formula_id": str(p.get("formula_id")),
            "bbox": bb, "rendered": rendered,
            "source_text": p.get("source_text") or "",
            "placement": p.get("placement"),
        })

    # ---- closure accounting per prose-bearing formula -------------------
    accounting = {
        "adopted_prose_span_count": 0,
        "recovered_prose_span_count": 0,
        "legitimate_formula_span_count": 0,
        "unaccounted_span_count": 0,
        "per_formula": [],
    }
    traces: List[Dict[str, Any]] = []

    # recovered PAF blocks (from v04 runner artifacts if available, else
    # rebuild dry-run)
    from prose_adopted_formula_recovery import recover_prose_adopted_formulas
    rec = recover_prose_adopted_formulas(model, translations, pdf, pidx,
                                         translator_fn=None)
    rec_boxes = [p.get("bbox") or [] for p in rec.get("recovered", [])]

    # figure/table/image regions are legal vector content (their internal
    # labels are NOT swallowed prose); the figure's caption band directly
    # below it is figure-owned too
    anchor_regions = []
    for r in model.get("regions", []):
        if r.get("type") not in ("figure", "table", "image"):
            continue
        bb = [float(v) for v in r.get("bbox", [])]
        if len(bb) != 4:
            continue
        anchor_regions.append(bb)
        anchor_regions.append([bb[0], bb[1], bb[2], bb[3] + 70.0])
    for fm in formulas:
        if not fm["rendered"]:
            # formula not rendered (fully prose-adopted & skipped): its
            # swallowed rows never leak into the final PDF -> not adopted
            continue
        lines = _source_lines_in_bbox(pdf, pidx, fm["bbox"])
        lines = [ln for ln in lines
                 if not any(_line_inside(ln["bbox"], ar)
                            for ar in anchor_regions)]
        prose_lines = [ln for ln in lines
                       if is_translatable_prose_line(ln["text"])]
        formula_lines = [ln for ln in lines
                         if not is_translatable_prose_line(ln["text"])]
        recovered_lines = [ln for ln in prose_lines
                           if any(_line_inside(ln["bbox"], rb)
                                  for rb in rec_boxes)]
        unaccounted = [ln for ln in prose_lines
                       if not any(_line_inside(ln["bbox"], rb)
                                  for rb in rec_boxes)]
        fm_acc = {
            "formula_id": fm["formula_id"],
            "rendered": fm["rendered"],
            "prose_line_count": len(prose_lines),
            "recovered_line_count": len(recovered_lines),
            "legitimate_formula_line_count": len(formula_lines),
            "unaccounted_line_count": len(unaccounted),
            "unaccounted_lines": [ln["text"] for ln in unaccounted],
        }
        accounting["adopted_prose_span_count"] += len(prose_lines)
        accounting["recovered_prose_span_count"] += len(recovered_lines)
        accounting["legitimate_formula_span_count"] += len(formula_lines)
        accounting["unaccounted_span_count"] += len(unaccounted)
        accounting["per_formula"].append(fm_acc)

        if unaccounted:
            # root-cause: PAF_PARTIAL_RECOVERY / FORMULA_CONTEXT_UNOWNED
            for ln in unaccounted:
                traces.append({
                    "fragment_id": "%s-%s" % (fm["formula_id"], "UNACC"),
                    "kind": "formula_vector_prose",
                    "root_cause": "PAF_PARTIAL_RECOVERY"
                    if fm["rendered"] else "FORMULA_CONTEXT_UNOWNED",
                    "source_span": {"text": ln["text"],
                                    "bbox": [round(v, 1) for v in ln["bbox"]]},
                    "source_formula_region": fm["formula_id"],
                    "formula_rendered": fm["rendered"],
                    "semantic_role": "formula_annotation"
                    if fm["rendered"] else "recovered_prose",
                    "translation_route": "NONE (not adopted)",
                    "canonical_target": None,
                    "recovered_block": None,
                    "html_block": None,
                    "final_visible": "vector ink (text_as_path)" if
                    fm["rendered"] else "unrendered",
                    "chain": [
                        "source span -> formula %s (bbox)"
                        % fm["formula_id"],
                        "prose-line test: translatable prose"
                        if is_translatable_prose_line(ln["text"])
                        else "prose-line test: NOT prose",
                        "PAF recovery: NOT adopted (plain-prose test "
                        "failed due to math symbols in the line)",
                        "formula SVG: %s" % ("rendered (English visible)"
                                             if fm["rendered"]
                                             else "excluded"),
                        "final PDF: %s" % ("vector English visible"
                                           if fm["rendered"]
                                           else "missing target"),
                    ],
                })

    # ---- semantic / heading loss trace ----------------------------------
    body_size = float(grid.get("body_size_estimated") or 10.0)
    headings = _source_heading_candidates(pdf, pidx, body_size)
    for h in headings:
        hkey = h["text"]
        owned = None
        for r in model.get("regions", []):
            if r.get("type") == "text":
                para = r.get("payload") or {}
                if hkey[:30] in para.get("source_text", ""):
                    owned = ("text", para.get("paragraph_id"))
                    break
        if owned is None:
            for p in rec.get("recovered", []):
                if hkey[:30] in p.get("source_text", ""):
                    owned = ("paf", p.get("paragraph_id"))
                    break
        if owned is None:
            continue
        traces.append({
            "fragment_id": "%s-HEADING" % (owned[1]),
            "kind": "heading_boundary_lost",
            "root_cause": "SEMANTIC_ROLE_LOST" if owned[0] == "paf"
            else "HEADING_BOUNDARY_LOST",
            "source_span": {"text": hkey,
                            "bbox": [round(v, 1) for v in h["bbox"]]},
            "source_formula_region": "recovered-PAF" if owned[0] == "paf"
            else None,
            "semantic_role": "heading (level %d)" % h["level"],
            "translation_route": "PAF body recovery (role not preserved)",
            "canonical_target": None,
            "recovered_block": owned[1],
            "html_block": "merged into PAF body paragraph",
            "final_visible": "heading glued to body text",
            "chain": [
                "source heading %r (Bold %.1fpt > body %.1fpt)"
                % (hkey, h["size"], body_size),
                "swallowed by formula region -> PAF recovery",
                "recovered as BODY block (semantic role lost)",
                "rendered merged with the previous/next body sentence",
                "final PDF: heading not an independent block",
            ],
        })

    # ---- orphan punctuation trace ---------------------------------------
    import re as _re
    for m in _re.finditer(
            r'<div class="paragraph-block" data-para="([^"]*)"[^>]*>'
            r'(.*?)</div>', html, _re.S):
        pid, inner = m.groups()
        text = _re.sub(r"<[^>]+>", "", inner).strip()
        if _re.fullmatch(r"[,:;.。，：；、]+", text):
            src = translations.get(pid) or ""
            traces.append({
                "fragment_id": "%s-PUNCT" % pid,
                "kind": "orphan_punctuation",
                "root_cause": "ORPHAN_PUNCTUATION",
                "source_span": {"text": src, "bbox": None},
                "source_formula_region": None,
                "semantic_role": "formula_introduction",
                "translation_route": "inline-formula token skipped",
                "canonical_target": src,
                "recovered_block": pid,
                "html_block": pid,
                "final_visible": "isolated punctuation %r" % text,
                "chain": [
                    "source paragraph %s carries an inline {{FORMULA_*}} "
                    "token + punctuation" % pid,
                    "formula was prose-adopted -> inline SVG excluded",
                    "paragraph text left = punctuation only",
                    "rendered as an isolated punctuation block",
                ],
            })

    return {
        "page": page, "doc": "ppat",
        "traces": traces,
        "closure_accounting": accounting,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    all_traces = []
    all_acc = {"adopted_prose_span_count": 0,
               "recovered_prose_span_count": 0,
               "legitimate_formula_span_count": 0,
               "unaccounted_span_count": 0}
    for pg in (4, 5, 6):
        print("== ppat p%03d" % pg, flush=True)
        t = trace_page(pg)
        all_traces.append(t)
        for k in all_acc:
            all_acc[k] += t["closure_accounting"][k]
        print("   unaccounted=%d adopted=%d recovered=%d legit=%d"
              % (t["closure_accounting"]["unaccounted_span_count"],
                 t["closure_accounting"]["adopted_prose_span_count"],
                 t["closure_accounting"]["recovered_prose_span_count"],
                 t["closure_accounting"]["legitimate_formula_span_count"]),
              flush=True)
    out = {
        "schema_version": "visual_v05.render_lifecycle_trace.v1",
        "pages": all_traces,
        "total_closure": all_acc,
        "root_cause_summary": _summarize(all_traces),
    }
    (OUT / "render_lifecycle_trace.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nwritten:", OUT / "render_lifecycle_trace.json")


def _summarize(pages):
    from collections import Counter
    c = Counter()
    for p in pages:
        for t in p["traces"]:
            c[t["root_cause"]] += 1
    return dict(c)


if __name__ == "__main__":
    main()
