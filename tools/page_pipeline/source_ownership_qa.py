# -*- coding: utf-8 -*-
"""Hard gates for visual-v07 Task 4F source ownership resolution.

The gate is deliberately data-driven.  It consumes ownership/render evidence
produced by the Task 4F runner and never selects a page, document, span, or
piece of text in production code.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


BEFORE_REQUIRED_POSITIVE = (
    "figure_internal_duplicate_render_count",
)

AFTER_REQUIRED_ZERO = (
    "source_span_multi_primary_owner_count",
    "source_span_render_owner_count_gt1",
    "figure_internal_duplicate_render_count",
    "figure_internal_soft_text_owner_count",
    "erroneous_figure_internal_slot_count",
    "external_caption_misowned_count",
    "final_block_collision_count",
    "production_special_case_count",
)


def evaluate_source_ownership_gate(
        before_metrics: dict[str, Any], after_metrics: dict[str, Any]
        ) -> dict[str, Any]:
    """Evaluate the before-proof and the fixed-artifact hard metrics."""
    before_failures = [
        "%s must be > 0 (got %s)" % (name, before_metrics.get(name))
        for name in BEFORE_REQUIRED_POSITIVE
        if int(before_metrics.get(name) or 0) <= 0
    ]
    after_failures = [
        "%s must be 0 (got %s)" % (name, after_metrics.get(name))
        for name in AFTER_REQUIRED_ZERO
        if int(after_metrics.get(name) or 0) != 0
    ]
    return {
        "schema_version": "visual_v07.source_ownership_qa.v1",
        "decision": "pass" if not before_failures and not after_failures
                    else "blocked",
        "qa_first_before_proof_present": not before_failures,
        "before_metrics": before_metrics,
        "after_metrics": after_metrics,
        "before_failures": before_failures,
        "after_failures": after_failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    result = evaluate_source_ownership_gate(
        before.get("metrics") or before,
        after.get("metrics") or after,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["decision"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
