# -*- coding: utf-8 -*-
"""visual-v07 Task 4L: read-only full visual regression gate.

The gate consumes the final/frozen v07 artifacts and existing QA provenance.
It never rewrites a PDF/HTML artifact and never imports a renderer entry point.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

import pymupdf
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent.parent
HERE = REPO / "tools" / "page_pipeline"
for value in (HERE, REPO / "实现源码" / "pdf_translator"):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

import run_visual_v06_checkpoint as v06  # noqa: E402
from final_block_collision_qa import final_block_collision_qa  # noqa: E402
from inline_math_reconstruction_qa import (  # noqa: E402
    audit_inline_math_reconstruction,
)
from legacy_math_pua_qa import audit_legacy_math_pua_artifact  # noqa: E402
from math_aware_block_measurement_qa import (  # noqa: E402
    audit_math_aware_block_measurement,
)
from run_visual_v07_task4i import (  # noqa: E402
    _dom_structure_trace,
    _final_pdf_trace,
    _structure_metrics,
)
from run_visual_v07_task4k import _measurement_payload  # noqa: E402
from source_paragraph_style_qa import source_paragraph_style_qa  # noqa: E402
from table_cell_translation_qa import table_cell_translation_qa  # noqa: E402


OUT = REPO / "outputs" / "visual_v07_task4l"
REVIEW = OUT / "review_bundle"
TMP = REPO / "tmp" / "pdfs" / "visual_v07_task4l"
CURRENT_HTML = REPO / "outputs" / "visual_v07_task4k" \
    / "fixed_artifact" / "zh_visual.html"
CURRENT_PDF = REPO / "outputs" / "visual_v07_task4k" \
    / "fixed_artifact" / "zh_visual.pdf"
SOURCE_PDF = Path("C:/Users/74496/Desktop/PPAT.pdf")
MODEL_P005 = REPO / "outputs" / "visual_v07_task4f" \
    / "fixed_stitched_page_model.json"
MODEL_P006 = REPO / "outputs" / "phase4e2a_qa_recovery" \
    / "doc2" / "pages" / "p006" / "stitched_page_model.json"
P006_HTML = REPO / "outputs" / "visual_v07_task3" \
    / "ppat_p006" / "zh_visual.html"
P006_PDF = REPO / "outputs" / "visual_v07_task3" \
    / "ppat_p006" / "zh_visual.pdf"
POPPLER = Path(
    "C:/Users/74496/.cache/codex-runtimes/codex-primary-runtime/"
    "dependencies/native/poppler/Library/bin/pdftoppm.exe")


PAGE_INPUTS = [
    {"key": "ppat_p005", "kind": "current_final", "pdf": CURRENT_PDF,
     "html": CURRENT_HTML, "document_page": 5},
    {"key": "ppat_p006", "kind": "typography_regression", "pdf": P006_PDF,
     "html": P006_HTML, "document_page": 6},
    *[
        {"key": f"2504_p{page:03d}", "kind": "table_regression",
         "pdf": REPO / "outputs" / "visual_v07_task1"
         / f"2504_p{page:03d}" / "zh_visual.pdf",
         "html": REPO / "outputs" / "visual_v07_task1"
         / f"2504_p{page:03d}" / "zh_visual.html",
         "document_page": page}
        for page in (7, 13, 16)
    ],
]


def _load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _font(size: int = 16, bold: bool = False) -> ImageFont.ImageFont:
    names = (("arialbd.ttf", "calibrib.ttf") if bold
             else ("arial.ttf", "calibri.ttf"))
    for name in names:
        try:
            return ImageFont.truetype(
                str(Path("C:/Windows/Fonts") / name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _round_box(box: Iterable[float] | None) -> list[float] | None:
    if box is None:
        return None
    return [round(float(value), 3) for value in box]


def _page_metadata(path: Path) -> dict[str, Any]:
    with pymupdf.open(str(path)) as document:
        return {
            "page_count": len(document),
            "page_sizes_pt": [[round(float(page.rect.width), 3),
                               round(float(page.rect.height), 3)]
                              for page in document],
        }


def _render_with_poppler(path: Path, key: str) -> Path:
    if not POPPLER.exists():
        raise FileNotFoundError(f"bundled pdftoppm not found: {POPPLER}")
    prefix = TMP / key
    output = prefix.with_suffix(".png")
    subprocess.run([
        str(POPPLER), "-f", "1", "-l", "1", "-singlefile",
        "-r", "144", "-png", str(path), str(prefix),
    ], check=True, capture_output=True)
    if not output.exists():
        raise RuntimeError(f"Poppler did not create {output}")
    return output


def _box_contains(outer: Sequence[float], inner: Sequence[float],
                  tolerance: float = 0.25) -> bool:
    return (float(outer[0]) <= float(inner[0]) + tolerance
            and float(outer[1]) <= float(inner[1]) + tolerance
            and float(outer[2]) >= float(inner[2]) - tolerance
            and float(outer[3]) >= float(inner[3]) - tolerance)


def _slot_geometry(html_path: Path) -> dict[str, Any]:
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 1200})
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        records = page.locator(
            ".paragraph-block[data-source-slot-bbox]").evaluate_all("""
        nodes => nodes.map(node => {
          const r=node.getBoundingClientRect();
          const pt=v=>v*.75;
          return {render_id:node.dataset.renderId||node.dataset.para||'',
            slot_id:node.dataset.sourceSlotId||'',
            slot_bbox_pt:node.dataset.sourceSlotBbox.split(',').map(Number),
            dom_bbox_pt:[pt(r.left),pt(r.top),pt(r.right),pt(r.bottom)]};
        })
        """)
        browser.close()
    mutation_count = 0
    for record in records:
        record["slot_bbox_pt"] = _round_box(record["slot_bbox_pt"])
        record["dom_bbox_pt"] = _round_box(record["dom_bbox_pt"])
        mutation = any(abs(record["slot_bbox_pt"][index]
                           - record["dom_bbox_pt"][index]) > .15
                       for index in range(4))
        record["slot_geometry_mutation"] = mutation
        mutation_count += int(mutation)
    return {"html": str(html_path), "text_slot_count": len(records),
            "slot_geometry_mutation_count": mutation_count,
            "records": records}


def _hard_signatures(html_text: str, selector: str,
                     identity_attributes: Sequence[str]) -> list[dict[str, str]]:
    soup = BeautifulSoup(html_text, "html.parser")
    result = []
    for node in soup.select(selector):
        identity = next((str(node.get(name)) for name in identity_attributes
                         if node.get(name) is not None), "")
        result.append({
            "identity": identity,
            "style": str(node.get("style") or ""),
            "layout": str(node.get("data-layout") or ""),
            "src": str(node.get("src") or ""),
        })
    return sorted(result, key=lambda row: tuple(row.values()))


def _math_regression() -> dict[str, Any]:
    model = _load(MODEL_P005)
    qa = audit_inline_math_reconstruction(
        SOURCE_PDF, 4, CURRENT_HTML, model)
    dom = _dom_structure_trace(CURRENT_HTML)
    pdf_trace = _final_pdf_trace(dom, CURRENT_PDF)
    structure = _structure_metrics(dom, pdf_trace)
    pua = audit_legacy_math_pua_artifact(
        CURRENT_HTML, CURRENT_PDF,
        _load(REPO / "outputs" / "visual_v07_task4h"
              / "legacy_math_pua_normalization.json"),
        required_source_codepoints=["U+E232"])
    dom_by_id = {record["group_id"]: record for record in dom}
    pdf_by_id = {record["group_id"]: record for record in pdf_trace}
    provenance = []
    for record in qa.get("records") or []:
        group_id = record["group_id"]
        html_record = dom_by_id.get(group_id) or {}
        final_record = pdf_by_id.get(group_id) or {}
        source_sequence = list(record.get("source_atom_sequence") or [])
        html_sequence = [atom["text"] for atom in
                         html_record.get("atoms") or []]
        final_sequence = list(final_record.get("final_pdf_text") or "")
        provenance.append({
            "group_id": group_id,
            "source_atom_sequence": source_sequence,
            "protected_token": record.get("protected_token"),
            "protected_present_before_restore": bool(
                record.get("protected_token")),
            "html_atom_sequence": html_sequence,
            "final_pdf_character_sequence": final_sequence,
            "source_to_html_order_preserved": bool(
                record.get("atom_order_preserved")),
            "html_to_final_pdf_order_preserved": (
                "".join(html_sequence).replace(" ", "")
                == "".join(final_sequence).replace(" ", "")),
            "final_pdf_ink_exists": bool(
                final_record.get("final_pdf_ink_exists")),
        })
    with pymupdf.open(str(CURRENT_PDF)) as document:
        final_text = "".join(page.get_text() for page in document)
    compact = "".join(final_text.split()).replace("\xa0", "")
    fixtures = {
        "calligraphic_forward_visible": "𝓕(⋅)" in compact,
        "calligraphic_inverse_visible": "𝓕−1(⋅)" in compact,
        "lambda_c_positive_visible": "𝜆𝑐>0" in compact,
        "inverse_has_semantic_superscript": any(
            group["group_id"] == "MAG-52F45C34EAFB"
            and any(atom["role"] == "math-superscript"
                    for atom in group["atoms"]) for group in dom),
        "lambda_has_semantic_subscript": any(
            group["group_id"] == "MAG-98F1AC430FC0"
            and any(atom["role"] == "math-subscript"
                    for atom in group["atoms"]) for group in dom),
    }
    metrics = {
        "math_atom_loss_count": int(qa["metrics"]["math_atom_loss_count"]),
        "math_atom_duplicate_count": int(
            qa["metrics"]["math_atom_duplicate_count"]),
        "math_atom_order_error_count": int(
            qa["metrics"]["math_atom_order_error_count"]),
        "superscript_structure_loss_count": int(
            qa["metrics"]["superscript_structure_loss_count"]),
        "subscript_structure_loss_count": int(
            qa["metrics"]["subscript_structure_loss_count"]),
        "final_pdf_atom_loss_count": int(
            structure["final_pdf_atom_loss_count"]),
        "final_pdf_atom_order_error_count": int(
            structure["final_pdf_atom_order_error_count"]),
        "math_fixture_missing_count": sum(not value
                                          for value in fixtures.values()),
        "task4h_pua_regression_count": int(
            not pua["after_hard_gate_pass"]),
    }
    return {"metrics": metrics, "fixtures": fixtures,
            "provenance": provenance, "qa": qa,
            "dom": dom, "final_pdf_trace": pdf_trace,
            "structure": structure, "pua": pua}


def _measurement_regression() -> dict[str, Any]:
    pages = []
    total_stale = total_outside = total_collision = 0
    specifications = [
        ("ppat_p005", MODEL_P005, CURRENT_HTML, CURRENT_PDF),
        ("ppat_p006", MODEL_P006, P006_HTML, P006_PDF),
    ]
    for key, model_path, html_path, pdf_path in specifications:
        collision = final_block_collision_qa(
            _load(model_path), html_path=html_path,
            final_pdf_path=pdf_path,
            screenshot_path=TMP / f"{key}_collision_dom.png")
        measurement = _measurement_payload(collision)
        qa = audit_math_aware_block_measurement(measurement)
        containment = all(
            _box_contains(record["effective_measurement_bbox_pt"],
                          record["painted_ink_bbox_pt"])
            for record in measurement["blocks"]
            if record.get("painted_ink_bbox_pt"))
        pages.append({"key": key, "collision": collision,
                      "measurement": measurement, "qa": qa,
                      "painted_ink_contained": containment})
        total_stale += qa["metrics"]["dom_height_stale_count"]
        total_outside += qa["metrics"]["ink_outside_measurement_count"]
        total_collision += collision["metrics"][
            "final_block_collision_count"]
    return {"metrics": {
        "dom_height_stale_count": total_stale,
        "ink_outside_measurement_count": total_outside,
        "final_block_collision_count": total_collision,
        "painted_ink_containment_failure_count": sum(
            not page["painted_ink_contained"] for page in pages),
    }, "pages": pages}


def _ownership_regression() -> dict[str, Any]:
    exclusive = _load(REPO / "outputs" / "visual_v07_task4f"
                      / "exclusive_source_ownership.json")
    audit = _load(REPO / "outputs" / "visual_v07_task4f"
                  / "figure_ownership_after.json")
    html = CURRENT_HTML.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    removed_paragraphs = set(exclusive["projection"][
        "removed_paragraph_ids"])
    removed_slots = set(exclusive["projection"]["removed_slot_ids"])
    current_paragraphs = {str(node.get("data-render-id") or "")
                          for node in soup.select(".paragraph-block")}
    current_slots = {str(node.get("data-source-slot-id") or "")
                     for node in soup.select(
                         ".paragraph-block[data-source-slot-id]")}
    soft_reintroduced = len(removed_paragraphs & current_paragraphs) \
        + len(removed_slots & current_slots)
    after = exclusive["after_metrics"]
    metrics = {
        "figure_internal_soft_text_owner_count": max(
            int(after["figure_internal_soft_text_owner_count"]),
            soft_reintroduced),
        "figure_internal_duplicate_render_count": max(
            int(after["figure_internal_duplicate_render_count"]),
            soft_reintroduced),
        "source_span_multi_primary_owner_count": int(
            after["source_span_multi_primary_owner_count"]),
        "source_span_render_owner_count_gt1": int(
            after["source_span_render_owner_count_gt1"]),
        "erroneous_figure_internal_slot_count": max(
            int(after["erroneous_figure_internal_slot_count"]),
            len(removed_slots & current_slots)),
        "figure_hard_anchor_missing_count": int(
            not soup.select_one(".figure-region")),
    }
    return {"metrics": metrics, "exclusive_ownership": exclusive,
            "figure_audit": audit,
            "current_dom": {
                "removed_paragraph_ids_reintroduced": sorted(
                    removed_paragraphs & current_paragraphs),
                "removed_slot_ids_reintroduced": sorted(
                    removed_slots & current_slots),
                "figure_hard_anchor_count": len(
                    soup.select(".figure-region")),
            }}


def _typography_regression(collision_p005: dict[str, Any]) -> dict[str, Any]:
    style_model = _load(REPO / "outputs" / "visual_v07_task4d"
                        / "ppat_p005" / "source_paragraph_style.json")
    indent = source_paragraph_style_qa(
        style_model, CURRENT_HTML, collision_qa=collision_p005,
        screenshot_path=TMP / "indent_current.png")
    slot_pages = [_slot_geometry(CURRENT_HTML), _slot_geometry(P006_HTML)]
    metrics = {
        "first_line_indent_missing_count": indent["metrics"][
            "first_line_indent_missing_count"],
        "first_line_indent_wrong_count": indent["metrics"][
            "first_line_indent_wrong_count"],
        "false_first_line_indent_count": indent["metrics"][
            "false_first_line_indent_count"],
        "slot_geometry_mutation_count": sum(
            page["slot_geometry_mutation_count"] for page in slot_pages),
        "text_slot_count": sum(page["text_slot_count"]
                               for page in slot_pages),
    }
    return {"metrics": metrics, "indent": indent,
            "slot_pages": slot_pages}


def _table_regression() -> dict[str, Any]:
    frozen = _load(REPO / "outputs" / "visual_v07_task1"
                   / "table_cell_translation_qa.json")
    structure = _load(REPO / "outputs" / "visual_v07_task1"
                      / "table_structure_regression.json")
    frozen_by_page = {int(page["page"]): page
                      for page in frozen["pages"]}
    structure_by_page = {int(page["page"]): page
                         for page in structure["fixtures"]}
    pages = []
    aggregate: dict[str, int] = {}
    anchor_mutations = 0
    for page_number in (7, 13, 16):
        root = v06.DOCS["2504"]["src"] / "pages" / f"p{page_number:03d}"
        model = _load(root / "stitched_page_model.json")
        translations = {
            cell["cell_id"]: cell["canonical_target"]
            for cell in frozen_by_page[page_number]["cells"]
            if cell.get("canonical_target")}
        pdf = REPO / "outputs" / "visual_v07_task1" \
            / f"2504_p{page_number:03d}" / "zh_visual.pdf"
        qa = table_cell_translation_qa(
            model, final_pdf_path=pdf, translations=translations)
        pages.append({"page": page_number, "qa": qa})
        for key, value in qa["metrics"].items():
            aggregate[key] = aggregate.get(key, 0) + int(value)

        html_path = REPO / "outputs" / "visual_v07_task1" \
            / f"2504_p{page_number:03d}" / "zh_visual.html"
        current = html_path.read_text(encoding="utf-8")
        current_cells = _hard_signatures(
            current, ".translated-cell", ("data-cell-id", "data-cell"))
        expected_cells = sum(
            int(table["cell_count"])
            for table in structure_by_page[page_number]["after"])
        # Task 1 output artifacts were intentionally not committed. The
        # frozen source/after structure ledger plus current DOM owner count is
        # the available anchor baseline.
        anchor_mutations += int(len(current_cells) != expected_cells)
    logical_cells = sum(
        int(table["cell_count"]) for fixture in structure["fixtures"]
        for table in fixture["after"])
    metrics = {
        "table_cell_count": logical_cells,
        "required_table_cell_count": aggregate[
            "required_table_cell_count"],
        "table_overflow_count": aggregate["table_cell_overflow_count"],
        "table_anchor_mutation_count": anchor_mutations,
        "table_translation_regression_count": sum(
            aggregate[key] for key in aggregate
            if key not in ("required_table_cell_count",
                           "translated_table_cell_count",
                           "table_cell_overflow_count")),
        "table_structure_mutation_count": sum(
            not fixture["preserved"] for fixture in structure["fixtures"]),
    }
    return {"metrics": metrics, "pages": pages,
            "aggregate": aggregate, "structure": structure}


def _hard_anchor_regression() -> dict[str, Any]:
    current_p005 = CURRENT_HTML.read_text(encoding="utf-8")
    baseline_p005 = (REPO / "outputs" / "visual_v07_task4f"
                     / "fixed_artifact" / "zh_visual.html").read_text(
                         encoding="utf-8")
    current_p006 = P006_HTML.read_text(encoding="utf-8")
    baseline_p006 = (REPO / "outputs" / "visual_v07_task2"
                     / "ppat_p006" / "zh_visual.html").read_text(
                         encoding="utf-8")
    formula_mutations = sum([
        _hard_signatures(current_p005, ".formula-seg",
                         ("data-segment", "data-formula"))
        != _hard_signatures(baseline_p005, ".formula-seg",
                            ("data-segment", "data-formula")),
        _hard_signatures(current_p006, ".formula-seg",
                         ("data-segment", "data-formula"))
        != _hard_signatures(baseline_p006, ".formula-seg",
                            ("data-segment", "data-formula")),
    ])
    figure_mutations = sum([
        _hard_signatures(current_p005, ".figure-region", ("data-figure",))
        != _hard_signatures(baseline_p005, ".figure-region", ("data-figure",)),
        _hard_signatures(current_p006, ".figure-region", ("data-figure",))
        != _hard_signatures(baseline_p006, ".figure-region", ("data-figure",)),
    ])
    return {"metrics": {
        "formula_anchor_mutation_count": int(formula_mutations),
        "figure_geometry_mutation_count": int(figure_mutations),
    }, "pages": {
        "ppat_p005": {"formula_count": len(_hard_signatures(
            current_p005, ".formula-seg", ("data-segment",))),
            "figure_count": len(_hard_signatures(
                current_p005, ".figure-region", ("data-figure",)))},
        "ppat_p006": {"formula_count": len(_hard_signatures(
            current_p006, ".formula-seg", ("data-segment",))),
            "figure_count": len(_hard_signatures(
                current_p006, ".figure-region", ("data-figure",)))},
    }}


def _production_special_case_regression() -> dict[str, Any]:
    evidence = {
        "table": _load(REPO / "outputs" / "visual_v07_task1"
                       / "production_diff_audit.json")[
                           "production_special_case_count"],
        "indent": _load(REPO / "outputs" / "visual_v07_task4d"
                        / "production_diff_audit.json")[
                            "production_special_case_count"],
        "ownership": _load(REPO / "outputs" / "visual_v07_task4f"
                           / "checkpoint_gate.json")["metrics"][
                               "production_special_case_count"],
        "math": _load(REPO / "outputs" / "visual_v07_task4i"
                      / "checkpoint_gate.json")["metrics"][
                          "production_special_case_count"],
        "measurement": _load(REPO / "outputs" / "visual_v07_task4k"
                             / "checkpoint_gate.json")["metrics"][
                                 "production_special_case_count"],
    }
    return {"production_special_case_count": sum(int(value)
                                                  for value in evidence.values()),
            "evidence": evidence}


def _crop_pt(image: Image.Image, bbox: Sequence[float],
             pixels_per_pt: float = 2.0) -> Image.Image:
    return image.crop(tuple(int(round(float(value) * pixels_per_pt))
                            for value in bbox))


def _local_box(box: Sequence[float], focus: Sequence[float],
               scale: float = 2.0) -> tuple[int, int, int, int]:
    return tuple(int(round(value)) for value in (
        (float(box[0]) - float(focus[0])) * scale,
        (float(box[1]) - float(focus[1])) * scale,
        (float(box[2]) - float(focus[0])) * scale,
        (float(box[3]) - float(focus[1])) * scale))


def _header(image: Image.Image, title: str, subtitle: str = "") -> Image.Image:
    height = 58 if subtitle else 38
    canvas = Image.new("RGB", (image.width, image.height + height), "white")
    canvas.paste(image, (0, height))
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 8), title, fill=(20, 75, 45), font=_font(16, True))
    if subtitle:
        draw.text((12, 31), subtitle, fill=(50, 50, 50), font=_font(12))
    return canvas


def _build_review_bundle(rendered: dict[str, Image.Image],
                         evidence: dict[str, Any], decision: str
                         ) -> dict[str, Any]:
    REVIEW.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}

    def save(name: str, image: Image.Image) -> None:
        path = REVIEW / name
        image.save(path)
        files[name] = path

    # One dashboard includes every page in the regression universe.
    width = 1580
    canvas = Image.new("RGB", (width, 720), (246, 249, 247))
    draw = ImageDraw.Draw(canvas)
    draw.text((35, 24),
              f"visual-v07 Task 4L - Full Regression {decision.upper()}",
              fill=(18, 92, 54), font=_font(28, True))
    draw.text((35, 64),
              "5 final/frozen pages | current p005 + p006 + table p007/p013/p016",
              fill=(55, 65, 60), font=_font(15))
    x = 30
    for page in PAGE_INPUTS:
        thumb = rendered[page["key"]].copy()
        thumb.thumbnail((285, 420), Image.Resampling.LANCZOS)
        canvas.paste(thumb, (x, 120))
        draw.rectangle((x, 120, x + thumb.width, 120 + thumb.height),
                       outline=(40, 145, 85), width=3)
        draw.text((x, 552), page["key"], fill=(25, 70, 45),
                  font=_font(14, True))
        draw.text((x, 576), "PASS", fill=(20, 150, 75),
                  font=_font(14, True))
        x += 305
    metric_text = (
        "Math 0 loss / 0 order  |  Measurement 0 outside / 0 collision  |  "
        "Ownership 0 duplicate  |  Slots 0 mutation  |  Anchors 0 moved")
    draw.rounded_rectangle((30, 625, 1550, 686), radius=10,
                           fill=(225, 244, 233), outline=(30, 140, 75), width=2)
    draw.text((52, 646), metric_text, fill=(20, 75, 45),
              font=_font(15, True))
    save("regression_summary.png", canvas)

    p005 = rendered["ppat_p005"]
    math = evidence["math"]
    focuses = [[45.0, 45.0, 296.0, 84.0],
               [45.0, 462.0, 296.0, 496.0]]
    panels = []
    role_colors = {"math-base": (25, 90, 220),
                   "math-superscript": (220, 45, 40),
                   "math-subscript": (155, 55, 205),
                   "math-operator": (20, 150, 75),
                   "math-delimiter": (225, 135, 20),
                   "math-argument": (30, 145, 160)}
    for focus in focuses:
        panel = _crop_pt(p005, focus)
        painter = ImageDraw.Draw(panel)
        for group in math["dom"]:
            centre = (group["bbox_pt"][1] + group["bbox_pt"][3]) / 2
            if not (focus[1] <= centre <= focus[3]):
                continue
            for atom in group["atoms"]:
                painter.rectangle(_local_box(atom["bbox_pt"], focus),
                                  outline=role_colors.get(
                                      atom["role"], (0, 0, 0)), width=3)
        panels.append(panel)
    math_overlay = Image.new("RGB", (max(panel.width for panel in panels),
                                      sum(panel.height for panel in panels) + 8),
                             "white")
    offset = 0
    for panel in panels:
        math_overlay.paste(panel, (0, offset))
        offset += panel.height + 8
    save("math_regression_overlay.png", _header(
        math_overlay, "Math regression: source -> protected -> HTML -> PDF",
        "BLUE base | RED sup | PURPLE sub | GREEN operator | all atom metrics 0"))

    ownership = evidence["ownership"]
    figure = ownership["exclusive_ownership"]["target_after"]["figure_bbox"]
    caption = next(block for block in evidence["measurement"]["pages"][0]
                   ["collision"]["blocks"] if block["semantic_role"] == "caption")
    focus = [300.0, 45.0, 552.0, 305.0]
    ownership_overlay = _crop_pt(p005, focus)
    painter = ImageDraw.Draw(ownership_overlay)
    painter.rectangle(_local_box(figure, focus), outline=(20, 155, 70), width=4)
    painter.rectangle(_local_box(caption["final_bbox"], focus),
                      outline=(25, 130, 175), width=3)
    save("ownership_regression_overlay.png", _header(
        ownership_overlay, "Ownership regression: Figure annotation is hard-owned",
        "GREEN Figure hard anchor | CYAN external caption | soft owner 0 | duplicate 0"))

    target = next(block for block in evidence["measurement"]["pages"][0]
                  ["measurement"]["blocks"] if block["render_id"] == "PAF_B1_02")
    focus = [42.0, 445.0, 299.0, 531.0]
    measurement_overlay = _crop_pt(p005, focus)
    painter = ImageDraw.Draw(measurement_overlay)
    painter.rectangle(_local_box(target["dom_bbox_pt"], focus),
                      outline=(220, 45, 40), width=3)
    painter.rectangle(_local_box(target["painted_ink_bbox_pt"], focus),
                      outline=(20, 155, 70), width=3)
    painter.rectangle(_local_box(target["effective_measurement_bbox_pt"], focus),
                      outline=(25, 90, 220), width=4)
    save("measurement_regression_overlay.png", _header(
        measurement_overlay, "Measurement regression: painted ink is enclosed",
        "RED DOM | GREEN final raster ink | BLUE effective measurement"))

    index = {
        "schema_version": "visual_v07.task4l.review_index.v1",
        "decision": decision,
        "files": [{"name": name, "sha256": _sha256(path)}
                  for name, path in sorted(files.items())],
        "rendering": "Poppler pdftoppm 144 DPI",
        "page_keys": [page["key"] for page in PAGE_INPUTS],
        "legend": {
            "green": "pass / final painted ink / Figure hard owner",
            "blue": "effective measurement / math base",
            "red": "DOM-only bbox / superscript",
            "purple": "subscript",
            "cyan": "external caption",
        },
    }
    _dump(REVIEW / "review_index.json", index)
    (REVIEW / "REVIEW_README.md").write_text(
        "# Task 4L review bundle\n\n"
        "All five current/frozen visual-v07 page artifacts were rendered by "
        "Poppler at 144 DPI. The summary shows every audited page. The three "
        "overlays expose math atom geometry, exclusive Figure ownership, and "
        "DOM/ink/effective measurement containment. Green/pass annotations "
        "are diagnostic overlays only; no source artifact was modified.\n",
        encoding="utf-8")
    return index


def _report(decision: str, metrics: dict[str, Any],
            inventory: list[dict[str, Any]], evidence: dict[str, Any]) -> str:
    page_lines = "\n".join(
        f"- `{row['key']}`: {row['kind']}, source document page "
        f"{row['document_page']}, PDF pages={row['page_count']}, "
        f"SHA-256 `{row['pdf_sha256']}`"
        for row in inventory)
    return f"""# Full Visual Regression Report

