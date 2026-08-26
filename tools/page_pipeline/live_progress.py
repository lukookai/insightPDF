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


_STAGE_DISPLAY_NAMES = {
    "preflight": "预检",
    "pdf_parse/page_model": "PDF 解析 / 页面模型",
    "translation/cache": "翻译 / 缓存",
    "ownership/math/slots": "文本归属 / 数学公式 / 文本槽",
    "html/layout": "HTML 构建 / 页面布局",
    "typography": "字体排版",
    "chromium_render": "浏览器生成 PDF",
    "fast_gate": "快速质量检查",
    "pdf_finalize": "合并最终 PDF",
    "document_partition": "文档分区",
    "page": "页面",
    "pipeline": "生产流程",
    "complete": "全部完成",
}

_SUMMARY_DISPLAY_NAMES = {
    "pdf_parse": "PDF 解析",
    "translation": "翻译",
    "ownership_math_slots": "归属 / 数学 / 文本槽",
    "layout": "页面布局",
    "typography": "字体排版",
    "chromium": "浏览器生成 PDF",
    "fast_gate": "快速质量检查",
    "finalize": "合并最终 PDF",
    "preflight": "预检",
}


def _stage_display_name(stage: str) -> str:
    """Return a Chinese terminal label while keeping stable JSONL keys."""
    return _STAGE_DISPLAY_NAMES.get(stage, stage)


def _summary_display_name(stage: str) -> str:
    return _SUMMARY_DISPLAY_NAMES.get(stage, _stage_display_name(stage))


def _human_info_message(
    stage: str,
    message: str | None,
    status: str,
    details: dict[str, Any],
) -> str:
    if stage == "translation/cache" \
            and ("cache_hit" in details or "cache_miss" in details):
        return (
            f"缓存命中={int(details.get('cache_hit') or 0)} "
            f"缓存缺失={int(details.get('cache_miss') or 0)}")
    if stage == "translation/cache" \
            and ("completed" in details or "total" in details):
        return (
            f"翻译进度：{int(details.get('completed') or 0)}/"
            f"{int(details.get('total') or 0)}")
    if stage == "fast_gate":
        decision = "通过" if status == "pass" else (
            "失败" if status == "failed" else "检查中")
        return f"快速质量检查：{decision}"
    return message or _stage_display_name(stage)


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
                    page_label = (
                        f"第 {active.page}/{active.page_count} 页 · ")
                self._write_terminal(
                    f"{self._prefix(total_elapsed)} 处理中 "
                    f"{page_label}{_stage_display_name(active.name)}\n"
                    f"{' ' * 19}当前阶段已用时={stage_elapsed:.1f}秒\n"
                    f"{' ' * 19}总用时={total_elapsed:.1f}秒")
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
                page_label = f"第 {page}/{page_count} 页 · "
            self._write_terminal(
                f"{self._prefix()} 开始  "
                f"{page_label}{_stage_display_name(stage)}")
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
                page_label = (
                    f"第 {active.page}/{active.page_count} 页 · ")
            self._write_terminal(
                f"{self._prefix()} 完成  {page_label}"
                f"{_stage_display_name(active.name)}  {elapsed:.1f}秒")
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
                f"{self._prefix()} 开始  第 {page}/{page_count} 页")
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
                f"{self._prefix()} 完成  第 {page}/{page_count} 页  "
                f"{elapsed:.1f}秒")
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
            display = _human_info_message(
                stage, message, status, details)
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
            self._write_terminal(f"{self._prefix()} 失败")
            self._write_terminal(f"{' ' * 19}页面={actual_page}")
            self._write_terminal(
                f"{' ' * 19}阶段={_stage_display_name(actual_stage)}")
            self._write_terminal(
                f"{' ' * 19}总用时={self.total_elapsed_s:.1f}秒")
            self._write_terminal(
                f"{' ' * 19}当前阶段已用时={stage_elapsed:.1f}秒")
            self._write_terminal(f"{' ' * 19}错误={message}")
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
            self._write_terminal(f"{self._prefix(total)} 全部完成")
            self._write_terminal(f"{' ' * 19}页数={pages}")
            self._write_terminal(f"{' ' * 19}总用时={total:.1f}秒")
            self._write_terminal(
                f"{' ' * 19}输出文件={Path(output).resolve()}")
            self._write_terminal("")
            self._write_terminal("耗时排名")
            self._write_terminal(f"  总用时             {total:.1f}秒")
            required = (
                "pdf_parse", "translation", "layout", "typography",
                "chromium", "fast_gate", "finalize")
            names = set(required) | set(self._durations)
            ranked = sorted(
                ((name, self._durations.get(name, 0.0)) for name in names),
                key=lambda item: item[1], reverse=True)
            for name, seconds in ranked:
                self._write_terminal(
                    f"  {_summary_display_name(name):<18} "
                    f"{seconds:.1f}秒")
            self._write_terminal(f"  最慢页面           {slowest_page}")
            self._write_terminal(
                f"  最慢页面耗时       {slowest_time:.1f}秒")
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
