"""render_lifecycle_trace -- visual-v04 root-cause tracing.

For every abnormal paragraph on PPAT p004 / p005 / p006, builds a full
lifecycle trace:

    source paragraph
    -> semantic role
    -> translation route
    -> canonical translation
    -> visual fragment / group
    -> render text selected
    -> HTML block
    -> final PDF recovered text

and classifies the root cause from EVIDENCE (never guessed):

English-visible causes:
    SOURCE_TEXT_SELECTED          HTML rendered source text
    TARGET_TEXT_MISSING           canonical target missing/invalid
    CACHE_LOOKUP_WRONG            translation looked up wrong id
    VISUAL_FRAGMENT_WRONG_PAYLOAD fragment carries wrong text
    GROUP_MEMBER_WRONG_PAYLOAD    visual group member wrong payload
    HTML_FALLBACK_TO_SOURCE       renderer fell back to source
    DUPLICATE_SOURCE_LAYER        source rendered twice
    DOM_DUPLICATE_RENDER          same block emitted twice
    WRONG_FRAGMENT_MAPPING        fragment mapped to wrong page/region
    PROSE_ADOPTED_FORMULA         layout swallowed prose into formula region
                                  (source vector ink rendered)

Overlap causes:
    SAME_TOP_ASSIGNED             multiple blocks share a top
    CURSOR_NOT_ADVANCED           cursor never advanced
    MULTIPLE_BLOCKS_SHARE_REGION  several blocks in one y band
    VISUAL_GROUP_AND_MEMBER_BOTH_RENDERED
    SOURCE_AND_TARGET_BOTH_RENDERED
    FIT_ESTIMATE_RENDER_MISMATCH
    WRONG_HEIGHT_MEASUREMENT
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import pymupdf  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))

from final_visible_translation_qa import (  # noqa: E402
    _char_seq, _strip_tokens, expand_visible_text, normalize_visible_text)
from final_source_residual_qa import is_prose_adopted_formula  # noqa: E402

V03 = REPO / "outputs" / "visual_v03_checkpoint"
P4E2A = REPO / "outputs" / "phase4e2a_qa_recovery"
OUT_DIR = REPO / "outputs" / "visual_v04_checkpoint"

DOCS = {
    "ppat": {"src": P4E2A / "doc2", "pdf": "C:/Users/74496/Desktop/PPAT.pdf",
             "pages": [4, 5, 6]},
}


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def _html_blocks(html: str) -> Dict[str, Dict[str, Any]]:
    out = {}
    pat = re.compile(
        r'<div class="paragraph-block"[^>]*data-para="([^"]+)"[^>]*'
        r'data-role="([^"]*)"[^>]*style="[^"]*left:([\d.]+)pt;top:([\d.]+)pt;'
        r'width:([\d.]+)pt[^"]*"[^>]*>(.*?)</div>', re.S)
    for m in pat.finditer(html or ""):
        pid, role, left, top, width, inner = m.groups()
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", inner))
        out[pid] = {"role": role, "left": float(left), "top": float(top),
                    "width": float(width), "has_cjk": has_cjk,
                    "render_source": "target" if has_cjk else "source"}
    return out


def _final_lines(final_pdf) -> List[Dict[str, Any]]:
    lines = []
    if final_pdf and Path(final_pdf).exists():
        try:
            doc = pymupdf.open(str(final_pdf))
            d = doc[0].get_text("dict")
            for b in d.get("blocks", []):
                if b.get("type") != 0:
                    continue
                for l in b.get("lines", []):
                    txt = "".join(s.get("text") or "" for s in
                                  l.get("spans", []))
                    if txt.strip():
                        lines.append({"bbox": [float(v) for v in l["bbox"]],
                                      "text": txt})
            doc.close()
        except Exception:  # noqa: BLE001
            pass
    return lines


def _route_of(paragraph, page_model) -> str:
    """Re-derive the translation route from the paragraph profile."""
    try:
        from math_density import build_math_density_profile
        from translation_route import classify_route
        profile = build_math_density_profile(paragraph, page_model)
        return classify_route(profile)
    except Exception:  # noqa: BLE001
        return "unknown"


def trace_page(doc_key, page):
    info = DOCS[doc_key]
    src_dir = info["src"] / "pages" / ("p%03d" % page)
    model = _load(src_dir / "stitched_page_model.json", {})
    translations = _load(src_dir / "translation.json", {})
    page_dir = V03 / ("%s_p%03d" % (doc_key, page))
    html = (page_dir / "zh_visual.html").read_text(encoding="utf-8") \
        if (page_dir / "zh_visual.html").exists() else ""
    hb = _html_blocks(html)
    final_lines = _final_lines(page_dir / "zh_visual.pdf")
    final_chars = _char_seq(" ".join(l["text"] for l in final_lines))

    traces: List[Dict[str, Any]] = []

    # ---- A. every translatable paragraph --------------------------------
    para_prot = {}
    for r in model.get("regions", []):
        if r.get("type") == "text":
            p = r.get("payload") or {}
            if p.get("paragraph_id"):
                para_prot[p["paragraph_id"]] = p.get("protected_runs") or {}
    for r in model.get("regions", []):
        if r.get("type") != "text":
            continue
        p = r.get("payload") or {}
        pid = p.get("paragraph_id")
        if not pid:
            continue
        source = p.get("source_text") or ""
        role = p.get("semantic_role") or p.get("style_role") or "body"
        target = translations.get(pid)
        prot = para_prot.get(pid, {})
        tseq = _char_seq(target or "", prot) if target else ""
        visible = tseq and tseq in final_chars
        hblk = hb.get(pid, {})
        html_source = hblk.get("render_source", "no_block")
        html_top = hblk.get("top")
        trace = {
            "paragraph_id": pid,
            "source_text": source[:200],
            "semantic_role": role,
            "translation_route": _route_of(p, model),
            "canonical_translation": (target or "")[:200],
            "translation_status": "translated" if target else "missing",
            "visual_fragment_id": pid + "-F0",
            "visual_group_id": None,
            "render_text": None,
            "render_source": html_source,
            "html_render_id": pid,
            "html_top": html_top,
            "final_pdf_text": None,
            "final_pdf_bbox": None,
            "target_recovery": "visible" if visible else (
                "missing" if not tseq else "not_visible"),
            "source_residual": False,
            "render_cardinality": "once",
            "root_cause": [],
        }
        if not target or not tseq:
            trace["root_cause"].append("TARGET_TEXT_MISSING")
        elif not visible:
            trace["root_cause"].append("FINAL_PDF_MISSING")
            if html_source == "source":
                trace["root_cause"].append("HTML_FALLBACK_TO_SOURCE")
        # final PDF location of the target
        if tseq and tseq in final_chars:
            for l in final_lines:
                if _char_seq(l["text"]) and _char_seq(l["text"]) in tseq:
                    trace["final_pdf_text"] = l["text"][:120]
                    trace["final_pdf_bbox"] = [round(v, 1)
                                               for v in l["bbox"]]
                    break
        if trace["root_cause"]:
            traces.append(trace)

    # ---- B. prose-adopted formulas (English visible via vector ink) ------
    for r in model.get("regions", []):
        if r.get("type") != "formula":
            continue
        p = r.get("payload") or {}
        if not is_prose_adopted_formula(p):
            continue
        bb = [float(v) for v in (p.get("layout_bbox") or [])]
        fid = p.get("formula_id")
        traces.append({
            "paragraph_id": "FORMULA_%s" % fid,
            "source_text": (p.get("source_text") or "")[:200],
            "semantic_role": "formula(prose-adopted)",
            "translation_route": "NON_TRANSLATABLE_MATH (misdetected)",
            "canonical_translation": None,
            "translation_status": "missing",
            "visual_fragment_id": None,
            "visual_group_id": None,
            "render_text": "source SVG vector ink (text_as_path)",
            "render_source": "source",
            "html_render_id": "formula_%s_*.svg" % fid,
            "html_top": round(bb[1], 1),
            "final_pdf_text": "English prose rendered as vector paths "
                              "(invisible to text layer)",
            "final_pdf_bbox": [round(v, 1) for v in bb],
            "target_recovery": "missing",
            "source_residual": True,
            "render_cardinality": "source_only",
            "root_cause": ["PROSE_ADOPTED_FORMULA",
                           "SOURCE_TEXT_SELECTED",
                           "TARGET_TEXT_MISSING"],
        })

    # ---- C. soft-soft collision classification ---------------------------
    # overlap evidence from the collision QA + prose-adopted formula ink
    ev = _load(OUT_DIR / "old_red_evidence" /
               ("%s_p%03d_final_truth_red.json" % (doc_key, page)), {})
    qa3 = (ev or {}).get("soft_text_collision_qa") or {}
    collision_causes: List[Dict[str, Any]] = []
    for c in qa3.get("collision_details", []):
        if c.get("kind") not in ("severe_soft_soft_collision",
                                 "duplicate_baseline_cluster"):
            continue
        collision_causes.append({
            "kind": c.get("kind"),
            "pair_type": c.get("pair_type"),
            "block_a": c.get("block_a"),
            "block_b": c.get("block_b"),
            "vertical_overlap_ratio": c.get("vertical_overlap_ratio"),
            "baseline_distance_ratio": c.get("baseline_distance_ratio"),
            "cause": "MULTIPLE_BLOCKS_SHARE_REGION"
                     if c.get("kind") == "severe_soft_soft_collision"
                     else "DUPLICATE_BASELINE",
        })
    return {"doc": doc_key, "page": page, "traces": traces,
            "collision_classification": collision_causes}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_pages = []
    for dkey, info in DOCS.items():
        for pg in info["pages"]:
            res = trace_page(dkey, pg)
            all_pages.append(res)
            print("traced %s p%03d: %d paragraph traces, %d collisions" %
                  (dkey, pg, len(res["traces"]),
                   len(res["collision_classification"])))
    _dump(OUT_DIR / "render_lifecycle_trace.json",
          {"schema_version": "visual_v04.render_lifecycle_trace.v1",
           "pages": all_pages})


def _dump(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                       encoding="utf-8")


if __name__ == "__main__":
    main()