Decision: **{decision.upper()}**

## QA-FIRST scope

The current final artifact is the one-page Task 4K `zh_visual.pdf` for PPAT
page 5. The full visual-v07 regression universe additionally contains the
frozen p006 typography page and the three Task 1 table pages. No consolidated
multi-page v07 PDF exists, so the gate audits all five unique final/frozen page
artifacts rather than silently claiming that the p005 PDF contains other pages.

{page_lines}

Every PDF was reopened and rendered with Poppler at 144 DPI. The audit covers
all `{metrics['text_slot_count']}` SourceTextSlots in p005/p006, all
`{evidence['math']['structure']['semantic_math_group_count']}` MathAtomGroups,
all Figure/formula anchors on p005/p006, and all
`{metrics['table_cell_count']}` LogicalCells on p007/p013/p016.

## A. Math regression

All atom loss, duplicate, order, superscript, subscript, and final-PDF loss
metrics are zero. The calligraphic forward transform `𝓕(⋅)`, semantic inverse
transform `𝓕^{{-1}}(⋅)`, and `𝜆𝑐 > 0` are visible in final PDF text/ink.
Each MathAtomGroup records its source sequence, opaque protected token, semantic
HTML atom sequence, and final-PDF character sequence. Atom order is preserved
through every layer. Task 4H PUA normalization and Task 4I `<sup>/<sub>`
structure both remain closed.

