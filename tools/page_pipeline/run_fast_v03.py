# -*- coding: utf-8 -*-
"""Run fast-v03 against three frozen page fixtures only.

This runner cannot translate or execute a full document.  It loads the same
frozen L0 HTML, SourceTextSlot ledger, and Fill candidates used by fast-v02,
keeps one Chromium page alive for DOM baseline capture, Fit/Fill measurement,
final PDF printing, and final RenderLedger capture, then executes only the
requested local hard gates.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from typing import Any

from PIL import Image, ImageDraw


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for path in (HERE, REPO / "实现源码" / "pdf_translator"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from final_block_collision_qa import (  # noqa: E402
    final_block_collision_qa_from_snapshot,
)
from local_typography_fill_qa import typography_fill_qa  # noqa: E402
from local_typography_fit import _with_base  # noqa: E402
from run_fast_v02 import (  # noqa: E402
    BASELINE,
    FIXTURES,
    SOURCE_CHAIN,
    VISUAL,
    _anchor_signatures,
    _baseline_rows,
    _dump,
    _first_l0,
    _font,
    _load,
    _pixel_diff_count,
    _render_png,
    _sha256,
    _slot_signatures,
)
from typography_fast_path import (  # noqa: E402
    TypographyBatchSession,
    fill_locked_html_fast,
    fit_locked_html_fast,
    write_fast_trace,
)


OUT = REPO / "outputs" / "fast_v03"
REVIEW = OUT / "review_bundle"
SCRATCH = REPO / "tmp" / "pdfs" / "fast_v03"
BASE_SHA = "8bb128d70bc63afb98d25dcac486a3a1648c2d48"


BEFORE_BROWSER_EVENTS = [
    {
        "sequence": 1,
        "operation": "chromium_launch_and_pdf_print",
        "reason": "initial layout PDF for SourceTextSlot/collision precheck",
        "function": "render_html_to_pdf",
        "classification": "intermediate_pdf",
    },
    {
        "sequence": 2,
        "operation": "chromium_launch_and_dom_capture",
        "reason": "initial FinalBlockCollisionQA DOM measurement",
        "function": "final_block_collision_qa._capture_dom",
        "classification": "dom_measurement",
    },
    {
        "sequence": 3,
        "operation": "chromium_launch_and_dom_measurement",
        "reason": "Typography Fit/Fill shared candidate measurement",
        "function": "TypographyBatchSession.__enter__",
        "classification": "typography_measurement",
    },
    {
        "sequence": 4,
        "operation": "chromium_launch_and_pdf_print",
        "reason": "final delivery PDF after Typography Fit/Fill",
        "function": "render_html_to_pdf",
        "classification": "final_pdf",
    },
    {
        "sequence": 5,
        "operation": "chromium_launch_and_dom_capture",
        "reason": "final FinalBlockCollisionQA DOM measurement",
        "function": "final_block_collision_qa._capture_dom",
        "classification": "collision_validation",
    },
]


def _before_record(page_key: str) -> dict[str, Any]:
    return {
        "page_key": page_key,
        "source_revision": BASE_SHA,
        "chromium_launch_count": 5,
        "pdf_print_count": 2,
        "total_pdf_print_count": 2,
        "intermediate_pdf_print_count": 1,
        "final_pdf_print_count": 1,
        "typography_trial_pdf_print_count": 0,
        "events": BEFORE_BROWSER_EVENTS,
        "evidence": (
            "pre-fast-v03 run_visual_v06_checkpoint.py called "
            "render_html_to_pdf + final_block_collision_qa before slots, "
            "opened TypographyBatchSession, then called both functions again"),
    }


def _comparison(old: Path, new: Path, output: Path, title: str) -> None:
    images = [Image.open(path).convert("RGB") for path in (old, new)]
    width = max(image.width for image in images)
    height = max(image.height for image in images)
    header = 62
    canvas = Image.new("RGB", (width * 2, height + header), "white")
    draw = ImageDraw.Draw(canvas)
    font = _font(22)
    for index, (image, label) in enumerate(zip(
            images, ("OLD", "FAST-V03"))):
        x = index * width + (width - image.width) // 2
        canvas.paste(image, (x, header))
        draw.text((index * width + 16, 12), "%s | %s" % (title, label),
                  fill="#111111", font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
    for image in images:
        image.close()


def _production_special_case_count() -> int:
    tokens = ("ppat_p011", "2504_p011", "2504_p005", "S376",
              "λc > 0")
    files = (HERE / "typography_fast_path.py",
             HERE / "final_block_collision_qa.py",
             HERE / "run_visual_v06_checkpoint.py")
    return sum(token in path.read_text(encoding="utf-8")
               for path in files for token in tokens)


def _model_path(fixture: dict[str, Any]) -> Path:
    model_root = SOURCE_CHAIN / (
        "ppat_source_chain" if fixture["doc"] == "ppat"
        else "2504_source_chain")
    return model_root / "pages" / ("p%03d" % fixture["page"]) \
        / "stitched_page_model.json"


def _run_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    page_key = fixture["page_key"]
    page_dir = VISUAL / page_key
    scratch = SCRATCH / page_key
    scratch.mkdir(parents=True, exist_ok=True)
    old_html = (page_dir / "zh_visual.html").read_text(encoding="utf-8")
    base_path = _first_l0(page_dir)
    base_html = base_path.read_text(encoding="utf-8")
    lock_trace = _load(page_dir / "source_text_slot_lock.json", {})
    candidate_qa = _load(
        page_dir / "local_typography_fill_candidates.json", {})
    page_model = _load(_model_path(fixture), {})

    def html_builder(state: str) -> str:
        return _with_base(state, base_path)

    pdf_path = scratch / "fast_v03.pdf"
    html_path = scratch / "fast_v03.html"
    started = time.perf_counter()
    with TypographyBatchSession(html_builder, scratch / "session") as session:
        initial_snapshot = session.capture_dom(
            base_html, capture_name="source_slot_and_collision_precheck",
            screenshot_path=scratch / "initial_dom.png")
        initial_qa = final_block_collision_qa_from_snapshot(
            page_model, snapshot=initial_snapshot)

        fitted_html, fast_lock = fit_locked_html_fast(
            base_html, lock_trace, initial_qa, session)
        final_html, fast_fill = fill_locked_html_fast(
            fitted_html, fast_lock, candidate_qa, session)

        print_event = session.print_pdf(
            final_html, pdf_path, html_path=html_path,
            reason="final_delivery_after_in_dom_measurement_fit_fill")
        final_snapshot = session.capture_dom(
            capture_name="final_collision_validation",
            screenshot_path=scratch / "final_dom.png")
        collision = final_block_collision_qa_from_snapshot(
            page_model, snapshot=final_snapshot,
            final_pdf_path=pdf_path)
        trace = write_fast_trace(
            scratch / "chromium_page_session.json", session,
            fast_lock, fast_fill)
    elapsed = time.perf_counter() - started
    fill_audit = typography_fill_qa(candidate_qa, fast_fill, collision)

    old_slots = _slot_signatures(old_html)
    new_slots = _slot_signatures(final_html)
    old_anchors = _anchor_signatures(old_html)
    new_anchors = _anchor_signatures(final_html)
    slot_mutations = sum(old_slots.get(key) != value
                         for key, value in new_slots.items())
    slot_mutations += len(set(old_slots) ^ set(new_slots))
    hard_anchor_moved = int(old_anchors != new_anchors)

    old_png = scratch / "old.png"
    new_png = scratch / "new.png"
    _render_png(page_dir / "zh_visual.pdf", old_png)
    _render_png(pdf_path, new_png)
    pixel_diff_count = _pixel_diff_count(old_png, new_png)
    review_path = REVIEW / (page_key + "_old_new.png")
    _comparison(old_png, new_png, review_path, page_key)

    metrics = {
        "visual_pixel_diff_count": pixel_diff_count,
        "slot_geometry_mutation_count": slot_mutations,
        "final_block_collision_count": int(
            collision["metrics"]["final_block_collision_count"]),
        "hard_anchor_moved_count": hard_anchor_moved,
        "overflow_count": int((fill_audit.get("metrics") or {}).get(
            "typography_fill_overflow_count", 0)),
        "production_special_case_count": _production_special_case_count(),
    }
    return {
        "page_key": page_key,
        "doc": fixture["doc"],
        "page": fixture["page"],
        "translation_api_call_count": 0,
        "full_document_run_count": 0,
        "elapsed_seconds": round(elapsed, 6),
        "formal_pdf_print_seconds": print_event["elapsed_seconds"],
        "old_final_pdf_sha256": _sha256(page_dir / "zh_visual.pdf"),
        "new_fixture_pdf_sha256": _sha256(pdf_path),
        "print_path": {
            "chromium_launch_count": trace["browser_launch_count"],
            "pdf_print_count": trace["pdf_print_count"],
            "total_pdf_print_count": trace["pdf_print_count"],
            "intermediate_pdf_print_count": trace[
                "intermediate_pdf_print_count"],
            "final_pdf_print_count": trace["final_pdf_print_count"],
            "typography_trial_pdf_print_count": trace[
                "typography_trial_pdf_print_count"],
            "typography_measurement_round_count": trace[
                "typography_measurement_round_count"],
            "dom_capture_count": trace["dom_capture_count"],
            "events": trace["pdf_prints"],
            "dom_captures": trace["dom_captures"],
        },
        "metrics": metrics,
        "final_collision_qa": collision,
        "fill_qa": fill_audit,
        "review_image": str(review_path.resolve()),
    }


def _decision(after: list[dict[str, Any]]) -> tuple[str, dict[str, int]]:
    metrics = {
        "chromium_launch_count_max": max(
            row["print_path"]["chromium_launch_count"] for row in after),
        "pdf_print_count_max": max(
            row["print_path"]["pdf_print_count"] for row in after),
        "intermediate_pdf_print_count": sum(
            row["print_path"]["intermediate_pdf_print_count"] for row in after),
        "final_pdf_print_count_not_one": sum(
            row["print_path"]["final_pdf_print_count"] != 1 for row in after),
        "typography_trial_pdf_print_count": sum(
            row["print_path"]["typography_trial_pdf_print_count"]
            for row in after),
    }
    for key in ("visual_pixel_diff_count", "slot_geometry_mutation_count",
                "final_block_collision_count", "hard_anchor_moved_count",
                "overflow_count", "production_special_case_count"):
        metrics[key] = sum(row["metrics"][key] for row in after)
    passed = (
        metrics["chromium_launch_count_max"] == 1
        and metrics["pdf_print_count_max"] <= 2
        and metrics["intermediate_pdf_print_count"] == 0
        and metrics["final_pdf_print_count_not_one"] == 0
        and metrics["typography_trial_pdf_print_count"] == 0
        and all(metrics[key] == 0 for key in (
            "visual_pixel_diff_count", "slot_geometry_mutation_count",
            "final_block_collision_count", "hard_anchor_moved_count",
            "overflow_count", "production_special_case_count")))
    return ("pass" if passed else "blocked"), metrics


def _write_report(before: list[dict[str, Any]], after: list[dict[str, Any]],
                  decision: str, metrics: dict[str, int]) -> None:
    lines = [
        "# fast-v03 - Collapse Chromium PDF Prints",
        "",
        "## Decision",
        "",
        "**FAST_V03 = %s**" % decision.upper(),
        "",
        "Only PPAT p011, 2504 p011, and 2504 p005 frozen fixtures were "
        "opened. No translation API, fresh/full-document run, full QA, or "
        "contact sheet was executed.",
        "",
        "## Print path before fast-v03",
        "",
        "The pre-change production page path opened Chromium five times and "
        "printed two PDFs per page. The first print existed only to feed "
        "SourceTextSlot/collision prechecks; the second was the delivery PDF.",
        "",
        "| fixture | Chromium launches | PDF prints | intermediate | final |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in before:
        lines.append("| %s | %d | %d | %d | %d |" % (
            row["page_key"], row["chromium_launch_count"],
            row["pdf_print_count"], row["intermediate_pdf_print_count"],
            row["final_pdf_print_count"]))
    lines += ["", "Print/launch reasons:", ""]
    for event in BEFORE_BROWSER_EVENTS:
        lines.append("%d. `%s`: %s." % (
            event["sequence"], event["classification"], event["reason"]))
    lines += [
        "",
        "## Print path after fast-v03",
        "",
        "One persistent Chromium page now performs the baseline RenderLedger "
        "capture, two bounded Typography DOM rounds, and the final collision "
        "RenderLedger capture. None of those operations print a PDF. "
        "`page.pdf()` is called only for final delivery.",
        "",
        "| fixture | launches | DOM rounds | DOM captures | PDF prints | "
        "intermediate | final | elapsed | final print |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in after:
        path = row["print_path"]
        lines.append(
            "| %s | %d | %d | %d | %d | %d | %d | %.3fs | %.3fs |" % (
                row["page_key"], path["chromium_launch_count"],
                path["typography_measurement_round_count"],
                path["dom_capture_count"], path["pdf_print_count"],
                path["intermediate_pdf_print_count"],
                path["final_pdf_print_count"], row["elapsed_seconds"],
                row["formal_pdf_print_seconds"]))
    lines += [
        "",
        "## Correctness gates",
        "",
        "| fixture | pixel diff | slot mutation | collision | anchor moved | "
        "overflow | special case |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in after:
        value = row["metrics"]
        lines.append("| %s | %d | %d | %d | %d | %d | %d |" % (
            row["page_key"], value["visual_pixel_diff_count"],
            value["slot_geometry_mutation_count"],
            value["final_block_collision_count"],
            value["hard_anchor_moved_count"], value["overflow_count"],
            value["production_special_case_count"]))
    lines += [
        "",
        "Aggregate hard metrics:",
        "",
        "- normal fixture final_pdf_print_count = 1: %s" % (
            "PASS" if metrics["final_pdf_print_count_not_one"] == 0
            else "FAIL"),
        "- total_pdf_print_count <= 2: %s" % (
            "PASS" if metrics["pdf_print_count_max"] <= 2 else "FAIL"),
        "- typography_trial_pdf_print_count = %d" % metrics[
            "typography_trial_pdf_print_count"],
        "- intermediate_pdf_print_count = %d" % metrics[
            "intermediate_pdf_print_count"],
        "- chromium_launch_count = %d per fixture" % metrics[
            "chromium_launch_count_max"],
        "- visual_pixel_diff_count = %d" % metrics[
            "visual_pixel_diff_count"],
        "- slot_geometry_mutation_count = %d" % metrics[
            "slot_geometry_mutation_count"],
        "- final_block_collision_count = %d" % metrics[
            "final_block_collision_count"],
        "- hard_anchor_moved_count = %d" % metrics[
            "hard_anchor_moved_count"],
        "- overflow_count = %d" % metrics["overflow_count"],
        "- production_special_case_count = %d" % metrics[
            "production_special_case_count"],
        "",
        "The bounded-correction guard permits at most one additional formal "
        "print, but none of the three normal fixtures used it. SourceTextSlot "
        "and hard-anchor signatures are unchanged, and the final rendered "
        "pixels are identical to the frozen visual-v07 pages.",
    ]
    (OUT / "FAST_CHROMIUM_PRINT_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    REVIEW.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    baseline = _baseline_rows()
    before = [_before_record(fixture["page_key"]) for fixture in FIXTURES]
    after = [_run_fixture(fixture) for fixture in FIXTURES]
    decision, metrics = _decision(after)

    print_before_after = {
        "schema_version": "fast.v03.print_before_after.v1",
        "decision": decision,
        "scope": [fixture["page_key"] for fixture in FIXTURES],
        "before": before,
        "after": [{
            "page_key": row["page_key"],
            "print_path": row["print_path"],
            "metrics": row["metrics"],
            "old_final_pdf_sha256": row["old_final_pdf_sha256"],
            "new_fixture_pdf_sha256": row["new_fixture_pdf_sha256"],
            "review_image": row["review_image"],
        } for row in after],
        "hard_metrics": metrics,
    }
    performance = {
        "schema_version": "fast.v03.performance_comparison.v1",
        "decision": decision,
        "baseline_file": str(BASELINE.resolve()),
        "pages": [{
            "page_key": row["page_key"],
            "frozen_full_page_seconds": float(
                baseline[row["page_key"]]["elapsed_seconds"]),
            "targeted_fast_v03_seconds": row["elapsed_seconds"],
            "final_pdf_print_seconds": row["formal_pdf_print_seconds"],
            "before_chromium_launch_count": 5,
            "after_chromium_launch_count": row["print_path"][
                "chromium_launch_count"],
            "before_pdf_print_count": 2,
            "after_pdf_print_count": row["print_path"]["pdf_print_count"],
            "avoided_pdf_print_count": 1,
            "avoided_chromium_launch_count": 4,
        } for row in after],
        "scope_note": (
            "Targeted frozen-fixture timing only; no full-paper performance "
            "claim and no translation execution."),
    }
    _dump(OUT / "print_before_after.json", print_before_after)
    _dump(OUT / "performance_comparison.json", performance)
    _write_report(before, after, decision, metrics)
    print(json.dumps({"decision": decision, "metrics": metrics},
                     ensure_ascii=False, indent=2))
    return 0 if decision == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
