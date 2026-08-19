# -*- coding: utf-8 -*-
"""Final-PDF truth QA for logical table-cell translation closure.

The QA deliberately starts from the existing TableModel/LogicalCell geometry.
It never reconstructs a table and never OCRs a page.  Visible text is read
from the PDF text layer inside each cell's locked ``layout_bbox``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pymupdf


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
_CITATION_RE = re.compile(
    r"^(?:\[\s*\d+(?:\s*[-,]\s*\d+)*\s*\]|"
    r"\(?[A-Z][A-Za-z'’-]+(?:\s+et\s+al\.?)?\s*,?\s*(?:19|20)\d{2}[a-z]?\)?)$")
_NUMBER_RE = re.compile(
    r"^[\s+\-−–—~≈<>≤≥=]*(?:\d[\d,.]*)(?:\s*%|\s*[×x]\s*\d+)?\s*$")
_UNIT_RE = re.compile(
    r"^(?:%|°[CF]|ms|s|min|h|Hz|kHz|MHz|GHz|B|KB|MB|GB|TB|"
    r"bit|bits|byte|bytes|px|pt|mm|cm|m|km|mg|g|kg|mL|L|W|kW|V|A)$")
_CODE_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_.:/\-]*\([^\n]*\)|"
    r"[A-Za-z_][A-Za-z0-9_.:/\-]*\s*=\s*\S+|`[^`]+`)$")


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "").casefold()


def _model_or_dataset_identifier(text: str) -> bool:
    """Conservative identifier policy; ordinary table labels stay required."""
    words = _LATIN_WORD_RE.findall(text)
    if not words or len(words) > 4:
        return False
    # Strong identifier evidence: digits, separators used by versions/model
    # names, camel-case, or multi-letter all-caps abbreviations.
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.+\-/]*", text)
    if not tokens:
        return False
    return all(
        bool(re.search(r"\d|[_.+/\-]", token))
        or bool(re.search(r"[a-z][A-Z]", token))
        or (token.isupper() and len(token) >= 2)
        for token in tokens
    )


def table_cell_translation_requirement(source_text: str | None) -> dict[str, Any]:
    """Classify a cell using the task's explicit KEEP allow-list."""
    source = (source_text or "").strip()
    if not source:
        return {"required": False, "route": "KEEP_EMPTY", "reason": "empty"}
    if _CJK_RE.search(source) and not _LATIN_WORD_RE.search(source):
        return {"required": False, "route": "KEEP_TARGET_LANGUAGE",
                "reason": "already_target_language"}
    if _NUMBER_RE.fullmatch(source):
        return {"required": False, "route": "KEEP_NUMERIC", "reason": "numeric"}
    if _UNIT_RE.fullmatch(source):
        return {"required": False, "route": "KEEP_UNIT", "reason": "unit"}
    if _URL_RE.fullmatch(source):
        return {"required": False, "route": "KEEP_URL", "reason": "url"}
    if _CITATION_RE.fullmatch(source):
        return {"required": False, "route": "KEEP_CITATION", "reason": "citation"}
    if _CODE_RE.fullmatch(source):
        return {"required": False, "route": "KEEP_CODE", "reason": "code"}
    if _model_or_dataset_identifier(source):
        return {"required": False, "route": "KEEP_IDENTIFIER",
                "reason": "model_or_dataset_identifier"}
    if not _LATIN_WORD_RE.search(source):
        return {"required": False, "route": "KEEP_NON_TRANSLATABLE_MATH",
                "reason": "non_translatable_math"}
    return {"required": True, "route": "REQUIRED_TRANSLATION",
            "reason": "natural_language"}


