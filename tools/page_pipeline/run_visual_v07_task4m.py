# -*- coding: utf-8 -*-
"""Fresh full-document visual-v07 regression runner (diagnostic only).

The raw-to-PageModel source chains are produced by ``run_document`` in fresh
Task 4M roots.  This runner consumes only those fresh models/translations,
renders every page through the current visual production path, merges the
one-page outputs into two complete PDFs, and executes final-artifact QA.

No renderer/layout/ownership/typography/math policy is changed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import pymupdf
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for path in (HERE, REPO / "tools" / "table_html_render",
             REPO / "实现源码" / "pdf_translator"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import run_visual_v06_checkpoint as visual  # noqa: E402
from figure_internal_text_ownership_audit import (  # noqa: E402
    figure_internal_text_ownership_audit,
)
from inline_math_reconstruction_qa import (  # noqa: E402
    audit_inline_math_reconstruction,
)
from math_aware_block_measurement_qa import (  # noqa: E402
    audit_math_aware_block_measurement,
)
from physical_pdf_qa import physical_pdf_qa  # noqa: E402
from run_visual_v07_task4i import (  # noqa: E402
    _dom_structure_trace,
    _final_pdf_trace,
    _structure_metrics,
)
from run_visual_v07_task4k import _measurement_payload  # noqa: E402


OUT = REPO / "outputs" / "visual_v07_task4m"
SOURCE_ROOT = OUT / "fresh_runs_v2"
VISUAL_ROOT = OUT / "fresh_visual_pages"
DOCUMENT_ROOT = OUT / "fresh_documents"
REVIEW = OUT / "review_bundle"
SCRATCH = REPO / "tmp" / "pdfs" / "visual_v07_task4m"

POPPLER = (Path.home() / ".cache" / "codex-runtimes"
           / "codex-primary-runtime" / "dependencies" / "native"
           / "poppler" / "Library" / "bin" / "pdftoppm.exe")

DOCS = {
    "ppat": {
        "label": "PPAT",
        "source_pdf": Path(r"C:\Users\74496\Desktop\PPAT.pdf"),
        "source_chain": SOURCE_ROOT / "ppat_source_chain",
        "page_count": 12,
        "full_pdf": DOCUMENT_ROOT / "PPAT_fresh_zh_visual.pdf",
    },
    "2504": {
        "label": "2504",
        "source_pdf": Path(r"C:\Users\74496\Desktop\2504.05732v2.pdf"),
        "source_chain": SOURCE_ROOT / "2504_source_chain",
        "page_count": 19,
        "full_pdf": DOCUMENT_ROOT / "2504_fresh_zh_visual.pdf",
    },
}

COVERAGE_METRICS = {
    "final_visible_translation_coverage",
    "translation_pipeline_coverage",
}


def _load(path: str | Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _dump(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pdf_pages(path: str | Path) -> int:
    document = pymupdf.open(str(path))
    try:
        return int(document.page_count)
    finally:
        document.close()


def _source_inventory() -> dict[str, Any]:
    inventory = {}
    failures = []
    for key, info in DOCS.items():
        raw = info["source_pdf"]
        chain = info["source_chain"]
        expected = int(info["page_count"])
        page_dirs = sorted((chain / "pages").glob("p[0-9][0-9][0-9]"))
        models = [page / "stitched_page_model.json" for page in page_dirs]
        translations = [page / "translation.json" for page in page_dirs]
        htmls = [page / "zh.html" for page in page_dirs]
        pdfs = [page / "zh.pdf" for page in page_dirs]
        record = {
            "source_pdf": str(raw.resolve()),
            "source_pdf_sha256": _sha256(raw),
            "source_page_count": _pdf_pages(raw),
            "fresh_source_chain": str(chain.resolve()),
            "fresh_model_count": sum(path.exists() for path in models),
            "fresh_translation_count": sum(path.exists()
                                           for path in translations),
            "fresh_base_html_count": sum(path.exists() for path in htmls),
            "fresh_base_pdf_count": sum(path.exists() for path in pdfs),
            "translation_cache_seed_only": True,
            "old_html_or_pdf_used_as_visual_input": False,
            "source_chain_delivery_gate": (_load(
                chain / "delivery_gate.json", {}).get("decision")),
            "source_chain_visual_gate": (_load(
                chain / "visual_layout_gate.json", {}).get(
                    "hard_decision")),
        }
        inventory[key] = record
        for metric in ("source_page_count", "fresh_model_count",
                       "fresh_translation_count", "fresh_base_html_count",
                       "fresh_base_pdf_count"):
            if int(record[metric]) != expected:
                failures.append({"doc": key, "metric": metric,
                                 "actual": record[metric],
                                 "expected": expected})
    return {"documents": inventory, "failures": failures,
            "valid": not failures}


def _configure_visual_runner() -> None:
    visual.OUT = VISUAL_ROOT
    visual.DOCS = {
        key: {
            "pdf": str(info["source_pdf"]),
            "src": info["source_chain"],
            "default_pages": list(range(1, int(info["page_count"]) + 1)),
        }
        for key, info in DOCS.items()
    }
    visual.TABLE_TRANSLATION_CACHE_PATHS = [
        DOCS["ppat"]["source_chain"] / "translation_cache.json",
        DOCS["2504"]["source_chain"] / "translation_cache.json",
        REPO / "outputs" / "phase4c_document_batch"
        / "translation_cache.json",
    ]


def _merge_pages(doc_key: str) -> dict[str, Any]:
    info = DOCS[doc_key]
    expected = int(info["page_count"])
    paths = [VISUAL_ROOT / f"{doc_key}_p{page:03d}"
             / "zh_visual.pdf" for page in range(1, expected + 1)]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        return {"created": False, "missing": missing}
    DOCUMENT_ROOT.mkdir(parents=True, exist_ok=True)
    merged = pymupdf.open()
    try:
        for path in paths:
            page_doc = pymupdf.open(str(path))
            try:
                merged.insert_pdf(page_doc)
            finally:
                page_doc.close()
        merged.save(str(info["full_pdf"]), garbage=4, deflate=True)
    finally:
        merged.close()
    return {
        "created": True,
        "path": str(info["full_pdf"].resolve()),
        "page_count": _pdf_pages(info["full_pdf"]),
        "sha256": _sha256(info["full_pdf"]),
        "source_page_pdf_count": len(paths),
    }


def render_all_pages(start_at: tuple[str, int] | None = None) -> dict[str, Any]:
    inventory = _source_inventory()
    if not inventory["valid"]:
        raise RuntimeError("fresh source-chain inventory incomplete: %s"
                           % inventory["failures"])
    _configure_visual_runner()
    VISUAL_ROOT.mkdir(parents=True, exist_ok=True)
    started = time.time()
    results = []
    partitions = {}
    closures = {}
    for doc_key, info in DOCS.items():
        pages = list(range(1, int(info["page_count"]) + 1))
        partition = visual.build_document_partition(doc_key, pages)
        partitions[doc_key] = partition["targets"]
        closures[doc_key] = partition["closure_qa"]
        _dump(VISUAL_ROOT / f"{doc_key}_fragment_closure_qa.json", {
            "schema_version": "visual_v07.task4m.fragment_closure.v1",
            "closure_qa": partition["closure_qa"],
        })
    ordered_docs = list(DOCS)
    for doc_key, info in DOCS.items():
        for page in range(1, int(info["page_count"]) + 1):
            page_dir = VISUAL_ROOT / f"{doc_key}_p{page:03d}"
            page_dir.mkdir(parents=True, exist_ok=True)
            if start_at is not None:
                start_doc, start_page = start_at
                before_start = (
                    ordered_docs.index(doc_key) < ordered_docs.index(start_doc)
                    or (doc_key == start_doc and page < start_page))
                if before_start:
                    previous = _load(page_dir / "visual_page_qa.json", {})
                    failure = _load(
                        page_dir / "task4m_render_failure.json", {})
                    if not previous and not failure:
                        raise RuntimeError(
                            f"cannot continue: missing prior evidence for "
                            f"{doc_key} p{page:03d}")
                    results.append({
                        "doc": doc_key, "page": page,
                        "decision": previous.get("decision")
                                    or failure.get("decision") or "blocked",
                        "passed": bool(previous.get("passed")),
                        "error": failure.get("error"),
                        "api_calls": int(previous.get("api_calls") or 0),
                        "continued_from_same_fresh_run": True,
                    })
                    continue
            print(f"[Task4M visual] {doc_key} p{page:03d}", flush=True)
            try:
                result = visual.render_visual_page(
                    doc_key, page, page_dir,
                    fragment_targets=partitions[doc_key],
                    dry_run=False,
                    table_cache_paths=visual.TABLE_TRANSLATION_CACHE_PATHS)
                closure_rows = closures[doc_key]
                result["fragment_closure"] = {
                    "closure_all_pass": all(
                        row.get("decision") == "pass"
                        for row in closure_rows.values()),
                    "partition_count": len(closure_rows),
                }
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                result = {
                    "doc": doc_key, "page": page,
                    "decision": "blocked", "passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
                _dump(page_dir / "task4m_render_failure.json", result)
            results.append({
                "doc": doc_key, "page": page,
                "decision": result.get("decision", "blocked"),
                "passed": bool(result.get("passed")),
                "error": result.get("error"),
                "api_calls": int(result.get("api_calls") or 0),
            })
    merged = {key: _merge_pages(key) for key in DOCS}
    record = {
        "schema_version": "visual_v07.task4m.render.v1",
        "started_at_epoch": started,
        "finished_at_epoch": time.time(),
        "source_inventory": inventory,
        "page_results": results,
        "merged_documents": merged,
        "visual_page_count": len(results),
        "render_failure_count": sum(bool(row.get("error"))
                                    for row in results),
        "old_html_or_final_pdf_reused": False,
        "translation_cache_reused": True,
    }
    _dump(OUT / "fresh_visual_render.json", record)
    return record


def _metric_category(metric: str) -> str:
    name = metric.casefold()
    if any(token in name for token in ("translation", "residual",
                                       "target_missing", "source_fallback")):
        return "TRANSLATION_CLOSURE"
    if "table" in name:
        return "TABLE"
    if any(token in name for token in ("math", "superscript", "subscript",
                                       "glyph", "formula")):
        return "MATH_FORMULA"
    if any(token in name for token in ("owner", "figure_internal",
                                       "duplicate_render")):
        return "SOURCE_OWNERSHIP"
    if any(token in name for token in ("measurement", "collision",
                                       "overflow", "height_stale", "ink_")):
        return "MEASUREMENT_COLLISION"
    if any(token in name for token in ("slot", "indent", "typography")):
        return "SOURCE_TEXT_SLOT_TYPOGRAPHY"
    if any(token in name for token in ("page", "physical", "renderer")):
        return "PHYSICAL_PDF"
    if any(token in name for token in ("anchor", "geometry", "crop")):
        return "HARD_ANCHOR"
    return "OTHER"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _source_chain_defects() -> list[dict[str, Any]]:
    output = []
    for doc_key, info in DOCS.items():
        chain = info["source_chain"]
        gate = _load(chain / "delivery_gate.json", {})
        for page in _load(chain / "defect_pages.json", []) or []:
            for defect in page.get("defects") or []:
                output.append({
                    "document": doc_key,
                    "page": int(page["page"]),
                    "stage": "fresh_source_chain",
                    "defect_type": defect.get("defect"),
                    "value": 1,
                    "root_cause_category": _metric_category(
                        str(defect.get("defect") or "")),
                    "detail": defect.get("detail"),
                    "evidence": str((chain / "defect_pages.json").resolve()),
                })
        if gate.get("decision") != "pass":
            output.append({
                "document": doc_key, "page": None,
                "stage": "fresh_source_chain",
                "defect_type": "source_chain_delivery_gate",
                "value": 1,
                "root_cause_category": "SOURCE_CHAIN_GATE",
                "detail": "blocked layers: %s" % ", ".join(
                    gate.get("blocked_layers") or []),
                "evidence": str((chain / "delivery_gate.json").resolve()),
            })
    return output


def _page_math_qa(doc_key: str, page: int, model: dict[str, Any],
                  html: Path, pdf: Path) -> dict[str, Any]:
    info = DOCS[doc_key]
    reconstruction = audit_inline_math_reconstruction(
        info["source_pdf"], page - 1, html, model)
    dom = _dom_structure_trace(html)
    final_trace = _final_pdf_trace(dom, pdf)
    structure = _structure_metrics(dom, final_trace)
    result = {
        "reconstruction": reconstruction,
        "dom_structure": dom,
        "final_pdf_trace": final_trace,
        "structure_metrics": structure,
    }
    _dump(html.parent / "task4m_math_qa.json", result)
    return result


def _page_measurement_qa(page_dir: Path,
                         visual_page: dict[str, Any]) -> dict[str, Any]:
    collision = visual_page.get("final_block_collision_qa") or {}
    payload = _measurement_payload(collision)
    qa = audit_math_aware_block_measurement(payload)
    result = {"payload": payload, "qa": qa}
    _dump(page_dir / "task4m_math_aware_measurement_qa.json", result)
    return result


def _page_ownership_qa(doc_key: str, page: int, model: dict[str, Any],
                       visual_page: dict[str, Any], html: Path
                       ) -> dict[str, Any]:
    figures = [region for region in model.get("regions") or []
               if region.get("type") == "figure"]
    if figures:
        audit = figure_internal_text_ownership_audit(
            model, DOCS[doc_key]["source_pdf"], page - 1,
            visual_page.get("source_text_slots") or {}, html)
    else:
        audit = {
            "schema_version": (
                "visual_v07.task4m.no_figure_ownership_fixture.v1"),
            "decision": "pass",
            "metrics": {
                "figure_internal_text_span_count": 0,
                "figure_internal_soft_text_owner_count": 0,
                "figure_internal_slot_count": 0,
                "figure_internal_double_owned_count": 0,
                "figure_internal_duplicate_render_count": 0,
                "ambiguous_figure_text_owner_count": 0,
                "ownership_evidence_missing_count": 0,
                "external_caption_count": 0,
                "external_caption_misclassified_inside_figure_count": 0,
            },
            "records": [], "external_captions": [],
        }
    audit["source_span_multi_primary_owner_count"] = int(
        (model.get("ownership") or {}).get(
            "source_span_multi_primary_owner_count") or 0)
    _dump(html.parent / "task4m_source_ownership_qa.json", audit)
    return audit


def _add_metric_defects(defects: list[dict[str, Any]], doc_key: str,
                        page: int, stage: str, metrics: dict[str, Any],
                        evidence: Path, exclude: Iterable[str] = ()) -> None:
    excluded = set(exclude)
    for metric, raw_value in metrics.items():
        if metric in excluded:
            continue
        value = _number(raw_value)
        if value is None or abs(value) < 1e-12:
            continue
        defects.append({
            "document": doc_key, "page": page, "stage": stage,
            "defect_type": metric, "value": raw_value,
            "root_cause_category": _metric_category(metric),
            "detail": f"{metric}={raw_value}",
            "evidence": str(evidence.resolve()),
        })


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _math_fixture_check() -> dict[str, Any]:
    page_dir = VISUAL_ROOT / "ppat_p005"
    html = (page_dir / "zh_visual.html").read_text(encoding="utf-8")
    document = pymupdf.open(str(page_dir / "zh_visual.pdf"))
    try:
        final_text = document[0].get_text("text")
    finally:
        document.close()
    compact_pdf = _compact(final_text)
    compact_html = _compact(
        BeautifulSoup(html, "html.parser").get_text("", strip=False))
    checks = {
        "calligraphic_forward_visible": "𝓕(⋅)" in compact_pdf,
        "calligraphic_inverse_visible": "𝓕−1(⋅)" in compact_pdf,
        "lambda_c_positive_visible": "𝜆𝑐>0" in compact_pdf,
        "inverse_has_semantic_superscript": bool(re.search(
            r"<sup[^>]*>−1</sup>", html)),
        "lambda_has_semantic_subscript": bool(re.search(
            r"<sub[^>]*>𝑐</sub>", html)),
        "html_contains_all_fixture_atoms": all(token in compact_html
            for token in ("𝓕(⋅)", "𝓕−1(⋅)", "𝜆𝑐>0")),
    }
    return {"checks": checks,
            "math_fixture_failure_count": sum(not value
                                               for value in checks.values())}


def _table_fixture_check(page_records: list[dict[str, Any]]) -> dict[str, Any]:
    target = next(row for row in page_records
                  if row["document"] == "2504" and row["page"] == 13)
    structures = target.get("table_structure") or []
    matching = [row for row in structures
                if int(row.get("row_count") or 0) == 21
                and int(row.get("col_count") or 0) == 5
                and int(row.get("cell_count") or 0) == 105
                and int(row.get("horizontal_ruling_count") or 0) == 3
                and int(row.get("vertical_ruling_count") or 0) == 0]
    return {
        "expected": {"row_count": 21, "col_count": 5,
                     "cell_count": 105,
                     "horizontal_ruling_count": 3,
                     "vertical_ruling_count": 0},
        "actual": structures,
        "fixture_pass": bool(matching),
        "p013_structure_failure_count": int(not matching),
    }


def _render_poppler(pdf: Path, prefix: Path) -> list[Path]:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(POPPLER), "-png", "-r", "72", str(pdf),
                    str(prefix)], check=True, capture_output=True)
    return sorted(prefix.parent.glob(prefix.name + "-*.png"))


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = ([r"C:\Windows\Fonts\arialbd.ttf",
              r"C:\Windows\Fonts\DejaVuSans-Bold.ttf"] if bold else
             [r"C:\Windows\Fonts\arial.ttf",
              r"C:\Windows\Fonts\DejaVuSans.ttf"])
    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def _contact_sheet(images: list[Path], title: str, columns: int = 4,
                   thumb_width: int = 250) -> Image.Image:
    opened = [Image.open(path).convert("RGB") for path in images]
    thumbs = []
    for index, image in enumerate(opened, 1):
        copy = image.copy()
        copy.thumbnail((thumb_width, 360), Image.Resampling.LANCZOS)
        panel = Image.new("RGB", (thumb_width + 20, 400), "white")
        panel.paste(copy, ((panel.width - copy.width) // 2, 24))
        draw = ImageDraw.Draw(panel)
        draw.text((10, 4), f"p{index:03d}", fill=(20, 70, 45),
                  font=_font(14, True))
        thumbs.append(panel)
    rows = math.ceil(len(thumbs) / columns)
    sheet = Image.new("RGB", (columns * (thumb_width + 20) + 30,
                              rows * 410 + 70), (245, 248, 246))
    draw = ImageDraw.Draw(sheet)
    draw.text((20, 18), title, fill=(18, 80, 48), font=_font(25, True))
    for index, panel in enumerate(thumbs):
        x = 15 + (index % columns) * (thumb_width + 20)
        y = 62 + (index // columns) * 410
        sheet.paste(panel, (x, y))
        draw.rectangle((x, y, x + panel.width, y + panel.height),
                       outline=(65, 125, 90), width=2)
    for image in opened:
        image.close()
    return sheet


def _paired_contact_sheet(source: list[Path], target: list[Path],
                          title: str) -> Image.Image:
    pairs = []
    for index, (source_path, target_path) in enumerate(zip(source, target), 1):
        source_image = Image.open(source_path).convert("RGB")
        target_image = Image.open(target_path).convert("RGB")
        source_image.thumbnail((210, 300), Image.Resampling.LANCZOS)
        target_image.thumbnail((210, 300), Image.Resampling.LANCZOS)
        panel = Image.new("RGB", (460, 345), "white")
        panel.paste(source_image, (10, 34))
        panel.paste(target_image, (240, 34))
        draw = ImageDraw.Draw(panel)
        draw.text((10, 7), f"p{index:03d} SOURCE",
                  fill=(65, 65, 65), font=_font(13, True))
        draw.text((240, 7), f"p{index:03d} TARGET",
                  fill=(20, 100, 55), font=_font(13, True))
        pairs.append(panel)
        source_image.close()
        target_image.close()
    columns = 2
    rows = math.ceil(len(pairs) / columns)
    sheet = Image.new("RGB", (columns * 470 + 30, rows * 355 + 70),
                      (245, 248, 246))
    draw = ImageDraw.Draw(sheet)
    draw.text((20, 18), title, fill=(18, 80, 48), font=_font(24, True))
    for index, panel in enumerate(pairs):
        x = 15 + (index % columns) * 470
        y = 62 + (index // columns) * 355
        sheet.paste(panel, (x, y))
        draw.rectangle((x, y, x + panel.width, y + panel.height),
                       outline=(80, 125, 100), width=2)
    return sheet


def _review_bundle(evidence: dict[str, Any]) -> dict[str, Any]:
    REVIEW.mkdir(parents=True, exist_ok=True)
    if SCRATCH.exists():
        resolved = SCRATCH.resolve()
        expected_parent = (REPO / "tmp" / "pdfs").resolve()
        if expected_parent not in resolved.parents:
            raise RuntimeError("unsafe scratch path")
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    files = {}
    rendered = {}
    try:
        for key, info in DOCS.items():
            rendered[(key, "source")] = _render_poppler(
                info["source_pdf"], SCRATCH / f"{key}_source")
            rendered[(key, "target")] = _render_poppler(
                info["full_pdf"], SCRATCH / f"{key}_target")

        for key, filename in (("ppat", "PPAT_full_12_pages_contact_sheet.png"),
                              ("2504", "2504_full_19_pages_contact_sheet.png")):
            sheet = _contact_sheet(
                rendered[(key, "target")],
                f"{DOCS[key]['label']} fresh target - all pages")
            path = REVIEW / filename
            sheet.save(path)
            files[filename] = path
        for key, filename in (("ppat", "PPAT_source_target_contact_sheet.png"),
                              ("2504", "2504_source_target_contact_sheet.png")):
            sheet = _paired_contact_sheet(
                rendered[(key, "source")], rendered[(key, "target")],
                f"{DOCS[key]['label']} fresh source / target")
            path = REVIEW / filename
            sheet.save(path)
            files[filename] = path

        metrics = evidence["metrics"]
        decision = evidence["decision"].upper()
        color = (22, 125, 65) if decision == "PASS" else (180, 45, 35)
        summary = Image.new("RGB", (1600, 900), (246, 248, 247))
        draw = ImageDraw.Draw(summary)
        draw.text((40, 28),
                  f"visual-v07 Task 4M - Fresh Full Regression {decision}",
                  fill=color, font=_font(31, True))
        draw.text((40, 82),
                  "31 fresh pages | PPAT 12 + 2504 19 | no old HTML/PDF reused",
                  fill=(50, 60, 55), font=_font(17))
        rows = [
            ("Physical pages", f"PPAT {metrics['ppat_final_page_count']}/12 | "
             f"2504 {metrics['2504_final_page_count']}/19"),
            ("Math", f"loss {metrics['math_atom_loss_count']} | duplicate "
             f"{metrics['math_atom_duplicate_count']} | order "
             f"{metrics['math_atom_order_error_count']}"),
            ("Measurement", f"stale {metrics['dom_height_stale_count']} | "
             f"ink outside {metrics['ink_outside_measurement_count']} | "
             f"collision {metrics['final_block_collision_count']}"),
            ("Ownership", f"Figure duplicate "
             f"{metrics['figure_internal_duplicate_render_count']} | "
             f"multi-owner {metrics['source_span_multi_primary_owner_count']}"),
            ("Table", f"required untranslated "
             f"{metrics['untranslated_required_table_cell_count']} | overflow "
             f"{metrics['table_cell_overflow_count']} | p013 structure fail "
             f"{metrics['p013_structure_failure_count']}"),
            ("Fresh source chain", f"hard evidence rows "
             f"{metrics['source_chain_hard_defect_count']}"),
            ("All final defects", str(metrics["all_hard_defect_count"])),
        ]
        y = 145
        for label, value in rows:
            draw.rounded_rectangle((40, y, 1560, y + 68), radius=9,
                                   fill="white", outline=(205, 215, 208),
                                   width=2)
            draw.text((62, y + 18), label, fill=(25, 70, 45),
                      font=_font(18, True))
            draw.text((390, y + 18), value,
                      fill=(25, 25, 25), font=_font(18))
            y += 82
        defects = evidence.get("defects") or []
        draw.text((40, 730), "First recorded defects (diagnostic only)",
                  fill=(80, 35, 30), font=_font(18, True))
        for index, defect in enumerate(defects[:4]):
            page = ("document" if defect.get("page") is None
                    else f"p{int(defect['page']):03d}")
            line = (f"{defect['document']} {page} | {defect['stage']} | "
                    f"{defect['defect_type']} = {defect['value']}")
            draw.text((58, 765 + index * 28), line,
                      fill=(80, 45, 40), font=_font(14))
        summary_path = REVIEW / "regression_summary.png"
        summary.save(summary_path)
        files[summary_path.name] = summary_path
    finally:
        if SCRATCH.exists():
            shutil.rmtree(SCRATCH)

    index = {
        "schema_version": "visual_v07.task4m.review_index.v1",
        "decision": evidence["decision"],
        "flat_bundle": True,
        "source_renderer": "Poppler pdftoppm 72 DPI",
        "files": [{"name": name, "sha256": _sha256(path)}
                  for name, path in sorted(files.items())],
    }
    _dump(REVIEW / "review_index.json", index)
    (REVIEW / "REVIEW_README.md").write_text(
        "# Task 4M review bundle\n\n"
        "This flat bundle contains all-page target contact sheets, paired "
        "source/target contact sheets, and a regression summary. Every image "
        "was created from the fresh Task 4M PDFs with Poppler. The bundle is "
        "diagnostic only; no source or production artifact was edited.\n",
        encoding="utf-8")
    return index


def _report(evidence: dict[str, Any]) -> str:
    metrics = evidence["metrics"]
    failures = evidence.get("defects") or []
    rows = []
    for defect in failures:
        page = ("document" if defect.get("page") is None
                else str(defect["page"]))
        detail = str(defect.get("detail") or "").replace("|", "\\|")
        rows.append(
            f"| {defect['document']} | {page} | {defect['stage']} | "
            f"{defect['defect_type']} | {defect['root_cause_category']} | "
            f"{detail} | `{defect['evidence']}` |")
    defect_table = "\n".join(rows) if rows else (
        "| - | - | - | - | - | no defects | - |")
    return f"""# Fresh Full-Document End-to-End Regression Report

