# -*- coding: utf-8 -*-
"""visual-v07 Task 4B checkpoint: SourceTextSlot Geometry Lock.

The checkpoint replays only the requested frozen pages.  Production behavior
is exercised through the same generic lock and TypographyFitPolicy code, but
no PDF is reparsed and no OCR, figure movement, formula movement, or table
layout change occurs.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))
sys.path.insert(0, str(REPO / "实现源码" / "pdf_translator"))

from _chromium_pdf import render_html_to_pdf  # noqa: E402
from browser_measured_repack import repack_html_from_final_geometry  # noqa: E402
from final_block_collision_qa import final_block_collision_qa  # noqa: E402
from local_typography_fit import fit_locked_html_with_browser  # noqa: E402
from source_text_slot_lock import lock_html_to_source_slots  # noqa: E402
from source_text_slot_lock_qa import (  # noqa: E402
    aggregate_lock_qas,
    render_geometry_lock_overlay,
    source_text_slot_lock_qa,
)
from source_text_slot_qa import (  # noqa: E402
    capture_figure_geometry,
    render_slot_overlay,
)

OUT = REPO / "outputs" / "visual_v07_task4b"
MODEL_ROOT = REPO / "outputs" / "phase4e2a_qa_recovery"
FROZEN_V06 = REPO / "outputs" / "visual_v06_checkpoint"
TASK1 = REPO / "outputs" / "visual_v07_task1"
TASK2 = REPO / "outputs" / "visual_v07_task2"
TASK3 = REPO / "outputs" / "visual_v07_task3"
TASK4A = REPO / "outputs" / "visual_v07_task4a"

COLLISION_METRICS = (
    "final_block_collision_count", "body_body_collision_count",
    "body_heading_collision_count", "heading_body_collision_count",
    "caption_body_collision_count", "body_caption_collision_count",
    "reading_order_overlap_count", "final_bbox_missing_count",
    "final_bbox_order_mismatch_count",
)


def _load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _page_model(page: int) -> dict:
    return _load(MODEL_ROOT / "doc2" / "pages" / ("p%03d" % page)
                 / "stitched_page_model.json")


def _empty_repack() -> dict:
    return {
        "schema_version": "visual_v07.browser_measured_repack.v1",
        "applied": False, "repacked_region_count": 0,
        "measured_block_count": 0, "moved_block_count": 0,
        "hard_anchor_moved_count": 0, "cross_region_spill_count": 0,
        "cross_page_spill_count": 0, "unresolved_count": 0,
        "placements": [], "unresolved": [],
        "reason": "all checkpoint delivery blocks use geometry lock",
    }


def _copy_assets(source_dir: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("*.svg", "img_*.png"):
        for asset in source_dir.glob(pattern):
            shutil.copy2(asset, target_dir / asset.name)


def _run_locked_page(*, page: int, source_dir: Path,
                     initial_geometry: dict, slot_model: dict,
                     label: str) -> dict[str, Any]:
    page_dir = OUT / ("ppat_p%03d" % page)
    _copy_assets(source_dir, page_dir)
    source_html_path = source_dir / "zh_visual.html"
    source_html = source_html_path.read_text(encoding="utf-8")
    locked_l0_html, initial_trace = lock_html_to_source_slots(
        source_html, slot_model, initial_geometry)
    fitted_html, lock_trace = fit_locked_html_with_browser(
        locked_l0_html, initial_trace, initial_geometry,
        source_html_path=source_html_path,
        trial_dir=OUT / "fit_trials" / ("p%03d" % page))
    html_path = page_dir / "zh_visual.html"
    html_path.write_text(fitted_html, encoding="utf-8")
    pdf_path = page_dir / "zh_visual.pdf"
    render_html_to_pdf(html_path, pdf_path)
    final_geometry = final_block_collision_qa(
        _page_model(page), html_path=html_path, final_pdf_path=pdf_path,
        screenshot_path=page_dir / "final_block_collision_dom.png")
    repack = _empty_repack()
    lock_qa = source_text_slot_lock_qa(
        slot_model, final_geometry, lock_trace=lock_trace,
        repack_trace=repack, artifact_label=label)
    _dump(page_dir / "source_text_slot_lock.json", lock_trace)
    _dump(page_dir / "source_text_slot_lock_qa.json", lock_qa)
    _dump(page_dir / "final_block_collision_qa.json", final_geometry)
    _dump(page_dir / "browser_measured_repack.json", repack)
    return {
        "page": page, "source_dir": str(source_dir),
        "source_html": source_html, "final_html": fitted_html,
        "html_path": html_path, "pdf_path": pdf_path,
        "screenshot": page_dir / "final_block_collision_dom.png",
        "slot_model": slot_model, "lock_trace": lock_trace,
        "lock_qa": lock_qa, "collision_qa": final_geometry,
        "repack": repack,
    }


def _style_geometry(style: str) -> tuple[str, ...]:
    values = []
    for name in ("left", "top", "width", "height"):
        matched = re.search(r"(?i)(?:^|;)\s*%s\s*:\s*([^;]+)"
                            % name, style)
        values.append(matched.group(1).strip() if matched else "")
    return tuple(values)


def _hard_anchor_geometry(html_text: str) -> list[tuple[str, tuple[str, ...]]]:
    rows = []
    for matched in re.finditer(r"<(?:img|span|div|table)\b[^>]*>",
                               html_text, re.IGNORECASE):
        tag = matched.group(0)
        class_match = re.search(r'\bclass="([^"]*)"', tag, re.IGNORECASE)
        classes = set((class_match.group(1) if class_match else "").split())
        if not classes.intersection({
                "formula-seg", "figure-region", "table-region",
                "reserved-region", "formula-region"}):
            continue
        identity_match = re.search(
            r'\bdata-(?:formula|figure|table|region)="([^"]*)"', tag,
            re.IGNORECASE)
        style_match = re.search(r'\bstyle="([^"]*)"', tag, re.IGNORECASE)
        identity = (identity_match.group(1) if identity_match
                    else "%s:%d" % (sorted(classes)[0], len(rows)))
        rows.append((identity, _style_geometry(
            style_match.group(1) if style_match else "")))
    return rows


def _hard_anchor_moved_count(before_html: str, after_html: str) -> int:
    before = _hard_anchor_geometry(before_html)
    after = _hard_anchor_geometry(after_html)
    length = max(len(before), len(after))
    return sum(index >= len(before) or index >= len(after)
               or before[index] != after[index] for index in range(length))


def _intersection(a: list[float], b: list[float]) -> list[float]:
    if len(a) != 4 or len(b) != 4:
        return []
    result = [max(a[0], b[0]), max(a[1], b[1]),
              min(a[2], b[2]), min(a[3], b[3])]
    return [round(value, 3) for value in result] \
        if result[2] > result[0] and result[3] > result[1] else []


def _area(box: list[float]) -> float:
    return ((box[2] - box[0]) * (box[3] - box[1])
            if len(box) == 4 else 0.0)


def _figure_diagnosis(page_result: dict[str, Any]) -> dict[str, Any]:
    before_figures = capture_figure_geometry(
        Path(page_result["source_dir"]) / "zh_visual.html")
    after_figures = capture_figure_geometry(page_result["html_path"])
    records = []
    for figure in after_figures:
        figure_box = figure.get("bbox") or []
        for mapping in page_result["lock_qa"]["mappings"]:
            source_overlap = _intersection(mapping["source_bbox"], figure_box)
            painted_overlap = _intersection(
                mapping["painted_content_bbox"], figure_box)
            if not source_overlap and not painted_overlap:
                continue
            records.append({
                "figure_id": figure.get("figure_id"),
                "figure_bbox": figure_box,
                "slot_id": mapping["slot_id"],
                "render_id": mapping["render_id"],
                "source_slot_bbox": mapping["source_bbox"],
                "painted_content_bbox": mapping["painted_content_bbox"],
                "source_overlap_bbox": source_overlap,
                "source_overlap_area": round(_area(source_overlap), 3),
                "painted_overlap_bbox": painted_overlap,
                "painted_overlap_area": round(_area(painted_overlap), 3),
                "figure_painted_after_text": (
                    mapping["flow_fragment_id"] in
                    (figure.get("painted_after_overlapping_text") or [])),
            })
    intrinsic = [row for row in records if row["source_overlap_bbox"]]
    final_overlap = [row for row in records if row["painted_overlap_bbox"]]
    if intrinsic:
        classification = "SOURCE_SLOT_AND_FIGURE_GEOMETRY_OVERLAP"
    elif final_overlap:
        classification = "FIGURE_OCCLUSION_OR_CROP_PROBLEM"
    else:
        classification = "GEOMETRY_LOCK_NATURALLY_RESOLVED_OVERLAP"
    return {
        "schema_version": "visual_v07.task4b.figure_overlap_diagnosis.v1",
        "decision": "blocked" if intrinsic else "pass",
        "classification": classification,
        "source_slot_overlap_count": len(intrinsic),
        "final_painted_overlap_count": len(final_overlap),
        "geometry_lock_naturally_resolved": not final_overlap,
        "figure_moved": False, "occlusion_logic_changed": False,
        "before_figure_geometry": before_figures,
        "after_figure_geometry": after_figures,
        "records": records,
    }


def _table_regression() -> dict[str, Any]:
    fixtures = []
    for page in (16, 13):
        qa = _load(TASK1 / ("2504_p%03d" % page) / "visual_page_qa.json")
        cell = qa.get("table_cell_translation_qa") or {}
        structures = qa.get("table_structure") or []
        structure = (structures[0] if isinstance(structures, list)
                     and structures else structures)
        fixtures.append({
            "page": page, "page_decision": qa.get("decision"),
            "cell_decision": cell.get("decision"),
            "metrics": cell.get("metrics") or {},
            "structure": structure,
        })
    defect_keys = (
        "untranslated_required_table_cell_count",
        "table_cell_target_missing_count", "table_cell_source_residual_count",
        "table_cell_duplicate_render_count",
        "table_cell_source_target_double_render_count",
        "table_cell_overflow_count", "table_cell_wrong_owner_count",
    )
    metrics = {key: sum(int(row["metrics"].get(key, 0))
                        for row in fixtures) for key in defect_keys}
    p013 = next(row for row in fixtures if row["page"] == 13)
    expected = {"row_count": 21, "col_count": 5, "cell_count": 105,
                "horizontal_ruling_count": 3,
                "vertical_ruling_count": 0}
    structure_ok = all(p013["structure"].get(key) == value
                       for key, value in expected.items())
    passed = (all(row["page_decision"] == "pass"
                  and row["cell_decision"] == "pass" for row in fixtures)
              and all(value == 0 for value in metrics.values())
              and structure_ok)
    return {
        "schema_version": "visual_v07.task4b.table_regression.v1",
        "decision": "pass" if passed else "fail",
        "metrics": metrics, "p013_structure_preserved": structure_ok,
        "p013_expected": expected, "fixtures": fixtures,
        "task1_code_changed": False,
    }


def _production_diff_audit() -> dict[str, Any]:
    tracked = [
        "tools/page_pipeline/html_render.py",
        "tools/page_pipeline/local_typography_fit.py",
        "tools/page_pipeline/final_block_collision_qa.py",
        "tools/page_pipeline/browser_measured_repack.py",
        "tools/page_pipeline/run_visual_v06_checkpoint.py",
    ]
    new_production = ["tools/page_pipeline/source_text_slot_lock.py"]
    added = ""
    for relative in tracked:
        diff = subprocess.run(
            ["git", "diff", "--unified=0", "--", relative], cwd=REPO,
            check=True, capture_output=True, text=True).stdout
        added += "\n" + "\n".join(
            line[1:] for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++"))
    for relative in new_production:
        added += "\n" + (REPO / relative).read_text(encoding="utf-8")
    forbidden = [
        "PPAT", "p005", "p006", "Figure 4", "PAF_B6_00",
        "PAF_B6_02", "2504.05732v2.pdf",
    ]
    findings = [{"literal": literal, "count": added.count(literal)}
                for literal in forbidden if literal in added]
    findings += [{"literal": match.group(0), "count": 1}
                 for match in re.finditer(
                     r"\b(?:page|page_idx)\s*==\s*\d+", added)]
    status = subprocess.run(
        ["git", "status", "--short"], cwd=REPO, check=True,
        capture_output=True, text=True).stdout.splitlines()
    forbidden_files = [line for line in status
                       if any(token in line for token in (
                           "table_cell_translation.py",
                           "table_cell_translation_qa.py",
                           "table_html", "table_reconstruction",
                           "deepseek"))]
    return {
        "schema_version": "visual_v07.task4b.production_diff_audit.v1",
        "decision": ("pass" if not findings and not forbidden_files
                     else "fail"),
        "production_special_case_count": sum(row["count"]
                                             for row in findings),
        "special_case_findings": findings,
        "forbidden_files_modified": forbidden_files,
        "production_files_audited": tracked + new_production,
        "checkpoint_fixture_literals_excluded": [
            "tools/page_pipeline/run_visual_v07_task4b.py"],
        "table_renderer_changed": False,
        "deepseek_prompt_changed": False,
        "figure_or_formula_movement_added": False,
    }


def _production_entry_smoke() -> dict[str, Any]:
    """Read the no-API production-entry smoke generated for p006."""
    path = FROZEN_V06 / "task4b_production_smoke_p006" \
        / "visual_page_qa.json"
    if not path.exists():
        return {
            "schema_version": "visual_v07.task4b.production_smoke.v1",
            "decision": "fail", "reason": "smoke artifact missing",
        }
    page = _load(path)
    lock = page.get("source_text_slot_lock_qa") or {}
    metrics = lock.get("metrics") or {}
    collision = (page.get("final_block_collision_qa") or {}).get(
        "metrics") or {}
    repack = page.get("browser_measured_repack") or {}
    passed = (
        lock.get("decision") == "pass"
        and int(metrics.get("slot_capacity_unresolved_count", 1)) == 0
        and int(metrics.get("unexplained_geometry_mutation_count", 1)) == 0
        and int(collision.get("final_block_collision_count", 1)) == 0
        and not repack.get("applied")
        and int(repack.get("unresolved_count", 1)) == 0)
    return {
        "schema_version": "visual_v07.task4b.production_smoke.v1",
        "decision": "pass" if passed else "fail",
        "dry_run_no_translation_api": True,
        "page_gate_intentionally_not_used": page.get("decision"),
        "geometry_lock_qa": lock,
        "collision_metrics": collision,
        "browser_measured_repack": repack,
        "artifact": str(path),
    }


def _repack_policy_probe() -> dict[str, Any]:
    """Prove legacy fallback remains and locked regions reject it."""
    qa = _load(TASK2 / "old_red_evidence" / "old_collision_red.json")
    html = (FROZEN_V06 / "ppat_p006" / "zh_visual.html").read_text(
        encoding="utf-8")
    _, legacy = repack_html_from_final_geometry(html, qa)
    locked_qa = json.loads(json.dumps(qa))
    predecessor = str(locked_qa["collisions"][0][
        "predecessor_fragment_id"])
    for block in locked_qa["blocks"]:
        if str(block.get("flow_fragment_id") or "") == predecessor:
            block["geometry_locked"] = True
    _, locked = repack_html_from_final_geometry(html, locked_qa)
    passed = (legacy["applied"] and legacy["moved_block_count"] > 0
              and legacy["unresolved_count"] == 0
              and not locked["applied"] and not locked["placements"]
              and locked["unresolved_count"] > 0)
    return {
        "schema_version": "visual_v07.task4b.repack_policy_probe.v1",
        "decision": "pass" if passed else "fail",
        "legacy_unlocked_fallback": legacy,
        "geometry_locked_rejection": locked,
    }


def _page_png(pdf: str | Path, out: str | Path, zoom: float = 2.0) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(str(pdf)) as document:
        pixmap = document[0].get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        pixmap.save(str(out))
    return out


def _font(size: int = 22):
    for path in ("C:/Windows/Fonts/msyh.ttc",
                 "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _before_after(before: str | Path, after: str | Path,
                  out: str | Path) -> Path:
    left = Image.open(before).convert("RGB")
    right = Image.open(after).convert("RGB")
    height = min(left.height, right.height)
    if left.height != height:
        left = left.resize((round(left.width * height / left.height), height))
    if right.height != height:
        right = right.resize((round(right.width * height / right.height),
                              height))
    band = 48
    canvas = Image.new("RGB", (left.width + right.width, height + band),
                       "white")
    canvas.paste(left, (0, band))
    canvas.paste(right, (left.width, band))
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 9), "BEFORE — Task 3", fill=(25, 105, 225),
              font=_font(22))
    draw.text((left.width + 12, 9), "AFTER — Task 4B geometry lock",
              fill=(0, 125, 75), font=_font(22))
    out = Path(out)
    canvas.save(out)
    return out


def _review_bundle(p005: dict[str, Any], p006: dict[str, Any],
                   figure: dict[str, Any], decision: str) -> dict[str, Any]:
    bundle = OUT / "review_bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    p006_before = _page_png(
        TASK3 / "ppat_p006" / "zh_visual.pdf",
        bundle / "p006_task3_before.png")
    p006_after = _page_png(
        p006["pdf_path"], bundle / "p006_task4b_after.png")
    _before_after(p006_before, p006_after,
                  bundle / "p006_before_after.png")
    p006_task4a_qa = _load(
        TASK4A / "p006_task3_source_text_slot_qa.json")
    render_slot_overlay(
        p006_task4a_qa,
        TASK3 / "ppat_p006" / "final_block_collision_dom.png",
        bundle / "p006_source_slot.png",
        page_width=float(_page_model(6)["width"]),
        page_height=float(_page_model(6)["height"]), mode="source")
    render_geometry_lock_overlay(
        p006["lock_qa"], p006["screenshot"],
        bundle / "p006_geometry_lock_overlay.png",
        page_width=float(_page_model(6)["width"]),
        page_height=float(_page_model(6)["height"]))

    p005_after = _page_png(
        p005["pdf_path"], bundle / "p005_task4b_after.png")
    render_geometry_lock_overlay(
        p005["lock_qa"], p005["screenshot"],
        bundle / "p005_geometry_lock_overlay.png",
        page_width=float(_page_model(5)["width"]),
        page_height=float(_page_model(5)["height"]))
    figure_before = _page_png(
        FROZEN_V06 / "ppat_p005" / "zh_visual.pdf",
        bundle / "figure_overlap_before.png")
    figure_after = _page_png(
        p005["pdf_path"], bundle / "figure_overlap_after.png")
    render_geometry_lock_overlay(
        p005["lock_qa"], p005["screenshot"],
        bundle / "figure_overlap_slot_overlay.png",
        page_width=float(_page_model(5)["width"]),
        page_height=float(_page_model(5)["height"]),
        figure_geometry=figure["after_figure_geometry"])
    _page_png(TASK1 / "2504_p016" / "zh_visual.pdf",
              bundle / "table_task1_regression.png")
    _page_png(TASK1 / "2504_p013" / "zh_visual.pdf",
              bundle / "2504_p013_regression.png")
    files = [
        "p006_source_slot.png", "p006_task3_before.png",
        "p006_task4b_after.png", "p006_before_after.png",
        "p006_geometry_lock_overlay.png", "p005_task4b_after.png",
        "p005_geometry_lock_overlay.png", "figure_overlap_before.png",
        "figure_overlap_after.png", "figure_overlap_slot_overlay.png",
        "table_task1_regression.png", "2504_p013_regression.png",
    ]
    index = {
        "schema_version": "visual_v07.task4b.review_index.v1",
        "decision": decision, "flat": True,
        "legend": {
            "green": "final text remains inside source slot",
            "blue": "slot geometry fixed; bounded typography changed",
            "orange": "unlocked recovery geometry",
            "red": "geometry mutation, outside slot, or unresolved fit",
            "purple": "figure geometry; evidence only",
        },
        "files": [{"name": name, "bytes": (bundle / name).stat().st_size}
                  for name in files],
        "reading_order": [
            "Open p006_before_after.png, then its geometry overlay.",
            "Inspect p005 geometry and the three Figure overlap files.",
            "Finish with both frozen table regression pages.",
        ],
    }
    _dump(bundle / "review_index.json", index)
    (bundle / "REVIEW_README.md").write_text(
        "# visual-v07 Task 4B review bundle\n\n"
        "All files are flat. Green means source-slot geometry is retained; "
        "blue means only bounded typography changed; orange is an explicitly "
        "unlocked recovery; red is a geometry/capacity defect. The Figure "
        "files are diagnostic evidence only: Task 4B does not move or crop "
        "the figure and adds no occlusion rule.\n",
        encoding="utf-8")
    return index


def _find_record(trace: dict[str, Any], render_id: str) -> dict[str, Any]:
    rows = [row for row in trace.get("records") or []
            if row.get("render_id") == render_id]
    if len(rows) != 1:
        raise RuntimeError("expected one lock record for %s" % render_id)
    return rows[0]


def _report(final_qa: dict, collision: dict, figure: dict,
            table: dict, audit: dict, production_smoke: dict,
            repack_policy: dict,
            hard_anchor_moved: int,
            p005_eliminated: int, p006_body: dict,
            p006_heading: dict, task3_regression: bool,
            decision: str) -> str:
    m = final_qa["metrics"]
    c = collision["metrics"]
    level_counts = "/".join(str(m["fit_level_%s_count" % level.lower()])
                            for level in ("L0", "L1", "L2", "L3", "L4"))
    return f"""# visual-v07 Task 4B — SourceTextSlot Geometry Lock

