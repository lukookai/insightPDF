# -*- coding: utf-8 -*-
"""Browser-measured, region-local second-pass placement for soft blocks."""
from __future__ import annotations

import copy
import html as html_lib
import re
from typing import Any

from region_local_packing import PACK_GAP


def _placement_plan(collision_qa: dict[str, Any], *, gap: float) -> tuple[
        list[dict[str, Any]], list[dict[str, Any]], int]:
    """Build one region-local placement plan from final DOM measurements."""
    by_fragment = {
        str(row.get("flow_fragment_id") or ""): row
        for row in collision_qa.get("blocks") or []}
    collision_groups = set()
    unresolved = []
    for collision in collision_qa.get("collisions") or []:
        predecessor = by_fragment.get(str(
            collision.get("predecessor_fragment_id") or ""), {})
        successor = by_fragment.get(str(
            collision.get("successor_fragment_id") or ""), {})
        locked = [row for row in (predecessor, successor)
                  if row.get("geometry_locked")]
        if locked:
            unresolved.append({
                "column": int(collision["column"]),
                "region_id": str(collision["region_id"]),
                "fragment_id": str(locked[0].get("flow_fragment_id") or ""),
                "reason": (
                    "geometry-locked collision must BLOCK; repack forbidden"),
            })
            continue
        collision_groups.add((int(collision["column"]),
                              str(collision["region_id"])))
    locked_region_groups = {
        (int(row.get("column") or 0), str(row.get("region_id") or ""))
        for row in collision_qa.get("blocks") or []
        if row.get("geometry_locked")}
    for key in sorted(collision_groups & locked_region_groups):
        unresolved.append({
            "column": key[0], "region_id": key[1], "fragment_id": "",
            "reason": (
                "region contains geometry-locked text; repack forbidden"),
        })
    collision_groups -= locked_region_groups
    groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for block in collision_qa.get("blocks") or []:
        key = (int(block["column"]), str(block["region_id"]))
        if key in collision_groups:
            groups.setdefault(key, []).append(block)

    placements = []
    for key, blocks in groups.items():
        blocks.sort(key=lambda block: int(block["reading_order"]))
        cursor = None
        region_bottom = min(float(block.get("region_bottom") or 0.0)
                            for block in blocks)
        for block in blocks:
            original_top = float(block["assigned_top"])
            new_top = original_top if cursor is None else max(original_top,
                                                               cursor)
            # Final ink can extend beyond the CSS/DOM height, especially for
            # MathAtomGroup sup/sub runs.  Consume the independent effective
            # measurement when available; legacy records retain the DOM
            # fallback.  This does not alter SourceTextSlot geometry.
            height = float(block.get("math_aware_measured_height")
                           or block["dom_measured_height"])
            new_bottom = new_top + height
            fits = new_bottom <= region_bottom + 0.5
            if not fits:
                unresolved.append({
                    "column": key[0], "region_id": key[1],
                    "fragment_id": str(block["flow_fragment_id"]),
                    "new_bottom": round(new_bottom, 3),
                    "region_bottom": round(region_bottom, 3),
                    "reason": "measured sequence exceeds source region",
                })
            placements.append({
                "column": key[0], "region_id": key[1],
                "render_id": block["render_id"],
                "flow_fragment_id": str(block["flow_fragment_id"]),
                "semantic_role": block["semantic_role"],
                "original_top": round(original_top, 3),
                "measured_height": round(height, 3),
                "new_top": round(new_top, 3),
                "new_bottom": round(new_bottom, 3),
                "shift": round(new_top - original_top, 3),
                "region_bottom": round(region_bottom, 3),
                "fits": fits,
            })
            cursor = new_bottom + gap
    return placements, unresolved, len(groups)


def _trace(placements: list[dict[str, Any]],
           unresolved: list[dict[str, Any]], region_count: int) -> dict:
    return {
        "schema_version": "visual_v07.browser_measured_repack.v1",
        "applied": bool(region_count),
        "repacked_region_count": region_count,
        "measured_block_count": len(placements),
        "moved_block_count": sum(row["shift"] > 0.01 for row in placements),
        "hard_anchor_moved_count": 0,
        "cross_region_spill_count": 0,
        "cross_page_spill_count": 0,
        "unresolved_count": len(unresolved),
        "placements": placements,
        "unresolved": unresolved,
    }


