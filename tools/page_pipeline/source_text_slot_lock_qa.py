# -*- coding: utf-8 -*-
"""Hard QA for SourceTextSlot geometry locking.

The QA compares source-owned slot envelopes with Chromium's final paragraph
envelopes and painted line geometry.  A fixed CSS ``height`` is an envelope,
not a demand that short target content fill the slot, so outside/overflow
metrics use the union of the actual painted line rectangles.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont


HIGH_CONFIDENCE = 0.85
POSITION_TOLERANCE = 0.05
PAINT_TOLERANCE = 0.20

ELIGIBLE_SOFT_ROLES = {
    "body", "body_bold_lead", "abstract_body", "list_item", "heading",
    "caption", "footnote", "title", "author", "affiliation",
    "front_matter_prose", "recovered_prose",
}
METADATA_ROLES = {
    "page_number", "header", "footer", "header_footer", "marginal",
    "metadata", "decorative",
}
PAGE_METADATA_RE = re.compile(
    r"^(?:page\s*\d+\s*of\s*\d+|"
    r"第?\s*\d+\s*页\s*[,，/]?\s*(?:共\s*)?\d+\s*页)$", re.I)


def _bbox(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return []
    return [round(float(value), 3) for value in values]


def _union(boxes: Iterable[Any]) -> list[float]:
    rows = [_bbox(box) for box in boxes]
    rows = [box for box in rows if box]
    if not rows:
        return []
    return [round(min(box[0] for box in rows), 3),
            round(min(box[1] for box in rows), 3),
            round(max(box[2] for box in rows), 3),
            round(max(box[3] for box in rows), 3)]


def _role(value: Any) -> str:
    role = str(value or "body").strip().lower().replace("-", "_")
    if role in {"section_heading", "subsection_heading", "subheading"}:
        return "heading"
    if role in {"figure_caption", "table_caption"}:
        return "caption"
    if role == "note":
        return "footnote"
    return role


def _metadata_text(value: Any) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return bool(PAGE_METADATA_RE.fullmatch(text))


def _translation_required(block: dict[str, Any],
                          slot: dict[str, Any] | None) -> bool:
    role = _role(block.get("semantic_role")
                 or (slot or {}).get("semantic_role"))
    if role in METADATA_ROLES or role not in ELIGIBLE_SOFT_ROLES:
        return False
    if block.get("render_source") not in (None, "", "canonical_target"):
        return False
    return not _metadata_text(block.get("text_preview") or block.get("text"))


def _slot_indexes(slots: Iterable[dict[str, Any]]) -> tuple[dict, dict]:
    by_fragment: dict[str, list[dict]] = {}
    by_identity: dict[str, list[dict]] = {}
    for slot in slots:
        fragment = str(slot.get("render_fragment_id") or "")
        if fragment:
            by_fragment.setdefault(fragment, []).append(slot)
        for identity in slot.get("render_identity_ids") or []:
            identity = str(identity or "")
            if identity:
                by_identity.setdefault(identity, []).append(slot)
    return by_fragment, by_identity


def _record_indexes(trace: dict[str, Any] | None) -> tuple[dict, dict]:
    by_fragment = {}
    by_render = {}
    for record in (trace or {}).get("records") or []:
        fragment = str(record.get("flow_fragment_id") or "")
        render_id = str(record.get("render_id") or "")
        if fragment:
            by_fragment[fragment] = record
        if render_id:
            by_render[render_id] = record
    return by_fragment, by_render


def _repacked_fragments(trace: dict[str, Any] | None) -> set[str]:
    return {
        str(row.get("flow_fragment_id") or "")
        for row in (trace or {}).get("placements") or []
        if abs(float(row.get("shift") or 0.0)) > POSITION_TOLERANCE
    }


def _outside(painted: list[float], source: list[float]) -> tuple[bool, bool]:
    if len(painted) != 4 or len(source) != 4:
        return True, True
    outside = (painted[0] < source[0] - PAINT_TOLERANCE
               or painted[1] < source[1] - PAINT_TOLERANCE
               or painted[2] > source[2] + PAINT_TOLERANCE
               or painted[3] > source[3] + PAINT_TOLERANCE)
    overflow = (painted[1] < source[1] - PAINT_TOLERANCE
                or painted[3] > source[3] + PAINT_TOLERANCE)
    return outside, overflow


def source_text_slot_lock_qa(
        slot_model: dict[str, Any], final_geometry: dict[str, Any], *,
        lock_trace: dict[str, Any] | None = None,
        repack_trace: dict[str, Any] | None = None,
        artifact_label: str = "",
        confidence_threshold: float = HIGH_CONFIDENCE) -> dict[str, Any]:
    """Audit unique high-confidence delivery text against source slots."""
    slots = list(slot_model.get("slots") or [])
    by_fragment, by_identity = _slot_indexes(slots)
    records_by_fragment, records_by_render = _record_indexes(lock_trace)
    repacked = _repacked_fragments(repack_trace)
    mappings = []
    missing = []
    ambiguous = []
    excluded = []

    for block in final_geometry.get("blocks") or []:
        fragment = str(block.get("flow_fragment_id") or "")
        render_id = str(block.get("render_id")
                        or block.get("paragraph_id") or "")
        candidates = list(by_fragment.get(fragment) or [])
        mapping_basis = "render_fragment_id"
        if not candidates:
            candidates = list(by_identity.get(render_id) or [])
            mapping_basis = "render_identity"
        candidates = list({row["slot_id"]: row
                           for row in candidates}.values())
        candidate = candidates[0] if len(candidates) == 1 else None
        if not _translation_required(block, candidate):
            excluded.append({
                "render_id": render_id, "flow_fragment_id": fragment,
                "semantic_role": block.get("semantic_role"),
                "reason": "not_translation_required_soft_delivery_text",
            })
            continue
        if not candidates:
            missing.append({
                "render_id": render_id, "flow_fragment_id": fragment,
                "semantic_role": block.get("semantic_role"),
            })
            continue
        if len(candidates) > 1:
            ambiguous.append({
                "render_id": render_id, "flow_fragment_id": fragment,
                "slot_ids": sorted(row["slot_id"] for row in candidates),
            })
            continue

        slot = candidates[0]
        source = _bbox(slot.get("source_bbox"))
        envelope = _bbox(block.get("dom_measured_bbox")
                         or block.get("final_bbox"))
        painted = _union(block.get("dom_line_bboxes") or []) or envelope
        high_confidence = (float(slot.get("slot_confidence") or 0.0)
                           >= confidence_threshold)
        record = (records_by_fragment.get(fragment)
                  or records_by_render.get(render_id) or {})
        locked = bool(record.get("geometry_locked"))
        dx = envelope[0] - source[0]
        dy = envelope[1] - source[1]
        source_width = source[2] - source[0]
        final_width = envelope[2] - envelope[0]
        width_delta = final_width - source_width
        x_mutation = abs(dx) > POSITION_TOLERANCE
        y_mutation = abs(dy) > POSITION_TOLERANCE
        width_mutation = abs(width_delta) > POSITION_TOLERANCE
        outside, overflow = _outside(painted, source)
        mutation = x_mutation or y_mutation or width_mutation or outside
        owner_type = str((slot.get("source_topology") or {}).get(
            "owner_type") or "")
        unlocked_recovery = (owner_type == "recovered_prose"
                             and not high_confidence)
        repack_used = bool(record.get("repack_used")) or fragment in repacked
        font_scale = float(record.get("font_scale") or 1.0)
        line_scale = float(record.get("line_height_scale") or 1.0)
        content_top_offset = float(record.get("content_top_offset") or 0.0)
        typography_changed = (str(record.get("fit_level") or "L0") != "L0"
                              or abs(font_scale - 1.0) > 0.001
                              or abs(line_scale - 1.0) > 0.001
                              or abs(content_top_offset) > 0.001)
        if mutation:
            classification = "RED_GEOMETRY_MUTATION"
        elif unlocked_recovery:
            classification = "ORANGE_UNLOCKED_RECOVERY_GEOMETRY"
        elif typography_changed:
            classification = "BLUE_TYPOGRAPHY_ONLY"
        else:
            classification = "GREEN_SOURCE_SLOT_GEOMETRY"
        mappings.append({
            "slot_id": slot["slot_id"], "render_id": render_id,
            "flow_fragment_id": fragment,
            "semantic_role": _role(block.get("semantic_role")
                                   or slot.get("semantic_role")),
            "mapping_basis": mapping_basis,
            "source_bbox": source, "final_envelope_bbox": envelope,
            "painted_content_bbox": painted,
            "dx": round(dx, 3), "dy": round(dy, 3),
            "width_delta": round(width_delta, 3),
            "x_mutation": x_mutation, "y_mutation": y_mutation,
            "width_mutation": width_mutation,
            "outside_slot": outside, "overflow": overflow,
            "geometry_lock_required": high_confidence and mutation,
            "geometry_locked": locked,
            "repack_used": repack_used,
            "fit_level": str(record.get("fit_level") or "L0"),
            "font_scale": round(font_scale, 3),
            "line_height_scale": round(line_scale, 3),
            "content_top_offset": round(content_top_offset, 3),
            "fit_success": record.get("fit_success"),
            "slot_confidence": slot.get("slot_confidence"),
            "source_owner_id": slot.get("source_owner_id"),
            "source_fragment_id": slot.get("source_fragment_id"),
            "visual_group_id": slot.get("visual_group_id"),
            "source_topology": slot.get("source_topology"),
            "unlocked_recovery_geometry": unlocked_recovery,
            "typography_changed": typography_changed,
            "classification": classification,
        })

    locked_rows = [row for row in mappings if row["geometry_locked"]]
    high_rows = [row for row in mappings
                 if float(row["slot_confidence"] or 0.0)
                 >= confidence_threshold]
    fit_levels = {"L0": 0, "L1": 0, "L2": 0, "L3": 0, "L4": 0}
    for row in locked_rows:
        level = row["fit_level"]
        if level in fit_levels:
            fit_levels[level] += 1
    unresolved = [row for row in (lock_trace or {}).get("records") or []
                  if row.get("geometry_locked")
                  and row.get("fit_success") is False]
    metrics = {
        "eligible_soft_block_count": len(mappings) + len(missing)
                                      + len(ambiguous),
        "geometry_lock_required_count": sum(
            row["geometry_lock_required"] for row in mappings),
        "geometry_locked_block_count": len(locked_rows),
        "geometry_preserved_block_count": sum(
            not row["x_mutation"] and not row["y_mutation"]
            and not row["width_mutation"] for row in locked_rows),
        "typography_change_only_count": sum(
            row["typography_changed"] and not row["x_mutation"]
            and not row["y_mutation"] and not row["width_mutation"]
            and not row["outside_slot"] for row in locked_rows),
        "geometry_locked_repack_count": sum(
            row["repack_used"] for row in locked_rows),
        "geometry_locked_x_mutation_count": sum(
            row["x_mutation"] for row in locked_rows),
        "geometry_locked_y_mutation_count": sum(
            row["y_mutation"] for row in locked_rows),
        "geometry_locked_width_mutation_count": sum(
            row["width_mutation"] for row in locked_rows),
        "geometry_locked_outside_slot_count": sum(
            row["outside_slot"] for row in locked_rows),
        "geometry_locked_overflow_count": sum(
            row["overflow"] for row in locked_rows),
        "slot_capacity_unresolved_count": len(unresolved),
        "unexplained_geometry_mutation_count": sum(
            row in high_rows and (row["x_mutation"]
                                  or row["y_mutation"]
                                  or row["width_mutation"]
                                  or row["outside_slot"])
            and not row["unlocked_recovery_geometry"] for row in mappings),
        "block_outside_source_slot_count": sum(
            row["outside_slot"] for row in high_rows),
        "slot_mapping_missing_count": len(missing),
        "slot_mapping_ambiguous_count": len(ambiguous),
        "unlocked_recovery_geometry_count": sum(
            row["unlocked_recovery_geometry"] for row in mappings),
        "excluded_page_metadata_count": len(excluded),
        "fit_level_l0_count": fit_levels["L0"],
        "fit_level_l1_count": fit_levels["L1"],
        "fit_level_l2_count": fit_levels["L2"],
        "fit_level_l3_count": fit_levels["L3"],
        "fit_level_l4_count": fit_levels["L4"],
    }
    hard_keys = (
        "geometry_locked_repack_count",
        "geometry_locked_x_mutation_count",
        "geometry_locked_y_mutation_count",
        "geometry_locked_width_mutation_count",
        "geometry_locked_outside_slot_count",
        "geometry_locked_overflow_count",
        "slot_capacity_unresolved_count",
        "unexplained_geometry_mutation_count",
        "slot_mapping_missing_count", "slot_mapping_ambiguous_count",
    )
    passed = all(int(metrics[key]) == 0 for key in hard_keys)
    return {
        "schema_version": "visual_v07.source_text_slot_lock_qa.v1",
        "artifact_label": artifact_label,
        "decision": "pass" if passed else "fail",
        "confidence_threshold": confidence_threshold,
        "metrics": metrics, "mappings": mappings,
        "missing": missing, "ambiguous": ambiguous,
        "excluded": excluded, "unresolved": unresolved,
        "geometry_truth": {
            "slot_envelope": "SourceTextSlot.source_bbox",
            "final_envelope": "Chromium paragraph getBoundingClientRect",
            "painted_content": "union of Chromium Range line rectangles",
        },
    }


def aggregate_lock_qas(qas: Iterable[dict[str, Any]]) -> dict[str, Any]:
    pages = list(qas)
    names = list((pages[0].get("metrics") or {}).keys()) if pages else []
    metrics = {name: sum(int(page["metrics"].get(name, 0))
                         for page in pages) for name in names}
    return {
        "schema_version": "visual_v07.source_text_slot_lock_qa.aggregate.v1",
        "decision": ("pass" if pages and all(page["decision"] == "pass"
                                              for page in pages)
                     else "fail"),
        "metrics": metrics,
        "pages": [{"artifact_label": page.get("artifact_label"),
                   "decision": page.get("decision"),
                   "metrics": page.get("metrics")} for page in pages],
    }


def _font(size: int = 13):
    for path in ("C:/Windows/Fonts/arial.ttf",
                 "C:/Windows/Fonts/msyh.ttc"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def render_geometry_lock_overlay(
        qa: dict[str, Any], screenshot_path: str | Path,
        out_path: str | Path, *, page_width: float,
        page_height: float,
        figure_geometry: Iterable[dict[str, Any]] | None = None) -> None:
    """Render source envelope, final envelope, and lock evidence panel."""
    page = Image.open(screenshot_path).convert("RGB")
    mappings = list(qa.get("mappings") or [])
    row_height = 105
    header_height = 44
    panel_width = 980
    canvas_height = max(page.height, header_height + row_height * len(mappings)
                        + 16)
    canvas = Image.new("RGB", (page.width + panel_width, canvas_height),
                       "white")
    canvas.paste(page, (0, 0))
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = _font(13)
    title_font = _font(16)
    sx, sy = page.width / page_width, page.height / page_height
    colors = {
        "GREEN_SOURCE_SLOT_GEOMETRY": (20, 155, 75, 245),
        "BLUE_TYPOGRAPHY_ONLY": (25, 105, 225, 245),
        "ORANGE_UNLOCKED_RECOVERY_GEOMETRY": (235, 130, 15, 245),
        "RED_GEOMETRY_MUTATION": (220, 35, 35, 245),
    }
    draw.text((page.width + 12, 10),
              "%s | geometry lock" % (qa.get("artifact_label") or "slot QA"),
              fill=(20, 20, 20, 255), font=title_font)
    for figure in figure_geometry or []:
        box = _bbox(figure.get("bbox"))
        if not box:
            continue
        xy = [box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy]
        draw.rectangle(xy, outline=(145, 45, 185, 245), width=5)
        draw.text((xy[0] + 4, xy[1] + 4),
                  "FIGURE %s (diagnosis only)" % (
                      figure.get("figure_id") or figure.get("region_id")),
                  fill=(120, 25, 160, 255), font=font,
                  stroke_width=2, stroke_fill=(255, 255, 255, 230))
    for index, row in enumerate(mappings):
        color = colors[row["classification"]]
        source = row["source_bbox"]
        final = row["final_envelope_bbox"]
        source_xy = [source[0] * sx, source[1] * sy,
                     source[2] * sx, source[3] * sy]
        final_xy = [final[0] * sx + 2, final[1] * sy + 2,
                    final[2] * sx - 2, final[3] * sy - 2]
        draw.rectangle(source_xy, outline=color, width=5)
        draw.rectangle(final_xy, outline=color, width=2)
        draw.ellipse((source_xy[0], source_xy[1],
                      source_xy[0] + 24, source_xy[1] + 24), fill=color)
        draw.text((source_xy[0] + 5, source_xy[1] + 3), str(index + 1),
                  fill=(255, 255, 255, 255), font=font)
        y = header_height + index * row_height
        label = (
            "#%d %s | %s | %s | %s\n"
            "source=%s | final=%s | painted=%s\n"
            "dx=%+.3f dy=%+.3f dw=%+.3f | font=%.3f line=%.3f\n"
            "geometry_locked=%s | repack_used=%s | fit=%s | top-pad=%.3f"
            % (index + 1, row["slot_id"], row["render_id"],
               row["semantic_role"], row["classification"],
               row["source_bbox"], row["final_envelope_bbox"],
               row["painted_content_bbox"], row["dx"], row["dy"],
               row["width_delta"], row["font_scale"],
               row["line_height_scale"],
               str(row["geometry_locked"]).lower(),
               str(row["repack_used"]).lower(), row["fit_level"],
               row["content_top_offset"]))
        draw.rectangle((page.width + 7, y - 3,
                        page.width + panel_width - 7, y + row_height - 6),
                       fill=(248, 248, 248, 255), outline=color, width=2)
        draw.multiline_text((page.width + 13, y), label,
                            fill=(30, 30, 30, 255), font=font, spacing=2)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="SourceTextSlot lock QA")
    parser.add_argument("--slots", required=True)
    parser.add_argument("--final-geometry", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--lock-trace")
    parser.add_argument("--repack")
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    load = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
    qa = source_text_slot_lock_qa(
        load(args.slots), load(args.final_geometry),
        lock_trace=load(args.lock_trace) if args.lock_trace else None,
        repack_trace=load(args.repack) if args.repack else None,
        artifact_label=args.label)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(qa, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(json.dumps({"decision": qa["decision"],
                      "metrics": qa["metrics"]}, ensure_ascii=False))
    return 0 if qa["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["HIGH_CONFIDENCE", "aggregate_lock_qas",
           "render_geometry_lock_overlay", "source_text_slot_lock_qa"]
