# -*- coding: utf-8 -*-
"""Phase 4E.1B-C2.1 re-render: apply ownership fix + placeholder restoration.

Re-renders the affected pages (fixtures) with the C2.1 Render-Exclusivity fix
applied at the translation level (canonical single-anchor inline formula +
restored dropped placeholders), WITHOUT re-running detection/translation.

The fix is deterministic and document-general:
  * each inline formula renders exactly once, in its anchor paragraph;
  * dropped formula placeholders are restored at the proportional source
    position (no API call).

Flows / grid / typography are reused unchanged so geometry stays frozen.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for path in (HERE, REPO / "tools" / "table_html_render",
             REPO / "实现源码" / "pdf_translator"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from _chromium_pdf import render_html_to_pdf  # noqa: E402
from html_render import build_unified_html  # noqa: E402
from front_matter import classify_front_matter  # noqa: E402
from typography import build_typography  # noqa: E402

DING_PDF = (Path.home() / "Desktop" /
            "Ding_SynthRGB-T_Language-Vision_Guided_"
            "Image_Translation_for_Diversity_Synthesis_CVPR_2026_paper.pdf")
DING_OUT = REPO / "outputs" / "phase4e1_ding_generalization"
OUT = REPO / "outputs" / "phase4e1b_c21_formula_ownership"
FIXTURES = (3, 4, 5)

FORMULA_TOKEN_RE = re.compile(r"\{\{FORMULA_[A-Z0-9_]+\}\}")


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _page_dir(page):
    return DING_OUT / "pages" / ("p%03d" % page)


def _span_bbox_map(page_model):
    out = {}
    for r in page_model.get("regions", []):
        if r.get("type") != "text":
            continue
        for line in (r.get("payload") or {}).get("lines", []):
            for s in line.get("spans", []):
                out[s.get("id")] = s.get("bbox")
    return out


def _para_of_span(page_model):
    out = {}
    for r in page_model.get("regions", []):
        if r.get("type") != "text":
            continue
        pid = (r.get("payload") or {}).get("paragraph_id")
        for sid in (r.get("payload") or {}).get("span_ids", []):
            out[sid] = pid
    return out


def _anchor_paragraphs(page_model):
    """inline formula_id -> anchor paragraph_id (top-left-most span)."""
    fsm = page_model.get("formula_span_map", {})
    span_bbox = _span_bbox_map(page_model)
    para_of = _para_of_span(page_model)
    formula_spans = {}
    for sid, token in fsm.items():
        fid = token.strip("{}").replace("FORMULA_", "")
        formula_spans.setdefault(fid, []).append(sid)
    anchors = {}
    for fid, sids in formula_spans.items():
        # top-left-most span = min (top, left)
        anchor = min(sids, key=lambda sid: (
            (span_bbox.get(sid) or [0, 0, 0, 0])[1],
            (span_bbox.get(sid) or [0, 0, 0, 0])[0]))
        anchors[fid] = para_of.get(anchor)
    return anchors


def _promote_multirow_inline(page_model):
    """Multi-row inline formulas are display formulas.

    Reverted to a no-op in C2.1: re-classifying inline->display is a render
    policy change that touches paragraph flow (frozen).  The multi-row inline
    formulas are instead handled by canonical single-anchor ownership at the
    translation level.
    """
    return set()


def fix_translation(page_model, translations, promoted):
    """Return a fixed translation dict (canonical single anchor + restore)."""
    anchors = _anchor_paragraphs(page_model)
    # source placeholder order per paragraph (for restoration)
    src_tokens = {}
    for r in page_model.get("regions", []):
        if r.get("type") != "text":
            continue
        pid = (r.get("payload") or {}).get("paragraph_id")
        src_tokens[pid] = FORMULA_TOKEN_RE.findall(
            (r.get("payload") or {}).get("source_text") or "")

    fixed = {}
    for pid, tgt in translations.items():
        # 1) dedup placeholders within the paragraph
        seen = set()
        rebuilt = []
        cursor = 0
        for m in FORMULA_TOKEN_RE.finditer(tgt):
            rebuilt.append(tgt[cursor:m.start()])
            tok = m.group(0)
            if tok not in seen:
                rebuilt.append(tok)
                seen.add(tok)
            cursor = m.end()
        rebuilt.append(tgt[cursor:])
        base = "".join(rebuilt)
        # 2) restore dropped placeholders at proportional source position
        present = set(FORMULA_TOKEN_RE.findall(base))
        missing = [tok for tok in src_tokens.get(pid, []) if tok not in present]
        if missing:
            plain = FORMULA_TOKEN_RE.sub("", base)
            for tok in missing:
                idx = src_tokens[pid].index(tok)
                pos = int(len(plain) * (idx / len(src_tokens[pid])))
                plain = plain[:pos] + tok + plain[pos:]
            base = plain
        fixed[pid] = base

    # 3) cross-paragraph canonical: keep each inline formula only in its
    #    anchor paragraph; promoted (display) formulas have NO placeholder.
    formula_paras = {}
    for pid, tgt in fixed.items():
        for m in FORMULA_TOKEN_RE.finditer(tgt):
            fid = m.group(0).strip("{}").replace("FORMULA_", "")
            formula_paras.setdefault(fid, []).append(pid)
    for fid, pids in formula_paras.items():
        token = "{{FORMULA_%s}}" % fid
        if fid in promoted:
            for pid in pids:
                fixed[pid] = fixed[pid].replace(token, "")
            continue
        anchor = anchors.get(fid)
        for pid in pids:
            if pid == anchor:
                continue
            fixed[pid] = fixed[pid].replace(token, "")
    return fixed


def _render_page(page, page_model, translations, flows, grid, typo_profile):
    page_dir = _page_dir(page)
    # flows carry render_text from the ORIGINAL translation; refresh each
    # paragraph flow item with the fixed translation so the re-render uses
    # the canonical single-anchor / restored placeholders.
    flows = _refresh_flow_text(flows, translations)
    ty = build_typography(body_size=(grid or {}).get("body_size_estimated"),
                          profile=typo_profile)
    frontmatter = classify_front_matter(str(DING_PDF), page - 1, grid or {})
    table_model = [r["payload"] for r in page_model.get("regions", [])
                   if r.get("type") == "table" and r.get("payload")]
    html = build_unified_html(page_model, translations, DING_PDF, page_dir,
                              table_model=table_model, flows=flows,
                              grid=grid, frontmatter=frontmatter,
                              typography=ty)
    html_path = page_dir / "zh_fixed.html"
    pdf_path = page_dir / "zh_fixed.pdf"
    html_path.write_text(html, encoding="utf-8")
    render_html_to_pdf(html_path, pdf_path)
    return html_path, pdf_path


def _dedup_tokens(text):
    """Deduplicate formula placeholders within a fragment's text."""
    seen = set()
    out = []
    cursor = 0
    for m in FORMULA_TOKEN_RE.finditer(text):
        out.append(text[cursor:m.start()])
        tok = m.group(0)
        if tok not in seen:
            out.append(tok)
            seen.add(tok)
        cursor = m.end()
    out.append(text[cursor:])
    return "".join(out)