def repack_flows_from_final_geometry(
        flows: list[dict[str, Any]], collision_qa: dict[str, Any], *,
        gap: float = PACK_GAP) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Advance colliding successors using measured DOM block heights.

    Only paragraph ``flow_y`` values in the same measured column/region are
    changed.  Formula/table/figure anchors are never touched; a region that
    cannot contain the measured sequence is returned unresolved and must
    BLOCK instead of spilling.
    """
    updated = copy.deepcopy(flows)
    item_by_fragment: dict[str, dict[str, Any]] = {}
    for flow in updated:
        for item in flow.get("items") or []:
            if item.get("kind") != "paragraph":
                continue
            fragment_id = str(item.get("flow_fragment_id")
                              or item.get("paragraph_id") or "")
            item_by_fragment[fragment_id] = item

    placements, unresolved, region_count = _placement_plan(
        collision_qa, gap=gap)
    if not unresolved:
        for placement in placements:
            fragment_id = placement["flow_fragment_id"]
            item = item_by_fragment.get(fragment_id)
            if item is None:
                unresolved.append({
                    "column": placement["column"],
                    "region_id": placement["region_id"],
                    "fragment_id": fragment_id,
                    "reason": "measured block has no paragraph flow item",
                })
                continue
            item["flow_y"] = placement["new_top"]
            item["browser_measured_top"] = placement["new_top"]
            item["browser_measured_height"] = placement["measured_height"]
            item["browser_repack_applied"] = True
    if unresolved:
        # The caller must BLOCK, but returning the original flow graph also
        # prevents accidental use of a partial placement.
        updated = copy.deepcopy(flows)
    return updated, _trace(placements, unresolved, region_count)


def repack_html_from_final_geometry(
        html_text: str, collision_qa: dict[str, Any], *,
        gap: float = PACK_GAP) -> tuple[str, dict[str, Any]]:
    """Apply the same measured placement plan to an already-rendered HTML.

    This is used for deterministic frozen-artifact replay.  It touches only
    the ``top`` declaration of complete paragraph blocks selected by their
    RenderIdentity fragment id; hard elements are never selected.
    """
    placements, unresolved, region_count = _placement_plan(
        collision_qa, gap=gap)
    updated = html_text
    if not unresolved:
        for placement in placements:
            fragment = html_lib.escape(placement["flow_fragment_id"],
                                       quote=True)
            tag_pattern = re.compile(
                r'<div\b(?=[^>]*\bclass="[^"]*\bparagraph-block\b[^"]*")'
                r'(?=[^>]*\bdata-flow-fragment="%s")[^>]*>'
                % re.escape(fragment), re.IGNORECASE)
            matched = tag_pattern.search(updated)
            if matched is None:
                unresolved.append({
                    "column": placement["column"],
                    "region_id": placement["region_id"],
                    "fragment_id": placement["flow_fragment_id"],
                    "reason": "measured block has no HTML RenderIdentity",
                })
                continue
            tag = matched.group(0)
            style_match = re.search(r'\bstyle="([^"]*)"', tag,
                                    re.IGNORECASE)
            if style_match is None:
                unresolved.append({
                    "column": placement["column"],
                    "region_id": placement["region_id"],
                    "fragment_id": placement["flow_fragment_id"],
                    "reason": "paragraph block has no inline placement style",
                })
                continue
            style = style_match.group(1)
            if not re.search(r'(?i)(?:^|;)\s*top\s*:', style):
                unresolved.append({
                    "column": placement["column"],
                    "region_id": placement["region_id"],
                    "fragment_id": placement["flow_fragment_id"],
                    "reason": "paragraph block has no top declaration",
                })
                continue
            new_style = re.sub(
                r'(?i)((?:^|;)\s*top\s*:)\s*[-+0-9.]+(?:pt|px)?',
                lambda match: "%s%.3fpt" % (
                    match.group(1), placement["new_top"]),
                style, count=1)
            new_tag = (tag[:style_match.start(1)] + new_style
                       + tag[style_match.end(1):])
            updated = updated[:matched.start()] + new_tag + updated[matched.end():]
    if unresolved:
        updated = html_text
    return updated, _trace(placements, unresolved, region_count)


__all__ = ["repack_flows_from_final_geometry",
           "repack_html_from_final_geometry"]
