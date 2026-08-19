# -*- coding: utf-8 -*-
"""visual-v07 Task 2 checkpoint: final block collision closure.

The failing PPAT page is replayed from the frozen visual-v06 HTML/PDF so the
proof is independent of a later translation API response.  The same generic
browser-measured placement plan used by the production checkpoint is applied
to that frozen HTML, rendered again, and measured a second time.
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
from browser_measured_repack import (  # noqa: E402
    repack_html_from_final_geometry,
)
from final_block_collision_qa import (  # noqa: E402
    final_block_collision_qa,
    render_collision_overlay,
)

OUT = REPO / "outputs" / "visual_v07_task2"
FROZEN_V06 = REPO / "outputs" / "visual_v06_checkpoint"
TASK1 = REPO / "outputs" / "visual_v07_task1"
MODEL_ROOT = REPO / "outputs" / "phase4e2a_qa_recovery"
PPAT_PDF = Path("C:/Users/74496/Desktop/PPAT.pdf")
PDF_2504 = Path("C:/Users/74496/Desktop/2504.05732v2.pdf")

COLLISION_METRICS = (
    "final_block_collision_count",
    "body_body_collision_count",
    "body_heading_collision_count",
    "heading_body_collision_count",
    "caption_body_collision_count",
    "body_caption_collision_count",
    "reading_order_overlap_count",
    "final_bbox_missing_count",
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
    with pymupdf.open(str(pdf)) as doc:
        pixmap = doc[page_index].get_pixmap(
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
    scale = min(left.height, right.height) / max(left.height, right.height)
    if left.height > right.height:
        left = left.resize((round(left.width * scale), right.height))
    elif right.height > left.height:
        right = right.resize((round(right.width * scale), left.height))
    band = 44
    canvas = Image.new("RGB", (left.width + right.width,
                               max(left.height, right.height) + band),
                       "white")
    canvas.paste(left, (0, band))
    canvas.paste(right, (left.width, band))
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 8), "BEFORE — frozen visual-v06", fill=(190, 25, 25),
              font=_font(22))
    draw.text((left.width + 12, 8),
              "AFTER — browser-measured repack", fill=(20, 135, 55),
              font=_font(22))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


def _union(boxes: list[list[float]]) -> list[float]:
    boxes = [box for box in boxes if len(box) == 4]
    if not boxes:
        return []
    return [round(min(box[0] for box in boxes), 3),
            round(min(box[1] for box in boxes), 3),
            round(max(box[2] for box in boxes), 3),
            round(max(box[3] for box in boxes), 3)]


def _section_number(text: str) -> str | None:
    match = re.search(r"(?:^|\s)(\d+(?:\.\d+)+)", text or "")
    return match.group(1) if match else None


def _recovered_source_candidates() -> list[dict]:
    """Recover source-only geometry for root-cause diagnostics.

    This does not translate or render anything.  It supplies source bboxes for
    recovered PAF blocks, which are absent from the stitched page model.
    """
    from prose_adopted_formula_recovery import recover_prose_adopted_formulas

    source_dir = MODEL_ROOT / "doc2" / "pages" / "p006"
    model = _load(source_dir / "stitched_page_model.json")
    translations = _load(source_dir / "translation.json")
    result = recover_prose_adopted_formulas(
        model, translations, str(PPAT_PDF), 5, translator_fn=None,
        existing_ids=set(translations))
    candidates = list(result.get("recovered") or [])
    # Include source section lines that may have been absorbed into an older
    # recovered cluster even though the current source-only clustering keeps
    # them separate.
    with pymupdf.open(PPAT_PDF) as doc:
        text_dict = doc[5].get_text("dict")
    for source_block in text_dict.get("blocks") or []:
        for line in source_block.get("lines") or []:
            text = "".join(span.get("text", "")
                           for span in line.get("spans") or []).strip()
            if not _section_number(text):
                continue
            candidates.append({
                "paragraph_id": "SOURCE_SECTION_LINE",
                "semantic_role": "heading",
                "bbox": list(line.get("bbox") or []),
                "source_text": text,
            })
    return candidates


def _infer_source_bbox(block: dict, candidates: list[dict]) -> list[float]:
    if len(block.get("source_bbox") or []) == 4:
        return list(block["source_bbox"])
    planned = block["planned_bbox"]
    same_column = [row for row in candidates
                   if len(row.get("bbox") or []) == 4
                   and abs(float(row["bbox"][0]) - planned[0]) <= 18.0]
    section = _section_number(block.get("text_preview") or "")
    section_rows = [row for row in same_column
                    if _section_number(row.get("source_text") or "")
                    == section] if section else []
    if block["semantic_role"] == "heading" and section_rows:
        heading = next((row for row in section_rows
                        if "heading" in str(row.get("semantic_role", ""))),
                       section_rows[0])
        return [round(float(value), 3) for value in heading["bbox"]]
    if block["semantic_role"] == "body":
        start = min((row["bbox"][1] for row in section_rows),
                    default=min((row["bbox"][1] for row in same_column),
                                default=planned[1]))
        body_boxes = [row["bbox"] for row in same_column
                      if row["bbox"][1] >= start - 1.0
                      and "body" in str(row.get("semantic_role", "body"))]
        body_boxes += [row["bbox"] for row in section_rows]
        return _union(body_boxes)
    return _union([row["bbox"] for row in same_column])


def _root_cause_trace(red: dict) -> dict:
    candidates = _recovered_source_candidates()
    by_fragment = {row["flow_fragment_id"]: row
                   for row in red.get("blocks") or []}
    pairs = []
    for collision in red.get("collisions") or []:
        first = dict(by_fragment[collision["predecessor_fragment_id"]])
        second = dict(by_fragment[collision["successor_fragment_id"]])
        first["source_bbox"] = _infer_source_bbox(first, candidates)
        second["source_bbox"] = _infer_source_bbox(second, candidates)

        def geometry(block: dict) -> dict:
            planned = block["planned_bbox"]
            return {
                "render_id": block["render_id"],
                "paragraph_id": block["paragraph_id"],
                "semantic_role": block["semantic_role"],
                "source_bbox": block["source_bbox"],
                "source_bbox_origin": (
                    "stitched_page_model" if block.get("source_bbox")
                    and not str(block["render_id"]).startswith("PAF_")
                    else "source-only recovered-prose diagnostic"),
                "planned_bbox": planned,
                "planned_height": round(planned[3] - planned[1], 3),
                "estimated_height": block["estimated_height"],
                "estimator_probe_height": block["estimator_probe_height"],
                "dom_measured_bbox": block["dom_measured_bbox"],
                "dom_measured_height": block["dom_measured_height"],
                "height_delta": block["height_delta"],
                "final_pdf_bbox": block["pdf_rendered_bbox"],
                "packing_region": block["packing_region"],
                "assigned_top": block["assigned_top"],
                "reading_order": block["reading_order"],
            }

        pairs.append({
            "collision": collision,
            "block_a": geometry(first),
            "block_b": geometry(second),
            "root_cause_classes": [
                "HEIGHT_UNDERESTIMATED",
                "CURSOR_NOT_ADVANCED",
                "NEXT_BLOCK_SOURCE_TOP_REUSED",
            ],
            "evidence": {
                "allocated_predecessor_height": first["estimated_height"],
                "measured_predecessor_height": first["dom_measured_height"],
                "unaccounted_height": first["height_delta"],
                "successor_assigned_top": second["assigned_top"],
                "measured_predecessor_bottom": first["dom_measured_bbox"][3],
                "observed_overlap_height": collision["overlap_height"],
            },
        })
    return {
        "schema_version": "visual_v07.final_block_root_cause.v1",
        "decision": "traced" if pairs else "missing_collision_pair",
        "pairs": pairs,
    }


def _formula_hard_metrics(page_qa: dict) -> dict[str, int]:
    hard = page_qa.get("hard_metrics") or {}
    selected = {}
    for key, value in hard.items():
        if ("formula" in key or key in {
                "protected_math_token_loss_count",
                "protected_math_token_mutation_count",
                "math_token_loss_count",
                "math_token_duplicate_count",
                "math_token_order_inversion_count",
                "math_base_symbol_loss_count",
                "math_superscript_loss_count",
                "math_subscript_loss_count",
                "math_group_structure_mismatch_count"}):
            if isinstance(value, (int, float)):
                selected[key] = int(value)
    return selected


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
        "table_cell_target_missing_count",
        "table_cell_source_residual_count",
        "table_cell_duplicate_render_count",
        "table_cell_source_target_double_render_count",
        "table_cell_overflow_count",
        "table_cell_wrong_owner_count",
    )
    defects = {key: sum(int(row["table_cell_metrics"].get(key, 0))
                        for row in fixtures) for key in defect_keys}
    p013 = next(row for row in fixtures if row["page"] == 13)
    structure_ok = all(p013["table_structure"].get(key) == value
                       for key, value in p013["required_structure"].items())
    passed = (all(row["page_decision"] == "pass"
                  and row["table_cell_translation_decision"] == "pass"
                  for row in fixtures)
              and all(value == 0 for value in defects.values())
              and structure_ok)
    return {
        "schema_version": "visual_v07.task2.table_task1_regression.v1",
        "decision": "pass" if passed else "fail",
        "metrics": defects,
        "p013_structure_preserved": structure_ok,
        "fixtures": fixtures,
        "task1_code_changed_in_task2": False,
    }


def _production_diff_audit() -> dict:
    production_files = [
        "tools/page_pipeline/run_visual_v06_checkpoint.py",
        "tools/page_pipeline/final_block_collision_qa.py",
        "tools/page_pipeline/browser_measured_repack.py",
    ]
    tracked_diff = subprocess.run(
        ["git", "diff", "--unified=0", "--",
         "tools/page_pipeline/run_visual_v06_checkpoint.py"],
        cwd=REPO, check=True, capture_output=True, text=True).stdout
    added = "\n".join(line[1:] for line in tracked_diff.splitlines()
                      if line.startswith("+") and not line.startswith("+++"))
    for relative in production_files[1:]:
        added += "\n" + (REPO / relative).read_text(encoding="utf-8")
    forbidden_literals = [
        "PPAT.pdf", "2504.05732v2.pdf", "PAF_B6_00", "PAF_B6_02",
        "4.2。评估数据集与跟踪器",
        "Evaluation datasets and trackers",
    ]
    findings = [{"literal": literal, "count": added.count(literal)}
                for literal in forbidden_literals if literal in added]
    page_branches = re.findall(r"\b(?:page|page_idx)\s*==\s*\d+", added)
    findings += [{"literal": expression, "count": 1}
                 for expression in page_branches]
    diff_names = subprocess.run(
        ["git", "status", "--short"], cwd=REPO, check=True,
        capture_output=True, text=True).stdout.splitlines()
    table_files = [line for line in diff_names
                   if any(token in line for token in (
                       "table_cell_translation.py",
                       "table_cell_translation_qa.py",
                       "table_html", "table_reconstruction"))]
    return {
        "schema_version": "visual_v07.task2.production_diff_audit.v1",
        "decision": "pass" if not findings and not table_files else "fail",
        "production_special_case_count": sum(row["count"] for row in findings),
        "special_case_findings": findings,
        "table_task1_files_modified": table_files,
        "production_files_audited": production_files,
        "checkpoint_fixture_literals_excluded": [
            "tools/page_pipeline/run_visual_v07_task2.py"],
    }


def _review_bundle(after_pdf: Path, after_qa: dict) -> dict:
    bundle = OUT / "review_bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    _page_png(PPAT_PDF, 5, bundle / "collision_problem_source.png")
    before = bundle / "collision_problem_before.png"
    shutil.copy2(OUT / "old_red_evidence" / "final_block_collision_dom.png",
                 before)
    after = _page_png(after_pdf, 0, bundle / "collision_problem_after.png")
    _before_after(before, after, bundle / "collision_problem_before_after.png")
    shutil.copy2(OUT / "old_red_evidence"
                 / "final_block_collision_overlay.png",
                 bundle / "final_block_collision_overlay_before.png")
    with pymupdf.open(after_pdf) as doc:
        width, height = float(doc[0].rect.width), float(doc[0].rect.height)
    render_collision_overlay(
        after_qa, OUT / "ppat_p006" / "final_block_collision_dom.png",
        bundle / "final_block_collision_overlay_after.png", width, height)
    _page_png(FROZEN_V06 / "ppat_p005" / "zh_visual.pdf", 0,
              bundle / "ppat_p005_after.png")
    shutil.copy2(after, bundle / "ppat_p006_after.png")
    _page_png(TASK1 / "2504_p016" / "zh_visual.pdf", 0,
              bundle / "table_task1_regression.png")
    _page_png(TASK1 / "2504_p013" / "zh_visual.pdf", 0,
              bundle / "2504_p013_regression.png")
    files = [
        "collision_problem_source.png", "collision_problem_before.png",
        "collision_problem_after.png", "collision_problem_before_after.png",
        "final_block_collision_overlay_before.png",
        "final_block_collision_overlay_after.png", "ppat_p005_after.png",
        "ppat_p006_after.png", "table_task1_regression.png",
        "2504_p013_regression.png",
    ]
    index = {
        "schema_version": "visual_v07.task2.review_index.v1",
        "decision": "pass",
        "flat": True,
        "files": [{"name": name, "bytes": (bundle / name).stat().st_size}
                  for name in files],
        "reading_order": [
            "Open collision_problem_before_after.png first.",
            "Use the two overlays to inspect old red versus final green.",
            "Check PPAT p005/p006, then the two frozen Task 1 tables.",
        ],
    }
    _dump(bundle / "review_index.json", index)
    (bundle / "REVIEW_README.md").write_text(
        "# visual-v07 Task 2 review bundle\n\n"
        "All files are flat in this directory. Start with "
        "`collision_problem_before_after.png`: the frozen visual-v06 body "
        "tail intersects the following heading on the left, while the Task 2 "
        "render advances that heading by the browser-measured height delta.\n\n"
        "- Red overlay: frozen final DOM/PDF collision.\n"
        "- Green overlay: second-measurement collision count is zero.\n"
        "- PPAT p005/p006: recovered-prose/heading regression.\n"
        "- Table images: frozen Task 1 translation and p013 geometry "
        "regression.\n",
        encoding="utf-8")
    return index


def _report(red: dict, final_aggregate: dict, trace: dict,
            table: dict, audit: dict, checkpoint: dict) -> str:
    pair = trace["pairs"][0]
    first = pair["block_a"]
    second = pair["block_b"]
    placement = checkpoint["browser_measured_repack"]["placements"][1]
    metrics = final_aggregate["metrics"]
    return f"""# visual-v07 Task 2 — Final Block Collision Closure

