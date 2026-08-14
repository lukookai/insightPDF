# -*- coding: utf-8 -*-
"""Document-level orchestration above the frozen Phase 4B.1 PageModel."""
from __future__ import annotations

import copy
import re

from flow_layout import _split_translation
from paragraphs import SourceTextBuilder


TERMINAL_RE = re.compile(r"[.!?\u3002\uff01\uff1f:;]\s*$")


def _text_regions(page_model):
    return [region for region in page_model.get("regions", [])
            if region.get("type") == "text"]


def _first_fragment(para):
    fragments = para.get("source_fragments") or []
    return fragments[0] if fragments else None


def _last_fragment(para):
    fragments = para.get("source_fragments") or []
    return fragments[-1] if fragments else None


def detect_cross_page_continuation(left_page, right_page):
    """Return evidence when the adjacent page boundary is one paragraph."""
    left_regions = _text_regions(left_page)
    right_regions = _text_regions(right_page)
    if not left_regions or not right_regions:
        return None
    left = left_regions[-1]["payload"]
    right = right_regions[0]["payload"]
    lf = _last_fragment(left)
    rf = _first_fragment(right)
    if not lf or not rf:
        return None
    left_text = (left.get("source_text") or "").rstrip()
    right_text = (right.get("source_text") or "").lstrip()
    if not left_text or not right_text or TERMINAL_RE.search(left_text):
        return None
    if left.get("style_role") not in ("body", "list_item"):
        return None
    if right.get("style_role") != "body":
        return None
    if not re.match(r"^[a-z(]", right_text):
        return None
    if lf.get("bbox", [0, 0, 0, 0])[3] < left_page["height"] - 110:
        return None
    if rf.get("anchor_y", 999) > 165:
        return None
    if abs((lf.get("base_font_size") or 0)
           - (rf.get("base_font_size") or 0)) > 1.0:
        return None
    # Protected/code runs cannot be safely duplicated across page-local token
    # namespaces.  Formula-bearing boundaries are likewise structural.
    boundary_markup = left.get("translation_source_text", "") + right.get(
        "translation_source_text", "")
    if "{{CODE_" in boundary_markup or "{{FORMULA_" in boundary_markup:
        return None
    return {
        "left_local_id": left["paragraph_id"],
        "right_local_id": right["paragraph_id"],
        "left_source_tail": left_text[-120:],
        "right_source_head": right_text[:120],
        "left_style_role": left.get("style_role"),
        "right_style_role": right.get("style_role"),
        "left_column": lf.get("column"),
        "right_column": rf.get("column"),
        "left_bottom": lf.get("bbox", [0, 0, 0, 0])[3],
        "right_top": rf.get("anchor_y"),
    }


def _join(left, right):
    return SourceTextBuilder.join_text(left, right)


