#!/usr/bin/env python
"""Stable human CLI for the existing FAST end-to-end production path."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
PIPELINE_DIR = REPO / "tools" / "page_pipeline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from fast_e2e_pipeline import run_fast_e2e
from live_progress import LiveProgressReporter
from production_fast_gate import QA_MODES

Pipeline = Callable[..., dict[str, Any]]


def _configure_utf8_console() -> None:
    """Keep Chinese progress readable in Windows terminals and log pipes."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace",
                        line_buffering=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the existing FAST PDF production path with live "
                    "progress telemetry.")
    parser.add_argument("--input", required=True, help="source PDF")
    parser.add_argument("--output-dir", required=True,
                        help="directory for PDF and progress.jsonl")
    parser.add_argument("--qa-mode", choices=QA_MODES, default="fast",
                        help="default: fast (FAST_GATE only)")
    parser.add_argument("--progress", action="store_true",
                        help="show flushed live terminal progress")
    parser.add_argument("--config", default="runs/config.json",
                        help="existing translation backend config")
    parser.add_argument(
        "--translation-cache", choices=("off", "on"), default="off",
        help="default: off; use on only for an explicit cache-enabled run")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    pipeline: Pipeline = run_fast_e2e,
    reporter_factory: Callable[..., LiveProgressReporter] = (
        LiveProgressReporter),
) -> int:
    _configure_utf8_console()
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir).resolve()
    reporter = reporter_factory(
        output_dir, terminal=bool(args.progress))
    try:
        result = pipeline(
            input_pdf=Path(args.input).resolve(),
            output_dir=output_dir,
            qa_mode=args.qa_mode,
            reporter=reporter,
            config=args.config,
            translation_cache_enabled=args.translation_cache == "on",
        )
        reporter.complete(
            pages=int(result["pages"]),
            output=result["output"],
            cache_hit=result.get("cache_hit"),
            cache_miss=result.get("cache_miss"),
        )
        return 0
    except KeyboardInterrupt as exc:
        reporter.fail(
            exc,
            page=getattr(exc, "page", None),
            page_count=getattr(exc, "page_count", None),
            stage=getattr(exc, "stage", None),
        )
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI must report every failure
        reporter.fail(
            exc,
            page=getattr(exc, "page", None),
            page_count=getattr(exc, "page_count", None),
            stage=getattr(exc, "stage", None),
        )
        return 1
    finally:
        reporter.close()


if __name__ == "__main__":
    raise SystemExit(main())