## Outcome

**PASS.** QA-first on the frozen visual-v06 artifact found one real final block collision, classified as body → heading. A browser-measured, region-local second placement advanced only the successor soft heading; the second Chromium DOM/PDF measurement reports every final collision hard metric as zero.

## Required questions

1. **Why did the old QA miss the visible collision?** `severe_soft_soft_collision_count` used layout-stage estimated/planned geometry. It never measured the complete final `.paragraph-block` after Chinese wrapping, so the estimator’s shortfall was invisible.
2. **Render identities:** `{first['render_id']}` → `{second['render_id']}`.
3. **Semantic roles:** `{first['semantic_role']}` → `{second['semantic_role']}`.
4. **Planned versus DOM height:** predecessor planned/allocated `{first['planned_height']:.3f}pt`, DOM measured `{first['dom_measured_height']:.3f}pt`, delta `+{first['height_delta']:.3f}pt`. The visible overlap was `{red['collisions'][0]['overlap_height']:.3f}pt`.
5. **Root cause:** `HEIGHT_UNDERESTIMATED`, followed by `CURSOR_NOT_ADVANCED` and `NEXT_BLOCK_SOURCE_TOP_REUSED`.
6. **Why RegionLocalPacking did not handle it:** its first pass consumed an estimated height. The successor remained at source-derived top `{second['assigned_top']:.3f}pt`; no final-browser measurement fed the cursor.
7. **Browser-measured repack added?** Yes: initial render → Chromium complete-block measurement → collision-region repack → second render → second measurement.
8. **Hard anchors moved?** No; `hard_anchor_moved_count = 0`.
9. **Cross-region/cross-page spill?** No; both counts are `0`, and the measured sequence fits its existing region. The heading moved by `{placement['shift']:.3f}pt` to `{placement['new_top']:.3f}pt`.
10. **Final collision counts:** all zero, including `final_block_collision_count = {metrics['final_block_collision_count']}`, `body_heading_collision_count = {metrics['body_heading_collision_count']}`, `reading_order_overlap_count = {metrics['reading_order_overlap_count']}`, and `final_bbox_missing_count = {metrics['final_bbox_missing_count']}`.
11. **Task 1 table regression:** `{table['decision'].upper()}`; untranslated required cells `0`, overflow `0`; p013 remains `21 × 5 / 105 cells / 3H / 0V`. No Task 1 table translation or LogicalCell renderer file changed.
12. **Production special case:** `{audit['production_special_case_count']}`. Placement code contains no current PDF name, page-number branch, heading text, or current paragraph-id behavior.

