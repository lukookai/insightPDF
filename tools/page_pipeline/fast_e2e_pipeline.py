"""Thin end-to-end adapter for the real FAST production route.

The adapter reuses source preparation and the current visual page renderer;
it contains no parsing, translation, layout, typography, rendering, or QA
policy of its own.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

if TYPE_CHECKING:
    from live_progress import LiveProgressReporter


class FastE2EError(RuntimeError):
    """Production failure carrying the exact progress context."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        page: int | None = None,
        page_count: int | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.page = page
        self.page_count = page_count


def _merge_page_pdfs(page_paths: list[Path], output_path: Path) -> None:
    """Finalize formal one-page FAST outputs into the delivery PDF."""
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


def _prepare_source_chain(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from fast_source_preparation import prepare_fast_source_chain
    return prepare_fast_source_chain(*args, **kwargs)


def _load_visual_module() -> Any:
    import run_visual_v06_checkpoint
    return run_visual_v06_checkpoint


@dataclass(frozen=True)
class FastE2EDependencies:
    """Injectable route boundaries used by the no-I/O routing smoke."""

    prepare_source_chain: Callable[..., dict[str, Any]] = (
        _prepare_source_chain)
    load_visual_module: Callable[[], Any] = _load_visual_module
    merge_page_pdfs: Callable[[list[Path], Path], None] = _merge_page_pdfs


def run_fast_e2e(
    *,
    input_pdf: str | Path,
    output_dir: str | Path,
    qa_mode: str = "fast",
    reporter: LiveProgressReporter,
    config: str | Path = "runs/config.json",
    dependencies: FastE2EDependencies | None = None,
) -> dict[str, Any]:
    """Prepare source models, render FAST pages, gate, and finalize."""
    source_pdf = Path(input_pdf).resolve()
    destination = Path(output_dir).resolve()
    if not source_pdf.is_file():
        raise FastE2EError(
            f"input PDF does not exist: {source_pdf}", stage="preflight")
    destination.mkdir(parents=True, exist_ok=True)
    source_chain = destination / "_source_chain"
    visual_root = destination / "pages"
    final_pdf = destination / f"{source_pdf.stem}_zh_fast.pdf"
    route = dependencies or FastE2EDependencies()

    source = route.prepare_source_chain(
        source_pdf,
        source_chain,
        config=config,
        reporter=reporter)
    page_count = int(source.get("page_count") or 0)
    if page_count <= 0:
        raise FastE2EError(
            "preflight found no physical pages", stage="preflight")

    # Configure, but do not copy, the current FAST page production function.
    visual = route.load_visual_module()
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
        Path(path) for path in source.get("translation_cache_paths", [])]

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
                str(result["error"]),
                stage="ownership/math/slots",
                page=page,
                page_count=page_count)
        if result.get("decision") != "pass":
            raise FastE2EError(
                f"FAST_GATE blocked page {page}/{page_count}: "
                f"{result.get('hard_metrics')}",
                stage="fast_gate",
                page=page,
                page_count=page_count)
        reporter.finish_page(page, page_count)
        page_pdf = page_dir / "zh_visual.pdf"
        if not page_pdf.is_file():
            raise FastE2EError(
                f"final page PDF missing: {page_pdf}",
                stage="chromium_render",
                page=page,
                page_count=page_count)
        page_pdfs.append(page_pdf)

    with reporter.stage("pdf_finalize"):
        route.merge_page_pdfs(page_pdfs, final_pdf)

    return {
        "pages": page_count,
        "output": str(final_pdf),
        "cache_hit": int(source.get("cache_hit") or 0),
        "cache_miss": int(source.get("cache_miss") or 0),
        "qa_mode": qa_mode,
        "canonical_translation_cache": source.get("canonical_cache"),
    }


__all__ = ["FastE2EDependencies", "FastE2EError", "run_fast_e2e"]