def build_document_model(source_pdf, raw_pages):
    """Deep-copy raw PageModels, stitch page boundaries and globalize IDs."""
    pages = copy.deepcopy(raw_pages)
    links = []
    linked_pairs = set()
    for index in range(len(pages) - 1):
        evidence = detect_cross_page_continuation(pages[index], pages[index + 1])
        if evidence:
            evidence.update({"from_page": index + 1, "to_page": index + 2})
            links.append(evidence)
            linked_pairs.add(((index + 1, evidence["left_local_id"]),
                              (index + 2, evidence["right_local_id"])))

    entries = []
    for page in pages:
        page_number = int(page.get("page") or (len(entries) + 1))
        for region in _text_regions(page):
            entries.append({"page_number": page_number, "region": region,
                            "payload": region["payload"],
                            "local_id": region["payload"]["paragraph_id"]})

    groups = []
    for entry in entries:
        if groups:
            previous = groups[-1][-1]
            key = ((previous["page_number"], previous["local_id"]),
                   (entry["page_number"], entry["local_id"]))
            if key in linked_pairs:
                groups[-1].append(entry)
                continue
        groups.append([entry])

    logical = []
    for group_index, group in enumerate(groups):
        logical_id = "DLP%05d" % group_index
        source_text = ""
        translation_source = ""
        protected_runs = {}
        inline_runs = []
        all_fragments = []
        fragment_index = 0
        for member_index, entry in enumerate(group):
            para = entry["payload"]
            source_text = _join(source_text, para.get("source_text", ""))
            marked = para.get("translation_source_text") or para.get("source_text", "")
            # Namespace CodeRun tokens if a cross-page group ever contains
            # more than one protected run with the same local token.
            for code_index, (token, value) in enumerate(
                    (para.get("protected_runs") or {}).items()):
                new_token = "{{CODE_%d_%d}}" % (member_index, code_index)
                marked = marked.replace(token, new_token)
                protected_runs[new_token] = value
            translation_source = _join(translation_source, marked)
            inline_runs.extend(copy.deepcopy(para.get("inline_runs") or []))
            for fragment in para.get("source_fragments") or []:
                frag = copy.deepcopy(fragment)
                frag["page_number"] = entry["page_number"]
                frag["flow_fragment_id"] = "%s-F%d" % (logical_id, fragment_index)
                frag["document_fragment_index"] = fragment_index
                frag["continuation"] = fragment_index > 0
                all_fragments.append(frag)
                fragment_index += 1

        doc_para = {
            "logical_paragraph_id": logical_id,
            "source_text": source_text,
            "translation_source_text": translation_source,
            "style_role": group[0]["payload"].get("style_role", "body"),
            "inline_runs": inline_runs,
            "protected_runs": protected_runs,
            "source_fragments": all_fragments,
            "page_numbers": sorted({entry["page_number"] for entry in group}),
            "cross_page_continuation": len(group) > 1,
            "source_span_refs": ["P%03d:%s" % (frag["page_number"], sid)
                                 for frag in all_fragments
                                 for sid in frag.get("span_ids", [])],
        }
        logical.append(doc_para)

        # Page-local projection: full semantic text, only local render
        # fragments.  PageModel structure itself is retained.
        for entry in group:
            old = entry["payload"]
            page_number = entry["page_number"]
            local_source_ids = set(old.get("span_ids", []))
            local_fragments = [copy.deepcopy(frag) for frag in all_fragments
                               if frag["page_number"] == page_number
                               and set(frag.get("span_ids", [])) <= local_source_ids]
            projected = copy.deepcopy(old)
            projected.update({
                "paragraph_id": logical_id,
                "logical_paragraph_id": logical_id,
                "source_text": source_text,
                "translation_source_text": translation_source,
                "style_role": doc_para["style_role"],
                "inline_runs": inline_runs,
                "protected_runs": protected_runs,
                "source_fragments": local_fragments,
                "document_source_fragments": all_fragments,
                "cross_page_continuation": len(group) > 1,
                "document_page_numbers": doc_para["page_numbers"],
            })
            entry["region"]["region_id"] = logical_id
            entry["region"]["payload"] = projected

    model = {
        "source_pdf": str(source_pdf),
        "page_count": len(pages),
        "pages": pages,
        "logical_paragraphs": logical,
        "cross_page_links": links,
        "translation_cache": None,
        "assets": {},
        "qa": {},
    }
    return model


def assign_fragment_translations(document_model, translations):
    """Split each global translation once, then project text to its pages."""
    fragment_text = {}
    for para in document_model["logical_paragraphs"]:
        pid = para["logical_paragraph_id"]
        translated = translations.get(pid, "")
        parts = _split_translation(translated, para["source_fragments"])
        para["translated_text"] = translated
        for fragment, text in zip(para["source_fragments"], parts):
            fragment_text[fragment["flow_fragment_id"]] = text
    for page in document_model["pages"]:
        for region in _text_regions(page):
            para = region["payload"]
            para["fragment_translation_texts"] = [
                fragment_text.get(fragment["flow_fragment_id"], "")
                for fragment in para.get("source_fragments", [])]
            para["translated_text"] = translations.get(para["paragraph_id"], "")
    return fragment_text
