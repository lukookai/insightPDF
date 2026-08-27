"""Lightweight smoke checks for FAST v05A (no PDF/Chromium/QA work)."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TOOLS = REPO / "tools"
for path in (TOOLS, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import run_fast_e2e as cli
from live_progress import LiveProgressReporter

REQUIRED_FIELDS = {
    "timestamp", "total_elapsed_s", "event", "stage", "page",
    "page_count", "stage_elapsed_s", "page_elapsed_s", "status",
}


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(
        encoding="utf-8").splitlines() if line.strip()]


def _success_smoke(root: Path) -> None:
    output_dir = root / "success"
    input_path = root / "synthetic-input.pdf"
    input_path.write_bytes(b"telemetry smoke only; never opened")
    stream = io.StringIO()

    def reporter_factory(path, *, terminal):
        return LiveProgressReporter(
            path, terminal=terminal, heartbeat_interval_s=0.03,
            stream=stream)

    def fake_pipeline(*, input_pdf, output_dir, qa_mode, reporter, config,
                      translation_cache_enabled):
        assert input_pdf == input_path.resolve()
        assert qa_mode == "fast"
        assert translation_cache_enabled is False
        assert config == "runs/config.json"
        with reporter.stage("preflight"):
            pass
        with reporter.stage("pdf_parse/page_model"):
            pass
        with reporter.stage("translation/cache"):
            reporter.info(
                "translation/cache", message="cache_hit=4 cache_miss=0",
                cache_hit=4, cache_miss=0)
            # The producer is still running: this proves JSONL is flushed,
            # not merely written when the reporter closes.
            live_log = (Path(output_dir) / "progress.jsonl").read_text(
                encoding="utf-8")
            assert '"cache_hit": 4' in live_log
        reporter.start_page(1, 1)
        for stage in ("ownership/math/slots", "html/layout"):
            with reporter.stage(stage, page=1, page_count=1):
                pass
        with reporter.stage("typography", page=1, page_count=1):
            time.sleep(0.08)
        with reporter.stage("chromium_render", page=1, page_count=1):
            pass
        with reporter.stage("fast_gate", page=1, page_count=1):
            reporter.info(
                "fast_gate", page=1, page_count=1,
                message="FAST_GATE PASS", status="pass")
        reporter.finish_page(1, 1)
        with reporter.stage("pdf_finalize"):
            pass
        target = Path(output_dir) / "synthetic_zh_fast.pdf"
        target.write_bytes(b"not a PDF; smoke output placeholder")
        return {"pages": 1, "output": str(target),
                "cache_hit": 4, "cache_miss": 0}

    code = cli.main(
        ["--input", str(input_path), "--output-dir", str(output_dir),
         "--qa-mode", "fast", "--progress"],
        pipeline=fake_pipeline, reporter_factory=reporter_factory)
    assert code == 0
    text = stream.getvalue()
    assert "处理中 第 1/1 页 · 字体排版" in text
    assert "快速质量检查：通过" in text
    assert "全部完成" in text
    assert "最慢页面" in text
    records = _records(output_dir / "progress.jsonl")
    assert records
    assert all(REQUIRED_FIELDS <= set(row) for row in records)
    assert any(row["event"] == "RUNNING" for row in records)
    assert records[-1]["event"] == "COMPLETE"
    assert records[-1]["status"] == "pass"


def _failure_smoke(root: Path) -> None:
    output_dir = root / "failure"
    input_path = root / "failure-input.pdf"
    input_path.write_bytes(b"never opened")
    stream = io.StringIO()

    def reporter_factory(path, *, terminal):
        return LiveProgressReporter(
            path, terminal=terminal, heartbeat_interval_s=0.03,
            stream=stream)

    def fake_pipeline(**kwargs):
        reporter = kwargs["reporter"]
        reporter.start_stage("typography", page=2, page_count=3)
        raise RuntimeError("synthetic smoke failure")

    code = cli.main(
        ["--input", str(input_path), "--output-dir", str(output_dir),
         "--progress"],
        pipeline=fake_pipeline, reporter_factory=reporter_factory)
    assert code == 1
    text = stream.getvalue()
    assert "失败" in text
    assert "页面=2" in text
    assert "阶段=字体排版" in text
    assert "synthetic smoke failure" in text
    records = _records(output_dir / "progress.jsonl")
    assert records[-1]["event"] == "FAILED"
    assert records[-1]["page"] == 2
    assert records[-1]["stage"] == "typography"
    assert records[-1]["status"] == "failed"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="fast-v05a-smoke-") as temp:
        root = Path(temp)
        _success_smoke(root)
        _failure_smoke(root)
    print("FAST_V05A_SMOKE = PASS", flush=True)
    print("pdf_open_count = 0", flush=True)
    print("chromium_launch_count = 0", flush=True)
    print("rasterize_count = 0", flush=True)
    print("deep_qa_count = 0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
