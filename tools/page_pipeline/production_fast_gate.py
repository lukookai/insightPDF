# -*- coding: utf-8 -*-
"""Low-cost post-render gate for the FAST production path.

The gate consumes already-built PageModel, SourceTextSlot lock records, the
live Chromium RenderLedger, and final print metadata.  It opens the delivered
PDF exactly once to validate structure/page count.  It never reparses the
source PDF, reconstructs math or ownership, rasterizes a page, starts a
browser, or emits review artifacts.
"""
from __future__ import annotations

import math
from pathlib import Path
import time
from typing import Any, Iterable

import pymupdf

from table_cell_translation_qa import table_cell_translation_requirement


QA_MODES = ("fast", "release", "debug", "regression")
HEAVY_QA_CATEGORIES = (
    "full_math_provenance_audit",
    "source_protected_html_pdf_chain_audit",
    "full_figure_ownership_audit",
    "painted_ink_deep_audit",
    "full_raster_visual_qa",
    "contact_sheet",
    "review_bundle",
    "full_document_regression",
)


def normalize_qa_mode(value: str | None) -> str:
    mode = str(value or "fast").strip().lower()
    if mode not in QA_MODES:
        raise ValueError("qa_mode must be one of: %s" % ", ".join(QA_MODES))
    return mode


def qa_mode_matrix() -> dict[str, Any]:
    rows = []
    for mode in QA_MODES:
        is_fast = mode == "fast"
        rows.append({
            "qa_mode": mode,
            "default_on_fast_branch": is_fast,
            "runs_fast_gate": is_fast,
            "runs_existing_deep_qa": not is_fast,
            "blocks_delivery_with": (
                "FAST_GATE" if is_fast else "existing full QA gate"),
            "heavy_qa_skipped_count": (
                len(HEAVY_QA_CATEGORIES) if is_fast else 0),
            "heavy_qa_categories": (
                list(HEAVY_QA_CATEGORIES) if is_fast else []),
            "requires_explicit_selection": not is_fast,
        })
    return {
        "schema_version": "fast.v04.qa_mode_matrix.v1",
        "default_qa_mode": "fast",
        "available_qa_modes": list(QA_MODES),
        "modes": rows,
        "old_qa_preserved": True,
    }


def _box(value: Any) -> list[float] | None:
    if isinstance(value, dict):
        keys = ("x", "y", "right", "bottom")
        if all(key in value for key in keys):
            value = [value[key] for key in keys]
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    return result if all(math.isfinite(item) for item in result) else None


def _valid_box(value: Any) -> bool:
    box = _box(value)
    return bool(box and box[2] > box[0] and box[3] > box[1])


def _block_box(block: dict[str, Any]) -> list[float] | None:
    return _box(block.get("dom_measured_bbox") or block.get("final_bbox")
                or block.get("rect"))


def _block_text(block: dict[str, Any]) -> str:
    return str(block.get("text") or block.get("text_preview") or "").strip()


def _block_id(block: dict[str, Any]) -> str:
    return str(block.get("flow_fragment_id") or block.get("render_id") or "")


def _hard_kind(record: dict[str, Any]) -> str:
    value = str(record.get("kind") or "")
    if "formula-seg" in value:
        return "formula"
    if "figure-region" in value or "figure-img" in value:
        return "figure"
    if "translated-cell" in value:
        return "table_cell"
    return "unknown"


def _hard_id(record: dict[str, Any]) -> str:
    kind = _hard_kind(record)
    if kind == "formula":
        return str(record.get("formula_id") or "")
    if kind == "figure":
        return str(record.get("figure_id") or record.get("region_id") or "")
    if kind == "table_cell":
        return str(record.get("cell_id") or "")
    return ""


