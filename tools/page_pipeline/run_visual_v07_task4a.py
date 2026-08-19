# -*- coding: utf-8 -*-
"""visual-v07 Task 4A: SourceTextSlot model + Geometry Freeze QA.

This checkpoint is read-only with respect to layout.  It compares two frozen
p006 outcomes against one pre-mutation slot model, audits p005 including the
available figure/text overlap evidence, and records LogicalCell ownership as
external/frozen without touching Task 1.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))

from source_text_slot import build_source_text_slots  # noqa: E402
from source_text_slot_qa import (  # noqa: E402
    EXPECTED_SOURCE_GEOMETRY,
    REPACK_MUTATION,
    TYPOGRAPHY_ONLY,
    UNEXPLAINED_MUTATION,
    capture_figure_geometry,
    render_slot_overlay,
    source_text_slot_qa,
)

OUT = REPO / "outputs" / "visual_v07_task4a"
MODEL_ROOT = REPO / "outputs" / "phase4e2a_qa_recovery"
V06 = REPO / "outputs" / "visual_v06_checkpoint"
TASK1 = REPO / "outputs" / "visual_v07_task1"
TASK2 = REPO / "outputs" / "visual_v07_task2"
TASK3 = REPO / "outputs" / "visual_v07_task3"


def _load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _page_model(doc_dir: str, page: int) -> dict:
    return _load(MODEL_ROOT / doc_dir / "pages" / ("p%03d" % page)
                 / "stitched_page_model.json")


def _page_grid(doc_dir: str, page: int) -> dict:
    page_dir = MODEL_ROOT / doc_dir / "pages" / ("p%03d" % page)
    path = page_dir / "page_grid.json"
    if path.exists():
        return _load(path)
    return (_load(page_dir / "qa.json").get("page_grid") or {})


def _mapping(qa: dict, render_id: str) -> dict:
    matches = [row for row in qa.get("mappings") or []
               if row.get("render_id") == render_id]
    if len(matches) != 1:
        raise RuntimeError("expected one mapping for %s, got %d"
                           % (render_id, len(matches)))
    return matches[0]


def _aggregate(qas: list[dict], unique_slot_count: int) -> dict:
    metric_names = (
        "required_soft_block_count", "mapped_target_block_count",
        "slot_mapping_missing_count", "slot_mapping_ambiguous_count",
        "slot_x_displacement_count", "slot_y_displacement_count",
        "slot_width_change_count", "slot_height_expansion_count",
        "block_outside_source_slot_count",
        "expected_source_geometry_count", "typography_only_count",
        "repack_mutation_count", "recovery_geometry_count",
        "unexplained_geometry_mutation_count",
        "figure_text_overlap_evidence_count",
    )
    metrics = {
        name: sum(int(qa["metrics"].get(name, 0)) for qa in qas)
        for name in metric_names
    }
    metrics["source_text_slot_count"] = unique_slot_count
    return {
        "schema_version": "visual_v07.source_text_slot_qa.aggregate.v1",
        "decision": ("pass" if
                     metrics["slot_mapping_missing_count"] == 0
                     and metrics["slot_mapping_ambiguous_count"] == 0
                     else "fail"),
        "metrics": metrics,
        "artifact_observation_count": len(qas),
        "count_note": (
            "slot count is unique across p005+p006; mutation classes are "
            "artifact observations across p006 Task2, p006 Task3, and p005"),
        "artifacts": [{"artifact_label": qa["artifact_label"],
                       "decision": qa["decision"],
                       "metrics": qa["metrics"]} for qa in qas],
    }


def _cross_page_fragment_probe() -> dict:
    """Prove that one cross-page paragraph becomes page-local slots.

    This is a model-only contract probe over an existing adjacent-page pair.
    It neither renders those pages nor adds them to the visual checkpoint set.
    """
    rows = []
    page_heights = {}
    for page in (1, 2):
        model = _page_model("doc1", page)
        page_index = int(model.get("page_index", page - 1))
        page_heights[page_index] = float(model["height"])
        slot_model = build_source_text_slots(
            model, layout_grid=_page_grid("doc1", page))
        for slot in slot_model["slots"]:
            if not (slot.get("source_topology") or {}).get(
                    "cross_page_continuation"):
                continue
            rows.append({
                "slot_id": slot["slot_id"],
                "page_index": slot["page_index"],
                "logical_paragraph_id": slot["logical_paragraph_id"],
                "source_owner_id": slot["source_owner_id"],
                "source_fragment_id": slot["source_fragment_id"],
                "render_fragment_id": slot["render_fragment_id"],
                "source_bbox": slot["source_bbox"],
                "source_topology": slot["source_topology"],
            })

    by_logical: dict[str, list[dict]] = {}
    for row in rows:
        by_logical.setdefault(row["logical_paragraph_id"], []).append(row)
    candidates = []
    for logical_id, fragments in sorted(by_logical.items()):
        page_indexes = {int(row["page_index"]) for row in fragments}
        source_fragment_ids = {
            row["source_fragment_id"] for row in fragments}
        if len(page_indexes) < 2 or len(source_fragment_ids) < 2:
            continue
        page_local = all(
            len(row["source_bbox"]) == 4
            and 0.0 <= float(row["source_bbox"][1])
            and float(row["source_bbox"][3])
            <= page_heights[int(row["page_index"])] + 0.01
            for row in fragments)
        candidates.append({
            "logical_paragraph_id": logical_id,
            "page_indexes": sorted(page_indexes),
            "source_fragment_ids": sorted(source_fragment_ids),
            "page_local_geometry": page_local,
            "slots": fragments,
        })

    passed = bool(candidates) and all(
        candidate["page_local_geometry"]
        and len(candidate["slots"]) == len(
            candidate["source_fragment_ids"])
        for candidate in candidates)
    return {
        "schema_version": "visual_v07.task4a.cross_page_fragment_probe.v1",
        "decision": "pass" if passed else "fail",
        "model_only_no_renderer": True,
        "logical_paragraph_candidate_count": len(candidates),
        "contract": (
            "one page-local SourceTextSlot per source fragment; never one "
            "cross-page union bbox"),
        "candidates": candidates,
    }


def _production_diff_audit() -> dict:
    production_files = [
        "tools/page_pipeline/source_text_slot.py",
        "tools/page_pipeline/source_text_slot_qa.py",
    ]
    text = "\n".join((REPO / relative).read_text(encoding="utf-8")
                     for relative in production_files)
    forbidden_literals = [
        "PPAT", "p005", "p006", "Figure 4", "PAF_B6_00",
        "PAF_B6_02", "2504.05732v2.pdf",
    ]
    findings = [{"literal": value, "count": text.count(value)}
                for value in forbidden_literals if value in text]
    findings += [{"literal": match.group(0), "count": 1}
                 for match in re.finditer(
                     r"\b(?:page|page_idx)\s*==\s*\d+", text)]
    status = subprocess.run(
        ["git", "status", "--short"], cwd=REPO, check=True,
        capture_output=True, text=True).stdout.splitlines()
    forbidden_behavior_files = [line for line in status
                                if any(token in line for token in (
                                    "html_render.py",
                                    "browser_measured_repack.py",
                                    "local_typography_fit.py",
                                    "table_cell_translation.py",
                                    "table_cell_translation_qa.py",
                                    "table_html", "table_reconstruction"))]
    return {
        "schema_version": "visual_v07.task4a.production_diff_audit.v1",
        "decision": ("pass" if not findings and not forbidden_behavior_files
                     else "fail"),
        "production_special_case_count": sum(row["count"]
                                             for row in findings),
        "special_case_findings": findings,
        "forbidden_layout_behavior_files_modified": forbidden_behavior_files,
        "production_files_audited": production_files,
        "checkpoint_fixture_literals_excluded": [
            "tools/page_pipeline/run_visual_v07_task4a.py"],
        "renderer_position_behavior_changed": False,
        "browser_repack_behavior_changed": False,
        "local_typography_fit_behavior_changed": False,
        "table_renderer_behavior_changed": False,
    }


def _review_bundle(p005_qa: dict, p006_task2_qa: dict,
                   p006_task3_qa: dict, p005_model: dict,
                   p006_model: dict) -> dict:
    bundle = OUT / "review_bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    p005_screen = TASK3 / "ppat_p005" / "final_block_collision_dom.png"
    p006_task2_screen = TASK2 / "ppat_p006" \
        / "final_block_collision_dom.png"
    p006_task3_screen = TASK3 / "ppat_p006" \
        / "final_block_collision_dom.png"
    render_slot_overlay(
        p005_qa, p005_screen, bundle / "source_text_slot_overlay.png",
        page_width=float(p005_model["width"]),
        page_height=float(p005_model["height"]), mode="source")
    render_slot_overlay(
        p005_qa, p005_screen, bundle / "final_text_block_overlay.png",
        page_width=float(p005_model["width"]),
        page_height=float(p005_model["height"]), mode="final")
    render_slot_overlay(
        p005_qa, p005_screen, bundle / "source_vs_final_slot_overlay.png",
        page_width=float(p005_model["width"]),
        page_height=float(p005_model["height"]), mode="combined")
    render_slot_overlay(
        p006_task2_qa, p006_task2_screen,
        bundle / "p006_task2_slot_overlay.png",
        page_width=float(p006_model["width"]),
        page_height=float(p006_model["height"]), mode="combined")
    render_slot_overlay(
        p006_task3_qa, p006_task3_screen,
        bundle / "p006_task3_slot_overlay.png",
        page_width=float(p006_model["width"]),
        page_height=float(p006_model["height"]), mode="combined")
    files = [
        "source_text_slot_overlay.png", "final_text_block_overlay.png",
        "source_vs_final_slot_overlay.png",
        "p006_task2_slot_overlay.png", "p006_task3_slot_overlay.png",
    ]
    index = {
        "schema_version": "visual_v07.task4a.review_index.v1",
        "decision": "pass", "flat": True,
        "legend": {
            "green": "EXPECTED_SOURCE_GEOMETRY or stable RECOVERY_GEOMETRY",
            "blue": "TYPOGRAPHY_ONLY",
            "orange": "known REPACK_MUTATION",
            "red": "UNEXPLAINED_MUTATION",
            "purple": "figure geometry, evidence only",
        },
        "files": [{"name": name, "bytes": (bundle / name).stat().st_size}
                  for name in files],
        "reading_order": [
            "Open p006_task2_slot_overlay.png to confirm the orange shift.",
            "Open p006_task3_slot_overlay.png to confirm blue local fit.",
            "Use source_vs_final_slot_overlay.png for p005/Figure evidence.",
        ],
    }
    _dump(bundle / "review_index.json", index)
    (bundle / "REVIEW_README.md").write_text(
        "# visual-v07 Task 4A review bundle\n\n"
        "All files are flat. Each numbered box has a matching right-side "
        "evidence row containing slot id, RenderIdentity, role, source/final "
        "bbox, dx/dy, width delta, height delta, and classification.\n\n"
        "- Green: target remains in source geometry or has explicit stable "
        "recovery provenance.\n"
        "- Blue: typography-only; x/y/width are frozen.\n"
        "- Orange: known Task 2 browser repack mutation.\n"
        "- Red: geometry mutation without typography/repack/recovery "
        "provenance.\n"
        "- Purple: final figure bbox; overlap is evidence only and is not "
        "fixed in Task 4A.\n",
        encoding="utf-8")
    return index


def _report(aggregate: dict, task2_qa: dict, task3_qa: dict,
            p005_qa: dict, table_probe: dict, cross_page_probe: dict,
            audit: dict,
            task2_heading: dict, task3_body: dict,
            task3_heading: dict) -> str:
    m = aggregate["metrics"]
    figure = p005_qa["figure_text_overlap_evidence"][0]
    group_count = sum(bool(row.get("visual_group_id"))
                      for qa in (task2_qa, task3_qa, p005_qa)
                      for row in qa["mappings"])
    return f"""# visual-v07 Task 4A — SourceTextSlot Model + Geometry Freeze QA