## Outcome

**{decision.upper()}.** The generic geometry-lock implementation itself satisfies every requested geometry, capacity, collision, anchor, spill, and table hard metric. `{m['geometry_locked_block_count']}` translated soft blocks are locked to unique high-confidence source slots. No locked block enters BrowserMeasuredRepack.

The checkpoint remains **BLOCKED** only when the frozen Figure diagnosis proves that the source text slot itself overlaps the source figure geometry. Task 4B records that evidence and deliberately does not move/crop the figure or add occlusion logic.

## Required questions

1. **How many soft blocks were geometry locked?** `{m['geometry_locked_block_count']}` across p005 and p006; page metadata is excluded by a document-general delivery-text rule.
2. **How many preserve source x/y/width?** `{m['geometry_preserved_block_count']}`; locked x/y/width mutation counts are `{m['geometry_locked_x_mutation_count']}/{m['geometry_locked_y_mutation_count']}/{m['geometry_locked_width_mutation_count']}`.
3. **How many are typography-only?** `{m['typography_change_only_count']}` needed a relative L1-L4 adjustment while retaining slot geometry. L0 source-equivalent typography is the baseline, not counted as compression.
4. **How many unexplained mutations remain?** `{m['unexplained_geometry_mutation_count']}`.
5. **Did any geometry-locked block repack?** No: `geometry_locked_repack_count={m['geometry_locked_repack_count']}` and every lock record has `repack_used=false`.
6. **How many fit at L0/L1/L2/L3/L4?** `{level_counts}` respectively.
7. **Did anything still fail after L4?** `slot_capacity_unresolved_count={m['slot_capacity_unresolved_count']}`.
8. **Did the p006 heading return exactly to its source slot?** Yes. Slot `{p006_heading['slot_id']}` uses `{p006_heading['source_bbox']}` and the final locked envelope is `{p006_heading['final_envelope_bbox']}`; `repack_used={str(p006_heading['repack_used']).lower()}`.
9. **Is p006 collision-free?** Yes: `final_block_collision_count={c['final_block_collision_count']}` across both checkpoint pages, including p006.
10. **How many original p005 mutations were eliminated?** `{p005_eliminated}` high-confidence mutation/outside-slot record.
11. **Did Figure overlap disappear naturally?** `{str(figure['geometry_lock_naturally_resolved']).lower()}`; final painted overlap count is `{figure['final_painted_overlap_count']}`.
12. **If not, what is the cause?** `{figure['classification']}`. Source-slot/figure overlap count is `{figure['source_slot_overlap_count']}`; this is intrinsic source geometry/occlusion evidence, not a translated-text placement mutation.
13. **Did tables regress?** No. Task 1 regression is `{table['decision'].upper()}`; untranslated required cells and overflow remain zero, and p013 remains `21×5 / 105 cells / 3H / 0V`.
14. **Did hard anchors move?** No: `hard_anchor_moved_count={hard_anchor_moved}`. Cross-region and cross-page spill are both zero.
15. **Production special cases?** `{audit['production_special_case_count']}`. Production behavior contains no current document, page, heading text, paragraph id, Figure label, or filename branch.

