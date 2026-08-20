"""QA for source-derived inline-math atom and script reconstruction.

The audit compares PDF-derived MathAtomGroups with visible HTML and semantic
``sup`` / ``sub`` elements.  It is usable against the frozen pre-fix artifact
as well as a newly rendered candidate.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Sequence

from bs4 import BeautifulSoup

from inline_math_reconstruction import (
    extract_inline_math_atom_groups,
    normalize_group_for_render,
)


def _parse_bbox(value: str | None) -> list[float] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [item for item in re.split(r"[, ]+", value.strip()) if item]
    if isinstance(parsed, list) and len(parsed) == 4:
        return [float(item) for item in parsed]
    return None


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _ordered_atom_coverage(atom_texts: Sequence[str], target: str
                          ) -> tuple[list[bool], bool]:
    compact_target = _compact(target)
    positions = []
    cursor = 0
    coverage = []
    for atom in atom_texts:
        compact_atom = _compact(atom)
        if not compact_atom:
            coverage.append(True)
            continue
        position = compact_target.find(compact_atom, cursor)
        coverage.append(position >= 0)
        if position >= 0:
            positions.append(position)
            cursor = position + len(compact_atom)
    return coverage, positions == sorted(positions)


def audit_inline_math_reconstruction(
        source_pdf: str | Path, page_index: int,
        html_path: str | Path, page_model: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
    source_pdf = str(Path(source_pdf).resolve())
    html_path = Path(html_path)
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    records = []
    atom_loss = atom_duplicates = order_errors = 0
    sup_loss = sub_loss = target_missing = 0

    blocks = soup.select(".paragraph-block[data-source-slot-bbox]")
    for block_index, block in enumerate(blocks):
        source_bbox = _parse_bbox(block.get("data-source-slot-bbox"))
        if source_bbox is None:
            continue
        target_text = block.get_text("", strip=False)
        groups = extract_inline_math_atom_groups(
            source_pdf, page_index, source_bbox, page_model=page_model)
        semantic_groups = {
            item.get("data-math-group-id"): item
            for item in block.select(".math-atom-group[data-math-group-id]")
        }
        for group in groups:
            normalized = normalize_group_for_render(
                group, source_pdf, page_index)
            if not (normalized.get("superscript")
                    or normalized.get("subscript")):
                continue
            atoms = [str(atom.get("text") or "")
                     for atom in normalized.get("atom_order") or []]
            semantic = semantic_groups.get(group.group_id)
            comparison_text = (semantic.get_text("", strip=False)
                               if semantic else target_text)
            coverage, ordered = _ordered_atom_coverage(atoms, comparison_text)
            missing_atoms = [atom for atom, present in zip(atoms, coverage)
                             if not present]
            normalized_compact = _compact(
                normalized.get("normalized_compact_text") or "")
            occurrence_count = len(block.select(
                '.math-atom-group[data-math-group-id="%s"]' % group.group_id))
            sup_expected = "".join(
                atom.get("text") or ""
                for atom in normalized.get("superscript") or [])
            sub_expected = "".join(
                atom.get("text") or ""
                for atom in normalized.get("subscript") or [])
            sup_actual = "".join(
                node.get_text("", strip=False) for node in
                (semantic.select("sup") if semantic else []))
            sub_actual = "".join(
                node.get_text("", strip=False) for node in
                (semantic.select("sub") if semantic else []))
            group_sup_loss = bool(sup_expected and sup_actual != sup_expected)
            group_sub_loss = bool(sub_expected and sub_actual != sub_expected)
            group_missing = len(missing_atoms)
            atom_loss += group_missing
            atom_duplicates += max(0, occurrence_count - 1)
            order_errors += int(not ordered and not group_missing)
            sup_loss += int(group_sup_loss)
            sub_loss += int(group_sub_loss)
            target_missing += int(not any(coverage))
            records.append({
                "record_id": "INLINE-MATH-QA-%03d" % len(records),
                "block_index": block_index,
                "paragraph_id": block.get("data-paragraph-id"),
                "source_slot_bbox": source_bbox,
                "group_id": group.group_id,
                "protected_token": group.protected_token,
                "primary_owner": group.primary_owner,
                "owner_id": group.owner_id,
                "source_bbox": group.source_bbox,
                "source_atom_sequence": atoms,
                "target_visible_text": target_text,
                "target_atom_coverage": coverage,
                "missing_atoms": missing_atoms,
                "atom_order_preserved": ordered,
                "normalized_group_occurrence_count": occurrence_count,
                "source_superscript": sup_expected,
                "final_superscript": sup_actual,
                "superscript_structure_lost": group_sup_loss,
                "source_subscript": sub_expected,
                "final_subscript": sub_actual,
                "subscript_structure_lost": group_sub_loss,
                "source_geometry_evidence": group.evidence,
                "source_atoms": [atom.to_dict() for atom in group.atom_order],
            })

    metrics = {
        "math_atom_loss_count": atom_loss,
        "math_atom_duplicate_count": atom_duplicates,
        "math_atom_order_error_count": order_errors,
        "superscript_structure_loss_count": sup_loss,
        "subscript_structure_loss_count": sub_loss,
        "inline_math_target_missing_count": target_missing,
    }
    return {
        "schema_version": "visual_v07.inline_math_reconstruction_qa.v1",
        "source_pdf": source_pdf,
        "source_page_index": int(page_index),
        "html_path": str(html_path.resolve()),
        "records": records,
        "metrics": metrics,
        "pass": all(value == 0 for value in metrics.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-pdf", required=True)
    parser.add_argument("--page-index", type=int, required=True)
    parser.add_argument("--html", required=True)
    parser.add_argument("--page-model")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    page_model = (json.loads(Path(args.page_model).read_text(encoding="utf-8"))
                  if args.page_model else None)
    result = audit_inline_math_reconstruction(
        args.source_pdf, args.page_index, args.html, page_model)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