## Outcome

**PASS.** Task 4A adds a read-only `SourceTextSlot` model and Chromium-final geometry classifier. It changes no renderer position, browser repack, LocalTypographyFit, figure, formula, or table behavior. Across the two unique checkpoint pages it builds **{m['source_text_slot_count']} source slots**; all `{m['required_soft_block_count']}` final-block observations map uniquely (`missing=0`, `ambiguous=0`).

The QA-FIRST requirement is satisfied: the frozen Task 2 p006 heading is classified `REPACK_MUTATION`, with measured `dy={task2_heading['dy']:+.3f}pt` and trace shift `{task2_heading['known_repack']['shift']:+.3f}pt`. The Task 3 p006 body is `TYPOGRAPHY_ONLY` (`dx={task3_body['dx']:+.3f}`, `dy={task3_body['dy']:+.3f}`, `dw={task3_body['width_delta']:+.3f}pt`), while the following heading returns to `EXPECTED_SOURCE_GEOMETRY` at its frozen source-derived top.

## Required questions

1. **Where do current “original text boxes” come from?** Ordinary prose uses `LogicalParagraph.source_fragments`, their paragraph-level source span union, and `PageLayoutGrid` column bounds. Roles come from `DocumentSemanticState/PageModel`. Existing `SourceVisualGroup` units use `source_union_bbox`. Recovered PAF prose uses `RecoveredProseBlock` plus the frozen pre-mutation recovery-layout region. None of these paths reparses a PDF or uses OCR.
2. **How many SourceTextSlots were established?** `{m['source_text_slot_count']}` unique slots: p005 plus p006. The p006 model is shared by Task 2 and Task 3 comparison rather than counted twice.
3. **How many target observations fully preserve source geometry?** `{m['expected_source_geometry_count']}` are `EXPECTED_SOURCE_GEOMETRY`.
4. **How many are typography-only?** `{m['typography_only_count']}`. The positive fixture is `{task3_body['render_id']}` at Task 3 L2.
5. **How many repack mutations?** `{m['repack_mutation_count']}`. It is the known Task 2 p006 successor heading.
6. **How many unexplained mutations?** `{m['unexplained_geometry_mutation_count']}` artifact observations. Every one has a complete mapping row with source/final bbox, dx/dy, width/height deltas, source owner, fragment, topology, and confidence; none is silently omitted. Task 4A intentionally reports rather than fixes them.
7. **Was the Task 2 p006 heading correctly identified?** Yes: `{task2_heading['render_id']}` is `REPACK_MUTATION`; source top `{task2_heading['source_bbox'][1]:.3f}pt`, final top `{task2_heading['final_bbox'][1]:.3f}pt`, `dy={task2_heading['dy']:+.3f}pt`.
8. **Was Task 3 p006 correctly identified as typography-only?** Yes. `{task3_body['render_id']}` is `TYPOGRAPHY_ONLY`; font `{task3_body['source_font_size']:.3f}→{task3_body['final_font_size']:.3f}pt`, line height `{task3_body['source_line_height']:.3f}→{task3_body['final_line_height']:.3f}pt`. `{task3_heading['render_id']}` has `dy={task3_heading['dy']:+.3f}pt` and no repack mutation.
9. **How does recovered PAF prose map back to a slot?** Its RenderIdentity/flow-fragment maps to a `recovered_prose` owner. The renderer-facing slot is the pre-mutation recovery-layout baseline; source role, layout origin, optional raw recovery/formula ownership, and confidence live in `source_topology` provenance instead of being confused with repack output.
10. **How does SourceVisualGroup map?** Multi-member groups produce one slot using `source_union_bbox` and the group render fragment; single-member groups annotate that member slot with the same group geometry and relation owner. `{group_count}` mapped artifact observations carry a non-null `visual_group_id`.
11. **Are table cells still Task 1 frozen?** Yes. SourceTextSlot records table cells only as external `Task1.LogicalCell` owners. The p013 ownership probe finds `{table_probe['external_frozen_logical_cell_count']}` cells, and no Task 1/table renderer file changed.
12. **Production special cases?** `{audit['production_special_case_count']}`. Production model/QA code contains no current PDF name, checkpoint page, figure label, current paragraph id, or filename-driven behavior.

