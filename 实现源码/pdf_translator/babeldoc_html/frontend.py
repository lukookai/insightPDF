from __future__ import annotations

import copy
import importlib
import json
import logging
import os
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from pdf_translator.translation import DEFAULT_MODEL


LOGGER = logging.getLogger(__name__)

DEFAULT_SKILL_ROOT = Path(
    "/home/qiuhongyun/.codex/skills/double6-pdf-translation"
)
DEFAULT_DOCLAYOUT_MODEL = Path(
    "/home/qiuhongyun/.cache/double6-pdf-translation/pdf2zh-home/"
    ".cache/babeldoc/models/doclayout_yolo_docstructbench_imgsz1024.onnx"
)
DEFAULT_BABELDOC_CACHE_HOME = Path(
    "/home/qiuhongyun/.cache/double6-pdf-translation/pdf2zh-home"
)

ACADEMIC_SYSTEM_PROMPT = (
    "You are a professional, authentic machine translation engine. "
    "Translate academic PDFs into fluent Simplified Chinese while preserving "
    "formulas, citations, URLs, named entities, terminology, and "
    "layout-sensitive placeholders. Only output the translated result without "
    "additional explanation. Do not leave ordinary English words untranslated; "
    "translate residual prose words unless they are protected spans, code, URLs, "
    "emails, or approved names."
)


def _timed(timings: dict[str, float], name: str, callback):
    started = time.monotonic()
    result = callback()
    timings[name] = round(time.monotonic() - started, 3)
    return result


def create_pdf_subset(source: Path, output: Path, page_limit: int | None) -> Path:
    """Create a physical page subset so every BabelDOC stage sees the same pages."""

    import pymupdf

    source = Path(source).resolve()
    if not page_limit:
        return source
    original = pymupdf.open(source)
    count = min(int(page_limit), original.page_count)
    subset = pymupdf.open()
    subset.insert_pdf(original, from_page=0, to_page=count - 1)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".new.pdf")
    subset.save(temporary)
    subset.close()
    original.close()
    temporary.replace(output)
    return output.resolve()


def _load_skill_proxy(skill_root: Path):
    scripts = Path(skill_root).resolve() / "scripts"
    proxy_file = scripts / "translation_compat_proxy.py"
    if not proxy_file.is_file():
        raise RuntimeError(f"缺少 double6 翻译兼容层：{proxy_file}")
    scripts_text = str(scripts)
    if scripts_text not in sys.path:
        sys.path.insert(0, scripts_text)
    return importlib.import_module("translation_compat_proxy")