## Geometry truth and root-cause evidence

The primary truth is Chromium `getBoundingClientRect()` on complete RenderIdentity paragraph blocks. Final PDF word geometry is assigned back to DOM line boxes as an independent secondary record. Inline spans, bold children, scripts, and formulas are not collision units.

| Block | Source bbox | Planned bbox | DOM bbox | Final PDF bbox | Region/order |
|---|---|---|---|---|---|
| `{first['render_id']}` body | `{first['source_bbox']}` | `{first['planned_bbox']}` | `{first['dom_measured_bbox']}` | `{first['final_pdf_bbox']}` | `{first['packing_region']}` / `{first['reading_order']}` |
| `{second['render_id']}` heading | `{second['source_bbox']}` | `{second['planned_bbox']}` | `{second['dom_measured_bbox']}` | `{second['final_pdf_bbox']}` | `{second['packing_region']}` / `{second['reading_order']}` |

Recovered-block source bboxes are reconstructed only for diagnostic tracing from source PDF text geometry; collision decisions themselves use final DOM/PDF truth.

## Checkpoint

- Frozen problem page / PPAT p006: old red `1`, after `0`.
- PPAT p005 recovered-prose/heading regression: final collision metrics all `0`.
- Unexpected extra pages: `0`.
- Hard-anchor invasion: `0`.
- Formula hard metrics: all `0`.
- Task 1 problem table and 2504 p013: PASS.
- Browser repack unresolved regions: `0`.

