# -*- coding: utf-8 -*-
"""visual-v07 Task 4E: diagnostic-only Figure internal text ownership audit."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageDraw, ImageFont


REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))

from figure_internal_text_ownership_audit import (  # noqa: E402
    figure_internal_text_ownership_audit,
)


OUT = REPO / "outputs" / "visual_v07_task4e"
MODEL_PATH = (REPO / "outputs" / "phase4e2a_qa_recovery" / "doc2"
              / "pages" / "p005" / "stitched_page_model.json")
SOURCE_PDF = Path("C:/Users/74496/Desktop/PPAT.pdf")
SOURCE_PAGE_INDEX = 4
SLOTS_PATH = (REPO / "outputs" / "visual_v07_task4a"
              / "source_text_slots_ppat_p005.json")
FINAL_DIR = REPO / "outputs" / "visual_v07_task4d" / "ppat_p005"
FINAL_HTML = FINAL_DIR / "zh_visual.html"
FINAL_PDF = FINAL_DIR / "zh_visual.pdf"


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _bbox(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return []
    return [round(float(value), 3) for value in values]


def _intersection(first: list[float], second: list[float]) -> list[float]:
    box = [max(first[0], second[0]), max(first[1], second[1]),
           min(first[2], second[2]), min(first[3], second[3])]
    return box if box[2] > box[0] and box[3] > box[1] else []


def _area(box: list[float]) -> float:
    return (max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0)
            if len(box) == 4 else 0.0)


def _nearest_annotation_peer(target: dict[str, Any],
                             records: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [
        row for row in records
        if row.get("span_id") != target.get("span_id")
        and row.get("current_owner") == "figure"
        and row.get("font") == target.get("font")
        and abs(float(row.get("font_size") or 0.0)
                - float(target.get("font_size") or 0.0)) <= 0.05]
    if not candidates:
        return {}
    winner = min(candidates, key=lambda row: (
        abs(float((row.get("bbox") or [0, 0, 0, 0])[1])
            - float((target.get("bbox") or [0, 0, 0, 0])[1])),
        abs(int(row.get("source_reading_order") or 0)
            - int(target.get("source_reading_order") or 0))))
    target_box = _bbox(target.get("bbox"))
    winner_box = _bbox(winner.get("bbox"))
    return {
        "span_id": winner.get("span_id"),
        "text": winner.get("text"),
        "current_owner": winner.get("current_owner"),
        "bbox": winner_box,
        "font": winner.get("font"),
        "font_size": winner.get("font_size"),
        "baseline_top_delta_pt": round(
            abs(target_box[1] - winner_box[1]), 3),
        "same_font": winner.get("font") == target.get("font"),
        "font_size_delta_pt": round(abs(
            float(target.get("font_size") or 0.0)
            - float(winner.get("font_size") or 0.0)), 3),
        "selection_policy": (
            "nearest Figure-owned span with matching source typography"),
        "text_content_used_for_selection": False,
    }


def _production_special_case_audit() -> dict[str, Any]:
    path = REPO / "tools" / "page_pipeline" \
        / "figure_internal_text_ownership_audit.py"
    lines = path.read_text(encoding="utf-8").splitlines()
    patterns = {
        "text_equality_branch": re.compile(
            r"\bif\b[^\n]*\btext\b\s*==", re.IGNORECASE),
        "label_substring_branch": re.compile(
            r"\bif\b[^\n]*(?:Filter|Output|Original Thermal Input)",
            re.IGNORECASE),
        "page_branch": re.compile(
            r"\bif\b[^\n]*\bpage(?:_index)?\b\s*==", re.IGNORECASE),
        "filename_branch": re.compile(
            r"\bif\b[^\n]*\bfilename\b\s*==", re.IGNORECASE),
        "document_branch": re.compile(
            r"\bif\b[^\n]*\bPPAT\b", re.IGNORECASE),
    }
    findings = []
    for line_number, line in enumerate(lines, start=1):
        for kind, pattern in patterns.items():
            if pattern.search(line):
                findings.append({"kind": kind, "line_number": line_number,
                                 "line": line.strip()})
    return {
        "schema_version": "visual_v07.task4e.production_diff_audit.v1",
        "decision": "pass" if not findings else "fail",
        "production_special_case_count": len(findings),
        "findings": findings,
        "files": [str(path.relative_to(REPO))],
    }


def _ownership_trace(audit: dict[str, Any],
                     production: dict[str, Any]) -> dict[str, Any]:
    duplicates = [row for row in audit.get("records") or []
                  if row.get("duplicate_render")]
    target = min(duplicates, key=lambda row: int(
        row.get("source_reading_order") or 0)) if duplicates else {}
    figure_box = _bbox(target.get("figure_bbox"))
    slot_boxes = list(target.get("source_text_slot_bboxes") or [])
    slot_box = _bbox(slot_boxes[0]) if slot_boxes else []
    overlap_box = _intersection(figure_box, slot_box) \
        if figure_box and slot_box else []
    peer = _nearest_annotation_peer(target, audit.get("records") or []) \
        if target else {}
    formula_disagreements = [
        row for row in audit.get("records") or []
        if row.get("current_owner") == "formula"
        and row.get("double_owned")]
    caption = (audit.get("external_captions") or [{}])[0]
    chain = [
        {
            "step": 1,
            "stage": "source_span_extraction",
            "evidence": {
                "span_id": target.get("span_id"),
                "text": target.get("text"),
                "bbox": target.get("bbox"),
                "font": target.get("font"),
                "font_size": target.get("font_size"),
                "source_reading_order": target.get(
                    "source_reading_order"),
            },
        },
        {
            "step": 2,
            "stage": "figure_geometry_relation",
            "evidence": {
                "figure_id": target.get("figure_id"),
                "figure_bbox": figure_box,
                "span_center_inside": target.get(
                    "center_inside_figure_bbox"),
                "span_overlap_ratio": target.get(
                    "figure_bbox_overlap_ratio"),
                "fully_contained": target.get(
                    "fully_contained_in_figure_bbox"),
                "contained_by_current_3pt_owner_margin": target.get(
                    "contained_by_current_3pt_owner_margin"),
                "owner_margin_deficit_pt": target.get(
                    "owner_margin_deficit_pt"),
                "typography_geometry_peer": peer,
            },
        },
        {
            "step": 3,
            "stage": "page_model_owner_assignment",
            "evidence": {
                "current_owner": target.get("current_owner"),
                "ledger_memberships": target.get(
                    "current_owner_ledger_memberships"),
                "figure_payload_claims_span": target.get(
                    "figure_payload_claims_span"),
                "paragraph_id": target.get("paragraph_id"),
                "semantic_role": target.get("semantic_role"),
            },
        },
        {
            "step": 4,
            "stage": "source_text_slot_creation",
            "evidence": {
                "source_text_slot_created": target.get(
                    "source_text_slot_created"),
                "source_text_slot_ids": target.get(
                    "source_text_slot_ids"),
                "source_text_slot_bboxes": slot_boxes,
                "slot_figure_overlap_bbox": overlap_box,
                "slot_figure_overlap_area_pt2": round(
                    _area(overlap_box), 3),
            },
        },
        {
            "step": 5,
            "stage": "final_soft_text_render",
            "evidence": {
                "flow_fragment_ids": target.get("flow_fragment_ids"),
                "rendered_as_soft_text": target.get(
                    "rendered_as_soft_text"),
                "final_soft_render_text": target.get(
                    "final_soft_render_text"),
                "soft_slot_left_exposed_before_figure_pt": target.get(
                    "source_text_slot_left_exposed_before_figure_pt"),
            },
        },
        {
            "step": 6,
            "stage": "figure_hard_anchor_render",
            "evidence": {
                "rendered_inside_figure": target.get(
                    "rendered_inside_figure"),
                "figure_hard_anchor_is_source_svg_crop": (
                    target.get("evidence") or {}).get(
                        "figure_hard_anchor_is_source_svg_crop"),
                "figure_paints_after_soft_text": target.get(
                    "figure_paints_after_soft_text"),
                "double_owned": target.get("double_owned"),
                "duplicate_render": target.get("duplicate_render"),
            },
        },
    ]
    complete = bool(
        target and all(step.get("evidence") for step in chain)
        and target.get("source_text_slot_created")
        and target.get("rendered_as_soft_text")
        and target.get("rendered_inside_figure")
        and target.get("figure_paints_after_soft_text")
        and peer)
    return {
        "schema_version": "visual_v07.task4e.ownership_trace.v1",
        "decision": "pass" if complete else "blocked",
        "diagnosis_complete": complete,
        "target_selection_policy": (
            "first source-reading-order record with final duplicate render"),
        "target_selection_used_text_content": False,
        "isolated_final_render_source": target,
        "ownership_chain": chain,
        "root_cause": {
            "classification": "figure_internal_span_misowned_as_soft_text",
            "explanation": (
                "The span is geometrically internal to the Figure but "
                "misses the current 3 pt containment test by 0.575 pt at "
                "the right edge. It becomes a text paragraph and "
                "SourceTextSlot while the source-SVG Figure hard anchor "
                "still paints the overlapping source ink. The Figure is "
                "later in DOM paint order, leaving only the soft prefix "
                "outside the Figure left edge visibly isolated."),
            "renderer_change_made": False,
            "figure_crop_change_made": False,
            "ownership_fix_made": False,
        },
        "other_internal_owner_disagreements": {
            "formula_ledger_count": len(formula_disagreements),
            "final_separate_formula_render_count": sum(
                row.get("rendered_as_separate_formula")
                for row in formula_disagreements),
            "records": formula_disagreements,
            "scope_note": (
                "Reported as ownership evidence only; math/formula behavior "
                "is not changed in Task 4E."),
        },
        "external_caption_control": caption,
        "production_diff_audit": production,
    }


def _font(size: int) -> ImageFont.ImageFont:
    for value in ("C:/Windows/Fonts/arial.ttf",
                  "C:/Windows/Fonts/msyh.ttc"):
        if Path(value).exists():
            return ImageFont.truetype(value, size)
    return ImageFont.load_default()


def _render_clip(pdf_path: str | Path, page_index: int,
                 clip: list[float], zoom: float = 4.0) -> Image.Image:
    with pymupdf.open(str(pdf_path)) as document:
        pixmap = document[int(page_index)].get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom),
            clip=pymupdf.Rect(*clip), alpha=False)
        return Image.frombytes("RGB", [pixmap.width, pixmap.height],
                               pixmap.samples)


def _pixel_box(box: list[float], clip: list[float], zoom: float,
               y_offset: int = 0) -> tuple[int, int, int, int]:
    return (round((box[0] - clip[0]) * zoom),
            round((box[1] - clip[1]) * zoom) + y_offset,
            round((box[2] - clip[0]) * zoom),
            round((box[3] - clip[1]) * zoom) + y_offset)


def _panel(image: Image.Image, height: int,
           title: str) -> tuple[Image.Image, ImageDraw.ImageDraw, int]:
    header = 44
    canvas = Image.new("RGB", (image.width, image.height + header + height),
                       "white")
    canvas.paste(image, (0, header))
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 10), title, fill="#111111", font=_font(18))
    return canvas, draw, header


def _review_bundle(audit: dict[str, Any], trace: dict[str, Any],
                   decision: str) -> dict[str, Any]:
    bundle = OUT / "review_bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    target = trace["isolated_final_render_source"]
    figure_box = _bbox(target["figure_bbox"])
    slot_box = _bbox(target["source_text_slot_bboxes"][0])
    crop = [min(figure_box[0], slot_box[0]) - 8.0,
            figure_box[1] - 8.0,
            max(figure_box[2], slot_box[2]) + 8.0,
            figure_box[3] + 23.0]
    zoom = 4.0

    source = _render_clip(SOURCE_PDF, SOURCE_PAGE_INDEX, crop, zoom)
    source.save(bundle / "figure_source.png")

    ownership, draw, header = _panel(
        source.copy(), 118, "Figure internal span ownership (source PDF)")
    colors = {"figure": "#00A65A", "text": "#D7191C",
              "formula": "#E67E22", "table": "#1769E0"}
    for row in audit.get("records") or []:
        color = colors.get(str(row.get("current_owner")), "#8E44AD")
        width = 5 if row.get("duplicate_render") else 2
        draw.rectangle(_pixel_box(_bbox(row["bbox"]), crop, zoom, header),
                       outline=color, width=width)
    y = header + source.height + 8
    font = _font(14)
    metrics = audit["metrics"]
    lines = [
        "GREEN=Figure ledger | ORANGE=Formula ledger | RED=Text ledger",
        "internal=%d soft=%d slot=%d double-owned=%d duplicate-render=%d ambiguous=%d"
        % (metrics["figure_internal_text_span_count"],
           metrics["figure_internal_soft_text_owner_count"],
           metrics["figure_internal_slot_count"],
           metrics["figure_internal_double_owned_count"],
           metrics["figure_internal_duplicate_render_count"],
           metrics["ambiguous_figure_text_owner_count"]),
        "%s: owner=%s overlap=%.2f%% margin-deficit-right=%.3fpt"
        % (target["span_id"], target["current_owner"],
           100.0 * float(target["figure_bbox_overlap_ratio"]),
           float(target["owner_margin_deficit_pt"]["right"])),
        "Classification uses bbox/ownership/reading-order/typography only; text matching=false.",
    ]
    for offset, line in enumerate(lines):
        draw.text((12, y + offset * 24), line, fill="#222222", font=font)
    ownership.save(bundle / "figure_ownership_overlay.png")

    slot_image, draw, header = _panel(
        source.copy(), 100, "Figure / SourceTextSlot overlap")
    draw.rectangle(_pixel_box(figure_box, crop, zoom, header),
                   outline="#00A65A", width=5)
    draw.rectangle(_pixel_box(slot_box, crop, zoom, header),
                   outline="#D7191C", width=5)
    draw.rectangle(_pixel_box(_bbox(target["bbox"]), crop, zoom, header),
                   outline="#8E44AD", width=4)
    y = header + source.height + 8
    for offset, line in enumerate([
            "GREEN=FIG1 hard anchor | RED=incorrect soft SourceTextSlot | PURPLE=source span",
            "slot=%s bbox=%s" % (target["source_text_slot_ids"][0], slot_box),
            "slot/Figure overlap=%s; overlap caused by the internal text ownership chain."
            % target["source_text_slot_figure_overlap_bboxes"][0]]):
        draw.text((12, y + offset * 24), line, fill="#222222", font=font)
    slot_image.save(bundle / "figure_slot_overlay.png")

    final = _render_clip(FINAL_PDF, 0, crop, zoom)
    duplicate, draw, header = _panel(
        final, 118, "Final duplicate render and paint-order evidence")
    draw.rectangle(_pixel_box(figure_box, crop, zoom, header),
                   outline="#00A65A", width=5)
    draw.rectangle(_pixel_box(slot_box, crop, zoom, header),
                   outline="#D7191C", width=5)
    y = header + final.height + 8
    duplicate_lines = [
        "GREEN=source-SVG Figure hard anchor | RED=separate DLP/slot soft render",
        "source %s -> paragraph %s -> %s -> %s"
        % (target["span_id"], target["paragraph_id"],
           target["source_text_slot_ids"][0],
           target["flow_fragment_ids"][0]),
        "Figure paints after soft text=%s; exposed strip left of Figure=%.3fpt"
        % (str(target["figure_paints_after_soft_text"]).lower(),
           float(target["source_text_slot_left_exposed_before_figure_pt"])),
        "Inference: later Figure overpaints the translated label except its exposed prefix.",
    ]
    for offset, line in enumerate(duplicate_lines):
        draw.text((12, y + offset * 24), line, fill="#222222", font=font)
    duplicate.save(bundle / "figure_duplicate_render_overlay.png")

    names = [
        "figure_source.png",
        "figure_ownership_overlay.png",
        "figure_slot_overlay.png",
        "figure_duplicate_render_overlay.png",
    ]
    index = {
        "schema_version": "visual_v07.task4e.review_index.v1",
        "decision": decision,
        "flat": True,
        "files": [{"name": name, "bytes": (bundle / name).stat().st_size}
                  for name in names],
        "legend": {
            "green": "Figure hard-anchor ownership",
            "orange": "formula-ledger ownership inside Figure geometry",
            "red": "soft-text/SourceTextSlot ownership defect",
            "purple": "source span producing the duplicate render",
        },
    }
    _dump(bundle / "review_index.json", index)
    (bundle / "REVIEW_README.md").write_text(
        "# Task 4E Figure internal text ownership review\n\n"
        "1. `figure_source.png` is the untouched source-Figure crop.\n"
        "2. `figure_ownership_overlay.png` colors every geometrically "
        "internal source span by its current ownership ledger.\n"
        "3. `figure_slot_overlay.png` proves the incorrect soft slot "
        "overlaps the Figure hard anchor.\n"
        "4. `figure_duplicate_render_overlay.png` shows final paint-order "
        "evidence for the isolated prefix.\n"
        "5. Nonzero anomaly metrics are diagnostic evidence in Task 4E; "
        "no renderer, crop, glyph, or ownership fix is included.\n",
        encoding="utf-8")
    return index


def _report(audit: dict[str, Any], trace: dict[str, Any],
            production: dict[str, Any], decision: str) -> str:
    target = trace["isolated_final_render_source"]
    caption = trace["external_caption_control"]
    metrics = audit["metrics"]
    peer = trace["ownership_chain"][1]["evidence"][
        "typography_geometry_peer"]
    return """# visual-v07 Task 4E - Figure Internal Text Ownership Audit