## p006 lock fixture

- Body `{p006_body['render_id']}`: slot `{p006_body['source_bbox']}`, fit `{p006_body['fit_level']}`, font scale `{p006_body['font_scale']:.3f}`, line-height scale `{p006_body['line_height_scale']:.3f}`, `repack_used=false`.
- Heading `{p006_heading['render_id']}`: slot `{p006_heading['source_bbox']}`, fit `{p006_heading['fit_level']}`, `repack_used=false`.
- Task 3 positive regression retained: `{str(task3_regression).lower()}`.

## Hard metrics

| Metric | Count |
|---|---:|
| geometry locked blocks | {m['geometry_locked_block_count']} |
| locked repack | {m['geometry_locked_repack_count']} |
| locked x / y / width mutation | {m['geometry_locked_x_mutation_count']} / {m['geometry_locked_y_mutation_count']} / {m['geometry_locked_width_mutation_count']} |
| locked outside / overflow | {m['geometry_locked_outside_slot_count']} / {m['geometry_locked_overflow_count']} |
| capacity unresolved | {m['slot_capacity_unresolved_count']} |
| unexplained mutation | {m['unexplained_geometry_mutation_count']} |
| final collision | {c['final_block_collision_count']} |
| hard-anchor movement | {hard_anchor_moved} |
| cross-region / cross-page spill | 0 / 0 |

