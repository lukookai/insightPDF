from __future__ import annotations

import html
import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pdf_translator.layout.doclayout_yolo import DocLayoutYoloDetector
from pdf_translator.layout.native_rule_html import build_native_rule_html
from pdf_translator.production_adapter import (
    attach_hybrid_translations,
    build_hybrid_document,
    extract_with_production_rules,
    hybrid_translation_payload,
)
from pdf_translator.translation import (
    DEFAULT_BASE_URL,
    translate_segments_file,
    translation_backend_name,
)
from pdf_translator.translation.lawai import LawAIClient
from pdf_translator.translation.runner import is_technical_identifier


CURRENT_NAME = "当前 DocLayout + HTML 排版版 PDF.pdf"
PRODUCTION_NAME = "生产规则核心桥接版 PDF.pdf"
HYBRID_NAME = "融合版 生产规则 + DocLayout + HTML 自适应排版 PDF.pdf"


def _timed(timings: dict[str, float], name: str, callback):
    started = time.monotonic()
    result = callback()
    timings[name] = round(time.monotonic() - started, 3)
    return result


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _render_html(html_path: Path, pdf_path: Path) -> None:
    from weasyprint import HTML

    HTML(filename=str(html_path)).write_pdf(str(pdf_path))


def _translatable_production_blocks(payload: dict):
    skipped = {"abandon", "abandon_cell", "table", "image"}
    for page in payload.get("pages", []):
        for index, block in enumerate(page.get("blocks", []), 1):
            content = str(block.get("content") or "").strip()
            if not content or block.get("content_type") in skipped or block.get("src_image"):
                continue
            yield page, index, block, content


def _translate_production_once(
    production_json: dict,
    *,
    token: str,
    base_url: str,
    concurrency: int,
) -> dict:
    """Mirror production's one-request-per-block policy after schema bridging."""

    jobs = list(_translatable_production_blocks(production_json))
    LawAIClient(base_url, token).verify()

    def translate(job):
        page, index, block, source = job
        try:
            output = LawAIClient(base_url, token).translate_text(source)
            error = None
        except Exception as exc:
            output = source
            error = str(exc)[:300]
        return page, index, block, source, output, error

    ledger = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        futures = [executor.submit(translate, job) for job in jobs]
        for completed, future in enumerate(as_completed(futures), 1):
            page, index, block, source, output, error = future.result()
            block["content"] = output
            ledger.append(
                {
                    "id": f"p{page['page_number']}-b{index}",
                    "source_text": source,
                    "translated_text": output,
                    "error": error,
                }
            )
            print(f"[生产桥接 {completed}/{len(jobs)}] p{page['page_number']}-b{index}", flush=True)
    return {
        "jobs": len(jobs),
        "errors": sum(bool(item["error"]) for item in ledger),
        "ledger": sorted(ledger, key=lambda item: item["id"]),
    }


def _suspicious_translation_compression(ledger: list[dict]) -> list[dict]:
    """Find long source blocks collapsed to implausibly short translations."""

    issues = []
    for item in ledger:
        source = re.sub(r"\s+", "", str(item.get("source_text") or ""))
        translated = re.sub(r"\s+", "", str(item.get("translated_text") or ""))
        if len(source) < 80 or len(translated) >= max(5, int(len(source) * 0.08)):
            continue
        issues.append(
            {
                "id": item.get("id"),
                "source_characters": len(source),
                "translated_characters": len(translated),
                "length_ratio": round(len(translated) / max(1, len(source)), 3),
            }
        )
    return issues


def _color_hex(value) -> str:
    try:
        unsigned = int(value) & 0xFFFFFF
    except (TypeError, ValueError):
        unsigned = 0
    return f"#{unsigned:06x}"


