# -*- coding: utf-8 -*-
"""Render-truth QA for provenance-backed legacy math PUA normalization.

The audit is deliberately independent from the normalizer.  It observes the
HTML payload and the final PDF text layer, then joins those observations to a
normalization trace when one is supplied.  A legacy PUA character with no
positive-width PDF glyph is a visible failure even if Chromium retained the
character in its text layer.
"""
from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import pymupdf


def _codepoint(character: str) -> str:
    return "U+%04X" % ord(character)


def is_private_use_character(character: str) -> bool:
    value = ord(character)
    return (0xE000 <= value <= 0xF8FF
            or 0xF0000 <= value <= 0xFFFFD
            or 0x100000 <= value <= 0x10FFFD)


def _pdf_characters(pdf_path: str | Path) -> list[dict[str, Any]]:
    document = pymupdf.open(str(pdf_path))
    try:
        rows: list[dict[str, Any]] = []
        for page_index, page in enumerate(document):
            raw = page.get_text("rawdict")
            for block in raw.get("blocks") or []:
                for line in block.get("lines") or []:
                    for span in line.get("spans") or []:
                        for char in span.get("chars") or []:
                            value = str(char.get("c") or "")
                            bbox = [float(item)
                                    for item in (char.get("bbox") or [])]
                            if not value or len(bbox) != 4:
                                continue
                            rows.append({
                                "page_index": page_index,
                                "character": value,
                                "unicode_codepoint": _codepoint(value),
                                "unicode_name": unicodedata.name(
                                    value, "PRIVATE-USE CHARACTER"),
                                "font": str(span.get("font") or ""),
                                "bbox": [round(item, 3) for item in bbox],
                                "bbox_width": round(bbox[2] - bbox[0], 3),
                                "bbox_height": round(bbox[3] - bbox[1], 3),
                            })
        return rows
    finally:
        document.close()


def _normalization_records(trace: dict[str, Any] | None
                           ) -> list[dict[str, Any]]:
    if not trace:
        return []
    records = trace.get("records")
    if isinstance(records, list):
        return [row for row in records if isinstance(row, dict)]
    return []


def audit_legacy_math_pua_artifact(
        html_path: str | Path,
        pdf_path: str | Path,
        normalization_trace: dict[str, Any] | None = None,
        required_source_codepoints: Iterable[str] | None = None,
        width_epsilon_pt: float = 0.05,
        ) -> dict[str, Any]:
    """Audit one rendered HTML/PDF pair without changing either artifact."""
    html_path = Path(html_path)
    pdf_path = Path(pdf_path)
    html = html_path.read_text(encoding="utf-8")
    pdf_chars = _pdf_characters(pdf_path)
    records = _normalization_records(normalization_trace)

    html_pua = [character for character in html
                if is_private_use_character(character)]
    pdf_pua = [row for row in pdf_chars
               if is_private_use_character(row["character"])]
    zero_width = [row for row in pdf_pua
                  if float(row["bbox_width"]) <= width_epsilon_pt]

    # Every HTML PUA occurrence must produce a positive-width glyph.  Extra
    # zero-width PUA rows are also failures; max() avoids double counting the
    # same occurrence from the HTML and PDF views.
    positive_pdf_pua_count = sum(
        float(row["bbox_width"]) > width_epsilon_pt for row in pdf_pua)
    visible_failure_count = max(
        max(len(html_pua) - positive_pdf_pua_count, 0), len(zero_width))

    required = set(required_source_codepoints or [])
    if not required:
        required = {str(row.get("source_codepoint")) for row in records
                    if row.get("required") is not False
                    and row.get("source_codepoint")}
    unresolved = [row for row in records
                  if row.get("required") is not False
                  and not row.get("normalized")]
    resolved_source = {str(row.get("source_codepoint")) for row in records
                       if row.get("normalized")}
    explicitly_unresolved_source = {
        str(row.get("source_codepoint")) for row in unresolved
        if row.get("source_codepoint")}
    unresolved_required_codepoints = sorted(
        required - resolved_source - explicitly_unresolved_source)

    normalized_rows = [row for row in records if row.get("normalized")]
    expected_targets = [str(row.get("standard_character") or "")
                        for row in normalized_rows]
    expected_targets = [value for value in expected_targets if value]
    pdf_text = "".join(row["character"] for row in pdf_chars)
    normalized_missing = []
    for row in normalized_rows:
        character = str(row.get("standard_character") or "")
        if not character:
            normalized_missing.append({
                "record_id": row.get("record_id"),
                "reason": "normalization_trace_has_no_standard_character",
            })
            continue
        if character not in html or character not in pdf_text:
            normalized_missing.append({
                "record_id": row.get("record_id"),
                "standard_codepoint": row.get("standard_codepoint"),
                "html_present": character in html,
                "pdf_text_present": character in pdf_text,
            })

    target_counts: dict[str, int] = {}
    for character in expected_targets:
        target_counts[character] = target_counts.get(character, 0) + 1
    duplicate_count = sum(max(html.count(character) - count, 0)
                          for character, count in target_counts.items())

    wrong_character = [row for row in normalized_rows
                       if (not row.get("registry_match")
                           or row.get("standard_character")
                           != row.get("registry_standard_character"))]

    metrics = {
        "legacy_pua_html_count": len(html_pua),
        "legacy_pua_pdf_count": len(pdf_pua),
        "legacy_pua_visible_failure_count": visible_failure_count,
        "zero_width_math_glyph_count": len(zero_width),
        "legacy_pua_normalized_count": len(normalized_rows),
        "unresolved_required_pua_count": (
            len(unresolved) + len(unresolved_required_codepoints)),
        "normalized_math_glyph_missing_count": len(normalized_missing),
        "normalization_wrong_character_count": len(wrong_character),
        "normalization_duplicate_character_count": duplicate_count,
    }
    after_pass = (
        metrics["legacy_pua_normalized_count"] > 0
        and metrics["unresolved_required_pua_count"] == 0
        and metrics["zero_width_math_glyph_count"] == 0
        and metrics["normalized_math_glyph_missing_count"] == 0
        and metrics["normalization_wrong_character_count"] == 0
        and metrics["normalization_duplicate_character_count"] == 0)
    return {
        "schema_version": "visual_v07.legacy_math_pua_qa.v1",
        "html_path": str(html_path.resolve()),
        "pdf_path": str(pdf_path.resolve()),
        "width_epsilon_pt": width_epsilon_pt,
        "metrics": metrics,
        "old_artifact_red": bool(
            metrics["legacy_pua_visible_failure_count"] > 0
            and metrics["zero_width_math_glyph_count"] > 0),
        "after_hard_gate_pass": after_pass,
        "unresolved_required_codepoints": unresolved_required_codepoints,
        "zero_width_math_glyphs": zero_width,
        "normalized_math_glyph_missing": normalized_missing,
        "normalization_wrong_character": wrong_character,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit visible legacy math PUA rendering")
    parser.add_argument("--html", required=True)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--trace")
    parser.add_argument("--required-source-codepoint", action="append",
                        default=[])
    parser.add_argument("--expect", choices=("red", "pass"),
                        default="pass")
    parser.add_argument("--output")
    args = parser.parse_args()
    trace = (json.loads(Path(args.trace).read_text(encoding="utf-8"))
             if args.trace else None)
    audit = audit_legacy_math_pua_artifact(
        args.html, args.pdf, trace, args.required_source_codepoint)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2),
            encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    success = (audit["old_artifact_red"] if args.expect == "red"
               else audit["after_hard_gate_pass"])
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
