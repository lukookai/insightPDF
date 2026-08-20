# -*- coding: utf-8 -*-
"""Source-derived inline-math structure for visual PDF restoration.

The source PDF is the authority.  This module deliberately does not parse
LaTeX and does not infer symbols from translated prose.  It groups PDF text
characters by visual baseline, identifies math-font runs on prose-bearing
lines, and records script roles from relative size / baseline geometry.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import statistics
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import pymupdf

from legacy_math_pua_normalizer import normalize_legacy_math_pua_text


TOKEN_PREFIX = "MATH_ATOM_GROUP"
TOKEN_RE = re.compile(r"\{\{MATH_ATOM_GROUP_[A-F0-9]{12}\}\}")
_MATH_FONT_RE = re.compile(r"(?:math|symbol|cmsy|cmmi)", re.I)
_MATH_UNICODE_RANGES = (
    (0x0370, 0x03FF),
    (0x2100, 0x214F),
    (0x2190, 0x22FF),
    (0x27C0, 0x2BFF),
    (0x1D400, 0x1D7FF),
    (0xE000, 0xF8FF),
)
_DELIMITERS = set("()[]{}〈〉⟨⟩|‖")
_SCRIPT_SIGNS = set("−-+")


def _round_bbox(bbox: Iterable[float]) -> list[float]:
    return [round(float(value), 3) for value in bbox]


def _bbox_union(boxes: Sequence[Sequence[float]]) -> list[float]:
    return [min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)]


def _bbox_center_inside(bbox: Sequence[float], outer: Sequence[float]) -> bool:
    cx = (float(bbox[0]) + float(bbox[2])) / 2.0
    cy = (float(bbox[1]) + float(bbox[3])) / 2.0
    return (float(outer[0]) <= cx <= float(outer[2])
            and float(outer[1]) <= cy <= float(outer[3]))


def _intersection_area(left: Sequence[float], right: Sequence[float]) -> float:
    width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    return width * height


def _is_math_unicode(character: str) -> bool:
    if not character or character.isspace():
        return False
    value = ord(character)
    if any(start <= value <= end for start, end in _MATH_UNICODE_RANGES):
        return True
    return unicodedata.category(character) in {"Sm", "Sk"}


@dataclass
class SourceMathCharacter:
    character: str
    bbox: list[float]
    origin: list[float]
    font: str
    font_size: float
    block_index: int
    line_index: int
    span_index: int
    char_index: int
    source_order: int

    @property
    def baseline(self) -> float:
        return float(self.origin[1])

    @property
    def is_primary_math(self) -> bool:
        return bool(_MATH_FONT_RE.search(self.font or "")
                    or _is_math_unicode(self.character))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MathAtom:
    atom_id: str
    text: str
    role: str
    bbox: list[float]
    font: str
    font_size: float
    baseline: float
    atom_order: int
    source_character_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MathAtomGroup:
    group_id: str
    protected_token: str
    source_bbox: list[float]
    source_line_bbox: list[float]
    source_text: str
    source_compact_text: str
    base: list[MathAtom]
    superscript: list[MathAtom]
    subscript: list[MathAtom]
    operator: list[MathAtom]
    delimiter: list[MathAtom]
    argument: list[MathAtom]
    atom_order: list[MathAtom]
    source_baseline: float
    source_font_size: float
    semantic_role: str
    primary_owner: str
    owner_id: str | None
    confidence: float
    evidence: list[str]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        return value

    @property
    def has_script_structure(self) -> bool:
        return bool(self.superscript or self.subscript)


@dataclass
class MathAtomSequence:
    sequence_id: str
    source_bbox: list[float]
    groups: list[MathAtomGroup]
    atom_order: list[str]
    source_sequence: list[str]
    protected_sequence: list[str]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_source_characters(
        pdf_path: str | Path, page_index: int,
        clip_bbox: Sequence[float] | None = None,
        ) -> list[SourceMathCharacter]:
    """Extract source PDF characters with font, bbox, and baseline data."""
    document = pymupdf.open(str(pdf_path))
    try:
        page = document[int(page_index)]
        kwargs: dict[str, Any] = {}
        if clip_bbox is not None:
            kwargs["clip"] = pymupdf.Rect(*[float(v) for v in clip_bbox])
        raw = page.get_text("rawdict", **kwargs)
    finally:
        document.close()

    output: list[SourceMathCharacter] = []
    order = 0
    for block_index, block in enumerate(raw.get("blocks") or []):
        if block.get("type") != 0:
            continue
        for line_index, line in enumerate(block.get("lines") or []):
            for span_index, span in enumerate(line.get("spans") or []):
                font = str(span.get("font") or "")
                size = float(span.get("size") or 0.0)
                span_origin = span.get("origin") or [0.0, 0.0]
                for char_index, char in enumerate(span.get("chars") or []):
                    value = str(char.get("c") or "")
                    if not value:
                        continue
                    bbox = _round_bbox(char.get("bbox") or [0, 0, 0, 0])
                    origin = char.get("origin") or span_origin
                    output.append(SourceMathCharacter(
                        character=value,
                        bbox=bbox,
                        origin=[round(float(origin[0]), 3),
                                round(float(origin[1]), 3)],
                        font=font,
                        font_size=round(size, 3),
                        block_index=block_index,
                        line_index=line_index,
                        span_index=span_index,
                        char_index=char_index,
                        source_order=order,
                    ))
                    order += 1
    return output


def group_source_characters_into_visual_lines(
        characters: Sequence[SourceMathCharacter],
        ) -> list[list[SourceMathCharacter]]:
    """Merge PDF line fragments that share a physical baseline.

    PyMuPDF may expose `where`, an italic base, its smaller subscript, the
    comparison operator, and the following prose as separate text lines.
    The font-relative baseline threshold joins those fragments without
    copying source line breaks into translated text.
    """
    lines: list[list[SourceMathCharacter]] = []
    for character in sorted(
            (item for item in characters if not item.character.isspace()),
            key=lambda item: (item.baseline, item.bbox[0], item.source_order)):
        best_index = None
        best_distance = None
        for index, line in enumerate(lines):
            large = [item for item in line
                     if item.font_size >= 0.86 * max(c.font_size for c in line)]
            line_baseline = statistics.median(
                item.baseline for item in (large or line))
            line_size = statistics.median(
                item.font_size for item in (large or line))
            distance = abs(character.baseline - line_baseline)
            tolerance = max(1.1, 0.38 * max(line_size, character.font_size))
            vertical_overlap = max(
                0.0,
                min(max(item.bbox[3] for item in line), character.bbox[3])
                - max(min(item.bbox[1] for item in line), character.bbox[1]))
            line_x0 = min(item.bbox[0] for item in line)
            line_x1 = max(item.bbox[2] for item in line)
            horizontal_gap = max(
                line_x0 - character.bbox[2],
                character.bbox[0] - line_x1,
                0.0)
            horizontal_tolerance = max(
                12.0, 1.35 * max(line_size, character.font_size))
            if (distance <= tolerance and vertical_overlap > 0.25
                    and horizontal_gap <= horizontal_tolerance):
                if best_distance is None or distance < best_distance:
                    best_index = index
                    best_distance = distance
        if best_index is None:
            lines.append([character])
        else:
            lines[best_index].append(character)
    # Reconcile overlapping fragments with the same dominant baseline after
    # every character has been placed.  This closes the common PDF encoding
    # pattern where a base and a parenthesized argument begin as separate raw
    # lines while their small script characters arrive later.
    changed = True
    while changed:
        changed = False
        for left_index in range(len(lines)):
            if changed:
                break
            left = lines[left_index]
            left_size = max(item.font_size for item in left)
            left_baseline = statistics.median(
                item.baseline for item in left
                if item.font_size >= .9 * left_size)
            left_bbox = _bbox_union([item.bbox for item in left])
            for right_index in range(left_index + 1, len(lines)):
                right = lines[right_index]
                right_size = max(item.font_size for item in right)
                right_baseline = statistics.median(
                    item.baseline for item in right
                    if item.font_size >= .9 * right_size)
                right_bbox = _bbox_union([item.bbox for item in right])
                gap = max(left_bbox[0] - right_bbox[2],
                          right_bbox[0] - left_bbox[2], 0.0)
                tolerance = max(12.0, 1.35 * max(left_size, right_size))
                if (abs(left_baseline - right_baseline) <= 1.1
                        and gap <= tolerance):
                    lines[left_index].extend(right)
                    del lines[right_index]
                    changed = True
                    break
    for line in lines:
        line.sort(key=lambda item: (item.bbox[0], item.source_order))
    # A superscript can initially form its own short baseline row because a
    # same-baseline body row is a closer match for the base character.  Fold
    # such short, smaller rows into the horizontally adjacent dominant row.
    consumed: set[int] = set()
    for small_index, small in enumerate(lines):
        if len(small) > 4:
            continue
        small_size = max(item.font_size for item in small)
        small_bbox = _bbox_union([item.bbox for item in small])
        ranked = []
        for large_index, large in enumerate(lines):
            if large_index == small_index or len(large) <= len(small):
                continue
            large_size = max(item.font_size for item in large)
            if small_size > .86 * large_size:
                continue
            large_bbox = _bbox_union([item.bbox for item in large])
            gap = max(large_bbox[0] - small_bbox[2],
                      small_bbox[0] - large_bbox[2], 0.0)
            baseline = statistics.median(
                item.baseline for item in large
                if item.font_size >= .9 * large_size)
            delta = abs(statistics.median(
                item.baseline for item in small) - baseline)
            if gap <= 1.5 * large_size and delta <= .5 * large_size:
                ranked.append((gap, delta, large_index))
        if ranked:
            target_index = sorted(ranked)[0][2]
            lines[target_index].extend(small)
            lines[target_index].sort(
                key=lambda item: (item.bbox[0], item.source_order))
            consumed.add(small_index)
    lines = [line for index, line in enumerate(lines) if index not in consumed]
    return sorted(lines, key=lambda line: (
        min(item.bbox[1] for item in line), min(item.bbox[0] for item in line)))


def visual_line_text(line: Sequence[SourceMathCharacter]) -> str:
    """Create a readable source line, preserving geometric word gaps."""
    output: list[str] = []
    previous: SourceMathCharacter | None = None
    for character in line:
        if previous is not None:
            gap = character.bbox[0] - previous.bbox[2]
            nominal = max(previous.font_size, character.font_size, 1.0)
            if gap > 0.23 * nominal and output and output[-1] != " ":
                output.append(" ")
        output.append(character.character)
        previous = character
    return "".join(output).strip()


def merged_source_text_lines(
        pdf_path: str | Path, page_index: int,
        regions: Sequence[Sequence[float]],
        ) -> list[dict[str, Any]]:
    """Return co-baseline merged source lines inside formula regions."""
    if not regions:
        return []
    union = [min(r[0] for r in regions), min(r[1] for r in regions),
             max(r[2] for r in regions), max(r[3] for r in regions)]
    characters = extract_source_characters(pdf_path, page_index, union)
    rows = []
    for line in group_source_characters_into_visual_lines(characters):
        bbox = _bbox_union([item.bbox for item in line])
        if not any(_bbox_center_inside(bbox, region)
                   or _intersection_area(bbox, region)
                   >= 0.6 * max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), .1)
                   for region in regions):
            continue
        rows.append({
            "bbox": _round_bbox(bbox),
            "text": visual_line_text(line),
            "size": round(max(item.font_size for item in line), 3),
            "characters": [item.to_dict() for item in line],
        })
    return rows


def _formula_owners(page_model: dict[str, Any] | None) -> list[dict[str, Any]]:
    output = []
    for region in (page_model or {}).get("regions") or []:
        if str(region.get("type") or "").casefold() != "formula":
            continue
        payload = region.get("payload") or {}
        bbox = payload.get("layout_bbox") or region.get("bbox") or []
        if len(bbox) != 4:
            continue
        output.append({
            "owner_id": str(payload.get("formula_id")
                            or region.get("region_id") or ""),
            "bbox": [float(value) for value in bbox],
        })
    return output


def _owner_for_bbox(bbox: Sequence[float], owners: Sequence[dict[str, Any]]) -> str | None:
    area = max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 0.01)
    ranked = []
    for owner in owners:
        inclusion = _intersection_area(bbox, owner["bbox"]) / area
        if inclusion >= 0.8:
            owner_area = ((owner["bbox"][2] - owner["bbox"][0])
                          * (owner["bbox"][3] - owner["bbox"][1]))
            ranked.append((-inclusion, owner_area, owner["owner_id"]))
    return sorted(ranked)[0][2] if ranked else None


def _char_is_run_member(character: SourceMathCharacter,
                        run_started: bool) -> bool:
    value = character.character
    if value.isspace():
        return run_started
    if character.is_primary_math:
        return True
    if value in _DELIMITERS or value in _SCRIPT_SIGNS:
        return run_started
    if value.isdigit() or unicodedata.category(value) in {"Po", "Pd"}:
        return run_started
    return False


def _split_math_runs(line: Sequence[SourceMathCharacter]
                     ) -> list[list[SourceMathCharacter]]:
    runs: list[list[SourceMathCharacter]] = []
    current: list[SourceMathCharacter] = []
    for index, character in enumerate(line):
        if (current and character.character == "("
                and current[-1].character == ")"):
            runs.append(current)
            current = [character]
            continue
        if _char_is_run_member(character, bool(current)):
            current.append(character)
            continue
        if current:
            while current and current[-1].character.isspace():
                current.pop()
            while (current and current[-1].character in ".,;:"
                   and not current[-1].is_primary_math):
                current.pop()
            if current:
                runs.append(current)
            current = []
        # A non-math character cannot start a run.  A subsequent math-font
        # character naturally starts a new one on its own iteration.
    if current:
        while current and current[-1].character.isspace():
            current.pop()
        while (current and current[-1].character in ".,;:"
               and not current[-1].is_primary_math):
            current.pop()
        if current:
            runs.append(current)
    return runs


def _atom_role(character: SourceMathCharacter, base_size: float,
               base_baseline: float, base_seen: bool) -> str:
    size_ratio = character.font_size / max(base_size, 0.1)
    baseline_delta = character.baseline - base_baseline
    if size_ratio <= 0.86 and baseline_delta <= -0.16 * base_size:
        return "superscript"
    if size_ratio <= 0.86 and baseline_delta >= 0.16 * base_size:
        return "subscript"
    value = character.character
    if value in _DELIMITERS:
        return "delimiter"
    if unicodedata.category(value) == "Sm" or value in "=<>≤≥≈≠±×÷∈∉":
        return "operator"
    if not base_seen and (character.is_primary_math
                          and (value.isalpha() or ord(value) >= 0xE000)):
        return "base"
    return "argument"


def _atoms_from_run(run: Sequence[SourceMathCharacter], group_id: str,
                    ) -> tuple[list[MathAtom], float, float]:
    non_space = [item for item in run if not item.character.isspace()]
    max_size = max(item.font_size for item in non_space)
    base_chars = [item for item in non_space if item.font_size >= .9 * max_size]
    base_baseline = statistics.median(
        item.baseline for item in (base_chars or non_space))
    atoms: list[MathAtom] = []
    base_seen = False
    for source_index, character in enumerate(non_space):
        role = _atom_role(character, max_size, base_baseline, base_seen)
        base_seen = base_seen or role == "base"
        source_id = "%s-C%03d" % (group_id, source_index)
        if (atoms and atoms[-1].role == role
                and atoms[-1].font == character.font
                and abs(atoms[-1].font_size - character.font_size) < 0.05
                and abs(atoms[-1].baseline - character.baseline) < 0.08):
            atoms[-1].text += character.character
            atoms[-1].bbox = _round_bbox(_bbox_union(
                [atoms[-1].bbox, character.bbox]))
            atoms[-1].source_character_ids.append(source_id)
            continue
        atoms.append(MathAtom(
            atom_id="%s-A%02d" % (group_id, len(atoms)),
            text=character.character,
            role=role,
            bbox=list(character.bbox),
            font=character.font,
            font_size=character.font_size,
            baseline=round(character.baseline, 3),
            atom_order=len(atoms),
            source_character_ids=[source_id],
        ))
    return atoms, round(base_baseline, 3), round(max_size, 3)


def extract_inline_math_atom_groups(
        pdf_path: str | Path, page_index: int,
        source_bbox: Sequence[float],
        page_model: dict[str, Any] | None = None,
        ) -> list[MathAtomGroup]:
    """Extract inline math groups from prose-bearing visual source lines."""
    characters = extract_source_characters(pdf_path, page_index, source_bbox)
    owners = _formula_owners(page_model)
    output: list[MathAtomGroup] = []
    for line in group_source_characters_into_visual_lines(characters):
        line_bbox = _bbox_union([item.bbox for item in line])
        if not _bbox_center_inside(line_bbox, source_bbox):
            # Slots may tightly clip a line edge; retain substantial overlap.
            area = max((line_bbox[2] - line_bbox[0])
                       * (line_bbox[3] - line_bbox[1]), .01)
            if _intersection_area(line_bbox, source_bbox) / area < .8:
                continue
        prose_chars = "".join(
            item.character for item in line
            if not item.is_primary_math)
        if len(re.findall(r"[A-Za-z]{2,}", prose_chars)) < 1:
            continue
        for run in _split_math_runs(line):
            non_space = [item for item in run if not item.character.isspace()]
            if not non_space or not any(item.is_primary_math for item in non_space):
                continue
            run_bbox = _bbox_union([item.bbox for item in non_space])
            identity = json.dumps({
                "page": int(page_index), "bbox": _round_bbox(run_bbox),
                "order": [item.source_order for item in non_space],
            }, sort_keys=True).encode("utf-8")
            digest = hashlib.sha256(identity).hexdigest()[:12].upper()
            group_id = "MAG-" + digest
            atoms, baseline, base_size = _atoms_from_run(non_space, group_id)
            roles = {role: [atom for atom in atoms if atom.role == role]
                     for role in ("base", "superscript", "subscript",
                                  "operator", "delimiter", "argument")}
            owner_id = _owner_for_bbox(run_bbox, owners)
            evidence = [
                "source PDF character bboxes and baselines",
                "math-font / mathematical-Unicode run on a prose-bearing line",
                "font-relative horizontal adjacency",
            ]
            if roles["superscript"]:
                evidence.append(
                    "smaller font and raised source baseline identify superscript")
            if roles["subscript"]:
                evidence.append(
                    "smaller font and lowered source baseline identify subscript")
            if owner_id:
                evidence.append("existing formula ownership contains the run")
            output.append(MathAtomGroup(
                group_id=group_id,
                protected_token="{{%s_%s}}" % (TOKEN_PREFIX, digest),
                source_bbox=_round_bbox(run_bbox),
                source_line_bbox=_round_bbox(line_bbox),
                source_text=visual_line_text(run),
                source_compact_text="".join(
                    item.character for item in non_space),
                base=roles["base"],
                superscript=roles["superscript"],
                subscript=roles["subscript"],
                operator=roles["operator"],
                delimiter=roles["delimiter"],
                argument=roles["argument"],
                atom_order=atoms,
                source_baseline=baseline,
                source_font_size=base_size,
                semantic_role="inline_math",
                primary_owner="FORMULA" if owner_id else "TEXT",
                owner_id=owner_id,
                confidence=0.99 if owner_id else 0.94,
                evidence=evidence,
            ))
    return sorted(output, key=lambda group: (
        group.source_bbox[1], group.source_bbox[0]))


def normalize_group_for_render(
        group: MathAtomGroup | dict[str, Any], pdf_path: str | Path,
        page_index: int,
        ) -> dict[str, Any]:
    """Apply Task 4H normalization with the group's source provenance."""
    value = group.to_dict() if isinstance(group, MathAtomGroup) else json.loads(
        json.dumps(group, ensure_ascii=False))
    records = []
    for atom in value.get("atom_order") or []:
        result = normalize_legacy_math_pua_text(
            str(atom.get("text") or ""), pdf_path, page_index,
            source_bbox=atom.get("bbox") or value.get("source_bbox"),
            context={"math_atom_group_id": value.get("group_id"),
                     "atom_id": atom.get("atom_id")})
        if result.get("blocked"):
            raise RuntimeError(
                "required legacy PUA unresolved in %s" % value.get("group_id"))
        atom["text"] = result["normalized_text"]
        records.extend(result.get("records") or [])
    by_id = {atom.get("atom_id"): atom
             for atom in value.get("atom_order") or []}
    for role in ("base", "superscript", "subscript", "operator",
                 "delimiter", "argument"):
        value[role] = [by_id.get(atom.get("atom_id"), atom)
                       for atom in value.get(role) or []]
    value["normalization_records"] = records
    value["normalized_compact_text"] = "".join(
        atom.get("text") or "" for atom in value.get("atom_order") or [])
    return value


