# -*- coding: utf-8 -*-
"""HeadingDuplicateQA (Phase 4C.2R.1).

A heading must be rendered as ONE coherent title.  Three defects are
audited on the FINAL PDF text layer (not the model):

1. repeated semantic consumption -- the same source fragment feeding more
   than one heading / a heading fragment also feeding an adjacent
   paragraph (p011: "A Information Bottleneck in Survey" + "Generation"
   were two source lines; the translation split into two <strong> spans
   and rendered "A 综述生成中的信息瓶颈 生成" -- "生成" duplicated).

2. duplicate semantic token inside the rendered heading -- a meaningful
   Chinese/English token appearing twice ("生成 ... 生成") is almost
   always a split-and-join artifact (the source heading is one phrase).

3. the same source_object_id entering a heading twice (source-level
   duplicate consumption).

Heading-line location on the final PDF (false-positive hardening):
  * column filter  -- only lines whose x0 is within the heading's own
    column band (|x0 - source_bbox.x0| < COLUMN_TOL)
  * vertical band  -- y inside [bbox_y1 - 6, bbox_y2 + 20]; rendered
    baselines sit a few pt BELOW the source box top, never above it
  * prefix check   -- a heading keeps its numbered prefix ("3.1",
    "B.2", "D.1.3") in the translation; a candidate line whose leading
    prefix differs is a body/neighbouring-heading line (p006 4.3.1 was
    previously matched to the sibling "4.3 评估指标" line)
  * fallback       -- when the tight band finds nothing (flow_locked
    formulas can push a heading far below its source anchor, p013's
    "D Details..." moved +112pt), search the same column up to
    FALLBACK_RANGE below with the prefix filter still enforced.

Outputs:
  heading_duplicate_count
  heading_duplicate_details[]  (para, rendered text, defect reason)
  heading_qa_clean            (bool)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
# numbered heading prefix: "A", "B.2", "D.1.3", "3", "4.3.1"
HEADING_PREFIX_RE = re.compile(r"^([A-Z](?:\.\d+)*|\d+(?:\.\d+)*)")
COLUMN_TOL = 20.0          # pt: same-column x tolerance
TIGHT_TOP = 6.0            # rendered line may sit above source box top
TIGHT_BOTTOM = 20.0        # ... or below its bottom
FALLBACK_RANGE = 170.0     # pt below source anchor (flow_locked shift)
MAX_LINE_CHARS = 60        # headings are short lines


def _pdf_plain_text(pdf_path):
    try:
        doc = pymupdf.open(str(pdf_path))
        page = doc[0]
        text = page.get_text("text")
        doc.close()
        return re.sub(r"\s+", " ", text)
    except Exception:  # noqa: BLE001
        return ""


def _heading_regions(page_model):
    """[(paragraph_id, role, source, translation_source)] heading-ish."""
    out = []
    for region in page_model.get("regions", []):
        if region.get("type") != "text":
            continue
        para = region["payload"]
        role = para.get("style_role") or "body"
        if role not in ("heading", "appendix_heading"):
            continue
        out.append({
            "para": para.get("paragraph_id"),
            "role": role,
            "source": (para.get("source_text") or "").strip(),
            "translation_source": (para.get("translation_source_text")
                                   or para.get("source_text") or ""),
            "bbox": [float(v) for v in para.get("bbox", [0, 0, 0, 0])],
        })
    return out


def _pdf_text_lines(pdf_path):
    """Final-PDF text layer grouped into visual lines: [(y, x0, x1, text)]."""
    lines = []
    try:
        doc = pymupdf.open(str(pdf_path))
        page = doc[0]
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") == 1:
                continue
            for line in block.get("lines", []):
                t = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                if not t:
                    continue
                bbox = line.get("bbox")
                lines.append({"y": (bbox[1] + bbox[3]) / 2.0,
                              "x0": bbox[0], "x1": bbox[2], "text": t})
        doc.close()
    except Exception:  # noqa: BLE001
        pass
    return lines


def _heading_prefix(text):
    """Leading numbered prefix of a heading line, or None."""
    m = HEADING_PREFIX_RE.match(text.strip())
    return m.group(1) if m else None


def _locate_heading_line(text_lines, heading):
    """Return the rendered heading line dict or None.

    Order of preference:
      1. same column, y inside the source vertical band
      2. (prefixed headings only) same column, y up to FALLBACK_RANGE
         below the source anchor -- flow_locked formulas push headings
    Within the candidates, require the numbered prefix to match (when the
    source has one); pick the nearest y.
    """
    x0, y1, y2 = heading["bbox"][0], heading["bbox"][1], heading["bbox"][3]
    src_prefix = _heading_prefix(heading["source"])
    base = [ln for ln in text_lines
            if len(ln["text"]) <= MAX_LINE_CHARS
            and abs(ln["x0"] - x0) < COLUMN_TOL]
    bands = [("tight", y1 - TIGHT_TOP, y2 + TIGHT_BOTTOM)]
    if src_prefix:
        bands.append(("fallback", y1 - TIGHT_TOP, y1 + FALLBACK_RANGE))
    seen = set()
    for _, top, bottom in bands:
        cands = [ln for ln in base if top <= ln["y"] <= bottom
                 and ln["y"] not in seen]
        if src_prefix:
            cands = [ln for ln in cands
                     if _heading_prefix(ln["text"]) == src_prefix]
        if cands:
            seen.update(ln["y"] for ln in cands)
            return min(cands, key=lambda ln: abs(ln["y"] - (y1 + y2) / 2.0))
    return None


def _duplicate_semantic_tokens(text):
    """Duplicated meaningful tokens inside ONE rendered heading line.

    A single heading is a short phrase -- a 2-char CJK word or an English
    word appearing twice is, in this corpus, always a split-and-join
    artifact (p011 "生成 ... 生成").  The whole-page scan that previously
    produced false positives is gone: only the single located line is
    inspected, so no stopword exemption is needed.
    """
    dup = []
    cjk_words = re.findall(r"[\u4e00-\u9fff]{2}", text)
    seen = {}
    for w in cjk_words:
        seen[w] = seen.get(w, 0) + 1
    for w, n in seen.items():
        if n >= 2:
            dup.append({"token": w, "count": n, "kind": "cjk"})
    eng_words = re.findall(r"[A-Za-z]{3,}", text.lower())
    seen_e = {}
    for w in eng_words:
        seen_e[w] = seen_e.get(w, 0) + 1
    for w, n in seen_e.items():
        if n >= 2:
            dup.append({"token": w, "count": n, "kind": "english"})
    return dup


def _source_fragment_reuse(page_model):
    """Any heading source fragment consumed by more than one region."""
    frag_owners = {}
    problems = []
    for region in page_model.get("regions", []):
        if region.get("type") != "text":
            continue
        para = region["payload"]
        pid = para.get("paragraph_id")
        role = para.get("style_role") or "body"
        for frag in para.get("source_fragments", []):
            fid = frag.get("flow_fragment_id") or frag.get("fragment_id")
            if not fid:
                continue
            if fid in frag_owners and frag_owners[fid] != pid:
                problems.append({
                    "fragment_id": fid,
                    "consumed_by": [frag_owners[fid], pid],
                    "roles": [frag_owners.get(fid, "?"), role],
                    "defect": "source_fragment_reused",
                })
            frag_owners[fid] = pid
    headings = {h["para"] for h in _heading_regions(page_model)}
    return [p for p in problems if any(r in headings for r in p["consumed_by"])]


_INSTITUTION_LIST_RE = re.compile(
    r"(?:大学|学院|研究院|学校|研究所|University|Institute|College|Laboratory)")
_AUTHOR_LIST_RE = re.compile(
    r"(?:(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?|[\u4e00-\u9fff]{2,4})"
    r"(?:\s*[*†‡\d]?\s*)){3,}")


def _is_institution_list(text):
    """A rendered line carrying >= 2 institution keywords is an
    affiliation list, not a heading split artifact."""
    return len(_INSTITUTION_LIST_RE.findall(text)) >= 2


def _is_author_list(text):
    """A rendered line with >= 3 names separated by spaces/numbers is an
    author list (the merged full-width AuthorBlock)."""
    return bool(_AUTHOR_LIST_RE.match(text)) and len(text) >= 12


def heading_duplicate_qa(page_model, pdf_path, out_dir=None):
    """Audit rendered headings for duplicate-consumption artifacts."""
    out_dir = Path(out_dir) if out_dir else Path(pdf_path).parent
    text_lines = _pdf_text_lines(pdf_path)
    details = []

    # defect 3: source fragment reuse involving a heading
    for prob in _source_fragment_reuse(page_model):
        details.append({
            "para": prob["consumed_by"][-1],
            "defect": prob["defect"],
            "fragment_id": prob["fragment_id"],
            "consumed_by": prob["consumed_by"],
        })

    # defects 1+2: rendered heading text problems
    for h in _heading_regions(page_model):
        para_id = h["para"]
        line = _locate_heading_line(text_lines, h)
        if line is None:
            continue  # heading not located on this page (cross-page etc.)
        rendered = line["text"]
        dups = _duplicate_semantic_tokens(rendered)
        if dups:
            # Phase 4D.1: an affiliation/author LIST rendered as one
            # full-width block legitimately repeats tokens ("清华大学
            # 北京邮电大学 南洋理工大学 北京交通大学" -- "大学" x4) --
            # that is NOT a split-and-join artifact.
            if _is_institution_list(rendered) or _is_author_list(rendered):
                continue
            details.append({
                "para": para_id,
                "defect": "duplicate_semantic_token",
                "rendered": rendered[:80],
                "duplicates": dups[:4],
                "source": h["source"][:80],
            })

    result = {
        "heading_duplicate_count": len(details),
        "heading_duplicate_details": details[:16],
        "heading_qa_clean": len(details) == 0,
    }
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "heading_duplicate_qa.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))
    result = heading_duplicate_qa(model, args.pdf, out_dir=args.out)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["heading_qa_clean"] else 2


if __name__ == "__main__":
    import sys
    sys.exit(main())
