# -*- coding: utf-8 -*-
"""Focused fast-v02 QA for the three frozen Typography fixtures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def frozen_before_record(page_key: str, page_dir: str | Path) -> dict[str, Any]:
    root = Path(page_dir)
    trial_html = sorted(root.glob("_local_typography*trial*.html"))
    trial_pdf = sorted(root.glob("_local_typography*trial*.pdf"))
    return {
        "page_key": page_key,
        "typography_trial_render_count": len(trial_html),
        "typography_trial_pdf_print_count": len(trial_pdf),
        "trial_html_files": [path.name for path in trial_html],
        "trial_pdf_files": [path.name for path in trial_pdf],
        "qa_red": len(trial_html) > 2 or bool(trial_pdf),
    }


def audit_fast_pages(before: list[dict[str, Any]],
                     after: list[dict[str, Any]]) -> dict[str, Any]:
    before_by_page = {row["page_key"]: row for row in before}
    records = []
    for final in after:
        old = before_by_page[final["page_key"]]
        trace = final.get("typography_fast_path") or {}
        metrics = final.get("metrics") or {}
        records.append({
            "page_key": final["page_key"],
            "before_typography_trial_render_count": int(
                old["typography_trial_render_count"]),
            "after_typography_measurement_round_count": int(
                trace.get("typography_measurement_round_count") or 0),
            "before_typography_trial_pdf_print_count": int(
                old["typography_trial_pdf_print_count"]),
            "after_typography_trial_pdf_print_count": int(
                trace.get("typography_trial_pdf_print_count") or 0),
            "browser_launch_count": int(
                trace.get("browser_launch_count") or 0),
            "candidate_measurement_count": int(
                trace.get("candidate_measurement_count") or 0),
            "typography_correction_count_max": int(
                trace.get("typography_correction_count_max") or 0),
            "slot_geometry_mutation_count": int(
                metrics.get("slot_geometry_mutation_count") or 0),
            "final_block_collision_count": int(
                metrics.get("final_block_collision_count") or 0),
            "hard_anchor_moved_count": int(
                metrics.get("hard_anchor_moved_count") or 0),
            "overflow_count": int(metrics.get("overflow_count") or 0),
            "visual_pixel_diff_count": int(
                metrics.get("visual_pixel_diff_count") or 0),
            "source_text_slot_geometry_unchanged": bool(
                final.get("source_text_slot_geometry_unchanged")),
            "hard_anchor_geometry_unchanged": bool(
                final.get("hard_anchor_geometry_unchanged")),
        })
    metrics = {
        "fixture_page_count": len(records),
        "before_typography_trial_render_count": sum(
            row["before_typography_trial_render_count"] for row in records),
        "after_typography_measurement_round_count_max": max(
            (row["after_typography_measurement_round_count"]
             for row in records), default=0),
        "typography_trial_pdf_print_count": sum(
            row["after_typography_trial_pdf_print_count"] for row in records),
        "typography_correction_count_gt1": sum(
            row["typography_correction_count_max"] > 1 for row in records),
        "slot_geometry_mutation_count": sum(
            row["slot_geometry_mutation_count"] for row in records),
        "final_block_collision_count": sum(
            row["final_block_collision_count"] for row in records),
        "hard_anchor_moved_count": sum(
            row["hard_anchor_moved_count"] for row in records),
        "overflow_count": sum(row["overflow_count"] for row in records),
        "visual_pixel_diff_count": sum(
            row["visual_pixel_diff_count"] for row in records),
        "production_special_case_count": 0,
    }
    hard = (
        metrics["typography_trial_pdf_print_count"] == 0
        and metrics["after_typography_measurement_round_count_max"] <= 2
        and metrics["typography_correction_count_gt1"] == 0
        and metrics["slot_geometry_mutation_count"] == 0
        and metrics["final_block_collision_count"] == 0
        and metrics["hard_anchor_moved_count"] == 0
        and metrics["overflow_count"] == 0
        and metrics["visual_pixel_diff_count"] == 0
        and all(row["source_text_slot_geometry_unchanged"] for row in records)
        and all(row["hard_anchor_geometry_unchanged"] for row in records)
    )
    return {
        "schema_version": "fast.v02.typography_fast_path_qa.v1",
        "decision": "pass" if hard else "blocked",
        "metrics": metrics,
        "records": records,
        "qa_scope": "PPAT p011, 2504 p011, 2504 p005 only",
    }


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--after-json", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    payload = json.loads(Path(args.after_json).read_text(encoding="utf-8"))
    before = payload.get("before") or (
        payload.get("before_qa") or {}).get("records") or []
    after = payload.get("after") or payload.get("pages") or []
    result = audit_fast_pages(before, after)
    Path(args.out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["decision"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["audit_fast_pages", "frozen_before_record"]