def render_math_atom_group_html(
        group: MathAtomGroup | dict[str, Any],
        pdf_path: str | Path | None = None,
        page_index: int = 0,
        ) -> str:
    """Render semantic atom roles, including real sup/sub elements."""
    if (isinstance(group, dict)
            and group.get("normalized_compact_text") is not None):
        value = json.loads(json.dumps(group, ensure_ascii=False))
    else:
        if pdf_path is None:
            raise ValueError("source PDF required for legacy PUA normalization")
        value = normalize_group_for_render(group, pdf_path, page_index)
    pieces = []
    for atom in value.get("atom_order") or []:
        role = str(atom.get("role") or "argument")
        text_value = html.escape(str(atom.get("text") or ""))
        attrs = ('class="math-atom math-%s" data-atom-id="%s" '
                 'data-source-baseline="%.3f"' % (
                     role, html.escape(str(atom.get("atom_id") or "")),
                     float(atom.get("baseline") or 0.0)))
        if role == "superscript":
            pieces.append("<sup %s>%s</sup>" % (attrs, text_value))
        elif role == "subscript":
            pieces.append("<sub %s>%s</sub>" % (attrs, text_value))
        else:
            # Comparison operators retain source-scale whitespace without
            # changing the surrounding block geometry.
            if role == "operator" and text_value in {"&gt;", "&lt;", "=", "≤", "≥"}:
                text_value = "&nbsp;%s&nbsp;" % text_value
            pieces.append("<span %s>%s</span>" % (attrs, text_value))
    atom_order = "|".join(
        "%s:%s" % (atom.get("role"), atom.get("atom_id"))
        for atom in value.get("atom_order") or [])
    return (
        '<span class="math-atom-group" data-math-group-id="%s" '
        'data-atom-order="%s" data-primary-owner="%s">%s</span>'
        % (html.escape(str(value.get("group_id") or "")),
           html.escape(atom_order),
           html.escape(str(value.get("primary_owner") or "")),
           "".join(pieces)))


