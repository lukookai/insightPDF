from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
import re
from pathlib import Path

from pdf_translator.layout.align_segments import attach_translations
from pdf_translator.layout.aligned_html import build_aligned_html
from pdf_translator.layout.pdf_geometry import (
    extract_pdf_geometry,
    augment_native_images,
    merge_unlimited_ocr,
    save_layout,
)
from pdf_translator.layout.segment_regions import build_region_segments
from pdf_translator.layout.typography import fit_text_style
from pdf_translator.ocr.unlimited_transformers import UnlimitedOCRTransformersRunner
from pdf_translator.translation import DEFAULT_BASE_URL, translate_segments_file
from pdf_translator.translation.runner import is_technical_identifier


def _page_count(pdf: Path) -> int:
    result = subprocess.run(
        ["pdfinfo", str(pdf)], check=True, capture_output=True, text=True
    )
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError(f"无法读取 PDF 页数：{pdf}")


def _timed(timings: dict, name: str, callback):
    started = time.monotonic()
    result = callback()
    timings[name] = round(time.monotonic() - started, 3)
    return result


def _quality_report(document: dict, timings: dict) -> dict:
    fits = []
    translations = []
    overlaps = []
    for page in document.get("pages", []):
        page_items = [item for item in page.get("translations", []) if item.get("bbox")]
        for left_index, left in enumerate(page_items):
            ax1, ay1, ax2, ay2 = left["bbox"]
            left_area = max(1.0, (ax2 - ax1) * (ay2 - ay1))
            for right in page_items[left_index + 1 :]:
                bx1, by1, bx2, by2 = right["bbox"]
                intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(
                    0.0, min(ay2, by2) - max(ay1, by1)
                )
                right_area = max(1.0, (bx2 - bx1) * (by2 - by1))
                ratio = intersection / min(left_area, right_area)
                if ratio >= 0.08:
                    overlaps.append(
                        {
                            "page": page["number"],
                            "left": left["id"],
                            "right": right["id"],
                            "intersection_ratio": round(ratio, 3),
                        }
                    )
        for item in page.get("translations", []):
            if not item.get("bbox"):
                continue
            translations.append(item)
            x1, y1, x2, y2 = item["bbox"]
            fit = fit_text_style(
                item.get("translated_text", ""),
                x2 - x1,
                y2 - y1,
                region_type=item.get("region_type", "text"),
            )
            fits.append(fit)
    sizes = [fit.font_size for fit in fits]
    exact_echo = [
        item["id"]
        for item in translations
        if item.get("translated_text", "").strip()
        == item.get("source_text", "").strip()
        and not is_technical_identifier(item.get("source_text", ""))
    ]
    empty = [
        item["id"]
        for item in translations
        if not item.get("translated_text", "").strip()
    ]
    no_chinese = [
        item["id"]
        for item in translations
        if len(item.get("source_text", "")) >= 20
        and not re.search(r"[\u3400-\u9fff]", item.get("translated_text", ""))
        and not is_technical_identifier(item.get("source_text", ""))
    ]
    report = {
        "pages": len(document.get("pages", [])),
        "translation_regions": len(fits),
        "estimated_overflow_regions": sum(fit.overflow for fit in fits),
        "font_size": {
            "minimum": min(sizes) if sizes else None,
            "median": statistics.median(sizes) if sizes else None,
            "maximum": max(sizes) if sizes else None,
        },
        "compressed_letter_spacing_regions": sum(
            fit.letter_spacing < 0 for fit in fits
        ),
        "reduced_indent_regions": sum(0 < fit.text_indent < 2 for fit in fits),
        "translation_quality": {
            "empty_regions": empty,
            "exact_source_echo_regions": exact_echo,
            "long_regions_without_chinese": no_chinese,
        },
        "layout_quality": {
            "overlapping_translation_regions": overlaps,
        },
        "timings_seconds": timings,
    }
    report["automatic_quality_pass"] = not any(
        (
            report["estimated_overflow_regions"],
            empty,
            exact_echo,
            no_chinese,
            overlaps,
        )
    )
    return report


