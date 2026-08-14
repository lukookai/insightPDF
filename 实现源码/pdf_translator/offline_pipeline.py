from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from pdf_translator.layout.doclayout_yolo import DocLayoutYoloDetector
from pdf_translator.layout.native_rule_html import build_native_rule_html
from pdf_translator.layout.native_rules import extract_native_rule_document
from pdf_translator.offline.network import allow_only_remote_hosts, host_from_url
from pdf_translator.translation import (
    DEFAULT_BASE_URL,
    translate_segments_file,
    translation_backend_name,
)
from pdf_translator.translation.runner import is_technical_identifier


def _timed(timings: dict[str, float], name: str, callback):
    started = time.monotonic()
    result = callback()
    timings[name] = round(time.monotonic() - started, 3)
    return result


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _render_html(html_path: Path, pdf_path: Path) -> None:
    from weasyprint import HTML

    HTML(filename=str(html_path)).write_pdf(str(pdf_path))


def _qa(document: dict, pdf_path: Path, fit_evidence: list[dict]) -> dict:
    import fitz

    translated_blocks = [
        block
        for page in document.get("pages", [])
        for block in page.get("blocks", [])
        if block.get("translate")
    ]
    untranslated = [
        block["id"]
        for block in translated_blocks
        if not re.search(r"[\u3400-\u9fff]", block.get("translated_text", ""))
        and len(block.get("source_text", "")) >= 20
    ]
    source_echo = [
        block["id"]
        for block in translated_blocks
        if block.get("source_text", "").strip()
        == block.get("translated_text", "").strip()
        and not is_technical_identifier(block.get("source_text", ""))
    ]
    output = fitz.open(pdf_path)
    page_sizes = [
        [round(float(page.rect.width), 3), round(float(page.rect.height), 3)]
        for page in output
    ]
    extracted = "\n".join(page.get_text() for page in output)
    output.close()
    expected_sizes = [
        [round(float(page["width"]), 3), round(float(page["height"]), 3)]
        for page in document.get("pages", [])
    ]
    overflows = [item["id"] for item in fit_evidence if item["estimated_overflow"]]
    tiny = [item["id"] for item in fit_evidence if item["font_size"] < 5.5]
    report = {
        "page_count": len(page_sizes),
        "expected_page_count": len(expected_sizes),
        "page_sizes": page_sizes,
        "expected_page_sizes": expected_sizes,
        "page_geometry_match": page_sizes == expected_sizes,
        "translated_blocks": len(translated_blocks),
        "preserved_blocks": sum(
            not block.get("translate")
            for page in document.get("pages", [])
            for block in page.get("blocks", [])
        ),
        "cjk_text_layer_characters": len(re.findall(r"[\u3400-\u9fff]", extracted)),
        "long_blocks_without_chinese": untranslated,
        "source_echo_blocks": source_echo,
        "estimated_overflow_blocks": overflows,
        "tiny_font_blocks": tiny,
    }
    report["automatic_quality_pass"] = bool(
        page_sizes == expected_sizes
        and report["cjk_text_layer_characters"] > 50
        and not untranslated
        and not source_echo
        and not overflows
    )
    return report


def _translation_payload(document: dict) -> dict:
    segments = []
    for page in document.get("pages", []):
        for block in page.get("blocks", []):
            if block.get("translate"):
                segments.append(
                    {
                        "id": block["id"],
                        "page": page["number"],
                        "role": block.get("role", "body"),
                        "source_text": block.get("source_text", ""),
                        "translated_text": "",
                    }
                )
    return {"schema_version": "1.0", "segments": segments}


