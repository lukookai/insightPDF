from __future__ import annotations

import json
import html
import re
import time
from pathlib import Path

from pdf_translator.translation import DEFAULT_BASE_URL, translation_backend_name

from .document import build_html_document
from .frontend import (
    DEFAULT_BABELDOC_CACHE_HOME,
    DEFAULT_SKILL_ROOT,
    run_babeldoc_frontend,
)
from .renderer import build_streaming_html
from .table_overlay import build_flow_tables
from .il_io import load_document_il
from pdf_translator.babeldoc_html.renderer import build_babeldoc_html
from pdf_translator.babeldoc_html.pipeline import _regenerate_backgrounds
from pdf_translator.babeldoc_html.pdf_writer_pymupdf import render_pdf as _render_pdf_pymupdf
from pdf_translator.babeldoc_html.pdf_writer_playwright import render_pdf as _render_html_to_pdf


def _timed(timings: dict[str, float], name: str, callback):
    started = time.monotonic()
    result = callback()
    timings[name] = round(time.monotonic() - started, 3)
    return result


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _render_pdf(html_path: Path, pdf_path: Path) -> bool:
    """Render the HTML middleware to PDF. Returns True on success.

    Both WeasyPrint (needs GTK) and the Chromium fallback may be unavailable on
    a given machine; in that case we log and return False instead of crashing —
    the HTML middleware is still the primary deliverable.
    """
    import logging

    logger = logging.getLogger(__name__)
    try:
        from weasyprint import HTML

        HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        return True
    except Exception as exc:  # WeasyPrint 缺少 GTK 本地库时回退到 Chromium
        logger.warning("WeasyPrint 不可用，改用 Chromium 渲染 PDF：%s", exc)
    try:
        from pdf_translator._chromium_pdf import render_html_to_pdf

        render_html_to_pdf(html_path, pdf_path)
        return True
    except Exception as exc:  # 两路渲染都不可用：保留 HTML，放弃 PDF
        logger.warning("PDF 渲染不可用（缺少系统库），仅产出 HTML：%s", exc)
        return False


def _repair_placeholder_order_with_lawai(
    document: dict,
    *,
    token: str,
    base_url: str,
) -> dict:
    """Retranslate bullet items without BabelDOC's movable formula placeholder."""

    from pdf_translator.translation.lawai import LawAIClient

    client = LawAIClient(base_url, token)
    repaired = []
    failures = []
    for page in document.get("pages", []):
        for block in page.get("blocks", []):
            source = block.get("source_text", "").strip()
            if not source.startswith(("•", "●", "‣")):
                continue
            body = source.lstrip("•●‣ ")
            body = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", body)
            body = re.sub(r"\s+", " ", body).strip()
            try:
                translated = client.translate_text(body)
            except Exception as exc:
                failures.append({"id": block["id"], "error": str(exc)[:300]})
                continue
            if not re.search(r"[\u3400-\u9fff]", translated):
                failures.append({"id": block["id"], "error": "no_cjk_output"})
                continue
            block["translated_text"] = f"• {translated}"
            block["translated_html"] = html.escape(block["translated_text"])
            block["translate"] = True
            repaired.append(block["id"])
    return {"repaired_blocks": repaired, "failures": failures}


