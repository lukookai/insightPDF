# -*- coding: utf-8 -*-
"""DocumentSemanticQA (Phase 4E.1B) -- report-only.

Runs :class:`DocumentSemanticState` over a document's pages in reading order
and measures whether the reference role is a REAL document state, not a
per-paragraph regex guess.  It reads existing PageModels (no re-render) and
compares the semantic state's reference membership against what the frozen
production actually assigned (reference_item_count etc.).

Metrics:
    reference_start_detected        -- a strong section start was observed
    reference_continuation_preserved-- later pages inherit the state
    reference_role_gap_count        -- paragraphs inside the reference STATE
                                       that production did NOT classify as
                                       reference (the generalization gap)
    reference_false_positive_body_count -- body paragraphs wrongly forced into
                                       the reference state (must stay 0)
    reference_state_orphan_count    -- paragraphs classified reference by
                                       production OUTSIDE any reference state
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from document_semantic_state import DocumentSemanticState, PageEvidence

_CITE_LEAD_RE = re.compile(r"^\s*(?:\[\d+\]|\d+\.)\s+", re.I)


def _is_ref_item(text):
    from translation_status import is_reference_item
    return bool(is_reference_item({"type": "paragraph", "source_text": text or ""}))


def page_evidence(page_no, page_model) -> PageEvidence:
    headings, paragraphs, style_roles = [], [], []
    ref_hits = 0
    cite_hits = 0
    n = 0
    for r in page_model.get("regions", []):
        if r.get("type") != "text":
            continue
        p = r.get("payload") or {}
        src = (p.get("source_text") or "").strip()
        if not src:
            continue
        n += 1
        paragraphs.append(src)
        role = (p.get("style_role") or "body").lower()
        style_roles.append(role)
        bb = p.get("bbox") or [0, 0, 0, 0]
        y = (float(bb[1]) + float(bb[3])) / 2
        if role in ("heading", "appendix_heading"):
            headings.append((src, role, y))
        if _is_ref_item(src):
            ref_hits += 1
        if _CITE_LEAD_RE.match(src):
            cite_hits += 1
    return PageEvidence(page_no, headings, paragraphs, style_roles,
                        ref_hits / max(n, 1), cite_hits / max(n, 1))


def document_semantic_qa(pages: list[int], models: dict[int, dict],
                         out_dir=None) -> dict:
    state = DocumentSemanticState()
    per_page = {}
    paragraphs = []
    for pno in pages:
        ev = page_evidence(pno, models.get(pno, {}))
        role = state.observe_page(ev)
        inherited = state.inherited_from_previous_page
        # y-threshold where references starts on THIS page (if it just entered)
        ref_y = None
        if role == "references":
            for text, hrole, y in ev.headings:
                if state._classify_heading(text) == "references":
                    ref_y = y
                    break
        per_page[pno] = {"state": role,
                         "inherited": inherited,
                         "ref_density": round(ev.ref_density, 3),
                         "ref_heading_y": ref_y}
        for r in models.get(pno, {}).get("regions", []):
            if r.get("type") != "text":
                continue
            p = r.get("payload") or {}
            src = (p.get("source_text") or "").strip()
            if not src:
                continue
            bb = p.get("bbox") or [0, 0, 0, 0]
            y = (float(bb[1]) + float(bb[3])) / 2
            if role == "references":
                if inherited or ref_y is None or y >= ref_y:
                    srole = "reference_item"
                else:
                    srole = "normal_body"  # above the References heading
            else:
                srole = role
            written = (p.get("semantic_role") or "")
            # production_ref = the document semantic state actually written
            # (the ONE truth); is_reference_item is only an auxiliary signal
            # and must NOT override the section state machine.
            production_ref = written.startswith("reference")
            paragraphs.append({
                "page": pno,
                "paragraph_id": p.get("paragraph_id"),
                "text": src[:40],
                "semantic_role": srole,
                "written_semantic_role": written,
                "production_ref": production_ref,
                "style_role": (p.get("style_role") or "body"),
            })
    ref_pages = sorted({p for p, info in per_page.items()
                        if info["state"] == "references"})
    # role gap: paragraphs inside reference state but production didn't mark ref
    role_gap = [pp for pp in paragraphs
                if pp["semantic_role"] == "reference_item"
                and not pp["production_ref"]
                and pp["style_role"] not in ("caption",)]
    # orphan: production ref items outside the reference state
    orphans = [pp for pp in paragraphs
               if pp["production_ref"] and pp["semantic_role"] != "reference_item"]
    # false positives: body forced into references by the state machine when
    # the page is a normal body page (shouldn't happen with the evidence rules)
    fp_body = [pp for pp in paragraphs
               if pp["semantic_role"] == "reference_item"
               and pp["style_role"] == "body"
               and not ref_pages]
    result = {
        "schema_version": "phase4e1b.document_semantic_qa.v1",
        "reference_start_detected": bool(ref_pages),
        "reference_start_page": ref_pages[0] if ref_pages else None,
        "reference_pages": ref_pages,
        "reference_continuation_preserved": bool(
            ref_pages and any(per_page[p]["inherited"] for p in ref_pages[1:])),
        "reference_role_gap_count": len(role_gap),
        "reference_false_positive_body_count": len(fp_body),
        "reference_state_orphan_count": len(orphans),
        "per_page": per_page,
        "role_gap_details": role_gap[:12],
        "orphan_details": orphans[:8],
        "hard": {
            "reference_role_gap_count": len(role_gap),
            "reference_state_orphan_count": len(orphans),
        },
        "decision": ("pass" if not role_gap and not orphans else "fail"),
    }
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "document_semantic_qa.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
