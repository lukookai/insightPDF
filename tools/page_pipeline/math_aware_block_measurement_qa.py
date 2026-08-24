# -*- coding: utf-8 -*-
"""QA for effective soft-block measurement versus final painted ink.

The audit deliberately separates immutable layout geometry from the effective
measurement consumed by collision/repack logic.  A block is stale when final
painted ink escapes that effective measurement, irrespective of whether CSS
overflow leaves the ink visible.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence


TOLERANCE_PT = 0.25


def _round_box(box: Iterable[float] | None) -> list[float] | None:
    if box is None:
        return None
    values = list(box)
    if len(values) != 4:
        return None
    return [round(float(value), 3) for value in values]


def _height(box: Sequence[float] | None) -> float:
    return max(0.0, float(box[3]) - float(box[1])) if box else 0.0


def _outside(measured: Sequence[float], ink: Sequence[float],
             tolerance: float) -> dict[str, float]:
    return {
        "top": round(max(0.0, float(measured[1]) - float(ink[1])), 3),
        "right": round(max(0.0, float(ink[2]) - float(measured[2])), 3),
        "bottom": round(max(0.0, float(ink[3]) - float(measured[3])), 3),
        "left": round(max(0.0, float(measured[0]) - float(ink[0])), 3),
        "tolerance_pt": round(float(tolerance), 3),
    }


def _task4j_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    trace = payload.get("measurements") or payload
    previous = trace.get("previous") or {}
    return [{
        "render_id": trace.get("previous_render_id")
                     or previous.get("render_id") or "",
        "source_slot_id": previous.get("source_slot_id") or "",
        "source_slot_bbox_pt": _round_box(
            previous.get("source_slot_bbox_pt")),
        "dom_bbox_pt": _round_box(previous.get("dom_bbox_pt")),
        "painted_ink_bbox_pt": _round_box(
            trace.get("previous_painted_ink_bbox_pt")),
        # Frozen Task 4J/4I behavior: final layout measurement stopped at the
        # DOM block box even when text/ink continued below it.
        "effective_measurement_bbox_pt": _round_box(
            previous.get("dom_bbox_pt")),
        "measurement_mode": "legacy_dom_only",
        "has_math_atom_group": bool(
            previous.get("has_sup") or previous.get("has_sub")),
        "has_sup": bool(previous.get("has_sup")),
        "has_sub": bool(previous.get("has_sub")),
        "math_atoms": previous.get("scripts") or [],
    }]


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("schema_version") == \
            "visual_v07.final_soft_text_paint_audit.v1":
        return _task4j_records(payload)
    return list(payload.get("blocks") or payload.get("measurements") or [])


def audit_math_aware_block_measurement(
        payload: dict[str, Any], *, tolerance_pt: float = TOLERANCE_PT,
        required_render_ids: Iterable[str] = ()) -> dict[str, Any]:
    """Compare the effective measurement with independent painted-ink truth."""
    observations = []
    stale_count = 0
    outside_count = 0
    for record in _records(payload):
        dom = _round_box(record.get("dom_bbox_pt")
                         or record.get("dom_measured_bbox"))
        ink = _round_box(record.get("painted_ink_bbox_pt")
                         or record.get("pdf_rendered_bbox"))
        measured = _round_box(record.get("effective_measurement_bbox_pt")
                              or record.get("measured_bbox_pt") or dom)
        if not dom or not ink or not measured:
            continue
        outside = _outside(measured, ink, tolerance_pt)
        ink_outside = any(outside[edge] > tolerance_pt
                          for edge in ("top", "right", "bottom", "left"))
        dom_stale = float(ink[3]) > float(measured[3]) + tolerance_pt \
            or float(ink[1]) < float(measured[1]) - tolerance_pt
        stale_count += int(dom_stale)
        outside_count += int(ink_outside)
        observations.append({
            "render_id": str(record.get("render_id") or ""),
            "source_slot_id": str(record.get("source_slot_id") or ""),
            "source_slot_bbox_pt": _round_box(
                record.get("source_slot_bbox_pt")
                or record.get("source_slot_bbox")),
            "dom_bbox_pt": dom,
            "dom_height_pt": round(_height(dom), 3),
            "painted_ink_bbox_pt": ink,
            "painted_ink_height_pt": round(_height(ink), 3),
            "effective_measurement_bbox_pt": measured,
            "measured_height_pt": round(_height(measured), 3),
            "previous_declared_bottom": dom[3],
            "previous_ink_bottom": ink[3],
            "measured_bottom": measured[3],
            "measurement_mode": record.get("measurement_mode")
                                or "unspecified",
            "has_math_atom_group": bool(
                record.get("has_math_atom_group")),
            "has_sup": bool(record.get("has_sup")),
            "has_sub": bool(record.get("has_sub")),
            "math_atoms": record.get("math_atoms") or [],
            "ink_outside_measurement": ink_outside,
            "dom_height_stale": dom_stale,
            "outside_pt": outside,
        })

    found = {row["render_id"] for row in observations}
    required = [str(value) for value in required_render_ids]
    missing_required = sorted(set(required) - found)
    metrics = {
        "dom_height_stale_count": stale_count,
        "ink_outside_measurement_count": outside_count,
        "measurement_record_missing_count": len(missing_required),
    }
    return {
        "schema_version": "visual_v07.math_aware_block_measurement_qa.v1",
        "decision": "pass" if all(value == 0 for value in metrics.values())
                    else "fail",
        "metrics": metrics,
        "required_render_ids": required,
        "missing_required_render_ids": missing_required,
        "observations": observations,
        "policy": {
            "effective_measurement": (
                "union of DOM block bbox and final painted-ink bbox"),
            "stale": (
                "painted ink escapes effective measurement by more than "
                f"{tolerance_pt:.3f}pt"),
            "source_text_slot_geometry": "read-only comparison input",
        },
    }


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit effective block measurement against painted ink")
    parser.add_argument("input_json", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--required-render-id", action="append", default=[])
    parser.add_argument("--expect-stale", action="store_true")
    args = parser.parse_args()
    payload = json.loads(args.input_json.read_text(encoding="utf-8"))
    result = audit_math_aware_block_measurement(
        payload, required_render_ids=args.required_render_id)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    sys.stdout.buffer.write((encoded + "\n").encode("utf-8"))
    if args.expect_stale:
        return 0 if result["metrics"]["dom_height_stale_count"] > 0 else 2
    return 0 if result["decision"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["audit_math_aware_block_measurement"]
