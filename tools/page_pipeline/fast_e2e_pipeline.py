"""Thin end-to-end adapter over the existing document and FAST page paths.

No translation, layout, typography, rendering, or QA policy lives here.  The
adapter invokes the repository's established generic source-document entry,
then invokes ``run_visual_v06_checkpoint.render_visual_page`` for the final
FAST pages and merges those formal one-page PDFs.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

if TYPE_CHECKING:
    from live_progress import LiveProgressReporter


_RAW_PAGE_RE = re.compile(r"raw page\s+(\d+)/(\d+):", re.IGNORECASE)
_CACHE_RE = re.compile(
    r"translation cache:\s*hit=(\d+)\s+miss=(\d+)", re.IGNORECASE)
_TRANSLATION_PROGRESS_RE = re.compile(
    r"translation progress:\s*(\d+)/(\d+)", re.IGNORECASE)
_SOURCE_RENDER_RE = re.compile(
    r"render page\s+(\d+)/(\d+)", re.IGNORECASE)


class FastE2EError(RuntimeError):
    """Production failure carrying the exact progress context."""

    def __init__(self, message: str, *, stage: str,
                 page: int | None = None,
                 page_count: int | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.page = page
        self.page_count = page_count


def _load(path: str | Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _source_chain_command(
    source_pdf: Path,
    source_chain: Path,
    *,
    config: str | Path,
) -> list[str]:
    """Return the existing generic production command without altering it."""
    return [
        sys.executable,
        "-u",
        str(HERE / "run_document.py"),
        "--pdf",
        str(source_pdf),
        "--out",
        str(source_chain),
        "--config",
        str(Path(config)),
        "--phase4d1c",
        "--typography-profile",
        str(REPO / "outputs" / "phase4d2a_typography_audit"
            / "balanced_chinese_profile.json"),
    ]


def _run_existing_source_chain(
    source_pdf: Path,
    source_chain: Path,
    *,
    config: str | Path,
    reporter: LiveProgressReporter,
) -> dict[str, int]:
    """Stream the existing source-chain process and expose its boundaries.

    Parsing these already-stable status lines only drives telemetry.  It does
    not choose, retry, skip, or modify any production operation.
    """
    source_chain.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONUTF8"] = "1"
    command = _source_chain_command(
        source_pdf, source_chain, config=config)
    tail: deque[str] = deque(maxlen=40)
    cache_hit = 0
    cache_miss = 0
    current_stage = "preflight"
    reporter.start_stage(current_stage)
    process = subprocess.Popen(
        command,
        cwd=str(REPO),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip()
        tail.append(line)
        raw_match = _RAW_PAGE_RE.search(line)
        if raw_match:
            if current_stage != "pdf_parse/page_model":
                reporter.transition("pdf_parse/page_model")
                current_stage = "pdf_parse/page_model"
            reporter.set_stage_context(
                page=int(raw_match.group(1)),
                page_count=int(raw_match.group(2)))
            reporter.info(
                current_stage,
                page=int(raw_match.group(1)),
                page_count=int(raw_match.group(2)),
                message=(f"page {raw_match.group(1)}/"
                         f"{raw_match.group(2)} page_model"))
            continue
        cache_match = _CACHE_RE.search(line)
        if cache_match:
            if current_stage != "translation/cache":
                reporter.transition("translation/cache")
                current_stage = "translation/cache"
            cache_hit = int(cache_match.group(1))
            cache_miss = int(cache_match.group(2))
            reporter.info(
                current_stage,
                message=f"cache_hit={cache_hit} cache_miss={cache_miss}",
                cache_hit=cache_hit,
                cache_miss=cache_miss)
            continue
        translation_match = _TRANSLATION_PROGRESS_RE.search(line)
        if translation_match:
            if current_stage != "translation/cache":
                reporter.transition("translation/cache")
                current_stage = "translation/cache"
            reporter.info(
                current_stage,
                message=(f"translation {translation_match.group(1)}/"
                         f"{translation_match.group(2)}"),
                completed=int(translation_match.group(1)),
                total=int(translation_match.group(2)))
            continue
        render_match = _SOURCE_RENDER_RE.search(line)
        if render_match:
            if current_stage != "source_chain_render":
                reporter.transition("source_chain_render")
                current_stage = "source_chain_render"
            reporter.set_stage_context(
                page=int(render_match.group(1)),
                page_count=int(render_match.group(2)))
            reporter.info(
                current_stage,
                page=int(render_match.group(1)),
                page_count=int(render_match.group(2)),
                message=(f"source chain page {render_match.group(1)}/"
                         f"{render_match.group(2)}"))
    return_code = process.wait()
    if return_code != 0:
        detail = "\n".join(tail)
        raise RuntimeError(
            f"existing source production pipeline exited {return_code}"
            + (f"\n{detail}" if detail else ""))
    reporter.finish_stage(cache_hit=cache_hit, cache_miss=cache_miss)
    audit = _load(source_chain / "translation_cache_audit.json", {}) or {}
    preflight = _load(source_chain / "preflight.json", {}) or {}
    return {
        "cache_hit": int(audit.get("cache_hit", cache_hit) or 0),
        "cache_miss": int(audit.get("cache_miss", cache_miss) or 0),
        "page_count": int(preflight.get("page_count") or 0),
    }


def _merge_page_pdfs(page_paths: list[Path], output_path: Path) -> None:
    """Finalize existing one-page FAST outputs into the delivery PDF."""
    import pymupdf

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged = pymupdf.open()
    try:
        for path in page_paths:
            page_document = pymupdf.open(str(path))
            try:
                merged.insert_pdf(page_document)
            finally:
                page_document.close()
        merged.save(str(output_path), garbage=4, deflate=True)
    finally:
        merged.close()


def run_fast_e2e(
    *,
    input_pdf: str | Path,
    output_dir: str | Path,
    qa_mode: str = "fast",
    reporter: LiveProgressReporter,
    config: str | Path = "runs/config.json",
) -> dict[str, Any]:
    """Run the existing generic source chain and existing FAST visual path."""
    source_pdf = Path(input_pdf).resolve()
    destination = Path(output_dir).resolve()
    if not source_pdf.is_file():
        raise FastE2EError(
            f"input PDF does not exist: {source_pdf}", stage="preflight")
    destination.mkdir(parents=True, exist_ok=True)
    source_chain = destination / "_source_chain"
    visual_root = destination / "pages"
    final_pdf = destination / f"{source_pdf.stem}_zh_fast.pdf"

    cache = _run_existing_source_chain(
        source_pdf, source_chain, config=config, reporter=reporter)
    page_count = int(cache.get("page_count") or 0)
    if page_count <= 0:
        raise FastE2EError(
            "preflight found no physical pages", stage="preflight")

    # Configure, but do not copy, the current FAST page production function.
    import run_visual_v06_checkpoint as visual

    doc_key = "fast_input"
    visual.OUT = destination
    visual.DOCS = {
        doc_key: {
            "pdf": str(source_pdf),
            "src": source_chain,
            "default_pages": list(range(1, page_count + 1)),
        }
    }
    visual.TABLE_TRANSLATION_CACHE_PATHS = [
        source_chain / "translation_cache.json"]

    with reporter.stage("document_partition"):
        partition = visual.build_document_partition(
            doc_key, list(range(1, page_count + 1)))

    page_pdfs: list[Path] = []
    for page in range(1, page_count + 1):
        page_dir = visual_root / f"p{page:03d}"
        page_dir.mkdir(parents=True, exist_ok=True)
        reporter.start_page(page, page_count)
        result = visual.render_visual_page(
            doc_key,
            page,
            page_dir,
            fragment_targets=partition["targets"],
            dry_run=False,
            table_cache_paths=visual.TABLE_TRANSLATION_CACHE_PATHS,
            qa_mode=qa_mode,
            progress_callback=reporter.callback,
            page_count=page_count,
        )
        if result.get("error"):
            raise FastE2EError(
                str(result["error"]), stage="ownership/math/slots",
                page=page, page_count=page_count)
        if result.get("decision") != "pass":
            raise FastE2EError(
                f"FAST_GATE blocked page {page}/{page_count}: "
                f"{result.get('hard_metrics')}", stage="fast_gate",
                page=page, page_count=page_count)
        reporter.finish_page(page, page_count)
        page_pdf = page_dir / "zh_visual.pdf"
        if not page_pdf.is_file():
            raise FastE2EError(
                f"final page PDF missing: {page_pdf}",
                stage="chromium_render", page=page,
                page_count=page_count)
        page_pdfs.append(page_pdf)

    with reporter.stage("pdf_finalize"):
        _merge_page_pdfs(page_pdfs, final_pdf)

    return {
        "pages": page_count,
        "output": str(final_pdf),
        "cache_hit": cache["cache_hit"],
        "cache_miss": cache["cache_miss"],
        "qa_mode": qa_mode,
    }


__all__ = ["FastE2EError", "run_fast_e2e"]