Decision: **{evidence['decision'].upper()}**

## Fresh-run lineage

Both documents were rebuilt from the original PDFs under the current HEAD.
The source-chain PageModels, translations, HTML and PDFs were newly generated
inside `outputs/visual_v07_task4m/fresh_runs_v2`. The visual-v07 renderer then
created every `zh_visual.html` and `zh_visual.pdf` in a separate fresh root.
No old HTML, final PDF, or frozen page artifact was used as production input.
Translation caches were reused as explicitly allowed.

- PPAT source pages: 12; final visual pages: {metrics['ppat_final_page_count']}.
- 2504 source pages: 19; final visual pages: {metrics['2504_final_page_count']}.
- Total final pages audited: {metrics['audited_physical_page_count']}.
- Unexpected extra pages: {metrics['unexpected_extra_page_count']}.
- Missing physical pages: {metrics['missing_physical_page_count']}.

An initial PPAT attempt used the system interpreter without `babeldoc`; it was
excluded and never supplied HTML/PDF to this run. The accepted fresh run used
the repository `.venv`, where DocLayout was available. This is recorded as an
environment-selection event, not a production fix.

## Final visual regression

Math atom loss/duplicate/order are
{metrics['math_atom_loss_count']}/{metrics['math_atom_duplicate_count']}/
{metrics['math_atom_order_error_count']}; superscript and subscript structure
loss are {metrics['superscript_structure_loss_count']} and
{metrics['subscript_structure_loss_count']}. Final-PDF atom loss is
{metrics['final_pdf_atom_loss_count']}. Fixture verification for `𝓕(⋅)`,
`𝓕^{{-1}}(⋅)`, and `𝜆𝑐 > 0` has
`math_fixture_failure_count={metrics['math_fixture_failure_count']}`.