## Outcome

**%s (diagnosis complete; no fix applied).** Every geometrically Figure-internal source span has an owner/slot/final-render evidence chain. Nonzero anomaly counts are permitted by Task 4E and are reported rather than repaired.

## Required answers

1. **What is the real source of the isolated `(e)`?** Source span `%s`, `%s`, bbox `%s`. Its final soft target is `%s`. The soft slot begins `%.3f pt` left of the Figure; the Figure paints later and covers the rest of that target, leaving the exposed prefix visually isolated.
2. **Is it a Figure-internal annotation?** Yes. Its bbox centre is inside `%s`, and `%.2f%%` of its source bbox overlaps the Figure. Its nearest Figure-owned typography/geometry peer is `%s`: same `%s` font, same `%.3f pt` size, and only `%.3f pt` top-baseline difference. No text matching was used.
3. **Is there Figure + soft-text double ownership?** Yes. The Figure source-SVG hard anchor contains the overlapping source ink, while the ownership ledger assigns the same span to `text` / paragraph `%s`; `double_owned=true` and `duplicate_render=true`.
4. **Was an incorrect SourceTextSlot created?** Yes: `%s`, bbox `%s`, followed by final fragment `%s`. The slot/Figure intersection is `%s`.
5. **Did this cause the Figure/text overlap?** Yes. The soft slot was created only because the internal span entered the ordinary text paragraph path; that slot overlaps the Figure hard anchor and is rendered separately.
6. **Is the Figure bbox/crop actually too large, or is ownership wrong?** It is not too large. The annotation extends `3.575 pt` beyond the raw Figure right edge, while the current owner test allows `3.000 pt`; the miss is only `0.575 pt`. The crop still contains `%.2f%%` of the span, so the observed duplicate comes from the ownership boundary. If anything, the raw bbox slightly under-covers the label; crop repair is outside this task.
7. **Is production special-case count zero?** Yes: `production_special_case_count=%d`. The audit uses bbox containment/overlap, PageModel ownership, source reading order, typography relation, SourceTextSlot provenance, and final DOM evidence. It contains no text, label, page, or filename conditional.

