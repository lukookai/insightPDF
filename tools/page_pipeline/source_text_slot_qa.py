# -*- coding: utf-8 -*-
"""Geometry-freeze QA for SourceTextSlot versus final Chromium text blocks."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

PT_PER_CSS_PX = 72.0 / 96.0

EXPECTED_SOURCE_GEOMETRY = "EXPECTED_SOURCE_GEOMETRY"
TYPOGRAPHY_ONLY = "TYPOGRAPHY_ONLY"
REPACK_MUTATION = "REPACK_MUTATION"
RECOVERY_GEOMETRY = "RECOVERY_GEOMETRY"
UNEXPLAINED_MUTATION = "UNEXPLAINED_MUTATION"

CLASS_COLORS = {
    EXPECTED_SOURCE_GEOMETRY: (20, 155, 75, 245),
    TYPOGRAPHY_ONLY: (25, 105, 225, 245),
    REPACK_MUTATION: (235, 130, 15, 245),
    RECOVERY_GEOMETRY: (20, 155, 75, 245),
    UNEXPLAINED_MUTATION: (220, 35, 35, 245),
}


def _bbox(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return []
    return [round(float(value), 3) for value in values]


def _intersection(a: list[float], b: list[float]) -> list[float]:
    if len(a) != 4 or len(b) != 4:
        return []
    box = [max(a[0], b[0]), max(a[1], b[1]),
           min(a[2], b[2]), min(a[3], b[3])]
    return [round(value, 3) for value in box] \
        if box[2] > box[0] and box[3] > box[1] else []


def _area(box: list[float]) -> float:
    return ((box[2] - box[0]) * (box[3] - box[1])
            if len(box) == 4 else 0.0)


def capture_figure_geometry(html_path: str | Path) -> list[dict[str, Any]]:
    """Read already-rendered figure geometry; never changes the page."""
    from playwright.sync_api import sync_playwright

    html_path = Path(html_path).resolve()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=[
            "--no-sandbox", "--disable-gpu", "--allow-file-access-from-files"])
        page = browser.new_page(viewport={"width": 900, "height": 1200})
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.wait_for_function(
            "Array.from(document.images).every(image => image.complete)")
        rows = page.evaluate(r"""() => {
          const all=Array.from(document.querySelectorAll('body *'));
          const paragraphs=Array.from(document.querySelectorAll(
            '.paragraph-block[data-flow-fragment]'));
          return Array.from(document.querySelectorAll('.figure-region')).map(el=>{
            const r=el.getBoundingClientRect();
            const covered=[];
            for(const p of paragraphs){
              const b=p.getBoundingClientRect();
              const overlap=Math.max(0,Math.min(r.right,b.right)-Math.max(r.left,b.left))
                *Math.max(0,Math.min(r.bottom,b.bottom)-Math.max(r.top,b.top));
              if(overlap>0.25 && (p.compareDocumentPosition(el)
                  & Node.DOCUMENT_POSITION_FOLLOWING)){
                covered.push(p.dataset.flowFragment||p.dataset.renderId||'');
              }
            }
            return {figure_id:el.dataset.figure||el.dataset.region||'',
              region_id:el.dataset.region||'',
              rect:[r.x,r.y,r.right,r.bottom],
              dom_index:all.indexOf(el),
              painted_after_overlapping_text:covered};
          });
        }""")
        browser.close()
    for row in rows:
        row["bbox"] = [round(value * PT_PER_CSS_PX, 3)
                       for value in row.pop("rect")]
    return rows


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


def _known_repack(repack_trace: dict[str, Any] | None) -> dict[str, dict]:
    return {
        str(row.get("flow_fragment_id") or ""): row
        for row in (repack_trace or {}).get("placements") or []
        if abs(float(row.get("shift") or 0.0)) > 0.01
    }


def _known_typography(typography_trace: dict[str, Any] | None) -> dict[str, dict]:
    return {
        str(row.get("flow_fragment_id") or ""): row
        for row in (typography_trace or {}).get("records") or []
        if row.get("fit_level") not in (None, "L0")
        and not row.get("repack_used")
    }


def source_text_slot_qa(
        slot_model: dict[str, Any], final_geometry: dict[str, Any], *,
        repack_trace: dict[str, Any] | None = None,
        typography_trace: dict[str, Any] | None = None,
        figure_geometry: Iterable[dict[str, Any]] | None = None,
        artifact_label: str = "",
        position_tolerance: float = 0.75,
        size_tolerance: float = 0.75) -> dict[str, Any]:
    """Classify final RenderIdentity geometry against immutable slots."""
    slots = list(slot_model.get("slots") or [])
    by_fragment, by_identity = _slot_indexes(slots)
    repacked = _known_repack(repack_trace)
    typography = _known_typography(typography_trace)
    mappings = []
    missing = []
    ambiguous = []

    for block in final_geometry.get("blocks") or []:
        fragment = str(block.get("flow_fragment_id") or "")
        render_id = str(block.get("render_id") or block.get("paragraph_id")
                        or "")
        candidates = list(by_fragment.get(fragment) or [])
        mapping_basis = "render_fragment_id"
        if not candidates:
            candidates = list(by_identity.get(render_id) or [])
            mapping_basis = "render_identity"
        # Deduplicate one slot reached through several identities.
        unique = {candidate["slot_id"]: candidate for candidate in candidates}
        candidates = list(unique.values())
        if not candidates:
            missing.append({
                "render_id": render_id, "flow_fragment_id": fragment,
                "semantic_role": block.get("semantic_role"),
                "final_bbox": _bbox(block.get("dom_measured_bbox")),
            })
            continue
        if len(candidates) > 1:
            ambiguous.append({
                "render_id": render_id, "flow_fragment_id": fragment,
                "slot_ids": sorted(candidate["slot_id"]
                                   for candidate in candidates),
            })
            continue
        slot = candidates[0]
        source_box = _bbox(slot.get("source_bbox"))
        final_box = _bbox(block.get("dom_measured_bbox")
                          or block.get("final_bbox"))
        dx = final_box[0] - source_box[0]
        dy = final_box[1] - source_box[1]
        source_width = source_box[2] - source_box[0]
        final_width = final_box[2] - final_box[0]
        source_height = source_box[3] - source_box[1]
        final_height = final_box[3] - final_box[1]
        width_delta = final_width - source_width
        height_delta = final_height - source_height
        x_changed = abs(dx) > position_tolerance
        y_changed = abs(dy) > position_tolerance
        width_changed = abs(width_delta) > size_tolerance
        height_expanded = height_delta > size_tolerance
        outside = (
            final_box[0] < source_box[0] - position_tolerance
            or final_box[1] < source_box[1] - position_tolerance
            or final_box[2] > source_box[2] + position_tolerance
            or final_box[3] > source_box[3] + position_tolerance)
        mutation = x_changed or y_changed or width_changed \
            or height_expanded or outside
        owner_type = str((slot.get("source_topology") or {}).get(
            "owner_type") or "")
        repack = repacked.get(fragment)
        fit = typography.get(fragment)
        if repack:
            classification = REPACK_MUTATION
            explanation = "Task2 browser-measured placement shifted this block"
        elif fit and not x_changed and not y_changed and not width_changed:
            classification = TYPOGRAPHY_ONLY
            explanation = (
                "font/line-height changed within the frozen x/y/width slot")
        elif not mutation:
            classification = EXPECTED_SOURCE_GEOMETRY
            explanation = "final block remains inside source-owned geometry"
        elif owner_type == "recovered_prose":
            classification = RECOVERY_GEOMETRY
            explanation = (
                "RecoveredProseBlock has explicit pre-mutation layout provenance")
        else:
            classification = UNEXPLAINED_MUTATION
            explanation = (
                "final geometry differs without typography/repack/recovery provenance")
        mappings.append({
            "slot_id": slot["slot_id"],
            "render_id": render_id,
            "flow_fragment_id": fragment,
            "semantic_role": block.get("semantic_role")
            or slot.get("semantic_role"),
            "classification": classification,
            "explanation": explanation,
            "mapping_basis": mapping_basis,
            "source_bbox": source_box, "final_bbox": final_box,
            "dx": round(dx, 3), "dy": round(dy, 3),
            "width_delta": round(width_delta, 3),
            "height_delta": round(height_delta, 3),
            "source_font_size": slot.get("source_font_size"),
            "source_line_height": slot.get("source_line_height"),
            "final_font_size": block.get("font_size"),
            "final_line_height": block.get("line_height"),
            "x_displaced": x_changed, "y_displaced": y_changed,
            "width_changed": width_changed,
            "height_expanded": height_expanded,
            "block_outside_source_slot": outside,
            "source_owner_id": slot.get("source_owner_id"),
            "logical_paragraph_id": slot.get("logical_paragraph_id"),
            "source_fragment_id": slot.get("source_fragment_id"),
            "visual_group_id": slot.get("visual_group_id"),
            "region_id": slot.get("region_id"),
            "column": slot.get("column"),
            "source_topology": slot.get("source_topology"),
            "slot_confidence": slot.get("slot_confidence"),
            "known_repack": repack,
            "known_typography_fit": fit,
        })

    figures = list(figure_geometry or [])
    figure_evidence = []
    for figure in figures:
        figure_box = _bbox(figure.get("bbox"))
        for mapping in mappings:
            source_overlap = _intersection(mapping["source_bbox"], figure_box)
            final_overlap = _intersection(mapping["final_bbox"], figure_box)
            if not source_overlap and not final_overlap:
                continue
            figure_evidence.append({
                "figure_id": figure.get("figure_id")
                or figure.get("region_id"),
                "figure_bbox": figure_box,
                "slot_id": mapping["slot_id"],
                "render_id": mapping["render_id"],
                "flow_fragment_id": mapping["flow_fragment_id"],
                "semantic_role": mapping["semantic_role"],
                "source_slot_bbox": mapping["source_bbox"],
                "final_text_bbox": mapping["final_bbox"],
                "source_overlap_bbox": source_overlap,
                "source_overlap_area": round(_area(source_overlap), 3),
                "final_overlap_bbox": final_overlap,
                "final_overlap_area": round(_area(final_overlap), 3),
                "figure_painted_after_text": (
                    mapping["flow_fragment_id"] in
                    (figure.get("painted_after_overlapping_text") or [])),
                "evidence_only_no_fix": True,
            })

    metrics = {
        "source_text_slot_count": len(slots),
        "required_soft_block_count": len(final_geometry.get("blocks") or []),
        "mapped_target_block_count": len(mappings),
        "slot_mapping_missing_count": len(missing),
        "slot_mapping_ambiguous_count": len(ambiguous),
        "slot_x_displacement_count": sum(row["x_displaced"]
                                         for row in mappings),
        "slot_y_displacement_count": sum(row["y_displaced"]
                                         for row in mappings),
        "slot_width_change_count": sum(row["width_changed"]
                                       for row in mappings),
        "slot_height_expansion_count": sum(row["height_expanded"]
                                           for row in mappings),
        "block_outside_source_slot_count": sum(
            row["block_outside_source_slot"] for row in mappings),
        "expected_source_geometry_count": sum(
            row["classification"] == EXPECTED_SOURCE_GEOMETRY
            for row in mappings),
        "typography_only_count": sum(
            row["classification"] == TYPOGRAPHY_ONLY for row in mappings),
        "repack_mutation_count": sum(
            row["classification"] == REPACK_MUTATION for row in mappings),
        "recovery_geometry_count": sum(
            row["classification"] == RECOVERY_GEOMETRY for row in mappings),
        "unexplained_geometry_mutation_count": sum(
            row["classification"] == UNEXPLAINED_MUTATION
            for row in mappings),
        "figure_text_overlap_evidence_count": len(figure_evidence),
    }
    required_pass = (metrics["slot_mapping_missing_count"] == 0
                     and metrics["slot_mapping_ambiguous_count"] == 0)
    return {
        "schema_version": "visual_v07.source_text_slot_qa.v1",
        "artifact_label": artifact_label,
        "decision": "pass" if required_pass else "fail",
        "metrics": metrics, "mappings": mappings,
        "missing": missing, "ambiguous": ambiguous,
        "figure_geometry": figures,
        "figure_text_overlap_evidence": figure_evidence,
        "geometry_truth": "Chromium DOM measured bbox",
        "classification_policy": {
            EXPECTED_SOURCE_GEOMETRY: "target remains inside source slot",
            TYPOGRAPHY_ONLY: "x/y/width frozen; recorded typography changed",
            REPACK_MUTATION: "Task 2 trace records a non-zero shift",
            RECOVERY_GEOMETRY: "RecoveredProseBlock layout provenance exists",
            UNEXPLAINED_MUTATION: "mutation has none of the above provenance",
        },
    }


def _font(size: int = 13):
    for path in ("C:/Windows/Fonts/arial.ttf",
                 "C:/Windows/Fonts/msyh.ttc"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def render_slot_overlay(qa: dict[str, Any], screenshot_path: str | Path,
                        out_path: str | Path, *, page_width: float,
                        page_height: float,
                        mode: str = "combined") -> None:
    """Render readable source/final evidence with a non-overlapping panel."""
    if mode not in {"source", "final", "combined"}:
        raise ValueError("unknown overlay mode: %s" % mode)
    page = Image.open(screenshot_path).convert("RGB")
    mappings = list(qa.get("mappings") or [])
    figures = list(qa.get("figure_geometry") or [])
    row_height = 91
    header_height = 44
    panel_width = 900
    canvas_height = max(page.height, header_height + row_height * len(mappings)
                        + 20)
    canvas = Image.new("RGB", (page.width + panel_width, canvas_height),
                       "white")
    canvas.paste(page, (0, 0))
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = _font(13)
    title_font = _font(16)
    sx, sy = page.width / page_width, page.height / page_height
    draw.text((page.width + 12, 10),
              "%s | %s" % (qa.get("artifact_label") or "slot QA", mode),
              fill=(20, 20, 20, 255), font=title_font)

    for index, mapping in enumerate(mappings):
        color = CLASS_COLORS[mapping["classification"]]
        source = mapping["source_bbox"]
        final = mapping["final_bbox"]
        source_xy = [source[0] * sx, source[1] * sy,
                     source[2] * sx, source[3] * sy]
        final_xy = [final[0] * sx, final[1] * sy,
                    final[2] * sx, final[3] * sy]
        if mode in {"source", "combined"}:
            draw.rectangle(source_xy, outline=color, width=5)
        if mode in {"final", "combined"}:
            inset = 3 if mode == "combined" else 0
            final_xy = [final_xy[0] + inset, final_xy[1] + inset,
                        final_xy[2] - inset, final_xy[3] - inset]
            draw.rectangle(final_xy, outline=color, width=3)
        marker_x = (final_xy[0] if mode == "final" else source_xy[0])
        marker_y = (final_xy[1] if mode == "final" else source_xy[1])
        draw.ellipse((marker_x, marker_y, marker_x + 24, marker_y + 24),
                     fill=color)
        draw.text((marker_x + 5, marker_y + 3), str(index + 1),
                  fill=(255, 255, 255, 255), font=font)
        y = header_height + index * row_height
        label = (
            "#%d %s | %s | %s | %s\n"
            "slot=%s\nsource=%s | final=%s\n"
            "dx=%+.3f dy=%+.3f dw=%+.3f dh=%+.3f"
            % (index + 1, mapping["slot_id"], mapping["render_id"],
               mapping["semantic_role"], mapping["classification"],
               mapping["source_fragment_id"], mapping["source_bbox"],
               mapping["final_bbox"], mapping["dx"], mapping["dy"],
               mapping["width_delta"], mapping["height_delta"]))
        draw.rectangle((page.width + 7, y - 3,
                        page.width + panel_width - 7, y + row_height - 6),
                       fill=(248, 248, 248, 255), outline=color, width=2)
        draw.multiline_text((page.width + 13, y), label,
                            fill=(30, 30, 30, 255), font=font, spacing=2)

    for figure in figures:
        box = figure.get("bbox") or []
        if len(box) != 4:
            continue
        xy = [box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy]
        draw.rectangle(xy, outline=(145, 45, 185, 245), width=4)
        draw.text((xy[0] + 4, xy[1] + 4),
                  "FIGURE %s (evidence only)" % figure.get("figure_id"),
                  fill=(120, 25, 160, 255), font=font,
                  stroke_width=2, stroke_fill=(255, 255, 255, 230))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="SourceTextSlot geometry QA")
    parser.add_argument("--slots", required=True)
    parser.add_argument("--final-geometry", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--repack")
    parser.add_argument("--typography")
    parser.add_argument("--html")
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    load = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
    result = source_text_slot_qa(
        load(args.slots), load(args.final_geometry),
        repack_trace=load(args.repack) if args.repack else None,
        typography_trace=load(args.typography) if args.typography else None,
        figure_geometry=(capture_figure_geometry(args.html)
                         if args.html else None),
        artifact_label=args.label)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps({"decision": result["decision"],
                      "metrics": result["metrics"]},
                     ensure_ascii=False))
    return 0 if result["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["capture_figure_geometry", "render_slot_overlay",
           "source_text_slot_qa"]