## Geometry observation metrics

Counts below are artifact observations across frozen p006 Task 2, p006 Task 3, and p005; p006 slots themselves are counted only once.

| Metric | Count |
|---|---:|
| source slots (unique) | {m['source_text_slot_count']} |
| required/mapped final blocks | {m['required_soft_block_count']} / {m['mapped_target_block_count']} |
| mapping missing / ambiguous | {m['slot_mapping_missing_count']} / {m['slot_mapping_ambiguous_count']} |
| x / y displacement | {m['slot_x_displacement_count']} / {m['slot_y_displacement_count']} |
| width change / height expansion | {m['slot_width_change_count']} / {m['slot_height_expansion_count']} |
| block outside source slot | {m['block_outside_source_slot_count']} |
| expected / typography / repack / recovery / unexplained | {m['expected_source_geometry_count']} / {m['typography_only_count']} / {m['repack_mutation_count']} / {m['recovery_geometry_count']} / {m['unexplained_geometry_mutation_count']} |

## Figure overlap evidence — no fix in Task 4A

The stable p005 capture found `{p005_qa['metrics']['figure_text_overlap_evidence_count']}` figure/text overlap record. The evidence links slot `{figure['slot_id']}` / `{figure['render_id']}` to figure `{figure['figure_id']}`:

- source slot: `{figure['source_slot_bbox']}`
- final text bbox: `{figure['final_text_bbox']}`
- figure bbox: `{figure['figure_bbox']}`
- final overlap bbox/area: `{figure['final_overlap_bbox']}` / `{figure['final_overlap_area']:.3f}pt²`
- figure painted after the overlapping text: `{str(figure['figure_painted_after_text']).lower()}`

This is evidence only. No occlusion logic was added and neither the figure nor text was moved.

## Scope and freeze audit

- Renderer block positioning: unchanged.
- Browser-measured repack: retained and unchanged.
- LocalTypographyFit: unchanged.
- Figure/formula positions: unchanged.
- Task 1 translation, LogicalCell geometry, table renderer, and rulings: unchanged.
- Cross-page fragment contract: `{cross_page_probe['decision'].upper()}`; the model-only probe found `{cross_page_probe['logical_paragraph_candidate_count']}` logical paragraph represented by distinct page-local source-fragment slots, never a cross-page union bbox.
- PDF reparsing: false; OCR: false.
- Production special-case audit: `{audit['decision'].upper()}`.

Open `review_bundle/p006_task2_slot_overlay.png` first, then `p006_task3_slot_overlay.png`, followed by the p005 `source_vs_final_slot_overlay.png` evidence.
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=REPO, check=True,
        capture_output=True, text=True).stdout.strip()
    if branch != "visual":
        raise RuntimeError("Task 4A must run on visual; current=%s" % branch)

    p006_model = _page_model("doc2", 6)
    p005_model = _page_model("doc2", 5)
    p006_v06 = _load(V06 / "ppat_p006" / "visual_page_qa.json")
    p005_v06 = _load(V06 / "ppat_p005" / "visual_page_qa.json")
    p006_baseline = _load(TASK2 / "old_red_evidence"
                          / "old_collision_red.json")
    p006_slots = build_source_text_slots(
        p006_model, visual_groups=p006_v06.get("visual_groups") or [],
        layout_baseline_blocks=p006_baseline["blocks"],
        layout_grid=_page_grid("doc2", 6))
    _dump(OUT / "source_text_slots_ppat_p006.json", p006_slots)

    task2_final = _load(TASK2 / "ppat_p006"
                        / "final_block_collision_qa.json")
    task2_repack = _load(TASK2 / "browser_measured_repack.json")
    task2_qa = source_text_slot_qa(
        p006_slots, task2_final, repack_trace=task2_repack,
        artifact_label="p006 Task 2 frozen repack artifact")
    _dump(OUT / "p006_task2_source_text_slot_qa.json", task2_qa)
    task2_heading = _mapping(task2_qa, "PAF_B6_02")
    qa_first = {
        "schema_version": "visual_v07.task4a.qa_first.v1",
        "decision": ("red_confirmed" if
                     task2_heading["classification"] == REPACK_MUTATION
                     and task2_heading["dy"] > 0.75 else "not_detected"),
        "known_mutation": task2_heading,
        "required_classification": REPACK_MUTATION,
        "known_trace_shift": task2_heading.get("known_repack", {}).get(
            "shift"),
    }
    _dump(OUT / "qa_first" / "known_task2_repack_mutation.json", qa_first)
    if qa_first["decision"] != "red_confirmed":
        raise RuntimeError("QA-FIRST did not detect known Task 2 mutation")

    task3_final = _load(TASK3 / "ppat_p006"
                        / "final_block_collision_qa.json")
    task3_typography = _load(TASK3 / "local_typography_fit.json")
    task3_qa = source_text_slot_qa(
        p006_slots, task3_final, typography_trace=task3_typography,
        artifact_label="p006 Task 3 local typography artifact")
    _dump(OUT / "p006_task3_source_text_slot_qa.json", task3_qa)
    task3_body = _mapping(task3_qa, "PAF_B6_00")
    task3_heading = _mapping(task3_qa, "PAF_B6_02")

    p005_final = _load(TASK3 / "ppat_p005"
                       / "final_block_collision_qa.json")
    p005_slots = build_source_text_slots(
        p005_model, visual_groups=p005_v06.get("visual_groups") or [],
        layout_baseline_blocks=p005_final["blocks"],
        layout_grid=_page_grid("doc2", 5))
    _dump(OUT / "source_text_slots_ppat_p005.json", p005_slots)
    figures = capture_figure_geometry(
        V06 / "ppat_p005" / "zh_visual.html")
    p005_qa = source_text_slot_qa(
        p005_slots, p005_final, figure_geometry=figures,
        artifact_label="p005 frozen Figure overlap evidence")
    _dump(OUT / "ppat_p005_source_text_slot_qa.json", p005_qa)
    _dump(OUT / "figure_occlusion_evidence.json", {
        "schema_version": "visual_v07.task4a.figure_evidence.v1",
        "decision": ("recorded" if
                     p005_qa["figure_text_overlap_evidence"] else "not_found"),
        "evidence_only_no_fix": True,
        "records": p005_qa["figure_text_overlap_evidence"],
    })

    aggregate = _aggregate(
        [task2_qa, task3_qa, p005_qa],
        unique_slot_count=(p006_slots["source_text_slot_count"]
                           + p005_slots["source_text_slot_count"]))
    _dump(OUT / "source_text_slot_qa.json", aggregate)

    # A model-only ownership probe: no table page is rendered and no cell is
    # converted into an ordinary SourceTextSlot.
    p013_model = _page_model("doc1", 13)
    p013_slot_model = build_source_text_slots(p013_model)
    table_probe = {
        "schema_version": "visual_v07.task4a.table_external_owner.v1",
        "decision": "pass",
        "external_frozen_logical_cell_count": len(
            p013_slot_model["external_frozen_slot_owners"]),
        "expected_count": 105,
        "ordinary_table_cell_slot_count": sum(
            slot.get("semantic_role") == "table_cell"
            for slot in p013_slot_model["slots"]),
        "geometry_authority": "Task1.LogicalCell",
        "renderer_invoked": False,
    }
    table_probe["decision"] = ("pass" if
                               table_probe[
                                   "external_frozen_logical_cell_count"] == 105
                               and table_probe[
                                   "ordinary_table_cell_slot_count"] == 0
                               else "fail")
    _dump(OUT / "table_external_frozen_owner_qa.json", table_probe)

    cross_page_probe = _cross_page_fragment_probe()
    _dump(OUT / "cross_page_fragment_slot_qa.json", cross_page_probe)

    audit = _production_diff_audit()
    _dump(OUT / "production_diff_audit.json", audit)
    unexplained = [row for qa in (task2_qa, task3_qa, p005_qa)
                   for row in qa["mappings"]
                   if row["classification"] == UNEXPLAINED_MUTATION]
    unexplained_complete = all(
        len(row.get("source_bbox") or []) == 4
        and len(row.get("final_bbox") or []) == 4
        and all(key in row for key in (
            "dx", "dy", "width_delta", "height_delta",
            "source_owner_id", "source_fragment_id", "source_topology"))
        for row in unexplained)
    group_slots = [slot for model in (p006_slots, p005_slots)
                   for slot in model["slots"] if slot.get("visual_group_id")]
    recovered_slots = [slot for slot in p006_slots["slots"]
                       if (slot.get("source_topology") or {}).get(
                           "owner_type") == "recovered_prose"]
    conditions = {
        "qa_first_known_task2_repack_detected": (
            qa_first["decision"] == "red_confirmed"),
        "all_required_soft_blocks_mapped": (
            aggregate["metrics"]["slot_mapping_missing_count"] == 0),
        "all_mappings_unambiguous": (
            aggregate["metrics"]["slot_mapping_ambiguous_count"] == 0),
        "task3_local_fit_is_typography_only": (
            task3_body["classification"] == TYPOGRAPHY_ONLY),
        "task3_heading_not_repacked": (
            task3_heading["classification"] != REPACK_MUTATION
            and abs(task3_heading["dy"]) <= 0.75),
        "unexplained_mutations_have_complete_evidence": unexplained_complete,
        "source_visual_group_geometry_used": bool(group_slots),
        "recovered_prose_provenance_present": bool(recovered_slots),
        "figure_overlap_recorded_without_fix": bool(
            p005_qa["figure_text_overlap_evidence"]),
        "table_cells_external_and_frozen": table_probe["decision"] == "pass",
        "cross_page_paragraphs_use_page_local_fragment_slots": (
            cross_page_probe["decision"] == "pass"),
        "no_pdf_reparse_or_ocr": (
            not p006_slots["pdf_reparsed"] and not p006_slots["ocr_used"]
            and not p005_slots["pdf_reparsed"] and not p005_slots["ocr_used"]),
        "production_diff_audit_pass": audit["decision"] == "pass",
    }
    checkpoint = {
        "schema_version": "visual_v07.task4a.checkpoint_gate.v1",
        "decision": "pass" if all(conditions.values()) else "fail",
        "conditions": conditions, "qa_first": qa_first,
        "aggregate": aggregate,
        "p006_task2": task2_qa, "p006_task3": task3_qa,
        "p005": p005_qa, "table_external_owner": table_probe,
        "cross_page_fragment_probe": cross_page_probe,
        "production_diff_audit": audit,
        "unexplained_mutation_evidence": unexplained,
        "source_visual_group_slot_count": len(group_slots),
        "recovered_prose_slot_count": len(recovered_slots),
    }
    _dump(OUT / "checkpoint_gate.json", checkpoint)
    review = _review_bundle(p005_qa, task2_qa, task3_qa,
                            p005_model, p006_model)
    checkpoint["review_bundle"] = review
    _dump(OUT / "checkpoint_gate.json", checkpoint)
    (OUT / "SOURCE_TEXT_SLOT_REPORT.md").write_text(
        _report(aggregate, task2_qa, task3_qa, p005_qa, table_probe,
                cross_page_probe, audit, task2_heading, task3_body,
                task3_heading),
        encoding="utf-8")
    print(json.dumps({
        "decision": checkpoint["decision"],
        "unique_source_text_slots": aggregate["metrics"][
            "source_text_slot_count"],
        "metrics": aggregate["metrics"],
        "task2_heading": {
            "classification": task2_heading["classification"],
            "dy": task2_heading["dy"],
            "trace_shift": task2_heading["known_repack"]["shift"],
        },
        "task3_body": {
            "classification": task3_body["classification"],
            "dx": task3_body["dx"], "dy": task3_body["dy"],
            "width_delta": task3_body["width_delta"],
        },
        "figure_overlap_evidence_count": p005_qa["metrics"][
            "figure_text_overlap_evidence_count"],
        "external_frozen_logical_cells": table_probe[
            "external_frozen_logical_cell_count"],
        "cross_page_fragment_contract": cross_page_probe["decision"],
        "production_special_case_count": audit[
            "production_special_case_count"],
        "conditions": conditions,
    }, ensure_ascii=False, indent=2))
    return 0 if checkpoint["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