def _translate_table_cells(
    document: dict,
    *,
    token: str,
    base_url: str,
    source_pdf: str | Path | None = None,
    concurrency: int = 8,
) -> dict:
    """Translate table cells that BabelDOC left untranslated (``fallback_line``).

    BabelDOC's skill policy skips table cells, so ``table_text`` blocks keep a
    ``fallback_line`` sentinel for *both* source and translation — no real text
    at all.  When that happens we recover the English cell text straight from
    the source PDF by clipping its bbox, then translate it with DeepSeek and
    write the Chinese back so the flow <table> carries a real translation.
    """
    import re as _re
    import fitz
    from concurrent.futures import ThreadPoolExecutor

    from pdf_translator.translation.lawai import LawAIClient

    _SENT = {"fallback_line", "plain text"}

    def _recover_page(pdf_doc, pnum: int, cells: list[dict], m: float = 3.0, xtol: float = 3.0):
        """Recover English cell text straight from the source PDF.

        BabelDOC's ``table_text`` cell bboxes sit a few points *above* the real
        glyphs (the text spills below the cell's lower edge), and its grid is
        finer than the actual text lines — so a single text word often straddles
        two thin cells.  We therefore assign every source word to the cell with
        the *largest overlap area* (expanded by a small ``m`` margin to absorb
        the drift).  This naturally drops the redundant "sliver" cells (which
        receive no word) and keeps each logical text line in exactly one cell.
        Words are sorted by (y, x) so a cell's text reads left-to-right.
        """
        page = pdf_doc[pnum - 1]
        words = page.get_text("words")
        assigned: dict[int, list[tuple[float, float, str]]] = {id(b): [] for b in cells}
        for w in words:
            wx0, wy0, wx1, wy1, wtext = w[:5]
            wcxc = (wx0 + wx1) / 2.0
            wcyc = (wy0 + wy1) / 2.0
            best = None
            best_ov = 0.0
            for b in cells:
                bx0, by0, bx1, by1 = (float(v) for v in b["bbox"])
                if not (bx0 - xtol <= wcxc <= bx1 + xtol):
                    continue
                ix0 = max(wx0, bx0 - m)
                iy0 = max(wy0, by0 - m)
                ix1 = min(wx1, bx1 + m)
                iy1 = min(wy1, by1 + m)
                ov = (ix1 - ix0) * (iy1 - iy0)
                if ov > best_ov:
                    best_ov = ov
                    best = b
            if best is not None and best_ov > 0:
                assigned[id(best)].append((wcyc, wcxc, wtext))
        out: dict[str, str] = {}
        for b in cells:
            ws = sorted(assigned[id(b)])
            txt = _re.sub(r"\s+", " ", " ".join(t for _, _, t in ws)).strip()
            if txt:
                out[b["id"]] = txt
        return out

    # Group table_text cells by page so we only scan each page's words once.
    by_page: dict[int, list[dict]] = {}
    for page in document.get("pages", []):
        for block in page.get("blocks", []):
            if block.get("role") != "table_text":
                continue
            by_page.setdefault(int(page["number"]), []).append(block)

    pdf = fitz.open(source_pdf) if source_pdf else None

    # BabelDOC leaves every table_text block as ``fallback_line`` for both
    # source and translation, so we recover the English from the source PDF and
    # stamp it onto source_text; the renderer then knows which cells are real.
    recovered_all: dict[str, str] = {}
    if pdf is not None:
        for pnum, cells in by_page.items():
            recovered_all.update(_recover_page(pdf, pnum, cells))

    targets: list[tuple[dict, str]] = []
    for cells in by_page.values():
        for block in cells:
            src = recovered_all.get(block["id"])
            if not src or not _re.search(r"[A-Za-z]", src):
                continue
            tr = str(block.get("translated_text") or "").strip()
            if tr not in _SENT and _re.search(r"[\u3400-\u9fff]", tr):
                # Already translated (e.g. a successful prior run) — keep it.
                block["source_text"] = src
                continue
            block["source_text"] = src
            targets.append((block, src))
    if not targets:
        return {"translated_cells": 0, "recovered_cells": len(recovered_all)}
    client = LawAIClient(base_url, token)

    def _tr_one(src: str) -> str | None:
        try:
            return client.translate_text(src)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        results = list(ex.map(_tr_one, [s for _, s in targets]))
    done = 0
    for (block, _), result in zip(targets, results):
        if result and _re.search(r"[\u3400-\u9fff]", result):
            # Skip DeepSeek's meta/prompt replies for empty or symbol cells.
            if any(k in result for k in ("请提供", "好的，", "需要翻译", "请发送", "请告诉我")):
                continue
            block["translated_text"] = result
            block["translate"] = True
            done += 1
    return {"translated_cells": done, "recovered_cells": len(recovered_all)}


def _quality_report(document: dict, pdf_path: Path, fit_evidence: list[dict]) -> dict:
    import pymupdf

    output = pymupdf.open(pdf_path)
    page_sizes = [
        [round(float(page.rect.width), 3), round(float(page.rect.height), 3)]
        for page in output
    ]
    extracted = "\n".join(page.get_text() for page in output)
    output.close()
    expected = [
        [round(float(page["width"]), 3), round(float(page["height"]), 3)]
        for page in document["pages"]
    ]
    translated_blocks = [
        block
        for page in document["pages"]
        for block in page["blocks"]
        if block["translate"]
    ]
    unchanged_long = [
        block["id"]
        for block in translated_blocks
        if len(block["source_text"]) >= 20
        and not re.search(r"[\u3400-\u9fff]", block["translated_text"])
    ]
    overflow = [item["id"] for item in fit_evidence if item["estimated_overflow"]]
    blocking_overflow = [
        item["id"]
        for item in fit_evidence
        if item["estimated_overflow"]
        and not (
            item.get("role") in {"figure_text", "table_text"}
            and item.get("translated_length", 0) <= 12
            and item.get("region_height", 99) <= 5
        )
    ]
    unchanged_english_candidates = []
    for page in document["pages"]:
        for block in page["blocks"]:
            source_text = block["source_text"].strip()
            if block["translate"] or block["role"] in {"formula", "abandon"}:
                continue
            if len(re.findall(r"[A-Za-z]", source_text)) < 20:
                continue
            # Author/affiliation/contact bands are intentionally protected by
            # the Skill role policy, not translation failures.
            if "@" in source_text or (
                page["number"] == 1
                and block["bbox"][1] < 225
                and not re.search(r"[.!?]", source_text)
            ):
                continue
            unchanged_english_candidates.append(block["id"])
    report = {
        "page_count": len(page_sizes),
        "expected_page_count": len(expected),
        "page_geometry_match": page_sizes == expected,
        "translated_blocks": len(translated_blocks),
        "preserved_blocks": sum(
            not block["translate"]
            for page in document["pages"]
            for block in page["blocks"]
        ),
        "doclayout_regions": sum(
            len(page["doclayout_regions"]) for page in document["pages"]
        ),
        "formula_assets": sum(
            len(block["formula_assets"])
            for page in document["pages"]
            for block in page["blocks"]
        ),
        "cjk_text_layer_characters": len(re.findall(r"[\u3400-\u9fff]", extracted)),
        "long_changed_blocks_without_chinese": unchanged_long,
        "estimated_overflow_blocks": overflow,
        "blocking_estimated_overflow_blocks": blocking_overflow,
        "unchanged_english_candidates": unchanged_english_candidates,
    }
    report["automatic_quality_pass"] = bool(
        report["page_geometry_match"]
        and report["translated_blocks"] > 0
        and report["cjk_text_layer_characters"] > 20
        and not unchanged_long
        and not blocking_overflow
        and not unchanged_english_candidates
    )
    return report


