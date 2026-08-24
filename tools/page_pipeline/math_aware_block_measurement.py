# -*- coding: utf-8 -*-
"""Math-aware effective measurement for final soft-text blocks.

SourceTextSlot geometry remains the immutable placement envelope.  This module
builds a separate effective measurement from the CSS/DOM block and independent
final raster painted-ink bounds. Collision and optional second-pass packing
consume the effective measurement; no CSS height, position, or anchor is
mutated.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence


def _box(box: Iterable[float] | None) -> list[float] | None:
    if box is None:
        return None
    values = list(box)
    if len(values) != 4:
        return None
    return [round(float(value), 3) for value in values]


def _height(box: Sequence[float] | None) -> float:
    return max(0.0, float(box[3]) - float(box[1])) if box else 0.0


def union_bbox(*boxes: Iterable[float] | None) -> list[float] | None:
    normalized = [value for value in (_box(box) for box in boxes)
                  if value is not None]
    if not normalized:
        return None
    return [round(min(box[0] for box in normalized), 3),
            round(min(box[1] for box in normalized), 3),
            round(max(box[2] for box in normalized), 3),
            round(max(box[3] for box in normalized), 3)]


def _script_extensions(math_groups: Iterable[dict[str, Any]]) -> dict[str, Any]:
    atoms = []
    maximum_sup_ascent = 0.0
    maximum_sub_descent = 0.0
    for group in math_groups:
        for atom in group.get("atoms") or []:
            bbox = _box(atom.get("bbox_pt"))
            base_bbox = _box(atom.get("base_bbox_pt"))
            if not bbox:
                continue
            role = str(atom.get("role") or "")
            ascent = (max(0.0, float(base_bbox[1]) - float(bbox[1]))
                      if role == "superscript" and base_bbox else 0.0)
            descent = (max(0.0, float(bbox[3]) - float(base_bbox[3]))
                       if role == "subscript" and base_bbox else 0.0)
            maximum_sup_ascent = max(maximum_sup_ascent, ascent)
            maximum_sub_descent = max(maximum_sub_descent, descent)
            atoms.append({
                "atom_id": str(atom.get("atom_id") or ""),
                "role": role,
                "text": str(atom.get("text") or ""),
                "bbox_pt": bbox,
                "base_bbox_pt": base_bbox,
                "ascent_extension_pt": round(ascent, 3),
                "descent_extension_pt": round(descent, 3),
            })
    return {
        "atom_count": len(atoms),
        "atoms": atoms,
        "max_superscript_ascent_extension_pt": round(
            maximum_sup_ascent, 3),
        "max_subscript_descent_extension_pt": round(
            maximum_sub_descent, 3),
    }


def measure_math_aware_block(
        dom_bbox: Iterable[float],
        painted_ink_bbox: Iterable[float] | None, *,
        math_groups: Iterable[dict[str, Any]] = (),
        painted_source: str = "final_pdf_text_layer_glyph_bbox",
        ) -> dict[str, Any]:
    """Return a non-mutating effective measurement for one final block.

    ``painted_ink_height`` is the vertical extent required to enclose painted
    ink relative to the DOM measurement (including any math ascent/descent).
    Therefore ``max(dom_height, painted_ink_height)`` is the effective height.
    """
    groups = list(math_groups)
    dom = _box(dom_bbox)
    if dom is None:
        raise ValueError("dom_bbox must contain four coordinates")
    ink = _box(painted_ink_bbox)
    effective = union_bbox(dom, ink) or dom
    dom_height = _height(dom)
    ink_bbox_height = _height(ink)
    painted_ink_height = _height(effective) if ink else dom_height
    painted_extent_height = (
        max(0.0, float(ink[3]) - float(dom[1])) if ink else dom_height)
    measured_height = max(dom_height, painted_ink_height)
    script = _script_extensions(groups)
    return {
        "schema_version": "visual_v07.math_aware_block_measurement.v1",
        "measurement_mode": "dom_plus_final_painted_ink",
        "dom_bbox_pt": dom,
        "dom_height_pt": round(dom_height, 3),
        "painted_ink_bbox_pt": ink,
        "painted_glyph_bbox_height_pt": round(ink_bbox_height, 3),
        "painted_ink_height_pt": round(painted_ink_height, 3),
        "painted_extent_height_from_dom_top_pt": round(
            painted_extent_height, 3),
        "effective_measurement_bbox_pt": effective,
        "measured_height_pt": round(measured_height, 3),
        "measured_height_from_dom_origin_pt": round(
            max(dom_height, painted_extent_height), 3),
        "ink_top_pt": ink[1] if ink else None,
        "ink_bottom_pt": ink[3] if ink else None,
        "ascent_extension_pt": round(
            max(0.0, float(dom[1]) - float(ink[1])) if ink else 0.0, 3),
        "descent_extension_pt": round(
            max(0.0, float(ink[3]) - float(dom[3])) if ink else 0.0, 3),
        "painted_source": painted_source if ink else "dom_fallback",
        "painted_ink_available": ink is not None,
        "has_math_atom_group": bool(groups),
        "math": script,
        "formula": (
            "measured_height = max(dom_height, painted_ink_height)"),
        "geometry_policy": (
            "effective measurement only; SourceTextSlot and hard anchors "
            "remain immutable"),
    }


def apply_math_aware_measurements(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach effective measurement fields to final-collision block records."""
    for block in blocks:
        painted = (block.get("final_pdf_painted_ink_bbox")
                   or block.get("pdf_rendered_bbox"))
        painted_source = (
            "final_pdf_raster_ink"
            if block.get("final_pdf_painted_ink_bbox")
            else "final_pdf_text_layer_glyph_bbox")
        measurement = measure_math_aware_block(
            block["dom_measured_bbox"], painted,
            math_groups=block.get("math_atom_groups") or [],
            painted_source=painted_source)
        block["math_aware_measurement"] = measurement
        block["painted_ink_bbox_pt"] = measurement["painted_ink_bbox_pt"]
        block["effective_measurement_bbox_pt"] = measurement[
            "effective_measurement_bbox_pt"]
        block["math_aware_measured_height"] = measurement[
            "measured_height_pt"]
        block["final_bbox"] = measurement["effective_measurement_bbox_pt"]
    return blocks


__all__ = ["apply_math_aware_measurements", "measure_math_aware_block",
           "union_bbox"]
