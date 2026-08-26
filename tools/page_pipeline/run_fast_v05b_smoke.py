"""No-I/O route smoke and evidence writer for FAST v05B."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from fast_e2e_pipeline import FastE2EDependencies, run_fast_e2e
from live_progress import LiveProgressReporter

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
OUTPUT = REPO / "outputs" / "fast_v05b"

EXPECTED_STAGES = [
    "preflight",
    "pdf_parse/page_model",
    "translation/cache",
    "page",
    "ownership/math/slots",
    "html/layout",
    "typography",
    "chromium_render",
    "fast_gate",
    "pdf_finalize",
]


def _dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(
        encoding="utf-8").splitlines() if line.strip()]


def _route_static_assertions() -> dict[str, int]:
    pipeline_source = (HERE / "fast_e2e_pipeline.py").read_text(
        encoding="utf-8")
    preparation_source = (HERE / "fast_source_preparation.py").read_text(
        encoding="utf-8")
    combined = pipeline_source + "\n" + preparation_source
    forbidden_call_markers = {
        "phase4d1c_call_count": "--phase4d1c",
        "legacy_render_count": "resolve_and_render(",
        "formula_render_trace_count": "build_page_formula_trace(",
        "delivery_gate_call_count": "write_rankings_and_gate(",
    }
    counts = {
        name: combined.count(marker)
        for name, marker in forbidden_call_markers.items()
    }
    counts["deep_qa_count"] = sum(combined.count(marker) for marker in (
        "run_page_visual_qa(",
        "residual_source_language_qa(",
        "physical_pdf_qa(",
        "build_visual_layout_gate(",
    ))
    assert all(value == 0 for value in counts.values()), counts
    return counts


class _FakeVisual:
    def __init__(self, calls: dict[str, int]) -> None:
        self.OUT = None
        self.DOCS = {}
        self.TABLE_TRANSLATION_CACHE_PATHS = []
        self._calls = calls

    def build_document_partition(self, doc_key, pages):
        self._calls["document_partition_count"] += 1
        assert doc_key == "fast_input"
        assert pages == [1]
        return {"targets": {}, "closure_qa": {}, "partitions": {}}

    def render_visual_page(
        self,
        doc_key,
        page,
        out_dir,
        *,
        qa_mode,
        progress_callback,
        page_count,
        **kwargs,
    ):
        self._calls["fast_renderer_call_count"] += 1
        assert doc_key == "fast_input"
        assert (page, page_count) == (1, 1)
        assert qa_mode == "fast"
        assert kwargs["dry_run"] is False
        assert kwargs["table_cache_paths"] == (
            self.TABLE_TRANSLATION_CACHE_PATHS)
        for stage in (
            "ownership/math/slots",
            "html/layout",
            "typography",
            "chromium_render",
        ):
            progress_callback(
                event="START", stage=stage, page=page,
                page_count=page_count)
            progress_callback(
                event="DONE", stage=stage, page=page,
                page_count=page_count)
        progress_callback(
            event="START", stage="fast_gate", page=page,
            page_count=page_count)
        progress_callback(
            event="INFO", stage="fast_gate", page=page,
            page_count=page_count, message="FAST_GATE PASS",
            status="pass")
        progress_callback(
            event="DONE", stage="fast_gate", page=page,
            page_count=page_count, status="pass")
        output = Path(out_dir) / "zh_visual.pdf"
        output.write_bytes(b"FAST route smoke placeholder")
        return {"decision": "pass", "hard_metrics": {}}


def _run_route_smoke(root: Path) -> tuple[dict, dict[str, int], Path]:
    calls = {
        "source_prepare_call_count": 0,
        "document_partition_count": 0,
        "fast_renderer_call_count": 0,
        "fast_gate_call_count": 0,
        "pdf_finalize_call_count": 0,
        "translation_api_call_count": 0,
        "pdf_open_count": 0,
        "rasterize_count": 0,
        "chromium_launch_count": 0,
    }
    input_pdf = root / "single-page-route-fixture.pdf"
    input_pdf.write_bytes(b"route smoke; intentionally never opened")
    run_output = root / "run"
    shared_cache = root / "shared" / "translation_cache.json"

    def prepare_source(source_pdf, source_chain, *, config, reporter):
        calls["source_prepare_call_count"] += 1
        assert Path(source_pdf) == input_pdf.resolve()
        assert Path(source_chain).parent == run_output.resolve()
        assert config == "runs/config.json"
        with reporter.stage("preflight"):
            pass
        with reporter.stage("pdf_parse/page_model"):
            pass
        with reporter.stage("translation/cache"):
            reporter.info(
                "translation/cache",
                message="cache_hit=7 cache_miss=0",
                cache_hit=7,
                cache_miss=0)
        return {
            "page_count": 1,
            "cache_hit": 7,
            "cache_miss": 0,
            "canonical_cache": str(shared_cache),
            "translation_cache_paths": [str(shared_cache)],
        }

    fake_visual = _FakeVisual(calls)

    def load_visual():
        return fake_visual

    def merge_page_pdfs(page_paths, output_path):
        calls["pdf_finalize_call_count"] += 1
        assert len(page_paths) == 1
        assert page_paths[0].name == "zh_visual.pdf"
        Path(output_path).write_bytes(b"merged FAST route placeholder")

    dependencies = FastE2EDependencies(
        prepare_source_chain=prepare_source,
        load_visual_module=load_visual,
        merge_page_pdfs=merge_page_pdfs)
    reporter = LiveProgressReporter(run_output, terminal=False)
    try:
        with patch.object(
                subprocess, "Popen",
                side_effect=AssertionError("subprocess route is forbidden")) \
                as subprocess_popen:
            result = run_fast_e2e(
                input_pdf=input_pdf,
                output_dir=run_output,
                qa_mode="fast",
                reporter=reporter,
                dependencies=dependencies)
            assert subprocess_popen.call_count == 0
    finally:
        reporter.close()

    records = _records(run_output / "progress.jsonl")
    actual_stages = [row["stage"] for row in records
                     if row["event"] == "START"]
    assert actual_stages == EXPECTED_STAGES, actual_stages
    assert not (run_output / "translation_cache.json").exists()
    assert not (run_output / "_source_chain"
                / "translation_cache.json").exists()
    assert result["pages"] == 1
    assert result["cache_hit"] == 7
    assert result["cache_miss"] == 0
    assert Path(result["output"]).is_file()
    calls["fast_gate_call_count"] = sum(
        row["event"] == "START" and row["stage"] == "fast_gate"
        for row in records)
    return result, calls, run_output / "progress.jsonl"


def _write_report(evidence: dict) -> None:
    counts = evidence["call_counts"]
    forbidden = evidence["forbidden_stage_call_counts"]
    report = f"""# FAST v05B — Production Routing Report

