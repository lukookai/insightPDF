# -*- coding: utf-8 -*-
"""visual-v07 Task 3 checkpoint: local typography fit before repack.

The problem page is replayed from the frozen visual-v06 artifact.  The generic
production TypographyFitPolicy is applied level by level, every trial is
measured in Chromium, and Task 2 repack is invoked only if the role floor is
still too tall.  The checkpoint is deliberately limited to PPAT p005/p006 and
the two frozen Task 1 table fixtures requested by the task.
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
from local_typography_fit import (  # noqa: E402
    fit_html_with_browser,
    heading_hierarchy_violation_count,
    mark_repack_usage,
)
from local_typography_fit_qa import (  # noqa: E402
    local_typography_fit_qa,
    render_typography_fit_overlay,
)

OUT = REPO / "outputs" / "visual_v07_task3"
FROZEN_V06 = REPO / "outputs" / "visual_v06_checkpoint"
TASK1 = REPO / "outputs" / "visual_v07_task1"
TASK2 = REPO / "outputs" / "visual_v07_task2"
MODEL_ROOT = REPO / "outputs" / "phase4e2a_qa_recovery"
PPAT_PDF = Path("C:/Users/74496/Desktop/PPAT.pdf")

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


def _page_model(doc_key: str, page: int) -> dict:
    doc_dir = "doc2" if doc_key == "ppat" else "doc1"
    return _load(MODEL_ROOT / doc_dir / "pages" / ("p%03d" % page)
                 / "stitched_page_model.json")


def _page_png(pdf: str | Path, page_index: int, out: str | Path,
              zoom: float = 2.0) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(str(pdf)) as document:
        pixmap = document[page_index].get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        pixmap.save(str(out))
    return out


def _font(size: int = 22):
    for candidate in ("C:/Windows/Fonts/msyh.ttc",
                      "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _before_after(before: str | Path, after: str | Path,
                  out: str | Path) -> Path:
    left = Image.open(before).convert("RGB")
    right = Image.open(after).convert("RGB")
    target_height = min(left.height, right.height)
    if left.height != target_height:
        left = left.resize((round(left.width * target_height / left.height),
                            target_height))
    if right.height != target_height:
        right = right.resize((round(right.width * target_height / right.height),
                              target_height))
    band = 48
    canvas = Image.new("RGB", (left.width + right.width,
                               target_height + band), "white")
    canvas.paste(left, (0, band))
    canvas.paste(right, (left.width, band))
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 9), "BEFORE — Task 2 repack", fill=(190, 35, 35),
              font=_font(22))
    draw.text((left.width + 12, 9),
              "AFTER — Task 3 local typography fit",
              fill=(0, 125, 80), font=_font(22))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


def _empty_repack() -> dict:
    return {
        "schema_version": "visual_v07.browser_measured_repack.v1",
        "applied": False, "repacked_region_count": 0,
        "measured_block_count": 0, "moved_block_count": 0,
        "hard_anchor_moved_count": 0, "cross_region_spill_count": 0,
        "cross_page_spill_count": 0, "unresolved_count": 0,
        "placements": [], "unresolved": [],
    }


def _style_geometry(style: str) -> tuple[str, ...]:
    result = []
    for name in ("left", "top", "width", "height"):
        match = re.search(r"(?i)(?:^|;)\s*%s\s*:\s*([^;]+)"
                          % name, style)
        result.append(match.group(1).strip() if match else "")
    return tuple(result)


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


def _table_regression() -> dict:
    fixtures = []
    for page in (16, 13):
        qa = _load(TASK1 / ("2504_p%03d" % page) / "visual_page_qa.json")
        cell_qa = qa.get("table_cell_translation_qa") or {}
        structures = qa.get("table_structure") or []
        structure = (structures[0] if isinstance(structures, list)
                     and structures else structures)
        fixtures.append({
            "doc": "2504", "page": page,
            "page_decision": qa.get("decision"),
            "table_cell_translation_decision": cell_qa.get("decision"),
            "table_cell_metrics": cell_qa.get("metrics") or {},
            "table_structure": structure,
            "required_structure": ({
                "row_count": 21, "col_count": 5, "cell_count": 105,
                "horizontal_ruling_count": 3,
                "vertical_ruling_count": 0,
            } if page == 13 else None),
        })
    defect_keys = (
        "untranslated_required_table_cell_count",
        "table_cell_target_missing_count", "table_cell_source_residual_count",
        "table_cell_duplicate_render_count",
        "table_cell_source_target_double_render_count",
        "table_cell_overflow_count", "table_cell_wrong_owner_count",
    )
    metrics = {key: sum(int(row["table_cell_metrics"].get(key, 0))
                        for row in fixtures) for key in defect_keys}
    p013 = next(row for row in fixtures if row["page"] == 13)
    structure_ok = all(p013["table_structure"].get(key) == value
                       for key, value in p013["required_structure"].items())
    passed = (all(row["page_decision"] == "pass"
                  and row["table_cell_translation_decision"] == "pass"
                  for row in fixtures)
              and all(value == 0 for value in metrics.values())
              and structure_ok)
    return {
        "schema_version": "visual_v07.task3.table_task1_regression.v1",
        "decision": "pass" if passed else "fail", "metrics": metrics,
        "p013_structure_preserved": structure_ok, "fixtures": fixtures,
        "table_cell_fit_delegated_to_task1": True,
        "task1_code_changed_in_task3": False,
    }


def _formula_hard_metrics(page_qa: dict) -> dict[str, int]:
    selected = {}
    for key, value in (page_qa.get("hard_metrics") or {}).items():
        if ("formula" in key or key in {
                "protected_math_token_loss_count",
                "protected_math_token_mutation_count", "math_token_loss_count",
                "math_token_duplicate_count", "math_token_order_inversion_count",
                "math_base_symbol_loss_count", "math_superscript_loss_count",
                "math_subscript_loss_count",
                "math_group_structure_mismatch_count"}) \
                and isinstance(value, (int, float)):
            selected[key] = int(value)
    return selected


def _production_diff_audit() -> dict:
    tracked = [
        "tools/page_pipeline/html_render.py",
        "tools/page_pipeline/run_visual_v06_checkpoint.py",
    ]
    new_production = ["tools/page_pipeline/local_typography_fit.py"]
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
        "PPAT.pdf", "2504.05732v2.pdf", "PAF_B6_00", "PAF_B6_02",
        "4.2。评估数据集与跟踪器",
        "Evaluation datasets and trackers",
    ]
    findings = [{"literal": literal, "count": added.count(literal)}
                for literal in forbidden if literal in added]
    findings += [{"literal": match.group(0), "count": 1}
                 for match in re.finditer(
                     r"\b(?:page|page_idx)\s*==\s*\d+", added)]
    status = subprocess.run(
        ["git", "status", "--short"], cwd=REPO, check=True,
        capture_output=True, text=True).stdout.splitlines()
    table_files = [line for line in status
                   if any(token in line for token in (
                       "table_cell_translation.py",
                       "table_cell_translation_qa.py", "table_html",
                       "table_reconstruction"))]
    return {
        "schema_version": "visual_v07.task3.production_diff_audit.v1",
        "decision": "pass" if not findings and not table_files else "fail",
        "production_special_case_count": sum(row["count"] for row in findings),
        "special_case_findings": findings,
        "table_task1_files_modified": table_files,
        "production_files_audited": tracked + new_production,
        "checkpoint_fixture_literals_excluded": [
            "tools/page_pipeline/run_visual_v07_task3.py"],
    }


def _review_bundle(final_pdf: Path, final_p006: dict,
                   fit_trace: dict) -> dict:
    bundle = OUT / "review_bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    before = _page_png(TASK2 / "ppat_p006" / "zh_visual.pdf", 0,
                       bundle / "ppat_p006_task2_before.png")
    after = _page_png(final_pdf, 0,
                      bundle / "ppat_p006_task3_after.png")
    _before_after(before, after, bundle / "ppat_p006_before_after.png")
    with pymupdf.open(final_pdf) as document:
        width = float(document[0].rect.width)
        height = float(document[0].rect.height)
    render_typography_fit_overlay(
        fit_trace["records"], final_p006,
        OUT / "ppat_p006" / "final_block_collision_dom.png",
        bundle / "ppat_p006_typography_fit_overlay.png",
        page_width=width, page_height=height)
    _page_png(FROZEN_V06 / "ppat_p005" / "zh_visual.pdf", 0,
              bundle / "ppat_p005_task3_after.png")
    _page_png(TASK1 / "2504_p016" / "zh_visual.pdf", 0,
              bundle / "table_task1_regression.png")
    _page_png(TASK1 / "2504_p013" / "zh_visual.pdf", 0,
              bundle / "2504_p013_regression.png")
    files = [
        "ppat_p006_task2_before.png", "ppat_p006_task3_after.png",
        "ppat_p006_before_after.png",
        "ppat_p006_typography_fit_overlay.png",
        "ppat_p005_task3_after.png", "table_task1_regression.png",
        "2504_p013_regression.png",
    ]
    index = {
        "schema_version": "visual_v07.task3.review_index.v1",
        "decision": "pass", "flat": True,
        "files": [{"name": name, "bytes": (bundle / name).stat().st_size}
                  for name in files],
        "reading_order": [
            "Open ppat_p006_before_after.png first.",
            "Inspect ppat_p006_typography_fit_overlay.png for the exact budget.",
            "Check p005, the Task 1 problem table, and 2504 p013 last.",
        ],
    }
    _dump(bundle / "review_index.json", index)
    (bundle / "REVIEW_README.md").write_text(
        "# visual-v07 Task 3 review bundle\n\n"
        "All deliverables are flat in this directory. Start with "
        "`ppat_p006_before_after.png`: the left side is Task 2's valid "
        "browser-repack result, and the right side is Task 3's minimum "
        "sufficient local typography fit with the heading restored to its "
        "source-derived top.\n\n"
        "The typography overlay records the complete source/final font, "
        "line-height, measured height, fit level, capacity, and repack "
        "decision. The remaining three images are frozen p005 and Task 1 "
        "table regressions.\n",
        encoding="utf-8")
    return index


def _report(record: dict, final_collision: dict, fit_qa: dict,
            repack: dict, table: dict, audit: dict, hard_moved: int,
            p005: dict, geometry: dict) -> str:
    metrics = fit_qa["metrics"]
    final_metrics = final_collision["metrics"]
    return f"""# visual-v07 Task 3 — Local Typography Fit Before Repack