Measurement has `dom_height_stale_count={metrics['dom_height_stale_count']}`,
`ink_outside_measurement_count={metrics['ink_outside_measurement_count']}` and
`final_block_collision_count={metrics['final_block_collision_count']}`.

Exclusive ownership has Figure-internal duplicate render
{metrics['figure_internal_duplicate_render_count']}, Figure-internal soft owner
{metrics['figure_internal_soft_text_owner_count']}, and multi-primary source
owner {metrics['source_span_multi_primary_owner_count']}.

Typography has first-line indent missing/wrong counts
{metrics['first_line_indent_missing_count']}/
{metrics['first_line_indent_wrong_count']} and slot geometry mutation
{metrics['slot_geometry_mutation_count']}.

Tables have untranslated required cells
{metrics['untranslated_required_table_cell_count']}, overflow
{metrics['table_cell_overflow_count']}, and p013 structure failure
{metrics['p013_structure_failure_count']}. The required p013 fixture remains
21x5, 105 cells, 3 horizontal rulings, and 0 vertical rulings only when that
last metric is zero.

## Source-chain gate evidence

The newly generated source chains are also part of the end-to-end evidence.
They contributed {metrics['source_chain_hard_defect_count']} recorded defect
rows. These failures were not repaired or suppressed. Consequently, Task 4M
can PASS only if both the source-chain and final visual metrics are all zero.