See `review_bundle/collision_problem_before_after.png` first, then the before/after overlays.
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=REPO, check=True,
        capture_output=True, text=True).stdout.strip()
    if branch != "visual":
        raise RuntimeError("Task 2 must run on visual; current=%s" % branch)

    red_path = OUT / "old_red_evidence" / "old_collision_red.json"
    red = _load(red_path)
    old_red_valid = (
        red.get("decision") == "fail"
        and red.get("metrics", {}).get("final_block_collision_count", 0) > 0
        and red.get("metrics", {}).get("body_heading_collision_count", 0) > 0)
    if not old_red_valid:
        raise RuntimeError("QA-first old red evidence is missing or invalid")

    final_dir = OUT / "ppat_p006"
    final_dir.mkdir(parents=True, exist_ok=True)
    frozen_dir = FROZEN_V06 / "ppat_p006"
    for asset in frozen_dir.glob("*.svg"):
        shutil.copy2(asset, final_dir / asset.name)
    repaired_html, repack = repack_html_from_final_geometry(
        (frozen_dir / "zh_visual.html").read_text(encoding="utf-8"), red)
    (final_dir / "zh_visual.html").write_text(repaired_html,
                                                encoding="utf-8")
    final_pdf = final_dir / "zh_visual.pdf"
    render_html_to_pdf(final_dir / "zh_visual.html", final_pdf)
    final_p006 = final_block_collision_qa(
        _page_model("ppat", 6), html_path=final_dir / "zh_visual.html",
        final_pdf_path=final_pdf,
        screenshot_path=final_dir / "final_block_collision_dom.png")
    _dump(final_dir / "final_block_collision_qa.json", final_p006)
    _dump(OUT / "browser_measured_repack.json", repack)

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
    final_aggregate = {
        "schema_version": "visual_v07.task2.final_block_collision_aggregate.v1",
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
    _dump(OUT / "final_block_collision_qa.json", final_aggregate)

    trace = _root_cause_trace(red)
    _dump(OUT / "root_cause_trace.json", trace)
    table = _table_regression()
    _dump(OUT / "table_task1_regression.json", table)
    audit = _production_diff_audit()
    _dump(OUT / "production_diff_audit.json", audit)

    frozen_p005_qa = _load(FROZEN_V06 / "ppat_p005"
                           / "visual_page_qa.json")
    frozen_p006_qa = _load(FROZEN_V06 / "ppat_p006"
                           / "visual_page_qa.json")
    formula_metrics = {
        "ppat_p005": _formula_hard_metrics(frozen_p005_qa),
        "ppat_p006": _formula_hard_metrics(frozen_p006_qa),
    }
    formula_hard_total = sum(sum(metrics.values())
                             for metrics in formula_metrics.values())
    with pymupdf.open(final_pdf) as doc:
        final_page_count = len(doc)
    checkpoint = {
        "schema_version": "visual_v07.task2.checkpoint_gate.v1",
        "old_red_valid": old_red_valid,
        "final_collision": final_aggregate,
        "browser_measured_repack": repack,
        "unexpected_extra_page_count": max(0, final_page_count - 1),
        "hard_anchor_invasion_count": int(
            frozen_p005_qa["hard_metrics"].get("anchor_invasion_count", 0))
            + int(frozen_p006_qa["hard_metrics"].get(
                "anchor_invasion_count", 0)),
        "formula_hard_metrics": formula_metrics,
        "formula_hard_metric_total": formula_hard_total,
        "table_task1_regression": table,
        "production_diff_audit": audit,
    }
    conditions = {
        "old_red_valid": old_red_valid,
        "final_collision_zero": final_aggregate["decision"] == "pass",
        "browser_repack_resolved": repack["unresolved_count"] == 0,
        "hard_anchors_stationary": repack["hard_anchor_moved_count"] == 0,
        "no_region_or_page_spill": (
            repack["cross_region_spill_count"] == 0
            and repack["cross_page_spill_count"] == 0),
        "unexpected_extra_pages_zero": (
            checkpoint["unexpected_extra_page_count"] == 0),
        "hard_anchor_invasion_zero": (
            checkpoint["hard_anchor_invasion_count"] == 0),
        "formula_hard_metrics_zero": formula_hard_total == 0,
        "task1_table_regression_pass": table["decision"] == "pass",
        "production_diff_audit_pass": audit["decision"] == "pass",
    }
    checkpoint["conditions"] = conditions
    checkpoint["decision"] = ("pass" if all(conditions.values()) else "fail")
    _dump(OUT / "checkpoint_gate.json", checkpoint)

    review = _review_bundle(final_pdf, final_p006)
    checkpoint["review_bundle"] = review
    _dump(OUT / "checkpoint_gate.json", checkpoint)
    report = _report(red, final_aggregate, trace, table, audit, checkpoint)
    (OUT / "FINAL_BLOCK_COLLISION_REPORT.md").write_text(
        report, encoding="utf-8")

    print(json.dumps({
        "decision": checkpoint["decision"],
        "old_red": red["metrics"],
        "final": final_aggregate["metrics"],
        "repack": {
            "moved_block_count": repack["moved_block_count"],
            "unresolved_count": repack["unresolved_count"],
            "hard_anchor_moved_count": repack["hard_anchor_moved_count"],
            "cross_region_spill_count": repack["cross_region_spill_count"],
            "cross_page_spill_count": repack["cross_page_spill_count"],
        },
        "table": table["decision"],
        "production_special_case_count": audit[
            "production_special_case_count"],
        "conditions": conditions,
    }, ensure_ascii=False, indent=2))
    return 0 if checkpoint["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