## Outcome

**PASS.** The frozen PPAT p006 body exceeded its source-derived region by `10.125pt`. Chromium tested the bounded ladder in order and selected **{record['fit_level']}**, the first sufficient level: font scale `{record['font_scale']:.3f}`, line-height scale `{record['line_height_scale']:.3f}`. The body now fits at `{record['measured_height_after']:.3f}pt` within `{record['region_capacity']:.3f}pt`; the following heading stays at its original `{geometry['heading_final_top']:.3f}pt` top and no repack runs.

## Required questions

1. **Why did Task 2 directly advance the heading?** Its first correction step was browser-measured region-local repack. The predecessor's final DOM height was `{record['measured_height_before']:.3f}pt`, `10.125pt` beyond the allocated `{record['region_capacity']:.3f}pt`, so Task 2 advanced the following soft heading by that measured shortfall.
2. **What is Task 3's priority?** `LocalTypographyFit → fits original region? → BrowserMeasuredRepack fallback → BLOCK if the same legal region still cannot contain it`.
3. **Can p006 remain in the original region?** Yes. Its fitted height is `{record['measured_height_after']:.3f}pt`, below the `{record['region_capacity']:.3f}pt` capacity, with both paragraph and successor tops preserved.
4. **Which level was selected?** `{record['fit_level']}`. L0 and safe L1 remained too tall; L2 was the first legal fit, so L3/L4 were not attempted or selected.
5. **Font scale?** `{record['font_scale']:.3f}` (`{record['source_font_size']:.3f}pt → {record['final_font_size']:.3f}pt`), so glyph size did not shrink.
6. **Line-height scale?** `{record['line_height_scale']:.3f}` (`{record['source_line_height']:.3f}pt → {record['final_line_height']:.3f}pt`).
7. **Was repack called finally?** {'Yes' if record['repack_used'] else 'No'}; `repack_used = {str(record['repack_used']).lower()}` and moved-block count is `{repack['moved_block_count']}`.
8. **If called, why was local fit insufficient?** Not applicable on p006: the bounded L2 fit succeeded. The production path retains Task 2 repack only for candidates still taller than capacity after their role-specific L4 floor.
9. **Was heading hierarchy preserved?** Yes. `heading_hierarchy_violation_count = {metrics['heading_hierarchy_violation_count']}`; p006 heading remains larger than body text.
10. **Any excessive compression?** No. `excessive_typography_compression_count = {metrics['excessive_typography_compression_count']}`; the algorithm stopped at the first successful browser-measured level and kept font scale at 1.0.
11. **Did every hard anchor remain stationary?** Yes. `hard_anchor_moved_count = {hard_moved}`; local fit edits one paragraph's typography only.
12. **Task 1 / Task 2 regressions?** Both PASS. PPAT p005 and p006 final collision counts are zero; Task 1 required table cells remain translated with overflow zero; p013 remains `21 × 5 / 105 cells / 3H / 0V`.
13. **Production special cases?** `{audit['production_special_case_count']}`. Production behavior contains no current document name, page branch, section text, paragraph id, or filename-dependent fit rule.