def _render_pdf(html: Path, pdf: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    vendor = project_root / "demo01" / "vendor"
    environment = os.environ.copy()
    if vendor.is_dir():
        old_path = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = f"{vendor}{os.pathsep}{old_path}".rstrip(
            os.pathsep
        )
    subprocess.run(
        [sys.executable, "-m", "weasyprint", str(html), str(pdf)],
        check=True,
        env=environment,
    )


def render_cached_layout(work_dir: Path) -> dict:
    """Rebuild HTML/PDF from cached OCR and translated segments only.

    This is the typography iteration path: it never initializes OCR and never
    creates a translation client, so it needs neither a GPU nor an API token.
    """
    work_dir = work_dir.resolve()
    document_path = work_dir / "document.ocr.json"
    translations_path = work_dir / "segments.zh-CN.json"
    missing = [
        str(path)
        for path in (document_path, translations_path)
        if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError("纯排版所需缓存不存在：" + "、".join(missing))

    timings: dict[str, float] = {"geometry": 0.0, "ocr": 0.0, "translation": 0.0}
    document = json.loads(document_path.read_text(encoding="utf-8"))
    translations = json.loads(translations_path.read_text(encoding="utf-8"))
    translated_document = _timed(
        timings,
        "align",
        lambda: attach_translations(document, translations),
    )
    translated_document_path = work_dir / "document.translated.json"
    html_path = work_dir / "translated.html"
    pdf_path = work_dir / "translated.pdf"
    save_layout(translated_document, translated_document_path)
    _timed(
        timings,
        "html",
        lambda: build_aligned_html(translated_document, output=html_path),
    )
    _timed(timings, "pdf", lambda: _render_pdf(html_path, pdf_path))

    report = _quality_report(translated_document, timings)
    report.update(
        {
            "source_pdf": document.get("source_pdf"),
            "output_pdf": str(pdf_path),
            "translation": {
                "mode": "pretranslated-cache",
                "segments": len(translations.get("segments", [])),
                "requests_made": 0,
            },
        }
    )
    (work_dir / "quality.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def run_pipeline(
    pdf: Path,
    work_dir: Path,
    *,
    model_dir: str,
    runtime_dir: Path,
    gpu: str = "0",
    token: str = "",
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    pdf = pdf.resolve()
    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    page_count = _page_count(pdf)
    geometry_path = work_dir / "geometry.json"
    ocr_dir = work_dir / "ocr"
    ocr_document_path = work_dir / "document.ocr.json"
    source_segments_path = work_dir / "segments.en.json"
    translated_segments_path = work_dir / "segments.zh-CN.json"
    translated_document_path = work_dir / "document.translated.json"
    html_path = work_dir / "translated.html"
    pdf_path = work_dir / "translated.pdf"

    if geometry_path.exists():
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        if "native_image_source" not in geometry:
            geometry = _timed(
                timings,
                "geometry",
                lambda: augment_native_images(geometry, pdf),
            )
            save_layout(geometry, geometry_path)
        else:
            timings["geometry"] = 0.0
    else:
        geometry = _timed(timings, "geometry", lambda: extract_pdf_geometry(pdf))
        save_layout(geometry, geometry_path)

    result_files = list(ocr_dir.glob(f"{pdf.stem}_page_*.md"))
    if len(result_files) < page_count:
        runner = UnlimitedOCRTransformersRunner(
            model_dir=model_dir,
            runtime_dir=runtime_dir,
            first_page=1,
            last_page=page_count,
            gpu=gpu,
        )
        _timed(timings, "ocr", lambda: runner.run_pdf(pdf, ocr_dir))
    else:
        timings["ocr"] = 0.0

    document = _timed(
        timings,
        "merge_ocr",
        lambda: merge_unlimited_ocr(geometry, pdf, ocr_dir),
    )
    save_layout(document, ocr_document_path)
    segment_payload = _timed(
        timings, "prepare_segments", lambda: build_region_segments(document)
    )
    source_segments_path.write_text(
        json.dumps(segment_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    translation_stats = _timed(
        timings,
        "translation",
        lambda: translate_segments_file(
            source_segments_path,
            translated_segments_path,
            token=token,
            base_url=base_url,
        ),
    )
    translated_payload = json.loads(
        translated_segments_path.read_text(encoding="utf-8")
    )
    translated_document = _timed(
        timings,
        "align",
        lambda: attach_translations(document, translated_payload),
    )
    save_layout(translated_document, translated_document_path)
    _timed(
        timings,
        "html",
        lambda: build_aligned_html(translated_document, output=html_path),
    )
    _timed(timings, "pdf", lambda: _render_pdf(html_path, pdf_path))

    report = _quality_report(translated_document, timings)
    report.update(
        {
            "source_pdf": str(pdf),
            "output_pdf": str(pdf_path),
            "translation": translation_stats,
        }
    )
    (work_dir / "quality.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
