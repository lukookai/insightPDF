"""Low-overhead live progress reporting for the FAST production path.

The reporter is deliberately independent of PDF, Chromium, and QA code.  It
only measures monotonic time, prints flushed terminal messages, and appends
flushed JSON Lines records.  Production work reports stage boundaries through
this module; the module never discovers or re-runs work on its own.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Self, TextIO

Clock = Callable[[], float]


def _elapsed_label(seconds: float) -> str:
    value = max(0, int(seconds))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _stage_summary_name(stage: str) -> str:
    return {
        "pdf_parse/page_model": "pdf_parse",
        "translation/cache": "translation",
        "ownership/math/slots": "ownership_math_slots",
        "html/layout": "layout",
        "chromium_render": "chromium",
        "pdf_finalize": "finalize",
    }.get(stage, stage)


@dataclass
class _ActiveStage:
    name: str
    started: float
    page: int | None
    page_count: int | None
    stop: threading.Event
    heartbeat: threading.Thread | None = None


class LiveProgressReporter:
    """Thread-safe terminal + JSONL reporter with a stage heartbeat.

    ``time.perf_counter`` is used for every elapsed duration.  Wall-clock time
    appears only in the machine-readable ``timestamp`` field.
    """

    def __init__(
        self,
        output_dir: str | Path,
        *,
        terminal: bool = True,
        heartbeat_interval_s: float = 10.0,
        stream: TextIO | None = None,
        clock: Clock = time.perf_counter,
    ) -> None:
        if heartbeat_interval_s <= 0:
            raise ValueError("heartbeat_interval_s must be positive")
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.output_dir / "progress.jsonl"
        self._log = self.log_path.open("a", encoding="utf-8", buffering=1)
        self._terminal = terminal
        self._stream = stream or sys.stdout
        self._heartbeat_interval_s = float(heartbeat_interval_s)
        self._clock = clock
        self._started = self._clock()
        self._lock = threading.RLock()
        self._active: _ActiveStage | None = None
        self._closed = False
        self._durations: dict[str, float] = {}
        self._page_started: dict[int, float] = {}
        self._page_durations: dict[int, float] = {}
        self._current_page: tuple[int, int] | None = None

    @property
    def total_elapsed_s(self) -> float:
        return max(0.0, self._clock() - self._started)

    @property
    def durations(self) -> dict[str, float]:
        with self._lock:
            return dict(self._durations)

    @property
    def page_durations(self) -> dict[int, float]:
        with self._lock:
            return dict(self._page_durations)

    def _prefix(self, total_elapsed_s: float | None = None) -> str:
        value = self.total_elapsed_s if total_elapsed_s is None \
            else total_elapsed_s
        return f"[{_elapsed_label(value)}]"

    def _write_terminal(self, line: str) -> None:
        if self._terminal:
            print(line, file=self._stream, flush=True)

    def _append(self, payload: dict[str, Any]) -> None:
        page = payload.pop("page", None)
        page_elapsed = payload.pop("page_elapsed_s", None)
        if page_elapsed is None and page in self._page_started:
            page_elapsed = max(
                0.0, self._clock() - self._page_started[page])
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_elapsed_s": round(self.total_elapsed_s, 6),
            "event": payload.pop("event"),
            "stage": payload.pop("stage", None),
            "page": page,
            "page_count": payload.pop("page_count", None),
            "stage_elapsed_s": round(float(
                payload.pop("stage_elapsed_s", 0.0)), 6),
            "page_elapsed_s": (
                round(float(page_elapsed), 6)
                if page_elapsed is not None else None),
            "status": payload.pop("status", None),
            **payload,
        }
        self._log.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._log.flush()

    def _heartbeat_loop(self, active: _ActiveStage) -> None:
        while not active.stop.wait(self._heartbeat_interval_s):
            with self._lock:
                if self._closed or self._active is not active:
                    return
                now = self._clock()
                stage_elapsed = max(0.0, now - active.started)
                total_elapsed = max(0.0, now - self._started)
                page_label = ""
                if active.page is not None and active.page_count is not None:
                    page_label = f"page {active.page}/{active.page_count} "
                self._write_terminal(
                    f"{self._prefix(total_elapsed)} RUNNING "
                    f"{page_label}{active.name}\n"
                    f"{' ' * 19}stage_elapsed={stage_elapsed:.1f}s\n"
                    f"{' ' * 19}total_elapsed={total_elapsed:.1f}s")
                self._append({
                    "event": "RUNNING", "stage": active.name,
                    "page": active.page, "page_count": active.page_count,
                    "stage_elapsed_s": stage_elapsed, "status": "running",
                })

    def start_stage(
        self,
        stage: str,
        *,
        page: int | None = None,
        page_count: int | None = None,
        **details: Any,
    ) -> None:
        with self._lock:
            if self._active is not None:
                raise RuntimeError(
                    f"stage {self._active.name!r} is already running")
            now = self._clock()
            active = _ActiveStage(
                name=stage, started=now, page=page, page_count=page_count,
                stop=threading.Event())
            self._active = active
            page_label = ""
            if page is not None and page_count is not None:
                page_label = f"page {page}/{page_count} "
            self._write_terminal(
                f"{self._prefix()} START  {page_label}{stage}")
            self._append({
                "event": "START", "stage": stage, "page": page,
                "page_count": page_count, "stage_elapsed_s": 0.0,
                "status": "running", **details,
            })
            thread = threading.Thread(
                target=self._heartbeat_loop, args=(active,),
                name=f"fast-progress-{stage}", daemon=True)
            active.heartbeat = thread
            thread.start()

    def finish_stage(self, *, status: str = "pass", **details: Any) -> float:
        with self._lock:
            active = self._active
            if active is None:
                raise RuntimeError("no stage is running")
            active.stop.set()
            elapsed = max(0.0, self._clock() - active.started)
            key = _stage_summary_name(active.name)
            self._durations[key] = self._durations.get(key, 0.0) + elapsed
            self._active = None
            page_label = ""
            if active.page is not None and active.page_count is not None:
                page_label = f"page {active.page}/{active.page_count} "
            self._write_terminal(
                f"{self._prefix()} DONE   {page_label}{active.name:<28}"
                f" {elapsed:.1f}s")
            self._append({
                "event": "DONE", "stage": active.name,
                "page": active.page, "page_count": active.page_count,
                "stage_elapsed_s": elapsed, "status": status, **details,
            })
        if active.heartbeat is not None \
                and active.heartbeat is not threading.current_thread():
            active.heartbeat.join(timeout=1.0)
        return elapsed

    @contextmanager
    def stage(
        self,
        stage: str,
        *,
        page: int | None = None,
        page_count: int | None = None,
        **details: Any,
    ) -> Iterator[None]:
        self.start_stage(stage, page=page, page_count=page_count, **details)
        yield
        self.finish_stage()

    def transition(
        self,
        stage: str,
        *,
        page: int | None = None,
        page_count: int | None = None,
        done_details: dict[str, Any] | None = None,
        **start_details: Any,
    ) -> None:
        with self._lock:
            has_active = self._active is not None
        if has_active:
            self.finish_stage(**(done_details or {}))
        self.start_stage(
            stage, page=page, page_count=page_count, **start_details)

    def set_stage_context(
        self, *, page: int | None, page_count: int | None
    ) -> None:
        """Update heartbeat context without creating a timing boundary."""
        with self._lock:
            if self._active is None:
                raise RuntimeError("no stage is running")
            self._active.page = page
            self._active.page_count = page_count

    def start_page(self, page: int, page_count: int) -> None:
        with self._lock:
            self._page_started[page] = self._clock()
            self._current_page = (page, page_count)
            self._write_terminal(
                f"{self._prefix()} START  page {page}/{page_count}")
            self._append({
                "event": "START", "stage": "page",
                "page": page, "page_count": page_count,
                "stage_elapsed_s": 0.0, "status": "running",
            })

    def finish_page(
        self, page: int, page_count: int, *, status: str = "pass"
    ) -> float:
        with self._lock:
            started = self._page_started.pop(page, self._clock())
            elapsed = max(0.0, self._clock() - started)
            self._page_durations[page] = elapsed
            if self._current_page == (page, page_count):
                self._current_page = None
            self._write_terminal(
                f"{self._prefix()} DONE   page {page}/{page_count:<18}"
                f" {elapsed:.1f}s")
            self._append({
                "event": "DONE", "stage": "page", "page": page,
                "page_count": page_count, "stage_elapsed_s": elapsed,
                "page_elapsed_s": elapsed,
                "status": status,
            })
            return elapsed

    def info(
        self,
        stage: str,
        *,
        page: int | None = None,
        page_count: int | None = None,
        message: str | None = None,
        status: str = "running",
        **details: Any,
    ) -> None:
        with self._lock:
            active = self._active
            stage_elapsed = (
                max(0.0, self._clock() - active.started)
                if active is not None and active.name == stage else 0.0)
            display = message or stage
            self._write_terminal(f"{self._prefix()}        {display}")
            self._append({
                "event": "INFO", "stage": stage, "page": page,
                "page_count": page_count,
                "stage_elapsed_s": stage_elapsed, "status": status,
                **details,
            })

    def callback(self, **payload: Any) -> None:
        """Consume the optional callback contract used by page production."""
        event = str(payload.pop("event")).upper()
        stage = str(payload.pop("stage"))
        page = payload.pop("page", None)
        page_count = payload.pop("page_count", None)
        if event == "START":
            self.start_stage(
                stage, page=page, page_count=page_count, **payload)
        elif event == "DONE":
            self.finish_stage(**payload)
        elif event == "INFO":
            self.info(
                stage, page=page, page_count=page_count, **payload)
        else:
            raise ValueError(f"unsupported progress callback event: {event}")

    def fail(
        self,
        error: BaseException | str,
        *,
        page: int | None = None,
        page_count: int | None = None,
        stage: str | None = None,
    ) -> None:
        with self._lock:
            active = self._active
            if active is not None:
                active.stop.set()
                actual_stage = stage or active.name
                actual_page = page if page is not None else active.page
                actual_page_count = (
                    page_count if page_count is not None
                    else active.page_count)
                stage_elapsed = max(0.0, self._clock() - active.started)
                self._active = None
            else:
                current_page = self._current_page
                actual_page = (
                    page if page is not None
                    else (current_page[0] if current_page else None))
                actual_page_count = (
                    page_count if page_count is not None
                    else (current_page[1] if current_page else None))
                actual_stage = stage or (
                    "page" if actual_page is not None else "pipeline")
                stage_elapsed = 0.0
            message = f"{type(error).__name__}: {error}" \
                if isinstance(error, BaseException) else str(error)
            self._write_terminal(f"{self._prefix()} FAILED")
            self._write_terminal(f"{' ' * 19}page={actual_page}")
            self._write_terminal(f"{' ' * 19}stage={actual_stage}")
            self._write_terminal(
                f"{' ' * 19}total_elapsed={self.total_elapsed_s:.1f}s")
            self._write_terminal(
                f"{' ' * 19}stage_elapsed={stage_elapsed:.1f}s")
            self._write_terminal(f"{' ' * 19}error={message}")
            self._append({
                "event": "FAILED", "stage": actual_stage,
                "page": actual_page, "page_count": actual_page_count,
                "stage_elapsed_s": stage_elapsed, "status": "failed",
                "error": message,
            })
        if active is not None and active.heartbeat is not None \
                and active.heartbeat is not threading.current_thread():
            active.heartbeat.join(timeout=1.0)

    def complete(
        self,
        *,
        pages: int,
        output: str | Path,
        cache_hit: int | None = None,
        cache_miss: int | None = None,
    ) -> dict[str, Any]:
        total = self.total_elapsed_s
        with self._lock:
            slowest_page = None
            slowest_time = 0.0
            if self._page_durations:
                slowest_page, slowest_time = max(
                    self._page_durations.items(), key=lambda item: item[1])
            self._write_terminal(f"{self._prefix(total)} COMPLETE")
            self._write_terminal(f"{' ' * 19}pages={pages}")
            self._write_terminal(f"{' ' * 19}total={total:.1f}s")
            self._write_terminal(f"{' ' * 19}output={Path(output).resolve()}")
            self._write_terminal("")
            self._write_terminal("DURATION RANKING")
            self._write_terminal(f"  TOTAL              {total:.1f}s")
            required = (
                "pdf_parse", "translation", "layout", "typography",
                "chromium", "fast_gate", "finalize")
            names = set(required) | set(self._durations)
            ranked = sorted(
                ((name, self._durations.get(name, 0.0)) for name in names),
                key=lambda item: item[1], reverse=True)
            for name, seconds in ranked:
                self._write_terminal(f"  {name:<18} {seconds:.1f}s")
            self._write_terminal(f"  slowest_page       {slowest_page}")
            self._write_terminal(f"  slowest_page_time  {slowest_time:.1f}s")
            summary = {
                "pages": pages, "total_elapsed_s": round(total, 6),
                "output": str(Path(output).resolve()),
                "durations_s": {
                    key: round(value, 6)
                    for key, value in self._durations.items()},
                "slowest_page": slowest_page,
                "slowest_page_time_s": round(slowest_time, 6),
                "cache_hit": cache_hit, "cache_miss": cache_miss,
            }
            self._append({
                "event": "COMPLETE", "stage": "complete", "page": None,
                "page_count": pages, "stage_elapsed_s": 0.0,
                "status": "pass", **summary,
            })
            return summary

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._active is not None:
                self._active.stop.set()
            self._closed = True
            self._log.flush()
            self._log.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


__all__ = ["LiveProgressReporter"]