def _flow_items(flows: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [item for flow in (flows or [])
            for item in (flow.get("items") or [])]


def _required_translation_ids(
        source_text_slot_lock: dict[str, Any]) -> tuple[set[str], list[dict]]:
    required: set[str] = set()
    invalid = []
    for record in source_text_slot_lock.get("records") or []:
        reason = str(record.get("lock_reason") or "")
        if reason == "not_translation_required_soft_delivery_text":
            continue
        identity = str(record.get("flow_fragment_id")
                       or record.get("render_id") or "")
        if identity:
            required.add(identity)
        if not record.get("geometry_locked"):
            invalid.append({
                "flow_fragment_id": identity,
                "reason": reason or "required_source_slot_not_locked",
            })
    return required, invalid


def _required_translation_missing(
        flows: Iterable[dict[str, Any]] | None,
        source_text_slot_lock: dict[str, Any],
        blocks: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    required, invalid_locks = _required_translation_ids(source_text_slot_lock)
    flow_by_id = {
        str(item.get("flow_fragment_id") or item.get("paragraph_id") or ""):
        item for item in _flow_items(flows) if item.get("kind") == "paragraph"
    }
    rendered = {_block_id(block): block for block in blocks if _block_id(block)}
    details = list(invalid_locks)
    for identity in sorted(required):
        flow = flow_by_id.get(identity)
        target = (str(flow.get("render_text") or "").strip()
                  if flow is not None else _block_text(rendered.get(identity, {})))
        if not target:
            details.append({"flow_fragment_id": identity,
                            "reason": "required_target_empty"})
        elif identity not in rendered:
            details.append({"flow_fragment_id": identity,
                            "reason": "required_target_not_in_render_ledger"})
    unique = {(row["flow_fragment_id"], row["reason"]): row
              for row in details}
    return len(unique), list(unique.values())


def _required_table_cell_missing(
        page_model: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    details = []
    for region in page_model.get("regions") or []:
        if region.get("type") != "table":
            continue
        for cell in (region.get("payload") or {}).get("cells") or []:
            source = str(cell.get("source_text") or "")
            if not table_cell_translation_requirement(source)["required"]:
                continue
            target = str(cell.get("canonical_target")
                         or cell.get("translated_text") or "").strip()
            if not target:
                details.append({
                    "cell_id": str(cell.get("cell_id") or ""),
                    "table_id": str(region.get("region_id") or ""),
                    "reason": "required_table_target_empty",
                })
    return len(details), details


def _expected_hard_ids(
        page_model: dict[str, Any], flows: Iterable[dict[str, Any]] | None,
        expected_override: dict[str, Iterable[str]] | None = None,
        ) -> dict[str, set[str]]:
    if expected_override is not None:
        return {kind: {str(value) for value in values if str(value)}
                for kind, values in expected_override.items()}
    expected = {"formula": set(), "figure": set(), "table_cell": set()}
    for item in _flow_items(flows):
        if item.get("kind") != "formula":
            continue
        for member in item.get("row_members") or []:
            identity = str(member.get("formula_id") or "")
            if identity:
                expected["formula"].add(identity)
    for region in page_model.get("regions") or []:
        payload = region.get("payload") or {}
        if region.get("type") in {"figure", "image"}:
            expected["figure"].add(str(
                payload.get("figure_id") or region.get("region_id") or ""))
        elif region.get("type") == "table":
            for cell in payload.get("cells") or []:
                identity = str(cell.get("cell_id") or "")
                if identity:
                    expected["table_cell"].add(identity)
    return expected


def _hard_anchor_missing(
        page_model: dict[str, Any], flows: Iterable[dict[str, Any]] | None,
        hard: list[dict[str, Any]],
        expected_override: dict[str, Iterable[str]] | None = None,
        ) -> tuple[int, list[dict[str, Any]]]:
    expected = _expected_hard_ids(page_model, flows, expected_override)
    actual = {"formula": set(), "figure": set(), "table_cell": set()}
    for record in hard:
        kind, identity = _hard_kind(record), _hard_id(record)
        if kind in actual and identity:
            actual[kind].add(identity)
    details = []
    for kind in expected:
        for identity in sorted(expected[kind] - actual[kind]):
            details.append({"kind": kind, "anchor_id": identity})
    return len(details), details


def _obvious_overflow(
        blocks: list[dict[str, Any]], page_model: dict[str, Any],
        tolerance: float = 0.25) -> tuple[int, list[dict[str, Any]]]:
    page_width = float(page_model.get("width") or 0.0)
    page_height = float(page_model.get("height") or 0.0)
    details = []
    for block in blocks:
        box = _block_box(block)
        if not box:
            continue
        identity = _block_id(block)
        page_escape = (box[0] < -tolerance or box[1] < -tolerance
                       or box[2] > page_width + tolerance
                       or box[3] > page_height + tolerance)
        slot = _box(block.get("source_slot_bbox"))
        slot_escape = bool(
            block.get("geometry_locked") and slot
            and (box[0] < slot[0] - tolerance
                 or box[1] < slot[1] - tolerance
                 or box[2] > slot[2] + tolerance
                 or box[3] > slot[3] + tolerance))
        if page_escape or slot_escape:
            details.append({
                "flow_fragment_id": identity,
                "page_escape": page_escape,
                "source_slot_escape": slot_escape,
                "bbox": box,
                "source_slot_bbox": slot,
            })
    for region in page_model.get("regions") or []:
        if region.get("type") != "table":
            continue
        for cell in (region.get("payload") or {}).get("cells") or []:
            if cell.get("overflow"):
                details.append({
                    "cell_id": str(cell.get("cell_id") or ""),
                    "reason": "table_cell_fit_overflow",
                })
    return len(details), details


def _invalid_bboxes(
        page_model: dict[str, Any], blocks: list[dict[str, Any]],
        hard: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    details = []
    for block in blocks:
        if not _valid_box(_block_box(block)):
            details.append({"kind": "soft_block", "id": _block_id(block)})
        slot = block.get("source_slot_bbox")
        if slot and not _valid_box(slot):
            details.append({"kind": "source_slot", "id": _block_id(block)})
    for index, record in enumerate(hard):
        if not _valid_box(record.get("rect") or record.get("bbox")):
            details.append({"kind": "hard_anchor", "id": _hard_id(record),
                            "index": index})
    for region in page_model.get("regions") or []:
        if region.get("bbox") and not _valid_box(region.get("bbox")):
            details.append({"kind": "page_model_region",
                            "id": str(region.get("region_id") or "")})
    return len(details), details


def run_fast_gate(
        page_model: dict[str, Any], *, final_pdf_path: str | Path,
        render_ledger: dict[str, Any],
        source_text_slot_lock: dict[str, Any],
        render_metadata: dict[str, Any],
        flows: Iterable[dict[str, Any]] | None = None,
        expected_physical_page_count: int = 1,
        expected_hard_anchor_ids: dict[str, Iterable[str]] | None = None,
        ) -> dict[str, Any]:
    """Run the nine deterministic FAST checks and return timing/counters."""
    started = time.perf_counter()
    pdf_path = Path(final_pdf_path)
    pdf_reopen_count = 0
    physical_pages = 0
    pdf_open_error = None
    if pdf_path.exists() and pdf_path.stat().st_size > 0:
        try:
            pdf_reopen_count += 1
            document = pymupdf.open(str(pdf_path))
            try:
                physical_pages = int(document.page_count)
            finally:
                document.close()
        except Exception as exc:  # noqa: BLE001
            pdf_open_error = "%s: %s" % (type(exc).__name__, exc)

    blocks = list(render_ledger.get("blocks") or [])
    hard = list(render_ledger.get("hard") or [])
    render_success = bool(
        pdf_path.exists() and pdf_path.stat().st_size > 0
        and pdf_open_error is None
        and int(render_metadata.get("final_pdf_print_count") or 0) >= 1)
    physical_valid = physical_pages == int(expected_physical_page_count)

    translation_missing, translation_details = (
        _required_translation_missing(flows, source_text_slot_lock, blocks))
    table_missing, table_details = _required_table_cell_missing(page_model)
    ownership_multi = int((page_model.get("ownership") or {}).get(
        "source_span_multi_primary_owner_count") or 0)
    collision_count = int((render_ledger.get("metrics") or {}).get(
        "final_block_collision_count") or 0)
    overflow_count, overflow_details = _obvious_overflow(
        blocks, page_model)
    anchor_missing, anchor_details = _hard_anchor_missing(
        page_model, flows, hard, expected_hard_anchor_ids)
    invalid_bbox, invalid_details = _invalid_bboxes(page_model, blocks, hard)

    metrics = {
        "required_translation_missing_count": translation_missing,
        "required_table_cell_missing_count": table_missing,
        "source_span_multi_primary_owner_count": ownership_multi,
        "obvious_block_collision_count": collision_count,
        "obvious_overflow_count": overflow_count,
        "hard_anchor_missing_count": anchor_missing,
        "invalid_bbox_count": invalid_bbox,
    }
    checks = {
        "render_success": render_success,
        "physical_page_count_valid": physical_valid,
        **{key: value == 0 for key, value in metrics.items()},
    }
    elapsed = time.perf_counter() - started
    return {
        "schema_version": "fast.v04.production_fast_gate.v1",
        "qa_mode": "fast",
        "decision": "pass" if all(checks.values()) else "blocked",
        "checks": checks,
        "metrics": metrics,
        "details": {
            "required_translation_missing": translation_details,
            "required_table_cell_missing": table_details,
            "obvious_overflow": overflow_details,
            "hard_anchor_missing": anchor_details,
            "invalid_bbox": invalid_details,
            "pdf_open_error": pdf_open_error,
        },
        "performance": {
            "fast_gate_time": round(elapsed, 6),
            "heavy_qa_skipped_count": len(HEAVY_QA_CATEGORIES),
            "pdf_reopen_count": pdf_reopen_count,
            "unnecessary_pdf_reopen_count": max(0, pdf_reopen_count - 1),
            "rasterize_count": 0,
            "chromium_launch_count": 0,
            "overlay_generation_count": 0,
        },
        "physical_page_count": physical_pages,
        "expected_physical_page_count": int(expected_physical_page_count),
        "heavy_qa_skipped": list(HEAVY_QA_CATEGORIES),
        "evidence_policy": {
            "page_model_reused": True,
            "render_ledger_reused": True,
            "final_render_metadata_reused": True,
            "source_pdf_reparsed": False,
            "math_atom_rebuilt": False,
            "ownership_recomputed": False,
            "full_page_rasterized": False,
        },
    }


__all__ = [
    "HEAVY_QA_CATEGORIES", "QA_MODES", "normalize_qa_mode",
    "qa_mode_matrix", "run_fast_gate",
]