def protect_source_math_groups(
        source_text: str,
        groups: Sequence[MathAtomGroup | dict[str, Any]],
        ) -> tuple[str, list[dict[str, Any]]]:
    """Replace source math runs with stable protected group tokens.

    Matching permits only whitespace differences introduced by PDF line
    assembly.  It never guesses missing characters and never examines prose
    content, filenames, page numbers, or paragraph identifiers.
    """
    output = source_text
    trace = []
    for group in groups:
        value = group.to_dict() if isinstance(group, MathAtomGroup) else group
        compact = str(value.get("source_compact_text") or "")
        if not compact:
            continue
        pattern = _whitespace_flexible_pattern(compact)
        match = re.search(pattern, output)
        if not match:
            trace.append({"group_id": value.get("group_id"),
                          "protected": False,
                          "reason": "source_sequence_not_found"})
            continue
        token = str(value.get("protected_token") or "")
        output = output[:match.start()] + token + output[match.end():]
        trace.append({"group_id": value.get("group_id"),
                      "protected": True,
                      "matched_source": match.group(0),
                      "protected_token": token})
    return output, trace


def _replace_text_node_with_html(node: Any, before: str, replacement: str,
                                 after: str) -> None:
    from bs4 import BeautifulSoup
    fragment = BeautifulSoup(
        html.escape(before) + replacement + html.escape(after),
        "html.parser")
    values = list(fragment.contents)
    for value in reversed(values):
        node.insert_after(value)
    node.extract()


