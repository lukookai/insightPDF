"""Source-only preparation for the FAST end-to-end production route.

This module is an orchestration layer over the existing parser, DocumentModel,
semantic-state, translation, and layout-context functions.  It deliberately
stops before every legacy render, delivery gate, and deep-QA entry point.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

if TYPE_CHECKING:
    from live_progress import LiveProgressReporter


class FastSourcePreparationError(RuntimeError):
    """Source-preparation failure with telemetry context."""

    def __init__(self, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.page = None
        self.page_count = None


def _dump(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _translation_cache_paths(
    canonical_path: str | Path | None = None,
) -> tuple[Path, list[Path]]:
    """Return one writable canonical cache plus existing read-only seeds.

    Both the canonical path and every cache key are independent of the run's
    output directory.  Existing caches are referenced in place and are never
    copied into a new run directory.
    """
    configured = canonical_path or os.environ.get(
        "FAST_TRANSLATION_CACHE_PATH")
    canonical = Path(configured).resolve() if configured else (
        REPO / "outputs" / "_fast_shared_translation_cache"
        / "translation_cache.json").resolve()

    candidates: list[Path] = []
    outputs = REPO / "outputs"
    if outputs.is_dir():
        candidates.extend(outputs.rglob("translation_cache.json"))
    configured_seeds = os.environ.get("FAST_TRANSLATION_CACHE_SEEDS", "")
    candidates.extend(
        Path(value).resolve()
        for value in configured_seeds.split(os.pathsep)
        if value.strip())

    unique: dict[Path, float] = {}
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved == canonical or not resolved.is_file():
            continue
        try:
            unique[resolved] = resolved.stat().st_mtime
        except OSError:
            continue
    seeds = [path for path, _ in sorted(
        unique.items(), key=lambda item: item[1], reverse=True)]
    return canonical, seeds


class SharedDocumentTranslationCache:
    """Composite view over existing caches with one shared writable store."""

    def __init__(
        self,
        cache_type: type,
        cache_key,
        *,
        canonical_path: str | Path | None = None,
    ) -> None:
        canonical, seeds = _translation_cache_paths(canonical_path)
        self.primary = cache_type(canonical)
        self.seed_caches = [cache_type(path) for path in seeds]
        self.path = self.primary.path
        self.data = self.primary.data
        self._cache_key = cache_key
        self._invalidated: set[str] = set()
        self.lookup_hit_by_path: dict[str, int] = {}
        self.lookup_miss_count = 0

    @property
    def source_paths(self) -> list[Path]:
        return [self.primary.path] + [cache.path for cache in self.seed_caches]

    def get(self, item: dict[str, Any]) -> str | None:
        key = self._cache_key(item)
        if key in self._invalidated:
            self.lookup_miss_count += 1
            return None
        for cache in (self.primary, *self.seed_caches):
            value = cache.get(item)
            if value is not None:
                label = str(cache.path.resolve())
                self.lookup_hit_by_path[label] = (
                    self.lookup_hit_by_path.get(label, 0) + 1)
                return value
        self.lookup_miss_count += 1
        return None

    def put(self, item: dict[str, Any], translation: str) -> None:
        self._invalidated.discard(self._cache_key(item))
        self.primary.put(item, translation)
        self.data = self.primary.data

    def delete(self, item: dict[str, Any]) -> bool:
        key = self._cache_key(item)
        self._invalidated.add(key)
        return bool(self.primary.delete(item))


def _build_document_layout_context(
    source_pdf: Path,
    document: dict[str, Any],
    *,
    run_document,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Reuse the established grid/context builders without running QA."""
    local_grids = [
        run_document.infer_page_grid(str(source_pdf), int(page["page"]) - 1)
        for page in document["pages"]
    ]
    models_by_page = {
        int(page["page"]): page for page in document["pages"]}
    layout_profile = (
        run_document.build_document_grid_profile_from_candidates(
            [{"page": grid["page"], "candidates": [grid]}
             for grid in local_grids],
            models_by_page))
    local_by_page = {int(grid["page"]): grid for grid in local_grids}

    typography_profile = None
    typography_profile_path = (
        REPO / "outputs" / "phase4d2a_typography_audit"
        / "balanced_chinese_profile.json")
    if typography_profile_path.is_file():
        from typography_profile import DocumentTypographyProfile
        typography_profile = DocumentTypographyProfile(
            str(typography_profile_path))

    page_context: dict[int, dict[str, Any]] = {}
    appendix_active = False
    for page in document["pages"]:
        page_number = int(page["page"])
        headings = [
            (region.get("payload") or {}).get("source_text", "").strip()
            for region in page.get("regions", [])
            if region.get("type") == "text"
            and "heading" in ((region.get("payload") or {}).get(
                "style_role") or "")
        ]
        if any(re.match(r"^[A-Z](?:\.|\s)", heading)
               for heading in headings):
            appendix_active = True
        grid, frontmatter, typography, resolution = (
            run_document.page_render_context(
                source_pdf,
                page_number - 1,
                profile=layout_profile,
                typo_profile=typography_profile,
                page_model=page,
                page_local_grid=local_by_page.get(page_number),
                return_resolution=True))
        grid = run_document.prepare_page_grid(
            page,
            grid,
            frontmatter=frontmatter,
            profile=layout_profile,
            appendix_active=appendix_active)
        page["page_layout_grid"] = grid
        page_context[page_number] = {
            "grid": grid,
            "frontmatter": frontmatter,
            "typography": typography,
            "grid_resolution": resolution,
        }
    return page_context, layout_profile