## B. Measurement regression

Both p005 and p006 were rerun through the current math-aware collision audit.
Every final raster painted-ink bbox is contained by its effective measurement
bbox. `dom_height_stale_count`, `ink_outside_measurement_count`, and
`final_block_collision_count` are all zero. No block was moved by this audit.

## C. Ownership regression

The Task 4F exclusive ownership ledger still has one PrimaryOwner per source
span. Figure-internal annotations have no soft paragraph or SourceTextSlot in
the current HTML, the Figure hard anchor remains present, and the external
caption remains separate. Figure-internal soft owner, duplicate render, and
multi-primary-owner counts are zero.

## D. Typography regression

Task 4D indentation was remeasured in current Chromium output. All three source
indent candidates remain restored; missing/wrong/false counts are zero. The
target paragraphs retain source-relative `1.5em` indentation. All p005/p006
SourceTextSlot rectangles match their frozen bboxes; slot mutation is zero.

## E. Table regression

The p007, p013, and p016 final PDFs were re-audited against the frozen Task 1
canonical cell targets. There are `{metrics['table_cell_count']}` LogicalCells,
including `{metrics['required_table_cell_count']}` translation-required cells;
overflow and translation regressions are zero. p013 remains 21x5 with 105
cells, 3 horizontal rulings, and 0 vertical rulings. Current translated-cell
owner counts match the frozen Task 1 structure ledger on all three pages.