def _render_production_backfill(
    source_pdf: Path,
    translated_json: dict,
    output_pdf: Path,
) -> dict:
    """PyMuPDF bridge compatible with production's coordinate backfill policy."""

    import fitz

    regular = Path(
        "/home/qiuhongyun/.cache/double6-pdf-translation/pdf2zh-home/"
        ".cache/babeldoc/fonts/SourceHanSerifCN-Regular.ttf"
    )
    bold = regular.with_name("SourceHanSerifCN-Bold.ttf")
    font_dir = output_pdf.parent / "字体子集"
    font_dir.mkdir(parents=True, exist_ok=True)
    normal_text = "".join(
        str(block.get("content") or "")
        for _, _, block, _ in _translatable_production_blocks(translated_json)
        if not block.get("font_bold")
    )
    bold_text = "".join(
        str(block.get("content") or "")
        for _, _, block, _ in _translatable_production_blocks(translated_json)
        if block.get("font_bold")
    )
    regular_subset = font_dir / "中文常规子集.ttf"
    bold_subset = font_dir / "中文粗体子集.ttf"
    _subset_font(regular, regular_subset, normal_text)
    if bold_text:
        _subset_font(bold, bold_subset, bold_text)
    else:
        bold_subset = regular_subset
    doc = fitz.open(source_pdf)
    insertions = 0
    negative_space = []
    for page_data in translated_json.get("pages", []):
        page = doc[int(page_data["page_number"]) - 1]
        for annot in list(page.annots() or []):
            page.delete_annot(annot)
        blocks = [
            block
            for block in page_data.get("blocks", [])
            if str(block.get("content") or "").strip()
            and block.get("content_type") not in {"abandon", "abandon_cell", "table", "image"}
            and not block.get("src_image")
        ]
        for block in blocks:
            page.add_redact_annot(fitz.Rect(*block["bbox"]), fill=False)
        if blocks:
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                text=fitz.PDF_REDACT_TEXT_REMOVE,
            )
        for block_index, block in enumerate(blocks, 1):
            font_path = bold_subset if block.get("font_bold") else regular_subset
            family = "production_zh"
            indent = max(0.0, float(block.get("indent") or 0.0))
            size = max(4.0, float(block.get("font_size") or 9.0))
            css = (
                f'@font-face{{font-family:"{family}";src:url("{font_path.resolve().as_posix()}")}}'
                f'*{{font-family:"{family}";color:{_color_hex(block.get("color", 0))};'
                f'font-size:{size}pt;line-height:1.2;text-indent:{indent}pt}}'
            )
            spare, scale = page.insert_htmlbox(
                fitz.Rect(*block["bbox"]),
                html.escape(str(block["content"])),
                css=css,
                rotate=int(block.get("rotation_angle") or 0),
                scale_low=0.45,
            )
            insertions += 1
            if spare < 0:
                negative_space.append(
                    f"p{page_data['page_number']}-b{block_index}"
                )
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_pdf, garbage=4, deflate=True)
    doc.close()
    return {"insertions": insertions, "negative_spare_height": negative_space}


def _subset_font(source: Path, output: Path, text: str) -> None:
    from fontTools.subset import Options, Subsetter
    from fontTools.ttLib import TTFont

    if output.is_file():
        return
    font = TTFont(source)
    subsetter = Subsetter(options=Options())
    subsetter.populate(text="".join(sorted(set(text))) or "中")
    subsetter.subset(font)
    font.save(output)