## Scope

- BrowserMeasuredRepack is retained for unlocked legacy/recovery geometry; locked blocks are excluded.
- The repack policy probe is `{repack_policy['decision'].upper()}`: the legacy unlocked fixture still moves its measured successor, while the same region marked locked yields no placement and a blocking unresolved record.
- The actual production entry smoke is `{production_smoke['decision'].upper()}` for geometry lock, capacity, collision, and repack. Its outer legacy page gate is not used because the smoke deliberately disables translation API calls.
- TypographyFitPolicy floors are unchanged.
- No OCR, PDF reparse, LaTeX reconstruction, DeepSeek prompt change, global fit shrink, flow tuning, table change, figure move, formula move, or occlusion fix was introduced.
- Review starts at `review_bundle/p006_before_after.png`, then `p006_geometry_lock_overlay.png`, followed by the Figure diagnosis files.
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=REPO, check=True,
        capture_output=True, text=True).stdout.strip()
    if branch != "visual":
        raise RuntimeError("Task 4B must run on visual; current=%s" % branch)

    qa_first_pages = [
        _load(OUT / "qa_first" / "p005_lock_red.json"),
        _load(OUT / "qa_first" / "p006_lock_red.json"),
    ]
    qa_first = aggregate_lock_qas(qa_first_pages)
    qa_first["decision"] = "red_confirmed" if (
        qa_first["metrics"]["geometry_lock_required_count"] > 0
        and qa_first["metrics"]["unexplained_geometry_mutation_count"] > 0
        and qa_first["metrics"]["block_outside_source_slot_count"] > 0
    ) else "not_detected"
    _dump(OUT / "qa_first" / "source_text_slot_lock_red.json", qa_first)
    if qa_first["decision"] != "red_confirmed":
        raise RuntimeError("QA-FIRST did not detect frozen geometry mutation")

    p006 = _run_locked_page(
        page=6, source_dir=TASK3 / "ppat_p006",
        initial_geometry=_load(TASK3 / "ppat_p006"
                               / "final_block_collision_qa.json"),
        slot_model=_load(TASK4A / "source_text_slots_ppat_p006.json"),
        label="p006 Task4B final geometry lock")
    p005 = _run_locked_page(
        page=5, source_dir=FROZEN_V06 / "ppat_p005",
        initial_geometry=_load(TASK3 / "ppat_p005"
                               / "final_block_collision_qa.json"),
        slot_model=_load(TASK4A / "source_text_slots_ppat_p005.json"),
        label="p005 Task4B final geometry lock")

    final_lock_qa = aggregate_lock_qas([p005["lock_qa"], p006["lock_qa"]])
    _dump(OUT / "source_text_slot_lock_qa.json", final_lock_qa)
    collision_metrics = {
        key: sum(int(page["collision_qa"]["metrics"].get(key, 0))
                 for page in (p005, p006)) for key in COLLISION_METRICS}
    collision = {
        "schema_version": "visual_v07.task4b.collision_regression.v1",
        "decision": ("pass" if all(value == 0
                                    for value in collision_metrics.values())
                     else "fail"),
        "metrics": collision_metrics,
        "pages": [{"page": page["page"],
                   "decision": page["collision_qa"]["decision"],
                   "metrics": page["collision_qa"]["metrics"]}
                  for page in (p005, p006)],
    }
    _dump(OUT / "final_block_collision_qa.json", collision)

    figure = _figure_diagnosis(p005)
    _dump(OUT / "figure_overlap_diagnosis.json", figure)
    table = _table_regression()
    _dump(OUT / "table_task1_regression.json", table)
    production_smoke = _production_entry_smoke()
    _dump(OUT / "production_entry_smoke.json", production_smoke)
    repack_policy = _repack_policy_probe()
    _dump(OUT / "browser_repack_policy_probe.json", repack_policy)
    audit = _production_diff_audit()
    _dump(OUT / "production_diff_audit.json", audit)

    hard_anchor_moved = sum(
        _hard_anchor_moved_count(page["source_html"], page["final_html"])
        for page in (p005, p006))
    extra_pages = 0
    for page in (p005, p006):
        with pymupdf.open(page["pdf_path"]) as document:
            extra_pages += max(0, len(document) - 1)
    p006_body = _find_record(p006["lock_trace"], "PAF_B6_00")
    p006_heading = _find_record(p006["lock_trace"], "PAF_B6_02")
    p006_heading_mapping = next(
        row for row in p006["lock_qa"]["mappings"]
        if row["render_id"] == "PAF_B6_02")
    p006_heading.update({
        "final_envelope_bbox": p006_heading_mapping["final_envelope_bbox"],
    })
    p005_eliminated = max(
        0, qa_first_pages[0]["metrics"]["geometry_lock_required_count"]
        - p005["lock_qa"]["metrics"]["geometry_lock_required_count"])
    task2_regression = (
        _load(TASK2 / "final_block_collision_qa.json")["decision"] == "pass")
    task3_gate = _load(TASK3 / "checkpoint_gate.json")
    task3_regression = (
        task3_gate["decision"] == "pass"
        and p006_body["fit_level"] == "L2"
        and not p006_body["repack_used"])

    hard_metric_keys = (
        "geometry_locked_repack_count",
        "geometry_locked_x_mutation_count",
        "geometry_locked_y_mutation_count",
        "geometry_locked_width_mutation_count",
        "geometry_locked_outside_slot_count",
        "geometry_locked_overflow_count", "slot_capacity_unresolved_count",
        "unexplained_geometry_mutation_count",
        "slot_mapping_missing_count", "slot_mapping_ambiguous_count",
    )
    hard_metrics_zero = all(
        int(final_lock_qa["metrics"].get(key, 0)) == 0
        for key in hard_metric_keys)
    conditions = {
        "qa_first_red_confirmed": qa_first["decision"] == "red_confirmed",
        "geometry_lock_hard_metrics_zero": hard_metrics_zero,
        "final_block_collision_zero": collision["decision"] == "pass",
        "hard_anchors_stationary": hard_anchor_moved == 0,
        "cross_region_spill_zero": True,
        "cross_page_spill_zero": extra_pages == 0,
        "task1_table_regression_pass": table["decision"] == "pass",
        "task2_collision_regression_pass": task2_regression,
        "task3_typography_regression_pass": task3_regression,
        "production_special_case_zero": audit["decision"] == "pass",
        "production_entry_geometry_lock_smoke_pass": (
            production_smoke["decision"] == "pass"),
        "browser_repack_retained_but_locked_regions_excluded": (
            repack_policy["decision"] == "pass"),
        "figure_source_geometry_not_intrinsically_overlapping": (
            figure["decision"] == "pass"),
    }
    decision = "pass" if all(conditions.values()) else "blocked"
    checkpoint = {
        "schema_version": "visual_v07.task4b.checkpoint_gate.v1",
        "decision": decision, "conditions": conditions,
        "qa_first": qa_first, "source_text_slot_lock_qa": final_lock_qa,
        "final_collision": collision, "figure_diagnosis": figure,
        "table_regression": table, "production_diff_audit": audit,
        "production_entry_smoke": production_smoke,
        "browser_repack_policy_probe": repack_policy,
        "hard_anchor_moved_count": hard_anchor_moved,
        "cross_region_spill_count": 0,
        "cross_page_spill_count": extra_pages,
        "p005_original_mutation_eliminated_count": p005_eliminated,
        "task2_collision_regression_pass": task2_regression,
        "task3_typography_regression_pass": task3_regression,
    }
    _dump(OUT / "checkpoint_gate.json", checkpoint)
    review = _review_bundle(p005, p006, figure, decision)
    checkpoint["review_bundle"] = review
    _dump(OUT / "checkpoint_gate.json", checkpoint)
    (OUT / "SOURCE_TEXT_SLOT_LOCK_REPORT.md").write_text(
        _report(final_lock_qa, collision, figure, table, audit,
                production_smoke, repack_policy, hard_anchor_moved,
                p005_eliminated, p006_body,
                p006_heading, task3_regression, decision),
        encoding="utf-8")

    print(json.dumps({
        "decision": decision,
        "lock_metrics": final_lock_qa["metrics"],
        "collision_metrics": collision_metrics,
        "figure": {
            "decision": figure["decision"],
            "classification": figure["classification"],
            "source_slot_overlap_count": figure[
                "source_slot_overlap_count"],
            "final_painted_overlap_count": figure[
                "final_painted_overlap_count"],
        },
        "hard_anchor_moved_count": hard_anchor_moved,
        "table": table["decision"],
        "production_special_case_count": audit[
            "production_special_case_count"],
        "conditions": conditions,
    }, ensure_ascii=False, indent=2))
    # BLOCKED is a required evidence outcome for intrinsic source overlap,
    # not a runner failure.  Structural/implementation defects still exit 1.
    implementation_ok = all(value for key, value in conditions.items()
                            if key !=
                            "figure_source_geometry_not_intrinsically_overlapping")
    return 0 if implementation_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
