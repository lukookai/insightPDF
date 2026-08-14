# -*- coding: utf-8 -*-
"""PDF Text Layer QA (Phase 4D.2C).

Verifies that the FINAL PDF's text layer is healthy -- i.e. Chinese text is
still copyable / searchable after the balanced CJK font (Noto Serif SC) is
embedded by Chromium as Type3 subsets.  Uses PyMuPDF as the primary extractor
and PDFium (pypdfium2) as an independent cross-check.

Exemptions (by design, never counted as failure):
    * inline / display formula Atomic SVG (no PDF text layer);
    * figure interior pixels (figure captions ARE text and are counted).

Table cell text is REQUIRED to be extractable and is counted.
"""
from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import pymupdf

try:
    import pypdfium2 as _pdfium  # noqa: N813
except Exception:  # pragma: no cover
    _pdfium = None

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"[0-9]")
_PLACEHOLDER = re.compile(r"\{\{FORMULA_[^}]+\}\}")
_STYLE = re.compile(r"\{\{(?:END_)?(?:BOLD|ITALIC|MONO)_\d+\}\}")
_CODE = re.compile(r"\{\{CODE_([^}]+)\}\}")
_REPLACEMENT = "\ufffd"


# ---------------------------------------------------------------- expected --
def _cell_texts(page_model: dict) -> list[str]:
    out = []
    for r in page_model.get("regions", []):
        if r.get("type") != "table":
            continue
        tbl = r.get("payload") or {}
        for row in tbl.get("rows", []) or []:
            for cell in row.get("cells", []) or []:
                text = cell.get("translated_text") or cell.get("source_text") or ""
                if text.strip():
                    out.append(text)
    return out


def expected_page_text(page_model: dict, translations: dict,
                       flows: list | None = None) -> str:
    """Concatenate the text-layer content that MUST be extractable.

    Formula placeholders are stripped (formula = SVG, no text layer).  Style
    markers are stripped; CODE tokens are replaced by their value using the
    OWNING paragraph's protected_runs (code tokens are only unique within a
    paragraph -- a global dict would collide).  Table cell text is included.

    When ``flows`` is provided, the expected text is the per-page FRAGMENT
    text (flow items' ``render_text``): a logical paragraph split across
    columns/pages only contributes the fragment actually rendered on THIS
    page -- otherwise cross-page fragments would be wrongly counted as lost.
    """
    para_protected = {}
    for r in page_model.get("regions", []):
        if r.get("type") != "text":
            continue
        payload = r.get("payload") or {}
        para_protected[payload.get("paragraph_id")] = payload.get(
            "protected_runs") or {}

    def clean_part(text, pr):
        text = _PLACEHOLDER.sub("", text or "")
        text = _STYLE.sub("", text)
        def repl(m):
            return pr.get("{{CODE_%s}}" % m.group(1), "")
        return _CODE.sub(repl, text)

    parts = []
    if flows:
        for flow in flows or []:
            for item in flow.get("items", []):
                if item.get("kind") != "paragraph":
                    continue
                pid = item.get("paragraph_id")
                rt = item.get("render_text") or translations.get(pid, "")
                if rt:
                    parts.append(clean_part(rt, para_protected.get(pid, {})))
    else:
        for r in page_model.get("regions", []):
            if r.get("type") != "text":
                continue
            payload = r.get("payload") or {}
            zh = translations.get(payload.get("paragraph_id")) or payload.get(
                "translated_text") or ""
            if zh:
                parts.append(clean_part(zh, payload.get("protected_runs") or {}))
    parts.extend(_cell_texts(page_model))
    return "".join(parts)


# --------------------------------------------------------------- extraction --
def _normalise(text: str) -> str:
    return "".join(ch for ch in (text or "") if not ch.isspace())


def _script_recovery(expected_counter: Counter, extracted_counter: Counter) -> float:
    total = sum(expected_counter.values())
    if total == 0:
        return 1.0
    recovered = sum(min(expected_counter[k], extracted_counter.get(k, 0))
                    for k in expected_counter)
    return recovered / total


def _count_script(text: str, regex) -> int:
    return len(regex.findall(text))


def measure_pymupdf(pdf_path: str | Path, page_index: int = 0) -> dict:
    doc = pymupdf.open(str(pdf_path))
    try:
        page = doc[page_index]
        text = page.get_text("text") or ""
        d = page.get_text("dict")
        spans = []
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    t = span.get("text") or ""
                    spans.append({"font": span.get("font") or "",
                                  "size": float(span.get("size") or 0),
                                  "text": t,
                                  "bbox": [float(v) for v in span.get("bbox")]})
        words = page.get_text("words")
    finally:
        doc.close()
    empty_spans = [s for s in spans if not s["text"].strip()]
    single_char = [s for s in spans if len(_normalise(s["text"])) == 1]
    suspicious_type3 = [
        s for s in spans
        if "Type3" in s["font"]
        and (not s["text"].strip() or _REPLACEMENT in s["text"])]
    return {
        "text": text,
        "spans": spans,
        "word_count": len(words),
        "extracted_chars": len(_normalise(text)),
        "empty_span_count": len(empty_spans),
        "single_char_span_count": len(single_char),
        "suspicious_type3_span_count": len(suspicious_type3),
        "replacement_character_count": text.count(_REPLACEMENT),
    }