def _find_compact_in_text(text: str, compact: str) -> re.Match[str] | None:
    if not compact:
        return None
    pattern = _whitespace_flexible_pattern(compact)
    return re.search(pattern, text)


def _whitespace_flexible_pattern(compact: str) -> str:
    characters = list(compact)
    if not characters:
        return ""
    return re.escape(characters[0]) + "".join(
        r"\s*" + re.escape(character) for character in characters[1:])


def reconstruct_html_math_from_source(
        html_text: str, source_pdf: str | Path, page_index: int,
        page_model: dict[str, Any] | None = None,
        ) -> tuple[str, dict[str, Any]]:
    """Repair a frozen HTML artifact from source geometry and atom order.

    Full groups are replaced exactly.  A missing *prefix* may be restored
    only when the surviving suffix is unique at the target block boundary;
    this is the protected-translation provenance failure mode where PDF line
    fragments preceding the first surviving atom were filtered before
    translation.  No source phrase or fixture identifier participates.
    """
    from bs4 import BeautifulSoup, NavigableString

    soup = BeautifulSoup(html_text, "html.parser")
    trace = []
    for block_index, block in enumerate(
            soup.select(".paragraph-block[data-source-slot-bbox]")):
        raw_bbox = block.get("data-source-slot-bbox") or ""
        try:
            source_bbox = [float(value) for value in raw_bbox.split(",")]
        except ValueError:
            continue
        if len(source_bbox) != 4:
            continue
        groups = extract_inline_math_atom_groups(
            source_pdf, page_index, source_bbox, page_model=page_model)
        for group in groups:
            normalized = normalize_group_for_render(
                group, source_pdf, page_index)
            if not (normalized.get("superscript")
                    or normalized.get("subscript")):
                continue
            if block.select_one(
                    '.math-atom-group[data-math-group-id="%s"]'
                    % group.group_id):
                continue
            compact = str(normalized.get("normalized_compact_text") or "")
            replacement = render_math_atom_group_html(normalized)
            applied = False
            method = None
            anchor = None
            for node in list(block.find_all(string=True)):
                if not isinstance(node, NavigableString):
                    continue
                match = _find_compact_in_text(str(node), compact)
                if match:
                    anchor = match.group(0)
                    _replace_text_node_with_html(
                        node, str(node)[:match.start()], replacement,
                        str(node)[match.end():])
                    applied = True
                    method = "exact_normalized_atom_sequence"
                    break
            if not applied:
                atoms = [str(atom.get("text") or "")
                         for atom in normalized.get("atom_order") or []]
                # Prefix loss can be closed only at the first visible target
                # position and only when the suffix anchor is unique.
                for suffix_start in range(1, len(atoms)):
                    suffix = "".join(atoms[suffix_start:])
                    if not suffix:
                        continue
                    nodes = list(block.find_all(string=True))
                    candidates = []
                    for node in nodes:
                        match = _find_compact_in_text(str(node), suffix)
                        if match:
                            candidates.append((node, match))
                    if len(candidates) != 1:
                        continue
                    node, match = candidates[0]
                    block_prefix = "".join(
                        str(item) for item in nodes[:nodes.index(node)])
                    block_prefix += str(node)[:match.start()]
                    if block_prefix.strip():
                        continue
                    anchor = match.group(0)
                    _replace_text_node_with_html(
                        node, str(node)[:match.start()], replacement,
                        str(node)[match.end():])
                    applied = True
                    method = "unique_boundary_suffix_atom_anchor"
                    break
            trace.append({
                "block_index": block_index,
                "source_slot_bbox": source_bbox,
                "group_id": group.group_id,
                "source_atom_sequence": [
                    atom.get("text") for atom in normalized.get("atom_order") or []],
                "has_superscript": bool(normalized.get("superscript")),
                "has_subscript": bool(normalized.get("subscript")),
                "applied": applied,
                "method": method,
                "target_anchor": anchor,
            })
    if soup.style and ".math-atom-group" not in soup.style.string:
        soup.style.append(
            ".math-atom-group{white-space:nowrap;font-family:inherit;}"
            ".math-atom-group sup,.math-atom-group sub{font-size:.75em;"
            "line-height:0;position:static;}"
            ".math-atom-group sup{vertical-align:.46em;}"
            ".math-atom-group sub{vertical-align:-.24em;}")
    return str(soup), {
        "schema_version": "visual_v07.inline_math_html_reconstruction.v1",
        "records": trace,
        "metrics": {
            "candidate_group_count": len(trace),
            "reconstructed_group_count": sum(
                bool(record["applied"]) for record in trace),
            "unresolved_group_count": sum(
                not record["applied"] for record in trace),
        },
    }