## Failure ledger

| Document | Page | Stage | Defect | Category | Evidence detail | Evidence file |
|---|---:|---|---|---|---|---|
{defect_table}

## Hard metrics

```json
{json.dumps(metrics, ensure_ascii=False, indent=2)}
```

This task made no production-code change and created no `visual-v07` tag.
"""


def audit_all_pages() -> dict[str, Any]:
    render_record = _load(OUT / "fresh_visual_render.json", {})
    if int(render_record.get("visual_page_count") or 0) != 31:
        raise RuntimeError("fresh visual render record does not cover 31 pages")
    defects = _source_chain_defects()
    source_chain_defect_count = len(defects)
    for row in render_record.get("page_results") or []:
        if not row.get("error"):
            continue
        doc_key = str(row["doc"])
        page = int(row["page"])
        failure_path = (VISUAL_ROOT / f"{doc_key}_p{page:03d}"
                        / "task4m_render_failure.json")
        error = str(row["error"])
        category = ("SOURCE_TEXT_SLOT_MEASUREMENT_MAPPING"
                    if "measured paragraph missing" in error
                    else "VISUAL_RENDER")
        defects.append({
            "document": doc_key, "page": page,
            "stage": "fresh_visual_render",
            "defect_type": "visual_render_failure",
            "value": 1,
            "root_cause_category": category,
            "detail": error,
            "evidence": str(failure_path.resolve()),
        })
    page_records = []
    totals: Counter[str] = Counter()
    for doc_key, info in DOCS.items():
        for page in range(1, int(info["page_count"]) + 1):
            page_dir = VISUAL_ROOT / f"{doc_key}_p{page:03d}"
            qa_path = page_dir / "visual_page_qa.json"
            html = page_dir / "zh_visual.html"
            pdf = page_dir / "zh_visual.pdf"
            model_path = (info["source_chain"] / "pages"
                          / f"p{page:03d}" / "stitched_page_model.json")
            if not all(path.exists() for path in (qa_path, html, pdf,
                                                  model_path)):
                defects.append({
                    "document": doc_key, "page": page,
                    "stage": "fresh_visual_artifact",
                    "defect_type": "missing_fresh_page_artifact",
                    "value": 1, "root_cause_category": "PHYSICAL_PDF",
                    "detail": "visual page QA/HTML/PDF/model missing",
                    "evidence": str(page_dir.resolve()),
                })
                continue
            visual_page = _load(qa_path, {})
            model = _load(model_path, {})
            hard = visual_page.get("hard_metrics") or {}
            _add_metric_defects(defects, doc_key, page, "visual_hard_gate",
                                hard, qa_path, COVERAGE_METRICS)
            for key, value in hard.items():
                if key not in COVERAGE_METRICS and _number(value) is not None:
                    totals[key] += float(value)

            math_qa = _page_math_qa(doc_key, page, model, html, pdf)
            math_metrics = dict(math_qa["reconstruction"]["metrics"])
            math_metrics.update({
                "final_pdf_atom_loss_count": math_qa["structure_metrics"][
                    "final_pdf_atom_loss_count"],
                "final_pdf_atom_order_error_count": math_qa[
                    "structure_metrics"]["final_pdf_atom_order_error_count"],
            })
            math_evidence = page_dir / "task4m_math_qa.json"
            _add_metric_defects(defects, doc_key, page, "math_atom_qa",
                                math_metrics, math_evidence)
            for key, value in math_metrics.items():
                totals[key] += int(value)

            measurement = _page_measurement_qa(page_dir, visual_page)
            measurement_metrics = measurement["qa"]["metrics"]
            measurement_evidence = (
                page_dir / "task4m_math_aware_measurement_qa.json")
            _add_metric_defects(defects, doc_key, page,
                                "math_aware_measurement_qa",
                                measurement_metrics, measurement_evidence)
            for key, value in measurement_metrics.items():
                totals[key] += int(value)

            ownership = _page_ownership_qa(
                doc_key, page, model, visual_page, html)
            ownership_metrics = ownership.get("metrics") or {}
            ownership_hard = {
                "figure_internal_soft_text_owner_count": int(
                    ownership_metrics.get(
                        "figure_internal_soft_text_owner_count") or 0),
                "figure_internal_duplicate_render_count": int(
                    ownership_metrics.get(
                        "figure_internal_duplicate_render_count") or 0),
                "erroneous_figure_internal_slot_count": int(
                    ownership_metrics.get("figure_internal_slot_count") or 0),
                "source_span_multi_primary_owner_count": int(
                    ownership.get(
                        "source_span_multi_primary_owner_count") or 0),
                "external_caption_misowned_count": int(
                    ownership_metrics.get(
                        "external_caption_misclassified_inside_figure_count")
                    or 0),
            }
            ownership_evidence = page_dir / "task4m_source_ownership_qa.json"
            _add_metric_defects(defects, doc_key, page,
                                "source_ownership_qa", ownership_hard,
                                ownership_evidence)
            for key, value in ownership_hard.items():
                totals[key] += int(value)

            page_records.append({
                "document": doc_key, "page": page,
                "visual_decision": visual_page.get("decision"),
                "hard_metrics": hard,
                "math_metrics": math_metrics,
                "measurement_metrics": measurement_metrics,
                "ownership_metrics": ownership_hard,
                "table_structure": visual_page.get("table_structure") or [],
                "table_cell_translation_metrics": (
                    (visual_page.get("table_cell_translation_qa") or {}).get(
                        "metrics") or {}),
                "source_text_slot_count": len(
                    (visual_page.get("source_text_slots") or {}).get(
                        "slots") or []),
            })

    physical = {}
    final_extra = final_missing = audited_pages = 0
    for doc_key, info in DOCS.items():
        expected = int(info["page_count"])
        path = info["full_pdf"]
        actual = _pdf_pages(path) if path.exists() else 0
        audited_pages += actual
        final_extra += max(0, actual - expected)
        final_missing += max(0, expected - actual)
        physical[doc_key] = physical_pdf_qa(
            path, page_model=None, expected_physical_page_count=expected,
            out_dir=OUT,
            output_filename=f"{doc_key}_fresh_physical_pdf_qa.json")
        if not physical[doc_key].get("all_assertions_passed", False):
            defects.append({
                "document": doc_key, "page": None,
                "stage": "full_document_physical_pdf_qa",
                "defect_type": "physical_pdf_qa_failure", "value": 1,
                "root_cause_category": "PHYSICAL_PDF",
                "detail": "full-document Physical PDF QA blocked",
                "evidence": str((OUT / f"{doc_key}_fresh_physical_pdf_qa.json").resolve()),
            })

    math_fixture = _math_fixture_check()
    if math_fixture["math_fixture_failure_count"]:
        defects.append({
            "document": "ppat", "page": 5,
            "stage": "math_fixture_qa",
            "defect_type": "math_fixture_failure_count",
            "value": math_fixture["math_fixture_failure_count"],
            "root_cause_category": "MATH_FORMULA",
            "detail": json.dumps(math_fixture["checks"], ensure_ascii=False),
            "evidence": str((VISUAL_ROOT / "ppat_p005"
                              / "task4m_math_qa.json").resolve()),
        })
    table_fixture = _table_fixture_check(page_records)
    if table_fixture["p013_structure_failure_count"]:
        defects.append({
            "document": "2504", "page": 13,
            "stage": "table_structure_qa",
            "defect_type": "p013_structure_failure_count", "value": 1,
            "root_cause_category": "TABLE",
            "detail": json.dumps(table_fixture["actual"], ensure_ascii=False),
            "evidence": str((VISUAL_ROOT / "2504_p013"
                              / "visual_page_qa.json").resolve()),
        })

    def total(name: str) -> int:
        return int(round(totals.get(name, 0)))

    metrics = {
        "ppat_source_page_count": 12,
        "ppat_final_page_count": _pdf_pages(DOCS["ppat"]["full_pdf"]),
        "2504_source_page_count": 19,
        "2504_final_page_count": _pdf_pages(DOCS["2504"]["full_pdf"]),
        "audited_physical_page_count": audited_pages,
        "unexpected_extra_page_count": final_extra,
        "missing_physical_page_count": final_missing,
        "math_atom_loss_count": total("math_atom_loss_count"),
        "math_atom_duplicate_count": total("math_atom_duplicate_count"),
        "math_atom_order_error_count": total("math_atom_order_error_count"),
        "superscript_structure_loss_count": total(
            "superscript_structure_loss_count"),
        "subscript_structure_loss_count": total(
            "subscript_structure_loss_count"),
        "final_pdf_atom_loss_count": total("final_pdf_atom_loss_count"),
        "final_pdf_atom_order_error_count": total(
            "final_pdf_atom_order_error_count"),
        "math_fixture_failure_count": int(
            math_fixture["math_fixture_failure_count"]),
        "dom_height_stale_count": total("dom_height_stale_count"),
        "ink_outside_measurement_count": total(
            "ink_outside_measurement_count"),
        "final_block_collision_count": total(
            "final_block_collision_count"),
        "figure_internal_soft_text_owner_count": total(
            "figure_internal_soft_text_owner_count"),
        "figure_internal_duplicate_render_count": total(
            "figure_internal_duplicate_render_count"),
        "source_span_multi_primary_owner_count": total(
            "source_span_multi_primary_owner_count"),
        "erroneous_figure_internal_slot_count": total(
            "erroneous_figure_internal_slot_count"),
        "first_line_indent_missing_count": total(
            "first_line_indent_missing_count"),
        "first_line_indent_wrong_count": total(
            "first_line_indent_wrong_count"),
        "slot_geometry_mutation_count": total(
            "slot_geometry_mutation_count"),
        "untranslated_required_table_cell_count": total(
            "untranslated_required_table_cell_count"),
        "table_cell_overflow_count": total("table_cell_overflow_count"),
        "p013_structure_failure_count": int(
            table_fixture["p013_structure_failure_count"]),
        "hard_anchor_moved_count": total("hard_anchor_moved_count"),
        "production_special_case_count": total(
            "production_special_case_count"),
        "source_chain_hard_defect_count": source_chain_defect_count,
        "visual_render_failure_count": int(
            render_record.get("render_failure_count") or 0),
    }
    metrics["all_hard_defect_count"] = len(defects)
    required_zero = [
        "unexpected_extra_page_count", "missing_physical_page_count",
        "math_atom_loss_count", "math_atom_duplicate_count",
        "math_atom_order_error_count", "superscript_structure_loss_count",
        "subscript_structure_loss_count", "final_pdf_atom_loss_count",
        "final_pdf_atom_order_error_count", "math_fixture_failure_count",
        "dom_height_stale_count", "ink_outside_measurement_count",
        "final_block_collision_count",
        "figure_internal_soft_text_owner_count",
        "figure_internal_duplicate_render_count",
        "source_span_multi_primary_owner_count",
        "erroneous_figure_internal_slot_count",
        "first_line_indent_missing_count", "first_line_indent_wrong_count",
        "slot_geometry_mutation_count",
        "untranslated_required_table_cell_count", "table_cell_overflow_count",
        "p013_structure_failure_count", "hard_anchor_moved_count",
        "production_special_case_count", "source_chain_hard_defect_count",
        "visual_render_failure_count", "all_hard_defect_count",
    ]
    decision = "pass" if all(metrics[name] == 0 for name in required_zero) \
        and metrics["ppat_final_page_count"] == 12 \
        and metrics["2504_final_page_count"] == 19 else "blocked"
    evidence = {
        "schema_version": "visual_v07.task4m.regression.v1",
        "decision": decision,
        "current_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, check=True,
            capture_output=True, text=True).stdout.strip(),
        "fresh_lineage": render_record.get("source_inventory"),
        "documents": render_record.get("merged_documents"),
        "metrics": metrics,
        "required_zero_metrics": required_zero,
        "math_fixture": math_fixture,
        "table_p013_fixture": table_fixture,
        "full_document_physical_qa": physical,
        "page_records": page_records,
        "defects": defects,
        "production_files_modified": False,
        "tag_created": False,
    }
    _dump(OUT / "fresh_full_document_regression.json", evidence)
    review_index = _review_bundle(evidence)
    evidence["review_bundle"] = review_index
    _dump(OUT / "checkpoint_gate.json", {
        "schema_version": "visual_v07.task4m.checkpoint.v1",
        "decision": decision,
        "metrics": metrics,
        "required_zero_metrics": required_zero,
        "failures_by_page": defects,
        "review_bundle": review_index,
        "production_files_modified": False,
        "tag_created": False,
        "commit_message": "test(visual): fresh full document regression",
    })
    (OUT / "FRESH_FULL_DOCUMENT_REGRESSION_REPORT.md").write_text(
        _report(evidence), encoding="utf-8")
    print(json.dumps({"decision": decision, "metrics": metrics,
                      "defect_count": len(defects)},
                     ensure_ascii=False, indent=2))
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("render", "audit", "all"),
                        default="all")
    parser.add_argument(
        "--start-at",
        help="continue the same fresh render at DOC:PAGE; prior pages must "
             "already have Task 4M QA or failure evidence")
    args = parser.parse_args()
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=REPO, check=True,
        capture_output=True, text=True).stdout.strip()
    if branch != "visual":
        raise RuntimeError(f"Task 4M must run on visual; current={branch}")
    start_at = None
    if args.start_at:
        doc_key, page_text = args.start_at.split(":", 1)
        if doc_key not in DOCS:
            raise ValueError(f"unknown document: {doc_key}")
        start_at = (doc_key, int(page_text))
    if args.stage in ("render", "all"):
        render_all_pages(start_at=start_at)
    if args.stage in ("audit", "all"):
        audit_all_pages()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