@contextmanager
def skill_translation_bridge(
    *,
    token: str,
    base_url: str,
    skill_root: Path,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Expose the selected translator through the Skill compatibility policy."""

    if not token.strip():
        raise RuntimeError("DEEPSEEK_API_KEY 未设置")

    # httpx/urllib honor the machine-wide HTTP_PROXY. Without NO_PROXY even
    # loopback calls are sent to that proxy and fail as HTTP 502.
    no_proxy_values = {
        value.strip()
        for value in os.environ.get("NO_PROXY", os.environ.get("no_proxy", "")).split(",")
        if value.strip()
    }
    no_proxy_values.update({"127.0.0.1", "localhost", "::1"})
    no_proxy = ",".join(sorted(no_proxy_values))
    os.environ["NO_PROXY"] = no_proxy
    os.environ["no_proxy"] = no_proxy

    from pdf_translator.translation.lawai_openai_adapter import (
        AdapterHandler,
        AdapterServer,
    )

    lawai_server = AdapterServer(("127.0.0.1", 0), AdapterHandler)
    lawai_server.upstream_base_url = base_url.rstrip("/")
    lawai_server.upstream_token = token
    lawai_server.request_timeout = 300
    lawai_thread = threading.Thread(target=lawai_server.serve_forever, daemon=True)
    lawai_thread.start()
    lawai_port = int(lawai_server.server_address[1])

    proxy_module = _load_skill_proxy(skill_root)
    proxy_config = proxy_module.ProxyConfig(
        model=DEFAULT_MODEL,
        upstream_base_url=f"http://127.0.0.1:{lawai_port}/v1",
        api_key="local-lawai-bridge",
        host="127.0.0.1",
        port=0,
    )
    proxy_server = proxy_module.start_translation_compat_proxy(proxy_config)
    try:
        yield proxy_config.base_url, proxy_config.stats
    finally:
        proxy_server.shutdown()
        proxy_server.server_close()
        lawai_server.shutdown()
        lawai_server.server_close()
        proxy_server_thread = getattr(proxy_server, "_thread", None)
        if proxy_server_thread:
            proxy_server_thread.join(timeout=2)
        lawai_thread.join(timeout=2)


def _run_babeldoc_stages(config, timings: dict[str, float]):
    """Run BabelDOC verbatim through IL translation, stopping before typesetting."""

    from pymupdf import Document

    from babeldoc.const import close_process_pool
    from babeldoc.format.pdf.document_il.midend.automatic_term_extractor import (
        AutomaticTermExtractor,
    )
    from babeldoc.format.pdf.document_il.midend.detect_scanned_file import (
        DetectScannedFile,
    )
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
    from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import (
        ILTranslatorLLMOnly,
    )
    from babeldoc.format.pdf.document_il.midend.layout_parser import LayoutParser
    from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
    from babeldoc.format.pdf.document_il.midend.styles_and_formulas import (
        StylesAndFormulas,
    )
    from babeldoc.format.pdf.document_il.xml_converter import XMLConverter
    from babeldoc.format.pdf.high_level import (
        fix_filter,
        fix_media_box,
        fix_null_page_content,
        fix_null_xref,
        safe_save,
        translator_supports_llm,
    )
    from babeldoc.format.pdf.new_parser.native_parse import (
        parse_prepared_pdf_with_new_parser_to_legacy_ir,
    )

    config.working_dir = Path(config.working_dir)
    config.working_dir.mkdir(parents=True, exist_ok=True)
    prepared_pdf = config.get_working_file_path("input.pdf")
    prepared_new = config.get_working_file_path("input.new.pdf")
    doc_pdf = Document(config.input_file)
    safe_save(doc_pdf, prepared_new)
    prepared_new.replace(prepared_pdf)
    try:
        fix_null_page_content(doc_pdf)
        fix_filter(doc_pdf)
        fix_null_xref(doc_pdf)
    except Exception:
        LOGGER.exception("BabelDOC 输入修复失败，继续按原流程解析")
    media_box = fix_media_box(doc_pdf)
    safe_save(doc_pdf, prepared_new)
    prepared_new.replace(prepared_pdf)

    docs = _timed(
        timings,
        "babeldoc_native_parse",
        lambda: parse_prepared_pdf_with_new_parser_to_legacy_ir(
            prepared_pdf,
            config=config,
            doc_pdf=doc_pdf,
        ),
    )
    converter = XMLConverter()
    converter.write_json(docs, config.get_working_file_path("create_il.debug.json"))

    if not config.skip_scanned_detection:
        _timed(
            timings,
            "babeldoc_scanned_detection",
            lambda: DetectScannedFile(config).process(docs, prepared_pdf, media_box),
        )
    else:
        timings["babeldoc_scanned_detection"] = 0.0

    docs = _timed(
        timings,
        "doclayout",
        lambda: LayoutParser(config).process(docs, doc_pdf),
    )
    close_process_pool()
    converter.write_json(docs, config.get_working_file_path("layout_generator.json"))

    _timed(
        timings,
        "babeldoc_paragraph_finder",
        lambda: ParagraphFinder(config).process(docs),
    )
    converter.write_json(docs, config.get_working_file_path("paragraph_finder.json"))

    _timed(
        timings,
        "babeldoc_styles_and_formulas",
        lambda: StylesAndFormulas(config).process(docs),
    )
    converter.write_json(docs, config.get_working_file_path("styles_and_formulas.json"))
    source_docs = copy.deepcopy(docs)

    translator = config.translator
    term_translator = config.get_term_extraction_translator()
    if translator_supports_llm(term_translator) and config.auto_extract_glossary:
        _timed(
            timings,
            "babeldoc_automatic_term_extraction",
            lambda: AutomaticTermExtractor(term_translator, config).procress(docs),
        )
    else:
        timings["babeldoc_automatic_term_extraction"] = 0.0

    translator_class = (
        ILTranslatorLLMOnly if translator_supports_llm(translator) else ILTranslator
    )

    def translate_document():
        engine = translator_class(translator, config)
        engine.translate(docs)

    _timed(timings, "babeldoc_il_translation", translate_document)
    converter.write_json(docs, config.get_working_file_path("il_translated.json"))
    doc_pdf.close()
    return source_docs, docs


def run_babeldoc_frontend(
    source_pdf: Path,
    working_root: Path,
    *,
    token: str,
    base_url: str,
    skill_root: Path = DEFAULT_SKILL_ROOT,
    doclayout_model_path: Path | None = None,
    babeldoc_cache_home: Path = DEFAULT_BABELDOC_CACHE_HOME,
    concurrency: int = 1,
    page_limit: int | None = None,
) -> dict[str, Any]:
    """Return BabelDOC documents immediately before its native typesetting stage."""

    # BabelDOC resolves its model/font cache from Path.home() at import time.
    # Configure the cache before the first babeldoc import, matching the Skill
    # runtime wrapper and preventing accidental model/font re-downloads.
    cache_home = Path(babeldoc_cache_home).expanduser().resolve()
    cache_home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(cache_home)
    os.environ["XDG_CACHE_HOME"] = str(cache_home / ".cache")
    os.environ["HF_HOME"] = str(cache_home / ".hf-home")

    from babeldoc import __version__ as babeldoc_version
    from babeldoc.docvision.base_doclayout import DocLayoutModel
    from babeldoc.docvision.doclayout import OnnxModel
    from babeldoc.format.pdf.high_level import get_translation_stage
    from babeldoc.format.pdf.translation_config import TranslationConfig
    from babeldoc.progress_monitor import ProgressMonitor
    from babeldoc.translator.translator import OpenAITranslator

    working_root = Path(working_root).resolve()
    working_root.mkdir(parents=True, exist_ok=True)
    concurrency = max(1, int(concurrency))
    timings: dict[str, float] = {}
    subset_path = _timed(
        timings,
        "prepare_page_subset",
        lambda: create_pdf_subset(
            Path(source_pdf), working_root / "source_subset.pdf", page_limit
        ),
    )

    with skill_translation_bridge(
        token=token,
        base_url=base_url,
        skill_root=skill_root,
    ) as (proxy_url, proxy_stats):
        translator = OpenAITranslator(
            "en",
            "zh",
            DEFAULT_MODEL,
            base_url=proxy_url,
            api_key="local-policy-bridge",
            ignore_cache=True,
            enable_json_mode_if_requested=True,
            send_temperature=True,
        )
        resolved_doclayout_model = Path(
            doclayout_model_path or DEFAULT_DOCLAYOUT_MODEL
        ).expanduser()
        if resolved_doclayout_model.is_file():
            doclayout_model = _timed(
                timings,
                "doclayout_model_load",
                lambda: OnnxModel(str(resolved_doclayout_model.resolve())),
            )
        elif doclayout_model_path:
            raise RuntimeError(f"DocLayout 模型不存在：{resolved_doclayout_model}")
        else:
            doclayout_model = _timed(
                timings, "doclayout_model_load", DocLayoutModel.load_onnx
            )
        config = TranslationConfig(
            translator=translator,
            term_extraction_translator=translator,
            input_file=subset_path,
            lang_in="en",
            lang_out="zh",
            doc_layout_model=doclayout_model,
            output_dir=working_root / "unused_babeldoc_pdf_output",
            working_dir=working_root / "babeldoc_work",
            debug=True,
            no_dual=True,
            no_mono=True,
            qps=concurrency,
            pool_max_workers=concurrency,
            term_pool_max_workers=concurrency,
            use_rich_pbar=False,
            skip_clean=True,
            min_text_length=5,
            custom_system_prompt=ACADEMIC_SYSTEM_PROMPT,
            auto_extract_glossary=True,
            save_auto_extracted_glossary=True,
            enable_graphic_element_process=True,
            merge_alternating_line_numbers=True,
            remove_non_formula_lines=True,
            non_formula_line_iou_threshold=0.9,
            figure_table_protection_threshold=0.9,
            skip_formula_offset_calculation=False,
            disable_same_text_fallback=True,
        )
        progress = ProgressMonitor(get_translation_stage(config))
        config.progress_monitor = progress
        source_docs, translated_docs = _run_babeldoc_stages(config, timings)
        safe_proxy_stats = json.loads(json.dumps(proxy_stats, ensure_ascii=False))

    return {
        "source_pdf": subset_path,
        "source_docs": source_docs,
        "translated_docs": translated_docs,
        "babeldoc_working_dir": Path(config.working_dir),
        "babeldoc_version": babeldoc_version,
        "doclayout_model": str(resolved_doclayout_model),
        "babeldoc_cache_home": str(cache_home),
        "concurrency": concurrency,
        "proxy_stats": safe_proxy_stats,
        "timings": timings,
    }