def math_group_map(groups: Iterable[MathAtomGroup | dict[str, Any]]
                   ) -> dict[str, dict[str, Any]]:
    output = {}
    for group in groups:
        value = group.to_dict() if isinstance(group, MathAtomGroup) else group
        output[str(value["protected_token"])] = value
    return output


def sequence_from_groups(groups: Sequence[MathAtomGroup]) -> MathAtomSequence:
    bbox = _bbox_union([group.source_bbox for group in groups]) if groups else [0, 0, 0, 0]
    digest = hashlib.sha256(json.dumps(
        [group.group_id for group in groups]).encode("utf-8")).hexdigest()[:12]
    return MathAtomSequence(
        sequence_id="MAS-" + digest.upper(),
        source_bbox=_round_bbox(bbox),
        groups=list(groups),
        atom_order=[atom.atom_id for group in groups for atom in group.atom_order],
        source_sequence=[atom.text for group in groups for atom in group.atom_order],
        protected_sequence=[group.protected_token for group in groups],
        confidence=min((group.confidence for group in groups), default=0.0),
    )


__all__ = [
    "MathAtom", "MathAtomGroup", "MathAtomSequence",
    "extract_inline_math_atom_groups", "extract_source_characters",
    "group_source_characters_into_visual_lines", "math_group_map",
    "merged_source_text_lines", "normalize_group_for_render",
    "render_math_atom_group_html", "sequence_from_groups",
    "protect_source_math_groups", "reconstruct_html_math_from_source",
    "visual_line_text",
]