def _qa(pdf_path: Path, source_pdf: Path, document: dict | None = None) -> dict:
    import fitz

    output = fitz.open(pdf_path)
    source = fitz.open(source_pdf)
    cjk_by_page = []
    image_only_untranslated = []
    for index, page in enumerate(output):
        text = page.get_text()
        cjk = len(re.findall(r"[\u3400-\u9fff]", text))
        cjk_by_page.append(cjk)
        source_text = source[index].get_text().strip()
        if not source_text and source[index].get_images(full=True) and cjk == 0:
            image_only_untranslated.append(index + 1)
    geometry = [
        (round(page.rect.width, 3), round(page.rect.height, 3)) for page in output
    ] == [
        (round(page.rect.width, 3), round(page.rect.height, 3)) for page in source
    ]
    output.close()
    source.close()
    report = {
        "page_geometry_match": geometry,
        "cjk_text_layer_characters": sum(cjk_by_page),
        "cjk_by_page": cjk_by_page,
        "image_only_untranslated_pages": image_only_untranslated,
    }
    if document is not None:
        translated = [
            block
            for page in document.get("pages", [])
            for block in page.get("blocks", [])
            if block.get("translate")
        ]
        report["translated_blocks"] = len(translated)
        report["translation_segments"] = sum(
            len(block.get("translation_segments", [])) for block in translated
        )
        report["blocks_without_chinese"] = [
            block["id"]
            for block in translated
            if len(block.get("source_text", "")) >= 20
            and not re.search(r"[\u3400-\u9fff]", block.get("translated_text", ""))
            and not is_technical_identifier(block.get("source_text", ""))
        ]
    report["automatic_quality_pass"] = bool(
        geometry
        and report["cjk_text_layer_characters"] > 20
        and not image_only_untranslated
        and not report.get("blocks_without_chinese", [])
    )
    return report


def run_production_bridge_pipeline(
    pdf: Path,
    output_dir: Path,
    *,
    token: str,
    base_url: str = DEFAULT_BASE_URL,
    production_repo: Path = Path("/data/qhy/cqnu/pdf2json_repo"),
    concurrency: int = 4,
) -> dict:
    pdf = Path(pdf).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timings = {}
    extracted_path = output_dir / "生产规则解析结果.json"
    production_json = _timed(
        timings,
        "production_rule_extraction",
        lambda: extract_with_production_rules(
            pdf,
            extracted_path,
            production_repo=production_repo,
            work_dir=output_dir / "production_work",
        ),
    )
    translation = _timed(
        timings,
        "own_api_translation",
        lambda: _translate_production_once(
            production_json,
            token=token,
            base_url=base_url,
            concurrency=concurrency,
        ),
    )
    translated_path = output_dir / "生产规则桥接译文.json"
    _write_json(translated_path, production_json)
    _write_json(output_dir / "生产规则桥接翻译记录.json", translation["ledger"])
    output_pdf = output_dir / PRODUCTION_NAME
    backfill = _timed(
        timings,
        "coordinate_backfill",
        lambda: _render_production_backfill(pdf, production_json, output_pdf),
    )
    qa = _timed(timings, "quality_assurance", lambda: _qa(output_pdf, pdf))
    qa["suspicious_translation_compression"] = _suspicious_translation_compression(
        translation["ledger"]
    )
    if qa["suspicious_translation_compression"]:
        qa["automatic_quality_pass"] = False
    manifest = {
        "schema_version": "1.0",
        "status": "ok" if qa["automatic_quality_pass"] else "partial",
        "source_pdf": str(pdf),
        "architecture": "production-pdf2json-rules + schema bridge + coordinate backfill",
        "production_code": {
            "repo": str(Path(production_repo).resolve()),
            "extractor": "extract_all_pages.py::extract_all_pages",
            "online_production_service_called": False,
        },
        "translation": {
            "backend": translation_backend_name(base_url),
            "base_url": base_url,
            "model": "deepseek-v4-flash" if translation_backend_name(base_url) == "deepseek" else None,
            "policy": "one request per production block",
            "jobs": translation["jobs"],
            "errors": translation["errors"],
        },
        "backfill": backfill,
        "qa": qa,
        "timings_seconds": timings,
        "total_seconds": round(sum(timings.values()), 3),
        "outputs": {"pdf": str(output_pdf), "json": str(translated_path)},
    }
    _write_json(output_dir / "生产规则桥接版清单.json", manifest)
    return manifest