def _refresh_flow_text(flows, translations):
    """Refresh paragraph flow render_text with the fixed translation.

    Single-fragment paragraphs take the fixed translation verbatim (this also
    restores dropped placeholders).  Cross-column paragraphs keep their own
    per-fragment text and only deduplicate placeholders inside each fragment,
    so the placeholder never leaks into the other column fragment.
    """
    from collections import defaultdict
    frags_by_pid = defaultdict(int)
    for flow in flows:
        for it in flow.get("items", []):
            if it.get("kind") == "paragraph":
                frags_by_pid[it.get("paragraph_id")] += 1

    out = []
    for flow in flows:
        items = []
        for it in flow.get("items", []):
            it = dict(it)
            if it.get("kind") == "paragraph" and it.get("paragraph_id") in translations:
                pid = it["paragraph_id"]
                if frags_by_pid[pid] == 1:
                    it["render_text"] = translations[pid]
                else:
                    it["render_text"] = _dedup_tokens(it.get("render_text", ""))
            items.append(it)
        flow = dict(flow)
        flow["items"] = items
        out.append(flow)
    return out


def main():
    from typography_profile import DocumentTypographyProfile
    profile = DocumentTypographyProfile(
        REPO / "outputs" / "phase4d2a_typography_audit"
        / "balanced_chinese_profile.json")

    for page in FIXTURES:
        page_dir = _page_dir(page)
        model = load(page_dir / "stitched_page_model.json", {})
        translations = load(page_dir / "translation.json", {})
        qa = load(page_dir / "qa.json", {})
        flows = qa.get("flows", [])
        grid = qa.get("page_grid") or {}
        if not model or not translations:
            print("skip p%03d (missing artifacts)" % page)
            continue

        promoted = _promote_multirow_inline(model)
        if promoted:
            print("p%03d promoted multi-row inline -> display: %s"
                  % (page, sorted(promoted)))
        fixed = fix_translation(model, translations, promoted)
        dump(page_dir / "translation_fixed.json", fixed)
        html_path, pdf_path = _render_page(
            page, model, fixed, flows, grid, profile)
        print("re-rendered p%03d -> %s" % (page, pdf_path.name))


if __name__ == "__main__":
    main()