def prepare_fast_source_chain(
    source_pdf: str | Path,
    source_chain: str | Path,
    *,
    config: str | Path,
    reporter: LiveProgressReporter,
    canonical_cache_path: str | Path | None = None,
    translation_cache_enabled: bool = False,
    translation_provider=None,
) -> dict[str, Any]:
    """Build FAST renderer inputs and stop before legacy render/deep QA."""
    pdf_path = Path(source_pdf).resolve()
    destination = Path(source_chain).resolve()
    destination.mkdir(parents=True, exist_ok=True)

    from document_preflight import run_preflight

    with reporter.stage("preflight"):
        preflight, allowed = run_preflight(pdf_path, destination)
        if not allowed:
            raise FastSourcePreparationError(
                "source PDF failed production preflight", stage="preflight")

    with reporter.stage("pdf_parse/page_model"):
        # Importing the established orchestration module gives this thin path
        # the exact mature parser/model/translation functions.  None of its
        # legacy render or QA entry points are invoked here.
        import run_document
        from document_model import build_document_model
        from document_semantic_state import apply_document_semantics

        raw_pages, failures, page_count = run_document.build_raw_pages(
            pdf_path, destination, resume=False, scan_only=False)
        if failures or len(raw_pages) != page_count:
            raise FastSourcePreparationError(
                "page model build did not cover every physical page: "
                f"built={len(raw_pages)} expected={page_count} "
                f"failures={failures}",
                stage="pdf_parse/page_model")
        document = build_document_model(pdf_path, raw_pages)
        apply_document_semantics(document)
        page_context, layout_profile = _build_document_layout_context(
            pdf_path, document, run_document=run_document)
        _dump(destination / "document_layout_profile.json", layout_profile)
        for page in document["pages"]:
            page_number = int(page["page"])
            page_dir = destination / "pages" / f"p{page_number:03d}"
            _dump(page_dir / "stitched_page_model.json", page)
            _dump(page_dir / "page_grid.json",
                  page_context[page_number]["grid"])

    with reporter.stage("translation/cache"):
        config_path = Path(config)
        if not config_path.is_absolute():
            config_path = REPO / config_path
        cache = None
        if translation_cache_enabled:
            cache = SharedDocumentTranslationCache(
                run_document.DocumentTranslationCache,
                run_document._cache_key,
                canonical_path=canonical_cache_path)
        translations, _ = run_document.translate_document(
            document,
            cache,
            run_document.load_config(config_path),
            destination,
            dry_run=False,
            cache_enabled=translation_cache_enabled,
            translation_provider=translation_provider)
        for page in document["pages"]:
            page_number = int(page["page"])
            page_dir = destination / "pages" / f"p{page_number:03d}"
            page_translation = run_document.page_translations(
                page, translations)
            context = page_context[page_number]
            bottom_reserved = run_document.build_bottom_reserved_regions(
                page,
                page_translation,
                context["grid"],
                context["frontmatter"],
                context["typography"])
            # translate_document mutates table cells and fragment targets;
            # persist that canonical post-translation PageModel.
            _dump(page_dir / "stitched_page_model.json", page)
            _dump(page_dir / "translation.json", page_translation)
            _dump(page_dir / "fast_source_context.json", {
                "schema_version": "fast.source_context.v1",
                "bottom_reserved_regions": bottom_reserved,
            })

        cache_hit = int(document.get("cache_hit", 0) or 0)
        provider_item_count = int(
            document.get("new_translation_call_count", 0) or 0)
        cache_lookup_count = int(
            document.get("cache_lookup_count", 0) or 0)
        cache_read_count = int(document.get("cache_read_count", 0) or 0)
        cache_write_count = int(document.get("cache_write_count", 0) or 0)
        if translation_cache_enabled:
            reporter.info(
                "translation/cache",
                message=(f"cache_enabled=true cache_hit={cache_hit} "
                         f"provider_items={provider_item_count}"),
                translation_cache_enabled=True,
                cache_hit=cache_hit,
                provider_item_count=provider_item_count,
                canonical_cache=str(cache.path),
                cache_seed_count=len(cache.seed_caches))
        else:
            reporter.info(
                "translation/cache",
                message=("cache_enabled=false "
                         f"provider_items={provider_item_count}"),
                translation_cache_enabled=False,
                cache_hit=0,
                provider_item_count=provider_item_count,
                cache_lookup_count=0,
                cache_read_count=0,
                cache_write_count=0)

    _dump(destination / "fast_source_manifest.json", {
        "schema_version": "fast.source_manifest.v1",
        "source_pdf": str(pdf_path),
        "page_count": page_count,
        "route": "parse_document_model_translation_only",
        "legacy_render_executed": False,
        "deep_qa_executed": False,
        "translation_cache_enabled": bool(translation_cache_enabled),
        "cache_lookup_count": cache_lookup_count,
        "cache_read_count": cache_read_count,
        "cache_write_count": cache_write_count,
        "cache_hit": cache_hit,
        "canonical_translation_cache": (
            str(cache.path) if cache is not None else None),
        "translation_cache_seed_count": (
            len(cache.seed_caches) if cache is not None else 0),
    })
    return {
        "page_count": int(preflight.get("page_count") or page_count),
        "cache_hit": cache_hit,
        "cache_miss": provider_item_count,
        "provider_item_count": provider_item_count,
        "translation_cache_enabled": bool(translation_cache_enabled),
        "cache_lookup_count": cache_lookup_count,
        "cache_read_count": cache_read_count,
        "cache_write_count": cache_write_count,
        "canonical_cache": (str(cache.path) if cache is not None else None),
        "translation_cache_paths": (
            [str(path) for path in cache.source_paths]
            if cache is not None else []),
        "cache_hit_by_path": (
            dict(cache.lookup_hit_by_path) if cache is not None else {}),
    }


__all__ = [
    "FastSourcePreparationError",
    "SharedDocumentTranslationCache",
    "prepare_fast_source_chain",
]