def run_production_doclayout_html_pipeline(
    pdf: Path,
    output_dir: Path,
    *,
    token: str,
    base_url: str = DEFAULT_BASE_URL,
    production_repo: Path = Path("/data/qhy/cqnu/pdf2json_repo"),
    doclayout_model: Path = Path(
        "/home/qiuhongyun/.cache/double6-pdf-translation/pdf2zh-home/"
        ".cache/babeldoc/models/doclayout_yolo_docstructbench_imgsz1024.onnx"
    ),
    page_limit: int | None = None,
    split_limit: int = 620,
    concurrency: int = 4,
) -> dict:
    pdf = Path(pdf).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timings = {}
    extracted_path = output_dir / "生产规则解析结果.json"
    production_json = _timed(
        timings,
        "production_rule_extraction",
        lambda: extract_with_production_rules(
            pdf,
            extracted_path,
            production_repo=production_repo,
            work_dir=output_dir / "production_work",
        ),
    )
    detector = _timed(
        timings,
        "doclayout_model_load",
        lambda: DocLayoutYoloDetector(doclayout_model),
    )
    document = _timed(
        timings,
        "doclayout_correction_and_split",
        lambda: build_hybrid_document(
            pdf,
            production_json,
            output_assets=output_dir / "assets",
            doclayout_detector=detector,
            page_limit=page_limit,
            split_limit=split_limit,
        ),
    )
    document_path = output_dir / "融合版排版文档.json"
    _write_json(document_path, document)
    segments_path = output_dir / "融合版待翻译分段.json"
    _write_json(segments_path, hybrid_translation_payload(document))
    translated_path = output_dir / "融合版中文分段.json"
    translation = _timed(
        timings,
        "own_api_translation",
        lambda: translate_segments_file(
            segments_path,
            translated_path,
            token=token,
            base_url=base_url,
            concurrency=concurrency,
        ),
    )
    attach_hybrid_translations(
        document,
        json.loads(translated_path.read_text(encoding="utf-8")),
    )
    _write_json(document_path, document)
    html_path = output_dir / "融合版 HTML 中间件.html"
    html_result = _timed(
        timings,
        "html_adaptive_layout",
        lambda: build_native_rule_html(document, html_path),
    )
    output_pdf = output_dir / HYBRID_NAME
    _timed(timings, "html_to_pdf", lambda: _render_html(html_path, output_pdf))
    qa = _timed(
        timings,
        "quality_assurance",
        lambda: _qa(output_pdf, pdf, document),
    )
    qa["estimated_overflow_blocks"] = [
        item["id"] for item in html_result["fit_evidence"] if item["estimated_overflow"]
    ]
    if qa["estimated_overflow_blocks"]:
        qa["automatic_quality_pass"] = False
    manifest = {
        "schema_version": "1.0",
        "status": "ok" if qa["automatic_quality_pass"] else "partial",
        "source_pdf": str(pdf),
        "architecture": {
            "rule_base": "production pdf2json extract_all_pages",
            "model_fallback": "local DocLayout-YOLO ONNX",
            "translation_segmentation": f"deterministic sentence chunks <= {split_limit} chars",
            "translation_backend": translation_backend_name(base_url),
            "translation_concurrency": concurrency,
            "final_layout_engine": "HTML/CSS -> WeasyPrint",
            "external_translation_api": translation_backend_name(base_url) == "deepseek",
        },
        "production_code": {
            "repo": str(Path(production_repo).resolve()),
            "online_production_service_called": False,
        },
        "document_statistics": document["statistics"],
        "translation": {"base_url": base_url, **translation},
        "qa": qa,
        "timings_seconds": timings,
        "total_seconds": round(sum(timings.values()), 3),
        "outputs": {
            "pdf": str(output_pdf),
            "html": str(html_path),
            "document": str(document_path),
            "segments": str(translated_path),
        },
    }
    _write_json(output_dir / "融合版运行清单.json", manifest)
    return manifest


def collect_current_output(current_manifest: dict, output_dir: Path) -> Path:
    source = Path(current_manifest["outputs"]["pdf"])
    target = Path(output_dir).resolve() / CURRENT_NAME
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return target