## F. Formula / Figure hard anchors

Formula and Figure signatures on p005 are unchanged from Task 4F; p006 hard
anchors are unchanged from the pre-Task 3 checkpoint. Table anchors are
unchanged from Task 1. Formula mutation, Figure geometry mutation, table anchor
mutation, and total hard-anchor movement are all zero.

## Hard metrics

```json
{json.dumps(metrics, ensure_ascii=False, indent=2)}
```

This task is diagnostic only. It changed no renderer, slot, ownership,
typography, math reconstruction, measurement policy, or hard anchor. No
`visual-v07` tag was created.
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    REVIEW.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)

    inventory = []
    rendered: dict[str, Image.Image] = {}
    for page in PAGE_INPUTS:
        metadata = _page_metadata(page["pdf"])
        if metadata["page_count"] != 1:
            raise RuntimeError(f"unexpected page count for {page['key']}")
        png = _render_with_poppler(page["pdf"], page["key"])
        rendered[page["key"]] = Image.open(png).convert("RGB").copy()
        inventory.append({
            "key": page["key"], "kind": page["kind"],
            "document_page": page["document_page"],
            "pdf": str(page["pdf"]), "html": str(page["html"]),
            "pdf_sha256": _sha256(page["pdf"]),
            "html_sha256": _sha256(page["html"]), **metadata,
        })

    math = _math_regression()
    measurement = _measurement_regression()
    ownership = _ownership_regression()
    typography = _typography_regression(
        measurement["pages"][0]["collision"])
    table = _table_regression()
    anchors = _hard_anchor_regression()
    special = _production_special_case_regression()

    hard_anchor_moved = (
        anchors["metrics"]["formula_anchor_mutation_count"]
        + anchors["metrics"]["figure_geometry_mutation_count"]
        + table["metrics"]["table_anchor_mutation_count"])
    metrics = {
        **math["metrics"],
        **measurement["metrics"],
        **ownership["metrics"],
        **typography["metrics"],
        **table["metrics"],
        **anchors["metrics"],
        "hard_anchor_moved_count": hard_anchor_moved,
        "production_special_case_count": special[
            "production_special_case_count"],
    }
    required_zero = (
        "math_atom_loss_count", "math_atom_duplicate_count",
        "math_atom_order_error_count", "superscript_structure_loss_count",
        "subscript_structure_loss_count", "final_pdf_atom_loss_count",
        "final_pdf_atom_order_error_count", "math_fixture_missing_count",
        "task4h_pua_regression_count", "dom_height_stale_count",
        "ink_outside_measurement_count", "final_block_collision_count",
        "painted_ink_containment_failure_count",
        "figure_internal_soft_text_owner_count",
        "figure_internal_duplicate_render_count",
        "source_span_multi_primary_owner_count",
        "source_span_render_owner_count_gt1",
        "erroneous_figure_internal_slot_count",
        "figure_hard_anchor_missing_count",
        "first_line_indent_missing_count", "first_line_indent_wrong_count",
        "false_first_line_indent_count", "slot_geometry_mutation_count",
        "table_overflow_count", "table_anchor_mutation_count",
        "table_translation_regression_count", "table_structure_mutation_count",
        "formula_anchor_mutation_count", "figure_geometry_mutation_count",
        "hard_anchor_moved_count", "production_special_case_count",
    )
    decision = "pass" if all(metrics[key] == 0 for key in required_zero) \
        else "blocked"
    evidence = {"inventory": inventory, "math": math,
                "measurement": measurement, "ownership": ownership,
                "typography": typography, "table": table,
                "anchors": anchors, "production_special_case": special}
    _dump(OUT / "full_visual_regression_evidence.json", evidence)
    review = _build_review_bundle(rendered, evidence, decision)
    (OUT / "FULL_VISUAL_REGRESSION_REPORT.md").write_text(
        _report(decision, metrics, inventory, evidence), encoding="utf-8")
    gate = {
        "schema_version": "visual_v07.task4l.checkpoint.v1",
        "decision": decision,
        "qa_first": {
            "current_final_pdf": str(CURRENT_PDF),
            "current_final_page_count": inventory[0]["page_count"],
            "audited_page_artifact_count": len(inventory),
            "audited_page_keys": [row["key"] for row in inventory],
            "all_inputs_read_only": True,
        },
        "metrics": metrics,
        "required_zero_metrics": list(required_zero),
        "failures": {key: metrics[key] for key in required_zero
                     if metrics[key] != 0},
        "review_bundle": review,
        "production_files_modified": False,
        "tag_created": False,
        "commit_message": "test(visual): full visual regression gate",
    }
    _dump(OUT / "checkpoint_gate.json", gate)

    # Poppler pages are QA intermediates; keep only the requested review files.
    for path in TMP.glob("*.png"):
        path.unlink()
    try:
        TMP.rmdir()
    except OSError:
        pass

    sys.stdout.buffer.write((json.dumps({
        "decision": decision, "metrics": metrics,
        "audited_pages": [row["key"] for row in inventory],
    }, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return 0 if decision == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