def _attach_translations(document: dict, translated_path: Path) -> None:
    payload = json.loads(translated_path.read_text(encoding="utf-8"))
    by_identity = {
        (item.get("id", ""), item.get("source_text", "")): item.get(
            "translated_text", ""
        )
        for item in payload.get("segments", [])
    }
    by_source = {
        item.get("source_text", ""): item.get("translated_text", "")
        for item in payload.get("segments", [])
    }
    for page in document.get("pages", []):
        for block in page.get("blocks", []):
            if not block.get("translate"):
                continue
            source = block.get("source_text", "")
            block["translated_text"] = by_identity.get(
                (block["id"], source), by_source.get(source, "")
            )


def run_native_rule_pipeline(
    pdf: Path,
    output_dir: Path,
    *,
    layout_mode: str,
    translation_cache: Path,
    token: str,
    base_url: str = DEFAULT_BASE_URL,
    doclayout_model: Path | None = None,
    page_limit: int = 2,
) -> dict:
    if layout_mode not in {"native", "doclayout"}:
        raise ValueError(f"未知布局模式：{layout_mode}")
    pdf = Path(pdf).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    network_attempts: list[dict] = []
    translation_cache = Path(translation_cache).resolve()

    with allow_only_remote_hosts({host_from_url(base_url)}, network_attempts):
        detector = None
        if layout_mode == "doclayout":
            if not doclayout_model:
                raise ValueError("DocLayout 版本必须提供 --doclayout-model")
            detector = _timed(
                timings,
                "doclayout_model_load",
                lambda: DocLayoutYoloDetector(doclayout_model),
            )
        else:
            timings["doclayout_model_load"] = 0.0

        document = _timed(
            timings,
            "native_pdf_parse_and_layout",
            lambda: extract_native_rule_document(
                pdf,
                output_assets=output_dir / "assets",
                page_limit=page_limit,
                doclayout_detector=detector,
            ),
        )
        document_path = output_dir / "document.json"
        _write_json(document_path, document)

        timings["translation_model_load"] = 0.0
        segments_path = output_dir / "translation_segments.en.json"
        _write_json(segments_path, _translation_payload(document))
        translation_stats = _timed(
            timings,
            "own_api_translation",
            lambda: translate_segments_file(
                segments_path,
                translation_cache,
                token=token,
                base_url=base_url,
            ),
        )
        _attach_translations(document, translation_cache)
        _write_json(document_path, document)

        html_path = output_dir / f"{pdf.stem}.{layout_mode}.html"
        html_result = _timed(
            timings,
            "html_layout",
            lambda: build_native_rule_html(document, html_path),
        )
        pdf_path = output_dir / f"{pdf.stem}.{layout_mode}.zh.pdf"
        _timed(timings, "html_to_pdf", lambda: _render_html(html_path, pdf_path))
        qa = _timed(
            timings,
            "quality_assurance",
            lambda: _qa(document, pdf_path, html_result["fit_evidence"]),
        )

    manifest = {
        "schema_version": "1.0",
        "status": "ok" if qa["automatic_quality_pass"] else "partial",
        "source_pdf": str(pdf),
        "layout_mode": layout_mode,
        "layout_rules": (
            "pdf-native-only"
            if layout_mode == "native"
            else "pdf-native-plus-local-doclayout-yolo"
        ),
        "page_limit": page_limit,
        "translation": {
            "backend": translation_backend_name(base_url),
            "base_url": base_url,
            "cache": str(translation_cache),
            **translation_stats,
        },
        "network_policy": {
            "mode": "allow-own-translation-api-only",
            "allowed_host": host_from_url(base_url),
            "blocked_attempts": network_attempts,
            "third_party_model_or_api_calls": 0,
            "own_translation_api_requests": translation_stats["requests_made"],
        },
        "outputs": {
            "pdf": str(pdf_path),
            "html": str(html_path),
            "document": str(document_path),
        },
        "qa": qa,
        "timings_seconds": timings,
        "total_seconds": round(sum(timings.values()), 3),
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    manifest["manifest"] = str(manifest_path)
    return manifest


# Compatibility alias for code written while this pipeline was local-only.
run_offline_native_pipeline = run_native_rule_pipeline