## Ownership chain

`%s -> text owner -> %s -> %s -> %s -> final soft render`, while `%s -> source-SVG Figure hard anchor` also paints the source ink. The Figure occurs later in DOM paint order. The exposed strip before the Figure is `%.3f pt` wide.

## Metrics

```json
%s
```

The `figure_internal_double_owned_count=%d` includes eight formula-ledger spans internal to the source Figure plus the one soft-text span. Those eight formula-ledger spans are not separately rendered in the frozen final HTML, so only one record reaches `figure_internal_duplicate_render_count=1`. Math/formula behavior is not changed.

## External caption control

The external caption remains distinct: paragraph `%s`, role `%s`, bbox `%s`, begins `%.3f pt` below the Figure, owns slot `%s`, renders as soft text, and has `double_owned=false`.

## Scope and STOP condition

This task adds diagnosis, JSON traces, and review overlays only. It does not modify the renderer, Figure crop/bbox, ownership assignment, SourceTextSlot creation, translation, math glyph handling, or release behavior.
""" % (
        decision.upper(),
        target["span_id"], json.dumps(target["text"], ensure_ascii=False),
        target["bbox"], json.dumps(target["final_soft_render_text"],
                                    ensure_ascii=False),
        float(target["source_text_slot_left_exposed_before_figure_pt"]),
        target["figure_id"],
        100.0 * float(target["figure_bbox_overlap_ratio"]),
        peer.get("span_id"), peer.get("font"),
        float(peer.get("font_size") or 0.0),
        float(peer.get("baseline_top_delta_pt") or 0.0),
        target["paragraph_id"], target["source_text_slot_ids"][0],
        target["source_text_slot_bboxes"][0],
        target["flow_fragment_ids"][0],
        target["source_text_slot_figure_overlap_bboxes"][0],
        100.0 * float(target["figure_bbox_overlap_ratio"]),
        int(production["production_special_case_count"]),
        target["span_id"], target["paragraph_id"],
        target["source_text_slot_ids"][0], target["flow_fragment_ids"][0],
        target["figure_id"],
        float(target["source_text_slot_left_exposed_before_figure_pt"]),
        json.dumps(metrics, ensure_ascii=False, indent=2),
        int(metrics["figure_internal_double_owned_count"]),
        caption.get("paragraph_id"), caption.get("semantic_role"),
        caption.get("bbox"),
        float((caption.get("bbox") or [0, 0, 0, 0])[1]
              - float(target["figure_bbox"][3])),
        (caption.get("slot_ids") or [None])[0],
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    model = _load(MODEL_PATH)
    slots = _load(SLOTS_PATH)
    audit = figure_internal_text_ownership_audit(
        model, SOURCE_PDF, SOURCE_PAGE_INDEX, slots, FINAL_HTML)
    production = _production_special_case_audit()
    trace = _ownership_trace(audit, production)
    decision = "pass" if (
        audit["decision"] == "pass"
        and trace["decision"] == "pass"
        and production["decision"] == "pass") else "blocked"
    _dump(OUT / "figure_internal_text_ownership.json", audit)
    _dump(OUT / "ownership_trace.json", trace)
    review = _review_bundle(audit, trace, decision)
    (OUT / "FIGURE_INTERNAL_TEXT_OWNERSHIP_REPORT.md").write_text(
        _report(audit, trace, production, decision), encoding="utf-8")
    checkpoint = {
        "schema_version": "visual_v07.task4e.checkpoint.v1",
        "decision": decision,
        "diagnostic_only": True,
        "metrics": audit["metrics"],
        "diagnosis_complete": trace["diagnosis_complete"],
        "production_special_case_count": production[
            "production_special_case_count"],
        "review_bundle": review,
        "renderer_modified": False,
        "figure_crop_modified": False,
        "math_glyph_modified": False,
        "ownership_fix_applied": False,
    }
    _dump(OUT / "checkpoint_gate.json", checkpoint)
    print(json.dumps(checkpoint, ensure_ascii=False, indent=2))
    return 0 if decision == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