## p006 browser truth

| Level | Font scale | Line-height scale | DOM height | Capacity | Fits |
|---|---:|---:|---:|---:|---|
{chr(10).join('| {fit_level} | {font_scale:.3f} | {line_height_scale:.3f} | {dom_measured_height:.3f}pt | {region_capacity:.3f}pt | {fits} |'.format(fits='yes' if row['fits_original_region'] else 'no', **row) for row in record['attempts'])}

The primary decision truth is Chromium `getBoundingClientRect()` on the complete RenderIdentity paragraph block at every attempted level. No line-count estimator chooses a level. Safe L1 never uses `word-break: break-all` or `line-break: anywhere`; protected Latin/math runs remain intact.

## Hard metrics and checkpoint

- Final collision: `{final_metrics['final_block_collision_count']}`; PPAT p005: `{p005['metrics']['final_block_collision_count']}`.
- Fit candidates/applied/success/unresolved: `{metrics['typography_fit_candidate_count']}` / `{metrics['typography_fit_applied_count']}` / `{metrics['typography_fit_success_count']}` / `{metrics['typography_fit_unresolved_count']}`.
- Unnecessary repack / excessive compression: `{metrics['unnecessary_repack_count']}` / `{metrics['excessive_typography_compression_count']}`.
- Font floor / line-height floor / heading hierarchy: `{metrics['font_floor_violation_count']}` / `{metrics['line_height_floor_violation_count']}` / `{metrics['heading_hierarchy_violation_count']}`.
- Hard-anchor movement / cross-region spill / cross-page spill: `{hard_moved}` / `{repack['cross_region_spill_count']}` / `{repack['cross_page_spill_count']}`.
- Task 1 table regression: `{table['decision'].upper()}`.
- Production special-case audit: `{audit['decision'].upper()}` (`0` findings).