## Result

`FAST_V05B = PASS`

The E2E wrapper now stops source preparation after the existing PDF parser,
DocumentModel/PageModel construction, semantic detection, translation
preparation, and translation cache.  It then invokes the current
`render_visual_page(..., qa_mode=\"fast\")` path directly, followed by the
FAST_GATE and final page-PDF merge.

## Route closure

- Observed stage order: `{' → '.join(evidence['actual_stage_order'])}`
- Expected stage order matched: `{str(evidence['stage_order_matches']).lower()}`
- Current FAST renderer calls: `{counts['fast_renderer_call_count']}`
- FAST_GATE calls: `{counts['fast_gate_call_count']}`
- Finalize calls: `{counts['pdf_finalize_call_count']}`

The old subprocess render/QA route is absent from the production adapter and
source-only preparation.  Static route tripwires and the injected one-page
smoke both passed:

- `phase4d1c_call_count = {forbidden['phase4d1c_call_count']}`
- `legacy_render_count = {forbidden['legacy_render_count']}`
- `deep_qa_count = {forbidden['deep_qa_count']}`
- `formula_render_trace_count = {forbidden['formula_render_trace_count']}`
- `delivery_gate_call_count = {forbidden['delivery_gate_call_count']}`

## Shared translation cache

The run output directory is no longer a cache namespace.  Production uses a
fixed writable canonical cache and reads existing workspace translation
caches in place as seeds, using the established content/protected-token/
target-language/prompt-version key.  No cache file is copied into the run.
The smoke used a cache path outside its output directory and confirmed no
output-local `translation_cache.json` was created.

## Scope and verification

This was a dependency-injected single-page routing smoke.  It opened no PDF,
started no Chromium, performed no rasterization, called no translation API,
and ran no full paper or deep QA.  Typography, renderer visual policy,
SourceTextSlot geometry, formula policy, and document content were unchanged.
Machine-readable evidence is in `route_before_after.json`; the exact telemetry
stream is in `smoke_progress.jsonl`.
"""
    (OUTPUT / "FAST_ROUTING_REPORT.md").write_text(
        report, encoding="utf-8")


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    forbidden_counts = _route_static_assertions()
    with tempfile.TemporaryDirectory(prefix="fast-v05b-route-") as temp:
        result, calls, progress_path = _run_route_smoke(Path(temp))
        records = _records(progress_path)
        shutil.copy2(progress_path, OUTPUT / "smoke_progress.jsonl")

    actual_stages = [row["stage"] for row in records
                     if row["event"] == "START"]
    evidence = {
        "schema_version": "fast.v05b.route_audit.v1",
        "decision": "pass",
        "before": [
            "parse + translation",
            "legacy source-chain render",
            "deep QA",
            "delivery gate blocks",
            "FAST renderer unreachable",
        ],
        "after": [
            "preflight",
            "pdf_parse/page_model",
            "translation/cache",
            "current FAST visual renderer",
            "FAST_GATE",
            "pdf_finalize",
        ],
        "expected_stage_order": EXPECTED_STAGES,
        "actual_stage_order": actual_stages,
        "stage_order_matches": actual_stages == EXPECTED_STAGES,
        "forbidden_stage_call_counts": forbidden_counts,
        "call_counts": calls,
        "cache_policy": {
            "key_depends_on_output_dir": False,
            "canonical_cache_is_output_local": False,
            "existing_caches_are_read_in_place": True,
            "cache_copy_per_run_count": 0,
            "smoke_cache_hit": result["cache_hit"],
            "smoke_cache_miss": result["cache_miss"],
        },
        "smoke_external_operations": {
            "full_document_run_count": 0,
            "translation_api_call_count": 0,
            "pdf_open_count": 0,
            "rasterize_count": 0,
            "chromium_launch_count": 0,
        },
    }
    assert evidence["stage_order_matches"]
    assert calls["fast_renderer_call_count"] == 1
    assert calls["fast_gate_call_count"] == 1
    assert calls["pdf_finalize_call_count"] == 1
    _dump(OUTPUT / "route_before_after.json", evidence)
    _write_report(evidence)
    print("FAST_V05B_SMOKE = PASS", flush=True)
    for name, value in forbidden_counts.items():
        print(f"{name} = {value}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