def run_babeldoc_html_pipeline(
    pdf: Path,
    output_dir: Path,
    *,
    token: str,
    base_url: str = DEFAULT_BASE_URL,
    skill_root: Path = DEFAULT_SKILL_ROOT,
    doclayout_model: Path | None = None,
    babeldoc_cache_home: Path = DEFAULT_BABELDOC_CACHE_HOME,
    concurrency: int = 1,
    page_limit: int | None = None,
    flow_tables: dict[int, list[tuple[float, str]]] | None = None,
) -> dict:
    pdf = Path(pdf).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    frontend = run_babeldoc_frontend(
        pdf,
        output_dir / "work",
        token=token,
        base_url=base_url,
        skill_root=skill_root,
        doclayout_model_path=doclayout_model,
        babeldoc_cache_home=babeldoc_cache_home,
        concurrency=concurrency,
        page_limit=page_limit,
    )
    timings.update(frontend["timings"])
    document = _timed(
        timings,
        "babeldoc_il_to_html_document",
        lambda: build_html_document(
            frontend["source_pdf"],
            frontend["source_docs"],
            frontend["translated_docs"],
            output_dir / "assets",
        ),
    )
    repair = _timed(
        timings,
        "lawai_placeholder_order_repair",
        lambda: _repair_placeholder_order_with_lawai(
            document, token=token, base_url=base_url
        ),
    )
    document_path = output_dir / "document.json"
    _write_json(document_path, document)

    html_path = output_dir / "当前 DocLayout + HTML 排版版.html"
    html_result = _timed(
        timings,
        "html_layout",
        lambda: build_streaming_html(document, html_path, flow_tables=flow_tables),
    )
    pdf_path = output_dir / "当前 DocLayout + HTML 排版版 PDF.pdf"
    pdf_ok = _timed(
        timings,
        "pdf_layout_pymupdf",
        lambda: _render_pdf_pymupdf(document, frontend["source_pdf"], pdf_path, dpi=150),
    )
    try:
        qa = _timed(
            timings,
            "quality_assurance",
            lambda: _quality_report(document, pdf_path, html_result.get("fit_evidence", [])),
        )
    except Exception as exc:  # QA 不应阻断交付
        qa = {"automatic_quality_pass": False, "qa_error": str(exc)[:300]}

    proxy_stats_path = output_dir / "translation_proxy_stats.json"
    _write_json(proxy_stats_path, frontend["proxy_stats"])
    manifest = {
        "schema_version": "1.0",
        "status": "ok" if qa["automatic_quality_pass"] else "partial",
        "source_pdf": str(pdf),
        "page_limit": page_limit,
        "architecture": {
            "parse_layout_formula_translation": "double6-skill-policy-plus-babeldoc",
            "doclayout": "BabelDOC DocLayout ONNX enabled",
            "interception_point": "after ILTranslator, before Typesetting",
            "disabled_final_stages": ["BabelDOC Typesetting", "BabelDOC PDFCreater"],
            "only_final_layout_engine": "PyMuPDF Story (direct from IL, pixel-aligned with pdf2zh mono)",
        },
        "runtime_versions": {
            "babeldoc": frontend["babeldoc_version"],
            "doclayout_model": frontend["doclayout_model"],
            "babeldoc_cache_home": frontend["babeldoc_cache_home"],
            "translation_concurrency": frontend["concurrency"],
        },
        "translation": {
            "backend": translation_backend_name(base_url),
            "base_url": base_url,
            "model": "deepseek-v4-flash" if translation_backend_name(base_url) == "deepseek" else None,
            "auth": "DEEPSEEK_API_KEY environment variable",
            "external_translation_api": translation_backend_name(base_url) == "deepseek",
            "proxy_stats": str(proxy_stats_path),
        },
        "outputs": {
            "pdf": str(pdf_path),
            "html": str(html_path),
            "document": str(document_path),
            "babeldoc_source_il": str(
                frontend["babeldoc_working_dir"] / "styles_and_formulas.json"
            ),
            "babeldoc_translated_il": str(
                frontend["babeldoc_working_dir"] / "il_translated.json"
            ),
        },
        "qa": qa,
        "translation_edge_case_repair": repair,
        "timings_seconds": timings,
        "total_seconds": round(sum(timings.values()), 3),
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    manifest["manifest"] = str(manifest_path)
    return manifest


def render_cached_babeldoc_il(
    source_pdf: Path,
    source_il: Path,
    translated_il: Path,
    output_dir: Path,
    *,
    output_stem: str,
    inherited_manifest: dict | None = None,
    repair_placeholder_order: bool = False,
    token: str = "",
    base_url: str = DEFAULT_BASE_URL,
    flow_tables: dict[int, list[tuple[float, str]]] | None = None,
) -> dict:
    """Resume after BabelDOC translation; API is used only for optional repair."""

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    source_docs = _timed(
        timings, "load_babeldoc_source_il", lambda: load_document_il(source_il)
    )
    translated_docs = _timed(
        timings,
        "load_babeldoc_translated_il",
        lambda: load_document_il(translated_il),
    )
    document = _timed(
        timings,
        "babeldoc_il_to_html_document",
        lambda: build_html_document(
            source_pdf, source_docs, translated_docs, output_dir / "assets"
        ),
    )
    if repair_placeholder_order:
        repair = _timed(
            timings,
            "lawai_placeholder_order_repair",
            lambda: _repair_placeholder_order_with_lawai(
                document, token=token, base_url=base_url
            ),
        )
    else:
        repair = {"repaired_blocks": [], "failures": []}
    if token:
        table_repair = _timed(
            timings,
            "table_cell_translation",
            lambda: _translate_table_cells(
                document,
                token=token,
                base_url=base_url,
                source_pdf=source_pdf,
                concurrency=8,
            ),
        )
    else:
        table_repair = {"translated_cells": 0}
    document_path = output_dir / "document.json"
    _write_json(document_path, document)

    # Blank every translated block (including the now-translated table cells)
    # out of the background raster so the absolute-position overlay matches
    # pdf2zh / scheme-3 (mono: original text removed, only translated overlay
    # + original vector graphics remain).  Generic for any input PDF.
    _regenerate_backgrounds(document, source_pdf, scale=2.0)

    html_path = output_dir / f"{output_stem}.babeldoc-html.zh.html"
    html_result = _timed(
        timings,
        "html_layout",
        lambda: build_babeldoc_html(document, html_path),
    )
    pdf_path = output_dir / f"{output_stem}.babeldoc-html.zh.pdf"
    _timed(
        timings,
        "pdf_layout_pymupdf",
        lambda: _render_pdf_pymupdf(document, source_pdf, pdf_path, dpi=150),
    )
    try:
        qa = _timed(
            timings,
            "quality_assurance",
            lambda: _quality_report(document, pdf_path, html_result.get("fit_evidence", [])),
        )
    except Exception as exc:
        qa = {"automatic_quality_pass": False, "qa_error": str(exc)[:300]}
    manifest = {
        "schema_version": "1.0",
        "status": "ok" if qa["automatic_quality_pass"] else "partial",
        "resumed_from_cached_pretypeset_il": True,
        "source_pdf": str(Path(source_pdf).resolve()),
        "architecture": {
            "interception_point": "after ILTranslator, before Typesetting",
            "disabled_final_stages": ["BabelDOC Typesetting", "BabelDOC PDFCreater"],
            "only_final_layout_engine": "PyMuPDF Story (direct from IL, pixel-aligned with pdf2zh mono)",
        },
        "outputs": {
            "pdf": str(pdf_path),
            "html": str(html_path),
            "document": str(document_path),
            "babeldoc_source_il": str(Path(source_il).resolve()),
            "babeldoc_translated_il": str(Path(translated_il).resolve()),
        },
        "qa": qa,
        "translation_edge_case_repair": repair,
        "render_timings_seconds": timings,
        "render_total_seconds": round(sum(timings.values()), 3),
    }
    if inherited_manifest:
        manifest["frontend_run"] = inherited_manifest
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    manifest["manifest"] = str(manifest_path)
    return manifest