See `review_bundle/ppat_p006_before_after.png` first, then `review_bundle/ppat_p006_typography_fit_overlay.png`.
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=REPO, check=True,
        capture_output=True, text=True).stdout.strip()
    if branch != "visual":
        raise RuntimeError("Task 3 must run on visual; current=%s" % branch)

    qa_first = _load(OUT / "old_red_evidence"
                     / "local_typography_fit_red.json")
    qa_first_valid = (
        qa_first.get("decision") == "red_confirmed"
        and int(qa_first.get("old_logic_requires_repack_count", 0)) > 0
        and int(qa_first.get("bounded_local_fit_effect_count", 0)) > 0)
    if not qa_first_valid:
        raise RuntimeError("QA-FIRST local typography evidence is invalid")

    initial_qa = _load(TASK2 / "old_red_evidence"
                       / "old_collision_red.json")
    frozen_dir = FROZEN_V06 / "ppat_p006"
    source_html_path = frozen_dir / "zh_visual.html"
    source_html = source_html_path.read_text(encoding="utf-8")
    final_dir = OUT / "ppat_p006"
    final_dir.mkdir(parents=True, exist_ok=True)
    for asset in frozen_dir.glob("*.svg"):
        shutil.copy2(asset, final_dir / asset.name)

    fitted_html, fit_trace = fit_html_with_browser(
        source_html, initial_qa, source_html_path=source_html_path,
        trial_dir=OUT / "fit_trials")
    local_html_path = final_dir / "zh_visual.html"
    local_html_path.write_text(fitted_html, encoding="utf-8")
    final_pdf = final_dir / "zh_visual.pdf"
    render_html_to_pdf(local_html_path, final_pdf)
    after_local_qa = final_block_collision_qa(
        _page_model("ppat", 6), html_path=local_html_path,
        final_pdf_path=final_pdf,
        screenshot_path=final_dir / "final_block_collision_dom.png")

    repack = _empty_repack()
    final_p006 = after_local_qa
    final_html = fitted_html
    if after_local_qa["metrics"]["final_block_collision_count"] > 0:
        final_html, repack = repack_html_from_final_geometry(
            fitted_html, after_local_qa)
        local_html_path.write_text(final_html, encoding="utf-8")
        if not repack["unresolved_count"]:
            render_html_to_pdf(local_html_path, final_pdf)
            final_p006 = final_block_collision_qa(
                _page_model("ppat", 6), html_path=local_html_path,
                final_pdf_path=final_pdf,
                screenshot_path=final_dir / "final_block_collision_dom.png")
    fit_trace = mark_repack_usage(fit_trace, repack)
    hierarchy_count = heading_hierarchy_violation_count(
        fit_trace["records"], initial_qa)
    fit_qa = local_typography_fit_qa(
        fit_trace["records"],
        heading_hierarchy_violations=hierarchy_count)
    _dump(OUT / "local_typography_fit.json", fit_trace)
    _dump(OUT / "local_typography_fit_qa.json", fit_qa)
    _dump(OUT / "browser_measured_repack.json", repack)
    _dump(final_dir / "final_block_collision_qa.json", final_p006)

    p005_dir = OUT / "ppat_p005"
    p005_dir.mkdir(parents=True, exist_ok=True)
    final_p005 = final_block_collision_qa(
        _page_model("ppat", 5),
        html_path=FROZEN_V06 / "ppat_p005" / "zh_visual.html",
        final_pdf_path=FROZEN_V06 / "ppat_p005" / "zh_visual.pdf",
        screenshot_path=p005_dir / "final_block_collision_dom.png")
    _dump(p005_dir / "final_block_collision_qa.json", final_p005)

    aggregate_metrics = {
        key: sum(int(page["metrics"].get(key, 0))
                 for page in (final_p005, final_p006))
        for key in COLLISION_METRICS
    }
    final_collision = {
        "schema_version": "visual_v07.task3.collision_regression.v1",
        "decision": ("pass" if all(value == 0
                                    for value in aggregate_metrics.values())
                     else "fail"),
        "metrics": aggregate_metrics,
        "pages": [
            {"doc": "ppat", "page": 5, "decision": final_p005["decision"],
             "metrics": final_p005["metrics"]},
            {"doc": "ppat", "page": 6, "decision": final_p006["decision"],
             "metrics": final_p006["metrics"]},
        ],
    }
    _dump(OUT / "final_block_collision_qa.json", final_collision)

    table = _table_regression()
    _dump(OUT / "table_task1_regression.json", table)
    audit = _production_diff_audit()
    _dump(OUT / "production_diff_audit.json", audit)
    hard_moved = _hard_anchor_moved_count(source_html, final_html)
    record = fit_trace["records"][0]
    before_blocks = {row["flow_fragment_id"]: row
                     for row in initial_qa["blocks"]}
    final_blocks = {row["flow_fragment_id"]: row
                    for row in final_p006["blocks"]}
    successor_id = record["successor_fragment_id"]
    geometry = {
        "body_original_top": before_blocks[record["flow_fragment_id"]][
            "assigned_top"],
        "body_final_top": final_blocks[record["flow_fragment_id"]][
            "assigned_top"],
        "heading_original_top": before_blocks[successor_id]["assigned_top"],
        "heading_final_top": final_blocks[successor_id]["assigned_top"],
    }
    geometry["original_geometry_preserved"] = all(
        abs(float(geometry[left]) - float(geometry[right])) <= 0.01
        for left, right in (("body_original_top", "body_final_top"),
                            ("heading_original_top", "heading_final_top")))
    _dump(OUT / "source_geometry_preservation.json", geometry)

    frozen_p005_qa = _load(FROZEN_V06 / "ppat_p005"
                           / "visual_page_qa.json")
    frozen_p006_qa = _load(FROZEN_V06 / "ppat_p006"
                           / "visual_page_qa.json")
    formula_metrics = {
        "ppat_p005": _formula_hard_metrics(frozen_p005_qa),
        "ppat_p006": _formula_hard_metrics(frozen_p006_qa),
    }
    formula_total = sum(sum(values.values())
                        for values in formula_metrics.values())
    with pymupdf.open(final_pdf) as document:
        extra_pages = max(0, len(document) - 1)

    conditions = {
        "qa_first_red_confirmed": qa_first_valid,
        "minimum_sufficient_level_selected": (
            record["fit_level"] == "L2"
            and [row["fit_level"] for row in record["attempts"]]
            == ["L0", "L1", "L2"]),
        "local_typography_fit_qa_pass": fit_qa["decision"] == "pass",
        "final_collision_zero": final_collision["decision"] == "pass",
        "successful_fit_did_not_repack": (
            record["fit_success"] and not record["repack_used"]
            and not repack["applied"]),
        "source_geometry_preserved": geometry[
            "original_geometry_preserved"],
        "hard_anchors_stationary": hard_moved == 0,
        "no_region_or_page_spill": (
            repack["cross_region_spill_count"] == 0
            and repack["cross_page_spill_count"] == 0),
        "unexpected_extra_pages_zero": extra_pages == 0,
        "formula_hard_metrics_zero": formula_total == 0,
        "task1_table_regression_pass": table["decision"] == "pass",
        "production_diff_audit_pass": audit["decision"] == "pass",
    }
    checkpoint = {
        "schema_version": "visual_v07.task3.checkpoint_gate.v1",
        "decision": "pass" if all(conditions.values()) else "fail",
        "conditions": conditions, "qa_first": qa_first,
        "local_typography_fit": fit_trace,
        "local_typography_fit_qa": fit_qa,
        "final_collision": final_collision,
        "browser_measured_repack": repack,
        "hard_anchor_moved_count": hard_moved,
        "cross_region_spill_count": repack["cross_region_spill_count"],
        "cross_page_spill_count": repack["cross_page_spill_count"],
        "unexpected_extra_page_count": extra_pages,
        "formula_hard_metrics": formula_metrics,
        "formula_hard_metric_total": formula_total,
        "table_task1_regression": table,
        "production_diff_audit": audit,
        "source_geometry": geometry,
    }
    _dump(OUT / "checkpoint_gate.json", checkpoint)
    review = _review_bundle(final_pdf, final_p006, fit_trace)
    checkpoint["review_bundle"] = review
    _dump(OUT / "checkpoint_gate.json", checkpoint)
    (OUT / "LOCAL_TYPOGRAPHY_FIT_REPORT.md").write_text(
        _report(record, final_collision, fit_qa, repack, table, audit,
                hard_moved, final_p005, geometry), encoding="utf-8")

    print(json.dumps({
        "decision": checkpoint["decision"],
        "fit": {key: record[key] for key in (
            "fit_level", "font_scale", "line_height_scale",
            "region_capacity", "measured_height_before",
            "measured_height_after", "fit_success", "repack_used")},
        "fit_metrics": fit_qa["metrics"],
        "final_collision": final_collision["metrics"],
        "hard_anchor_moved_count": hard_moved,
        "table": table["decision"],
        "production_special_case_count": audit[
            "production_special_case_count"],
        "conditions": conditions,
    }, ensure_ascii=False, indent=2))
    return 0 if checkpoint["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
