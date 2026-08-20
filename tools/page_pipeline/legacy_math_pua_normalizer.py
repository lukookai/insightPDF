# -*- coding: utf-8 -*-
"""Normalize verified legacy math PUA glyphs before HTML rendering.

The same private-use codepoint can mean different things in unrelated legacy
fonts.  Consequently the registry below is never consulted by codepoint
alone: a match also requires the embedded source font family, PostScript
name, and glyph name recovered from the PDF font program.  Low-confidence or
unknown provenance is reported and left byte-for-byte unchanged.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import pymupdf


def codepoint(character: str) -> str:
    return "U+%04X" % ord(character)


def is_private_use_character(character: str) -> bool:
    value = ord(character)
    return (0xE000 <= value <= 0xF8FF
            or 0xF0000 <= value <= 0xFFFFD
            or 0x100000 <= value <= 0x10FFFD)


@dataclass(frozen=True)
class LegacyMathPUAMapping:
    registry_id: str
    legacy_font_family: str
    legacy_postscript_name: str
    legacy_codepoint: str
    legacy_glyph_name: str
    standard_character: str
    confidence: float
    evidence: tuple[str, ...]

    @property
    def standard_codepoint(self) -> str:
        return codepoint(self.standard_character)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence"] = list(self.evidence)
        value["standard_codepoint"] = self.standard_codepoint
        value["standard_unicode_name"] = unicodedata.name(
            self.standard_character, "")
        return value


# This is a registry record, not a code branch.  Adding a mapping requires the
# same four-way provenance evidence and a reviewed standard Unicode target.
DEFAULT_LEGACY_MATH_PUA_REGISTRY = (
    LegacyMathPUAMapping(
        registry_id="stix1-calligraphy-regular-uniE232-bold-script-capital-f",
        legacy_font_family="STIXMathCalligraphy",
        legacy_postscript_name="STIXMathCalligraphy-Regular",
        legacy_codepoint="U+E232",
        legacy_glyph_name="uniE232",
        standard_character="\U0001D4D5",
        confidence=1.0,
        evidence=(
            "embedded Type1 family and PostScript names identify the STIX 1 "
            "calligraphy face",
            "embedded Encoding names the source glyph uniE232",
            "PDF text trace records the source glyph identity and positive "
            "source ink",
            "the reviewed standard Unicode identity is MATHEMATICAL BOLD "
            "SCRIPT CAPITAL F (U+1D4D5)",
        ),
    ),
)


def registry_as_dicts(
        registry: Iterable[LegacyMathPUAMapping] =
        DEFAULT_LEGACY_MATH_PUA_REGISTRY) -> list[dict[str, Any]]:
    return [row.to_dict() for row in registry]


def _strip_subset_prefix(value: str) -> str:
    return re.sub(r"^[A-Z]{6}\+", "", value or "")


def _font_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _strip_subset_prefix(value).casefold())


def _font_name_matches(trace_font: str, base_font: str) -> bool:
    left = _font_key(trace_font)
    right = _font_key(base_font)
    # PyMuPDF truncates some Type1 span names ("...-Regu").  Prefix matching
    # is limited to long normalized names so unrelated short fonts cannot be
    # accidentally joined.
    return bool(left and right and (left == right
                or (min(len(left), len(right)) >= 16
                    and (left.startswith(right) or right.startswith(left)))))


def _decode_type1_identity(data: bytes, fallback_name: str
                           ) -> dict[str, Any]:
    clear = (data or b"").split(b"eexec", 1)[0].decode(
        "latin-1", errors="replace")

    def capture(pattern: str) -> str | None:
        match = re.search(pattern, clear)
        return match.group(1).strip() if match else None

    font_name = capture(r"/FontName\s+/([^\s]+)") or fallback_name
    full_name = capture(r"/FullName\s+\(([^)]*)\)")
    family_name = capture(r"/FamilyName\s+\(([^)]*)\)")
    encoding = {
        int(index): glyph for index, glyph in re.findall(
            r"dup\s+(\d+)\s+/([^\s]+)\s+put", clear)
    }
    return {
        "postscript_name": _strip_subset_prefix(font_name),
        "full_name": full_name,
        "font_family": family_name,
        "encoding": encoding,
        "encoded_glyph_names": sorted(set(encoding.values())),
    }


@lru_cache(maxsize=32)
def _page_font_programs(pdf_path: str, page_index: int
                        ) -> tuple[dict[str, Any], ...]:
    document = pymupdf.open(pdf_path)
    try:
        rows = []
        for values in document[int(page_index)].get_fonts(full=True):
            xref, extension, font_type, base_font, resource_name, encoding, \
                referencer = values
            embedded_name, extracted_extension, extracted_type, data = (
                document.extract_font(int(xref)))
            identity = (_decode_type1_identity(data, embedded_name)
                        if data and (str(extension).casefold() == "pfa"
                                     or str(font_type).casefold() == "type1")
                        else {
                            "postscript_name": _strip_subset_prefix(
                                embedded_name or base_font),
                            "full_name": None,
                            "font_family": None,
                            "encoding": {},
                            "encoded_glyph_names": [],
                        })
            rows.append({
                "xref": int(xref),
                "base_font": str(base_font or ""),
                "resource_name": str(resource_name or ""),
                "pdf_encoding": str(encoding or ""),
                "referencer": int(referencer or 0),
                "extension": str(extracted_extension or extension or ""),
                "font_type": str(extracted_type or font_type or ""),
                **identity,
            })
        return tuple(rows)
    finally:
        document.close()


def _font_program_for_trace(pdf_path: str, page_index: int,
                            trace_font: str) -> dict[str, Any] | None:
    matches = [row for row in _page_font_programs(pdf_path, page_index)
               if _font_name_matches(trace_font, row["base_font"])
               or _font_name_matches(
                   trace_font, str(row.get("postscript_name") or ""))]
    if len(matches) != 1:
        return None
    return matches[0]


def _bbox_center_inside(inner: list[float], outer: list[float] | None
                        ) -> bool:
    if not outer or len(outer) != 4:
        return True
    center_x = (inner[0] + inner[2]) / 2.0
    center_y = (inner[1] + inner[3]) / 2.0
    return (float(outer[0]) <= center_x <= float(outer[2])
            and float(outer[1]) <= center_y <= float(outer[3]))


@lru_cache(maxsize=32)
def _page_source_pua_provenance(pdf_path: str, page_index: int
                                ) -> tuple[dict[str, Any], ...]:
    document = pymupdf.open(pdf_path)
    try:
        trace = document[int(page_index)].get_texttrace()
    finally:
        document.close()
    output = []
    record_number = 0
    for span_index, span in enumerate(trace):
        trace_font = str(span.get("font") or "")
        program = _font_program_for_trace(pdf_path, page_index, trace_font)
        for char_index, values in enumerate(span.get("chars") or []):
            unicode_value, glyph_id, origin, bbox = values
            character = chr(int(unicode_value))
            if not is_private_use_character(character):
                continue
            glyph_name = "uni%04X" % int(unicode_value)
            program_has_glyph = bool(
                program and glyph_name in (
                    program.get("encoded_glyph_names") or []))
            output.append({
                "source_provenance_id": "p%04d-s%04d-c%04d-pua%03d" % (
                    int(page_index), span_index, char_index, record_number),
                "source_pdf_path": str(Path(pdf_path).resolve()),
                "source_page_index": int(page_index),
                "source_span_index": span_index,
                "source_char_index": char_index,
                "source_sequence_number": span.get("seqno"),
                "source_character": character,
                "source_codepoint": codepoint(character),
                "source_span_font": trace_font,
                "source_font_family": (
                    program.get("font_family") if program else None),
                "source_postscript_name": (
                    program.get("postscript_name") if program else None),
                "source_font_xref": program.get("xref") if program else None,
                "source_glyph_id": int(glyph_id),
                "source_glyph_name": glyph_name if program_has_glyph else None,
                "source_font_program_contains_glyph_name": program_has_glyph,
                "source_origin": [round(float(item), 3) for item in origin],
                "source_bbox": [round(float(item), 3) for item in bbox],
                "source_bbox_width": round(float(bbox[2] - bbox[0]), 3),
                "source_ink_exists": bool(float(bbox[2] - bbox[0]) > 0.05
                                          and float(bbox[3] - bbox[1]) > 0.05),
                "source_span_provenance_complete": bool(
                    program and program_has_glyph
                    and program.get("font_family")
                    and program.get("postscript_name")),
            })
            record_number += 1
    return tuple(output)


def extract_source_pua_provenance(
        pdf_path: str | Path, page_index: int,
        source_bbox: Iterable[float] | None = None,
        ) -> list[dict[str, Any]]:
    resolved = str(Path(pdf_path).resolve())
    bbox = ([float(item) for item in source_bbox]
            if source_bbox is not None else None)
    return [copy.deepcopy(row)
            for row in _page_source_pua_provenance(resolved, int(page_index))
            if _bbox_center_inside(row["source_bbox"], bbox)]


class LegacyMathPUANormalizer:
    """Apply only exact, provenance-complete registry matches."""

    def __init__(
            self,
            registry: Iterable[LegacyMathPUAMapping] =
            DEFAULT_LEGACY_MATH_PUA_REGISTRY,
            ) -> None:
        self.registry = tuple(registry)
        self.required_source_codepoints = {
            row.legacy_codepoint for row in self.registry}

    @staticmethod
    def _mapping_matches(mapping: LegacyMathPUAMapping,
                         provenance: dict[str, Any]) -> bool:
        return bool(
            provenance.get("source_span_provenance_complete")
            and provenance.get("source_codepoint")
            == mapping.legacy_codepoint
            and provenance.get("source_font_family")
            == mapping.legacy_font_family
            and provenance.get("source_postscript_name")
            == mapping.legacy_postscript_name
            and provenance.get("source_glyph_name")
            == mapping.legacy_glyph_name
            and mapping.confidence >= 0.95)

    def normalize(self, text: str,
                  source_provenance: Iterable[dict[str, Any]],
                  context: dict[str, Any] | None = None,
                  ) -> dict[str, Any]:
        provenance = list(source_provenance)
        used: set[int] = set()
        output = list(text)
        records = []
        for target_index, character in enumerate(text):
            if not is_private_use_character(character):
                continue
            cp = codepoint(character)
            candidates = [(index, row)
                          for index, row in enumerate(provenance)
                          if index not in used
                          and row.get("source_codepoint") == cp]
            provenance_index = candidates[0][0] if candidates else None
            source = (candidates[0][1] if candidates else None)
            required = cp in self.required_source_codepoints
            matches = ([mapping for mapping in self.registry
                        if source and self._mapping_matches(mapping, source)])
            mapping = matches[0] if len(matches) == 1 else None
            normalized = mapping is not None
            if provenance_index is not None:
                used.add(provenance_index)
            if normalized:
                output[target_index] = mapping.standard_character
            record = {
                "record_id": "legacy-pua-target-%04d" % target_index,
                "target_text_index": target_index,
                "required": required,
                "source_character": character,
                "source_codepoint": cp,
                "source_provenance": copy.deepcopy(source),
                "registry_match": normalized,
                "registry_id": mapping.registry_id if mapping else None,
                "registry_standard_character": (
                    mapping.standard_character if mapping else None),
                "normalized": normalized,
                "standard_character": (
                    mapping.standard_character if mapping else None),
                "standard_codepoint": (
                    mapping.standard_codepoint if mapping else None),
                "confidence": mapping.confidence if mapping else None,
                "resolution": (
                    "normalized_exact_provenance_match" if normalized
                    else ("BLOCK_unresolved_required_pua"
                          if required
                          else "unresolved_unregistered_pua_left_unchanged")),
                "production_context": copy.deepcopy(context or {}),
            }
            records.append(record)
        normalized_text = "".join(output)
        required_unresolved = [row for row in records
                               if row["required"] and not row["normalized"]]
        return {
            "schema_version": "visual_v07.legacy_math_pua_normalization.v1",
            "original_text": text,
            "normalized_text": normalized_text,
            "records": records,
            "metrics": {
                "legacy_pua_target_count": len(records),
                "legacy_pua_normalized_count": sum(
                    bool(row["normalized"]) for row in records),
                "unresolved_required_pua_count": len(required_unresolved),
                "unregistered_pua_count": sum(
                    not row["required"] for row in records),
            },
            "blocked": bool(required_unresolved),
        }

    def normalize_from_pdf(
            self, text: str, pdf_path: str | Path, page_index: int,
            source_bbox: Iterable[float] | None = None,
            context: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
        provenance = extract_source_pua_provenance(
            pdf_path, page_index, source_bbox)
        result = self.normalize(text, provenance, context=context)
        result["source_bbox"] = (
            [round(float(item), 3) for item in source_bbox]
            if source_bbox is not None else None)
        result["source_provenance"] = provenance
        return result


def normalize_legacy_math_pua_text(
        text: str, pdf_path: str | Path, page_index: int,
        source_bbox: Iterable[float] | None = None,
        context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
    return LegacyMathPUANormalizer().normalize_from_pdf(
        text, pdf_path, page_index, source_bbox, context)


def apply_normalization_to_segments(
        segments: Iterable[dict[str, Any]], normalization: dict[str, Any],
        ) -> list[dict[str, Any]]:
    """Apply target-index replacements while preserving paragraph styles."""
    output = copy.deepcopy(list(segments))
    replacements = {
        int(row["target_text_index"]): str(row["standard_character"])
        for row in normalization.get("records") or []
        if row.get("normalized") and row.get("standard_character")
    }
    offset = 0
    for segment in output:
        chars = list(str(segment.get("text") or ""))
        for local_index in range(len(chars)):
            global_index = offset + local_index
            if global_index in replacements:
                chars[local_index] = replacements[global_index]
        segment["text"] = "".join(chars)
        offset += len(chars)
    return output


__all__ = [
    "DEFAULT_LEGACY_MATH_PUA_REGISTRY",
    "LegacyMathPUAMapping",
    "LegacyMathPUANormalizer",
    "apply_normalization_to_segments",
    "extract_source_pua_provenance",
    "normalize_legacy_math_pua_text",
    "registry_as_dicts",
]
