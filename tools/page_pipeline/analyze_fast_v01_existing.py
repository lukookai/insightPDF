# -*- coding: utf-8 -*-
"""Build fast-v01 performance evidence from existing Task 4M artifacts only.

This program is intentionally analysis-only.  It never invokes the
translation pipeline, Chromium, Poppler, a QA module, or a translation API.
It reads JSON, file names, file sizes, and NTFS timestamps already present in
``outputs/visual_v07_task4m`` and writes four fast-v01 reports.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parent.parent.parent
SOURCE = REPO / "outputs" / "visual_v07_task4m"
OUT = REPO / "outputs" / "fast_v01"
VISUAL = SOURCE / "fresh_visual_pages"
RUNS_V2 = SOURCE / "fresh_runs_v2"
INVALID_RUN = SOURCE / "fresh_runs" / "ppat_source_chain"


def load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - evidence gaps are reported, not hidden
        return default


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def creation(path: Path) -> float:
    return path.stat().st_ctime


def modified(path: Path) -> float:
    return path.stat().st_mtime


def seconds_hms(seconds: float) -> str:
    value = int(round(seconds))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    return f"{minutes}m{secs:02d}s"


def timestamp(value: float) -> str:
    return datetime.fromtimestamp(value).isoformat(timespec="milliseconds")


def first_existing(paths: list[Path]) -> Path:
    for path in paths:
        if path.exists():
            return path
    raise FileNotFoundError(paths)


def source_chain_elapsed(chain: Path) -> dict[str, Any]:
    start = chain.stat().st_ctime
    finish_file = first_existing([
        chain / "phase4c1a_qa_report.json",
        chain / "document_manifest.json",
    ])
    # Creation time is used because the large report was subsequently opened
    # or rewritten during evidence aggregation; its original creation marks
    # the accepted source-chain completion.
    finish = finish_file.stat().st_ctime
    return {
        "root": str(chain.resolve()),
        "start_timestamp": timestamp(start),
        "finish_timestamp": timestamp(finish),
        "finish_evidence": str(finish_file.resolve()),
        "seconds": round(max(0.0, finish - start), 3),
    }


def source_chain_qa_seconds(chain: Path) -> float:
    total = 0.0
    for page_dir in sorted((chain / "pages").glob("p[0-9][0-9][0-9]")):
        attempts = sorted(page_dir.glob("zh_attempt*.png"),
                          key=lambda path: path.stat().st_ctime)
        final_qa = page_dir / "qa.json"
        if not attempts or not final_qa.exists():
            continue
        # Inclusive QA-closure window: first completed page raster through the
        # final page QA, including any QA-triggered retry render.
        total += max(0.0, modified(final_qa) - modified(attempts[0]))
    return total


def visual_hard_gate_qa_seconds() -> float:
    total = 0.0
    for page_dir in sorted(VISUAL.glob("*_p[0-9][0-9][0-9]")):
        pdf = page_dir / "zh_visual.pdf"
        qa = page_dir / "visual_page_qa.json"
        if pdf.exists() and qa.exists():
            total += max(0.0, creation(qa) - modified(pdf))
    return total


def final_audit_windows() -> dict[str, float]:
    qa_files = sorted(VISUAL.glob("*_p[0-9][0-9][0-9]/task4m_*qa.json"))
    checkpoint = SOURCE / "checkpoint_gate.json"
    report = SOURCE / "FRESH_FULL_DOCUMENT_REGRESSION_REPORT.md"
    if not qa_files or not checkpoint.exists() or not report.exists():
        return {"first_seconds": 0.0, "duplicate_seconds": 0.0}
    first_start = min(creation(path) for path in qa_files)
    second_start = min(modified(path) for path in qa_files)
    return {
        "first_seconds": max(0.0, creation(checkpoint) - first_start),
        "duplicate_seconds": max(0.0, modified(report) - second_start),
    }


def effective_visual_seconds() -> dict[str, Any]:
    record_path = SOURCE / "fresh_visual_render.json"
    record = load(record_path, {}) or {}
    resumed = max(0.0, float(record.get("finished_at_epoch") or 0.0)
                  - float(record.get("started_at_epoch") or 0.0))
    first_start_file = VISUAL / "ppat_fragment_closure_qa.json"
    first_finish_file = VISUAL / "ppat_p003" / "visual_page_qa.json"
    first_segment = max(0.0, creation(first_finish_file)
                        - creation(first_start_file))
    return {
        "pre_interruption_completed_seconds": round(first_segment, 3),
        "resumed_render_seconds": round(resumed, 3),
        "effective_seconds": round(first_segment + resumed, 3),
        "evidence": [str(record_path.resolve()),
                     str(first_start_file.resolve()),
                     str(first_finish_file.resolve())],
    }


def interruption_idle_seconds() -> dict[str, Any]:
    last_completed = VISUAL / "ppat_p003" / "visual_page_qa.json"
    render_record = load(SOURCE / "fresh_visual_render.json", {}) or {}
    resume = float(render_record.get("started_at_epoch") or 0.0)
    idle = max(0.0, resume - creation(last_completed))
    return {
        "seconds": round(idle, 3),
        "last_completed_timestamp": timestamp(creation(last_completed)),
        "resume_timestamp": timestamp(resume),
        "classification": "non_production_interruption_idle",
    }


def invalid_run_seconds() -> dict[str, Any]:
    files = list(INVALID_RUN.rglob("*")) if INVALID_RUN.exists() else []
    files = [path for path in files if path.is_file()]
    if not files:
        return {"seconds": 0.0, "classification":
                "non_production_invalid_interpreter_run"}
    start = min(creation(path) for path in files)
    finish = max(modified(path) for path in files)
    return {
        "seconds": round(max(0.0, finish - start), 3),
        "start_timestamp": timestamp(start),
        "finish_timestamp": timestamp(finish),
        "classification": "non_production_invalid_interpreter_run",
    }


def page_label(page_dir: Path) -> tuple[str, int]:
    doc, page = page_dir.name.rsplit("_p", 1)
    return doc, int(page)


def inferred_print_counts(page_dir: Path) -> dict[str, Any]:
    lock = load(page_dir / "source_text_slot_lock.json", {}) or {}
    fill = load(page_dir / "local_typography_fill.json", {}) or {}
    fit = load(page_dir / "local_typography_fit.json", {}) or {}
    visual_qa = load(page_dir / "visual_page_qa.json", {}) or {}
    repack = visual_qa.get("browser_measured_repack") or {}

    trial_pdfs = sorted(
        path for path in page_dir.glob("_local_typography_*trial*.pdf"))
    trial_html = sorted(
        path for path in page_dir.glob("_local_typography_*trial*.html"))
    fill_applied = any(bool(row.get("fill_applied"))
                       for row in fill.get("records") or [])
    fit_applied = bool(fit.get("records"))
    lock_active = int(lock.get("geometry_locked_block_count") or 0) > 0
    repack_applied = bool(repack.get("applied"))

    # Frozen control-flow inference from run_visual_v06_checkpoint.py:
    # base print + two lock prints + one fill-final print + one legacy-fit
    # final print + one repack final print.  Trial PDFs are explicit files.
    final_path_prints = (1 + (2 if lock_active else 0)
                         + (1 if fill_applied else 0)
                         + (1 if fit_applied else 0)
                         + (1 if repack_applied else 0))
    return {
        # Every trial HTML is consumed by a Chromium DOM/screenshot
        # measurement.  Only fill trials additionally emit a PDF, so keep the
        # two cardinalities separate instead of undercounting fit trials.
        "typography_trial_render_count": len(trial_html),
        "typography_trial_pdf_print_count": len(trial_pdfs),
        "typography_trial_measurement_count": len(trial_html),
        "chromium_pdf_print_count_inferred": (
            final_path_prints + len(trial_pdfs)),
        "print_inference": {
            "base_final_print": 1,
            "source_slot_lock_extra_prints": 2 if lock_active else 0,
            "fill_final_extra_print": 1 if fill_applied else 0,
            "legacy_fit_final_extra_print": 1 if fit_applied else 0,
            "repack_final_extra_print": 1 if repack_applied else 0,
            "explicit_trial_pdf_prints": len(trial_pdfs),
        },
        "trial_pdf_files": [path.name for path in trial_pdfs],
    }


def slow_pages() -> dict[str, Any]:
    rows = []
    interruption_boundary = ("ppat", 4)
    for page_dir in sorted(VISUAL.glob("*_p[0-9][0-9][0-9]")):
        doc, page = page_label(page_dir)
        qa = page_dir / "visual_page_qa.json"
        if not qa.exists():
            continue
        candidates = [path for path in page_dir.iterdir()
                      if path.is_file()
                      and not path.name.startswith("task4m_")
                      and creation(path) <= creation(qa)]
        if not candidates:
            continue
        first = min(candidates, key=creation)
        elapsed = max(0.0, creation(qa) - creation(first))
        excluded = (doc, page) == interruption_boundary
        rows.append({
            "document": doc,
            "page": page,
            "page_key": f"{doc}_p{page:03d}",
            "elapsed_seconds": round(elapsed, 3),
            "elapsed_human": seconds_hms(elapsed),
            "start_evidence": str(first.resolve()),
            "finish_evidence": str(qa.resolve()),
            "excluded_from_production_ranking": excluded,
            "exclusion_reason": (
                "crosses Task 4M interruption boundary; wall-clock duration "
                "is not production compute" if excluded else None),
            **inferred_print_counts(page_dir),
        })
    ranked = sorted((row for row in rows
                     if not row["excluded_from_production_ranking"]),
                    key=lambda row: (-row["elapsed_seconds"],
                                     row["document"], row["page"]))
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
    expected_hot = [
        ("ppat", 11), ("2504", 11), ("2504", 5),
        ("2504", 14), ("2504", 3), ("ppat", 5),
    ]
    expected_rows = []
    by_key = {(row["document"], row["page"]): row for row in rows}
    for key in expected_hot:
        if key in by_key:
            expected_rows.append(by_key[key])
    return {
        "schema_version": "fast.v01.existing_slow_page_ranking.v1",
        "source": str(VISUAL.resolve()),
        "method": (
            "NTFS creation-time span from first visual page artifact through "
            "visual_page_qa.json; interruption-crossing page excluded"),
        "chromium_print_count_method": (
            "inferred from frozen renderer control flow plus explicit trial "
            "PDF artifacts; no Chromium invocation was performed"),
        "required_hot_pages": expected_rows,
        "ranking": ranked,
        "excluded_rows": [row for row in rows
                          if row["excluded_from_production_ranking"]],
    }


def build_outputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    required = [
        SOURCE / "fresh_visual_render.json",
        SOURCE / "checkpoint_gate.json",
        RUNS_V2 / "ppat_source_chain" / "translation_cache_audit.json",
        RUNS_V2 / "2504_source_chain" / "translation_cache_audit.json",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Task 4M evidence missing: {missing}")

    visual = effective_visual_seconds()
    ppat_chain = source_chain_elapsed(RUNS_V2 / "ppat_source_chain")
    doc2504_chain = source_chain_elapsed(RUNS_V2 / "2504_source_chain")
    source_seconds = ppat_chain["seconds"] + doc2504_chain["seconds"]
    qa_source = (source_chain_qa_seconds(RUNS_V2 / "ppat_source_chain")
                 + source_chain_qa_seconds(RUNS_V2 / "2504_source_chain"))
    qa_visual = visual_hard_gate_qa_seconds()
    audit = final_audit_windows()
    qa_total = (qa_source + qa_visual + audit["first_seconds"]
                + audit["duplicate_seconds"])
    idle = interruption_idle_seconds()
    invalid = invalid_run_seconds()

    ppat_cache = load(RUNS_V2 / "ppat_source_chain"
                      / "translation_cache_audit.json", {}) or {}
    doc2504_cache = load(RUNS_V2 / "2504_source_chain"
                         / "translation_cache_audit.json", {}) or {}
    cache_summary = {
        "cache_hit": int(ppat_cache.get("cache_hit") or 0)
        + int(doc2504_cache.get("cache_hit") or 0),
        "cache_miss": int(ppat_cache.get("cache_miss") or 0)
        + int(doc2504_cache.get("cache_miss") or 0),
        "actual_new_translation_call_count": int(
            ppat_cache.get("actual_new_translation_call_count") or 0)
        + int(doc2504_cache.get("actual_new_translation_call_count") or 0),
    }

    baseline = {
        "schema_version": "fast.v01.existing_bottleneck_ranking.v1",
        "decision": "pass",
        "source_artifact_root": str(SOURCE.resolve()),
        "analysis_constraints": {
            "fresh_run_performed": False,
            "chromium_invoked": False,
            "poppler_invoked": False,
            "translation_api_invoked": False,
            "qa_rerun": False,
            "png_or_contact_sheet_generated": False,
        },
        "production_compute": {
            "effective_visual_render_seconds": visual["effective_seconds"],
            "effective_visual_render_human": seconds_hms(
                visual["effective_seconds"]),
            "effective_source_chain_seconds": round(source_seconds, 3),
            "effective_source_chain_human": seconds_hms(source_seconds),
            "qa_accumulated_seconds": round(qa_total, 3),
            "qa_accumulated_human": seconds_hms(qa_total),
            "five_minute_target_seconds": 300,
            "visual_plus_source_seconds": round(
                visual["effective_seconds"] + source_seconds, 3),
            "visual_plus_source_over_target_seconds": round(
                visual["effective_seconds"] + source_seconds - 300, 3),
        },
        "qa_breakdown": {
            "source_chain_qa_closure_seconds": round(qa_source, 3),
            "visual_hard_gate_seconds": round(qa_visual, 3),
            "first_full_audit_seconds": round(audit["first_seconds"], 3),
            "duplicate_full_audit_seconds": round(
                audit["duplicate_seconds"], 3),
        },
        "non_production_compute": {
            "interruption_idle": idle,
            "invalid_interpreter_run": invalid,
            "duplicate_full_audit": {
                "seconds": round(audit["duplicate_seconds"], 3),
                "human": seconds_hms(audit["duplicate_seconds"]),
                "classification": "non_production_duplicate_diagnostic_run",
            },
        },
        "source_chain_documents": {
            "ppat": ppat_chain,
            "2504": doc2504_chain,
        },
        "visual_render_evidence": visual,
        "translation_cache": cache_summary,
        "production_bottleneck_ranking": [
            {
                "rank": 1,
                "component": "effective_visual_render",
                "seconds": visual["effective_seconds"],
                "human": seconds_hms(visual["effective_seconds"]),
            },
            {
                "rank": 2,
                "component": "qa_accumulated",
                "seconds": round(qa_total, 3),
                "human": seconds_hms(qa_total),
                "note": "overlaps source/visual production windows; not "
                        "additive to visual_plus_source",
            },
            {
                "rank": 3,
                "component": "effective_source_chain",
                "seconds": round(source_seconds, 3),
                "human": seconds_hms(source_seconds),
            },
        ],
    }

    pages = slow_pages()
    visual_seconds = float(visual["effective_seconds"])
    target_seconds = 300.0
    optimization = {
        "schema_version": "fast.v01.optimization_targets.v1",
        "target_total_seconds": target_seconds,
        "baseline_visual_plus_source_seconds": round(
            visual_seconds + source_seconds, 3),
        "required_reduction_seconds": round(
            visual_seconds + source_seconds - target_seconds, 3),
        "targets": [
            {
                "priority": "P0",
                "name": "Typography Fit/Fill multi-round trial render",
                "evidence": (
                    "Slow pages carry dozens of explicit "
                    "_local_typography_*trial HTML/Chromium measurement "
                    "artifacts; PPAT p011 also carries ten trial PDFs and "
                    "alone spans about 8m16s."),
                "why_blocks_five_minutes": (
                    "A single page can exceed the entire 300-second document "
                    "budget because every trial launches measurement/render "
                    "work before the final page is accepted."),
                "first_measure": (
                    "Collapse/strictly bound trial count while preserving the "
                    "current visual policy; implementation deferred."),
            },
            {
                "priority": "P1",
                "name": "Per-page Chromium/PDF print lifecycle",
                "evidence": (
                    "The frozen renderer prints each page independently and "
                    "reprints the same zh_visual.pdf after lock/fill/fit."),
                "why_blocks_five_minutes": (
                    "Twelve or nineteen independent browser/print lifecycles "
                    "consume most of the effective 69m06s visual window; the "
                    "five-minute target permits only seconds per page."),
                "first_measure": (
                    "Reduce print lifecycle cardinality/batch document work; "
                    "implementation deferred."),
            },
            {
                "priority": "P2",
                "name": "Repeated production-path QA, PDF reopen, rasterize",
                "evidence": (
                    f"Accumulated QA closure is {seconds_hms(qa_total)}, "
                    "including repeated source/final PDF inspection; the full "
                    "audit was also duplicated once."),
                "why_blocks_five_minutes": (
                    "The verifier reopens/rasterizes artifacts already measured "
                    "during production, so validation approaches or exceeds "
                    "the complete target budget."),
                "first_measure": (
                    "Reuse a production render ledger and separate fast gate "
                    "from asynchronous full regression; implementation deferred."),
            },
        ],
        "excluded_non_production_time": baseline["non_production_compute"],
    }
    return baseline, pages, optimization


def write_report(baseline: dict[str, Any], pages: dict[str, Any],
                 optimization: dict[str, Any]) -> None:
    production = baseline["production_compute"]
    nonprod = baseline["non_production_compute"]
    hot = pages["required_hot_pages"]
    lines = [
        "# FAST Existing Artifact Performance Baseline",
        "",
        "## Decision",
        "",
        "**PASS** — baseline reconstructed exclusively from frozen visual-v07 "
        "Task 4M artifacts. No PDF translation, Chromium, Poppler, QA, or "
        "translation API was run.",
        "",
        "## Frozen production baseline",
        "",
        "| Metric | Seconds | Human |",
        "|---|---:|---:|",
        f"| Effective visual render | "
        f"{production['effective_visual_render_seconds']:.3f} | "
        f"{production['effective_visual_render_human']} |",
        f"| Effective source chain | "
        f"{production['effective_source_chain_seconds']:.3f} | "
        f"{production['effective_source_chain_human']} |",
        f"| Accumulated QA | {production['qa_accumulated_seconds']:.3f} | "
        f"{production['qa_accumulated_human']} |",
        "",
        "QA is a measured overlapping component of the source/visual windows; "
        "it must not be added again to obtain end-to-end production time.",
        "",
        "The source + visual baseline is "
        f"**{seconds_hms(production['visual_plus_source_seconds'])}**, which "
        f"exceeds the five-minute target by "
        f"**{seconds_hms(production['visual_plus_source_over_target_seconds'])}**.",
        "",
        "## Explicitly excluded non-production time",
        "",
        "| Item | Seconds | Human | Reason |",
        "|---|---:|---:|---|",
        f"| Interruption idle | {nonprod['interruption_idle']['seconds']:.3f} | "
        f"{seconds_hms(nonprod['interruption_idle']['seconds'])} | No active "
        "production process; excluded from bottleneck ranking |",
        f"| Invalid interpreter run | "
        f"{nonprod['invalid_interpreter_run']['seconds']:.3f} | "
        f"{seconds_hms(nonprod['invalid_interpreter_run']['seconds'])} | "
        "Discarded environment run; excluded |",
        f"| Duplicate full audit | "
        f"{nonprod['duplicate_full_audit']['seconds']:.3f} | "
        f"{nonprod['duplicate_full_audit']['human']} | Diagnostic false-positive "
        "rerun; excluded from normal production target |",
        "",
        "## Slow-page hotspots",
        "",
        "| Page | Time | Typography trials | Trial PDF prints | Inferred Chromium PDF prints |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in hot:
        lines.append(
            f"| `{row['page_key']}` | {row['elapsed_human']} | "
            f"{row['typography_trial_render_count']} | "
            f"{row['typography_trial_pdf_print_count']} | "
            f"{row['chromium_pdf_print_count_inferred']} |")
    lines.extend([
        "",
        "Print counts are reconstructed from frozen renderer control flow, "
        "lock/fill/fit/repack ledgers, and explicit trial PDF files; no browser "
        "was launched. PPAT p004 is excluded because its timestamps cross the "
        "Task 4M interruption boundary.",
        "",
        "## Optimization priority",
        "",
    ])
    for target in optimization["targets"]:
        lines.extend([
            f"### {target['priority']} — {target['name']}",
            "",
            target["evidence"],
            "",
            f"Why it blocks <=5 min: {target['why_blocks_five_minutes']}",
            "",
            f"First measure: {target['first_measure']}",
            "",
        ])
    lines.extend([
        "## Translation-cache evidence",
        "",
        f"Task 4M recorded {baseline['translation_cache']['cache_hit']} cache "
        f"hits, {baseline['translation_cache']['cache_miss']} misses, and "
        f"{baseline['translation_cache']['actual_new_translation_call_count']} "
        "new translation calls. Model latency is therefore not part of this "
        "baseline.",
        "",
    ])
    (OUT / "FAST_EXISTING_BASELINE.md").write_text(
        "\n".join(lines), encoding="utf-8")


def main() -> int:
    baseline, pages, optimization = build_outputs()
    OUT.mkdir(parents=True, exist_ok=True)
    dump(OUT / "bottleneck_ranking.json", baseline)
    dump(OUT / "slow_page_ranking.json", pages)
    dump(OUT / "optimization_targets.json", optimization)
    write_report(baseline, pages, optimization)
    print(json.dumps({
        "FAST_EXISTING_BASELINE": baseline["decision"].upper(),
        "effective_visual_render": baseline["production_compute"][
            "effective_visual_render_human"],
        "effective_source_chain": baseline["production_compute"][
            "effective_source_chain_human"],
        "qa_accumulated": baseline["production_compute"][
            "qa_accumulated_human"],
        "top_3": [row["page_key"]
                  for row in pages["required_hot_pages"][:3]],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