def _table_models(page_model: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for index, region in enumerate(page_model.get("regions") or []):
        if region.get("type") != "table" or not region.get("payload"):
            continue
        out.append((str(region.get("region_id") or "table_%d" % index),
                    region["payload"]))
    return out


def _visible_text(page: pymupdf.Page, bbox: list[float]) -> str:
    rect = pymupdf.Rect(*[float(v) for v in bbox])
    # Ownership is by glyph/word centre, not by loose rectangle intersection;
    # that prevents a neighbouring row's line box from contaminating the cell.
    owned = []
    for word in page.get_text("words"):
        wb = pymupdf.Rect(word[:4])
        centre = pymupdf.Point((wb.x0 + wb.x1) / 2.0,
                               (wb.y0 + wb.y1) / 2.0)
        if centre in rect:
            owned.append(word)
    owned.sort(key=lambda word: (round(float(word[1]), 2), float(word[0])))
    return re.sub(r"\s+", " ", " ".join(str(word[4]) for word in owned)).strip()


def table_cell_translation_qa(
        page_model: dict[str, Any], *, final_pdf_path: str | Path,
        translations: dict[str, str] | None = None,
        page_number: int = 0) -> dict[str, Any]:
    """Audit every LogicalCell against canonical target and final PDF text."""
    translations = translations or {}
    doc = pymupdf.open(str(final_pdf_path))
    try:
        page = doc[page_number]
        records: list[dict[str, Any]] = []
        for table_id, model in _table_models(page_model):
            for cell in model.get("cells") or []:
                source = str(cell.get("source_text") or "").strip()
                requirement = table_cell_translation_requirement(source)
                cell_id = str(cell.get("cell_id") or
                              "R%dC%d" % (cell.get("row", -1),
                                          cell.get("col", -1)))
                canonical = (cell.get("canonical_target")
                             or cell.get("translated_text")
                             or translations.get(cell_id) or "")
                status = str(cell.get("translation_status") or "")
                if status == "translated" and canonical:
                    render_text = canonical
                else:
                    render_text = source
                bbox = list(cell.get("layout_bbox") or cell.get("bbox") or [])
                final_text = _visible_text(page, bbox) if len(bbox) == 4 else ""
                src_norm, tgt_norm, final_norm = (_norm(source), _norm(canonical),
                                                   _norm(final_text))
                source_residual = bool(requirement["required"] and src_norm
                                       and src_norm in final_norm)
                target_missing = bool(requirement["required"] and not canonical)
                target_visible = bool(canonical and tgt_norm and tgt_norm in final_norm)
                untranslated = bool(requirement["required"] and
                                    (target_missing or source_residual
                                     or not target_visible))
                source_hits = final_norm.count(src_norm) if src_norm else 0
                target_hits = final_norm.count(tgt_norm) if tgt_norm else 0
                duplicate = bool((canonical and target_hits > 1)
                                 or (source_residual and source_hits > 1))
                double_render = bool(requirement["required"] and canonical
                                     and src_norm != tgt_norm
                                     and source_hits > 0 and target_hits > 0)
                # The cell-local fitter records overflow against the locked
                # layout box.  PDF word rectangles are intentionally not used
                # here: an overlong neighbouring cell may intersect this box
                # while still belonging to its original owner.
                overflow = bool(cell.get("overflow"))
                records.append({
                    "table_id": table_id,
                    "cell_id": cell_id,
                    "row_index": int(cell.get("row", -1)),
                    "col_index": int(cell.get("col", -1)),
                    "bbox": bbox,
                    "source_text": source,
                    "semantic_role": ("table_header" if cell.get("is_header")
                                      else "table_cell"),
                    "translation_required": bool(requirement["required"]),
                    "translation_route": str(cell.get("translation_route")
                                             or requirement["route"]),
                    "translation_requirement_reason": requirement["reason"],
                    "canonical_target": canonical,
                    "render_text": render_text,
                    "render_source": str(cell.get("render_source") or
                                         ("canonical_target" if status == "translated"
                                          and canonical else "source_text")),
                    "final_visible_text": final_text,
                    "target_missing": target_missing,
                    "target_visible": target_visible,
                    "source_residual": source_residual,
                    "duplicate_render": duplicate,
                    "source_target_double_render": double_render,
                    "overflow": overflow,
                    "wrong_owner": False,
                    "untranslated_required": untranslated,
                })
    finally:
        doc.close()

    # Target ownership is a cross-cell invariant: a target visible only in a
    # different LogicalCell is a hard wrong-owner defect.
    for record in records:
        target_norm = _norm(record["canonical_target"])
        if not record["translation_required"] or not target_norm:
            continue
        own_norm = _norm(record["final_visible_text"])
        if target_norm in own_norm:
            continue
        record["wrong_owner"] = any(
            other is not record
            and target_norm in _norm(other["final_visible_text"])
            for other in records)

    # Cache over-completion can make a line-fragment target contain the next
    # continuation cell's entire target.  Treat that as a duplicate render
    # even though the two copies technically have different cell owners.
    ordered = sorted(records, key=lambda item: (
        item["table_id"], item["col_index"], item["row_index"]))
    for previous, current in zip(ordered, ordered[1:]):
        same_column = (previous["table_id"] == current["table_id"]
                       and previous["col_index"] == current["col_index"])
        adjacent = current["row_index"] == previous["row_index"] + 1
        current_source = current["source_text"].lstrip()
        continuation = bool(current_source and current_source[0].islower())
        if not (same_column and adjacent and continuation):
            continue
        previous_target = _norm(previous["canonical_target"])
        current_target = _norm(current["canonical_target"])
        if min(len(previous_target), len(current_target)) >= 4 \
                and (previous_target in current_target
                     or current_target in previous_target):
            previous["duplicate_render"] = True
            current["duplicate_render"] = True

    required = [r for r in records if r["translation_required"]]
    translated = [r for r in required if not r["untranslated_required"]]
    metrics = {
        "required_table_cell_count": len(required),
        "translated_table_cell_count": len(translated),
        "untranslated_required_table_cell_count": sum(
            int(r["untranslated_required"]) for r in records),
        "table_cell_target_missing_count": sum(int(r["target_missing"])
                                               for r in records),
        "table_cell_source_residual_count": sum(int(r["source_residual"])
                                                for r in records),
        "table_cell_duplicate_render_count": sum(int(r["duplicate_render"])
                                                 for r in records),
        "table_cell_source_target_double_render_count": sum(
            int(r["source_target_double_render"]) for r in records),
        "table_cell_overflow_count": sum(int(r["overflow"]) for r in records),
        "table_cell_wrong_owner_count": sum(int(r["wrong_owner"])
                                            for r in records),
    }
    defect_keys = [key for key in metrics if key not in (
        "required_table_cell_count", "translated_table_cell_count")]
    return {
        "schema_version": "visual_v07.table_cell_translation_qa.v1",
        "decision": "pass" if all(metrics[key] == 0 for key in defect_keys)
                    else "fail",
        "metrics": metrics,
        "cells": records,
    }


__all__ = ["table_cell_translation_qa",
           "table_cell_translation_requirement"]


def _main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Audit logical table cells")
    parser.add_argument("--model", required=True)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--translations")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))
    translations = {}
    if args.translations:
        translations = json.loads(Path(args.translations).read_text(
            encoding="utf-8"))
    result = table_cell_translation_qa(
        model, final_pdf_path=args.pdf, translations=translations)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps({"decision": result["decision"],
                      "metrics": result["metrics"]}, ensure_ascii=False))
    return 0 if result["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
