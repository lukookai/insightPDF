# -*- coding: utf-8 -*-
"""LogicalParagraphRenderClosureQA for flowed render fragments."""
from __future__ import annotations

import json
from pathlib import Path


def _norm(text):
    return " ".join((text or "").split()).strip()


def logical_paragraph_render_closure_qa(page_model, translations, flows,
                                        out_dir=None,
                                        bottom_reserved_regions=None):
    paragraphs = {r["payload"].get("paragraph_id"): r["payload"]
                  for r in page_model.get("regions", [])
                  if r.get("type") == "text"}
    by_pid = {}
    reserved = {pid for region in (bottom_reserved_regions or [])
                for pid in region.get("owner_paragraph_ids", [])}
    for flow in flows or []:
        for item in flow.get("items", []):
            if item.get("kind") == "paragraph":
                by_pid.setdefault(item.get("paragraph_id"), []).append(item)
    details = []
    translated_count = fallback_count = unconsumed_count = 0
    for pid, para in paragraphs.items():
        translated = _norm(translations.get(pid))
        if not translated:
            continue
        if pid in reserved:
            # BottomReservedRegion is rendered by its dedicated FootnoteBlock,
            # not by ColumnFlow; it is consumed, not missing.
            translated_count += len(para.get("source_fragments") or [None])
            continue
        fragments = para.get("source_fragments") or []
        rendered = sorted(by_pid.get(pid, []),
                          key=lambda x: x.get("fragment_index", 0))
        local_fallback = 0
        for item in rendered:
            idx = int(item.get("fragment_index", 0))
            source = _norm(fragments[idx].get("source_text")) \
                if idx < len(fragments) else ""
            value = _norm(item.get("render_text"))
            if value and source and value == source and translated != source:
                local_fallback += 1
            elif value:
                translated_count += 1
        fallback_count += local_fallback
        local_unconsumed = max(0, len(fragments) - len(rendered))
        unconsumed_count += local_unconsumed
        if local_fallback or local_unconsumed:
            details.append({
                "logical_paragraph_id": pid,
                "translation_source_text": para.get("translation_source_text"),
                "translated_text": translations.get(pid),
                "rendered_fragments": [x.get("render_text") for x in rendered],
                "source_fallback_fragment_count": local_fallback,
                "unconsumed_translation_fragment_count": local_unconsumed,
            })
    result = {
        "translated_fragment_count": translated_count,
        "source_fallback_fragment_count": fallback_count,
        "unconsumed_translation_fragment_count": unconsumed_count,
        "logical_paragraph_render_closure_clean": (
            fallback_count == 0 and unconsumed_count == 0),
        "details": details[:20],
    }
    if out_dir:
        path = Path(out_dir) / "logical_paragraph_render_closure_qa.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return result