def measure_pdfium(pdf_path: str | Path, page_index: int = 0) -> dict:
    if _pdfium is None:
        return {"available": False, "text": ""}
    try:
        pdf = _pdfium.PdfDocument(str(pdf_path))
        page = pdf[page_index]
        tp = page.get_textpage()
        text = tp.get_text_range() or ""
        tp.close()
        page.close()
        pdf.close()
        return {"available": True, "text": text,
                "extracted_chars": len(_normalise(text))}
    except Exception as exc:  # noqa: BLE001
        return {"available": True, "text": "", "error": str(exc)}


# --------------------------------------------------------------------- gate --
def pdf_text_layer_qa(pdf_path: str | Path, page_model: dict,
                      translations: dict, out_dir: str | Path | None = None,
                      page_label: str = "page", flows: list | None = None) -> dict:
    expected = expected_page_text(page_model, translations, flows)
    norm_expected = _normalise(expected)
    expected_counter = Counter(norm_expected)

    pm = measure_pymupdf(pdf_path)
    extracted = pm["text"]
    norm_extracted = _normalise(extracted)
    extracted_counter = Counter(norm_extracted)
    pdi = measure_pdfium(pdf_path)

    unicode_ratio = _script_recovery(expected_counter, extracted_counter)
    cjk_ratio = _script_recovery(
        Counter(c for c in norm_expected if _CJK.match(c)),
        Counter(c for c in norm_extracted if _CJK.match(c)))
    latin_ratio = _script_recovery(
        Counter(c for c in norm_expected if _LATIN.match(c)),
        Counter(c for c in norm_extracted if _LATIN.match(c)))
    numeric_ratio = _script_recovery(
        Counter(c for c in norm_expected if _DIGIT.match(c)),
        Counter(c for c in norm_extracted if _DIGIT.match(c)))

    # a "text cell" that is unrecoverable: a paragraph whose fragment text
    # (the part rendered on this page) is entirely missing from extraction
    unrecoverable_cells = 0
    if flows:
        para_texts = [(it.get("paragraph_id"), it.get("render_text") or "")
                      for flow in flows or []
                      for it in flow.get("items", [])
                      if it.get("kind") == "paragraph"]
    else:
        para_texts = []
        for r in page_model.get("regions", []):
            if r.get("type") != "text":
                continue
            payload = r.get("payload") or {}
            para_texts.append(
                (payload.get("paragraph_id"),
                 translations.get(payload.get("paragraph_id"))
                 or payload.get("translated_text") or ""))
    for pid, zh in para_texts:
        zh_norm = _normalise(_PLACEHOLDER.sub("", _STYLE.sub("", zh or "")))
        if len(zh_norm) < 2:
            continue
        # does the fragment's CJK content appear in the extraction?
        para_cjk = Counter(c for c in zh_norm if _CJK.match(c))
        if para_cjk and _script_recovery(para_cjk, extracted_counter) < 0.5:
            unrecoverable_cells += 1

    suspicious = pm["suspicious_type3_span_count"]
    pdfium_ok = (not pdi.get("available")
                 or _script_recovery(expected_counter,
                                     Counter(_normalise(pdi.get("text") or ""))) >= 0.99)
    hard = {
        "unicode_recovery_ratio": round(unicode_ratio, 4),
        "cjk_recovery_ratio": round(cjk_ratio, 4),
        "replacement_character_count": pm["replacement_character_count"],
        "unrecoverable_text_cell_count": unrecoverable_cells,
        "pdfium_cross_check": bool(pdfium_ok),
    }
    hard_ok = (unicode_ratio >= 0.99 and cjk_ratio >= 0.99
               and pm["replacement_character_count"] == 0
               and unrecoverable_cells == 0 and pdfium_ok)
    result = {
        "page": page_label,
        "source_expected_text_chars": len(norm_expected),
        "rendered_extractable_chars": len(norm_extracted),
        "unicode_recovery_ratio": round(unicode_ratio, 4),
        "cjk_recovery_ratio": round(cjk_ratio, 4),
        "latin_recovery_ratio": round(latin_ratio, 4),
        "numeric_recovery_ratio": round(numeric_ratio, 4),
        "replacement_character_count": pm["replacement_character_count"],
        "empty_span_count": pm["empty_span_count"],
        "fragmented_character_ratio": round(
            pm["single_char_span_count"] / max(len(pm["spans"]), 1), 4),
        "suspicious_type3_span_count": suspicious,
        "unrecoverable_text_cell_count": unrecoverable_cells,
        "pdfium": {"available": pdi.get("available"),
                   "cross_check_ok": pdfium_ok},
        "hard": hard,
        "decision": "pass" if hard_ok else "fail",
        "note": ("Type3 Noto Serif SC subsets are allowed with evidence "
                 "when CJK extraction / recovery is healthy."),
    }
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "%s_pdf_text_layer_qa.json" % page_label).write_text(
            __import__("json").dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8")
    return result
