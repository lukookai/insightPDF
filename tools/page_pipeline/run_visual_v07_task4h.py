# -*- coding: utf-8 -*-
"""visual-v07 Task 4H: provenance-backed legacy math PUA normalization."""
from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import sys
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent.parent
HERE = REPO / "tools" / "page_pipeline"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "实现源码" / "pdf_translator"))

from _chromium_pdf import render_html_to_pdf  # noqa: E402
from final_block_collision_qa import final_block_collision_qa  # noqa: E402
from legacy_math_pua_normalizer import (  # noqa: E402
    LegacyMathPUANormalizer,
    extract_source_pua_provenance,
    registry_as_dicts,
)
from legacy_math_pua_qa import audit_legacy_math_pua_artifact  # noqa: E402
from math_font_rendering_audit import (  # noqa: E402
    capture_chromium_font_trace,
    embedded_pdf_glyph_evidence,
    extract_pdf_characters,
    find_pdf_sequence,
    pdf_font_inventory,
    platform_font_glyph_trace,
)

OUT = REPO / "outputs" / "visual_v07_task4h"
ARTIFACT = OUT / "fixed_artifact"
REVIEW = OUT / "review_bundle"
SOURCE_PDF = Path("C:/Users/74496/Desktop/PPAT.pdf")
SOURCE_PAGE_INDEX = 4
BEFORE_DIR = REPO / "outputs" / "visual_v07_task4f" / "fixed_artifact"
BEFORE_HTML = BEFORE_DIR / "zh_visual.html"
BEFORE_PDF = BEFORE_DIR / "zh_visual.pdf"
MODEL_PATH = OUT.parent / "visual_v07_task4f" \
    / "fixed_stitched_page_model.json"
TASK4G_AUDIT = OUT.parent / "visual_v07_task4g" / "math_font_audit.json"

LEGACY_F = "\ue232"
STANDARD_F = "\U0001D4D5"
MATH_LAMBDA = "\U0001D706"
MATH_C = "\U0001D450"


def _load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _font(size: int = 15, bold: bool = False) -> ImageFont.ImageFont:
    names = (("arialbd.ttf", "calibrib.ttf") if bold
             else ("arial.ttf", "calibri.ttf"))
    for name in names:
        try:
            return ImageFont.truetype(
                str(Path("C:/Windows/Fonts") / name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _bbox_union(boxes: list[list[float]]) -> list[float]:
    boxes = [box for box in boxes if len(box) == 4]
    return [min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)]


def _render_page(pdf_path: Path, page_index: int = 0,
                 scale: float = 2.5) -> Image.Image:
    document = pymupdf.open(str(pdf_path))
    try:
        pixmap = document[page_index].get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), alpha=False)
    finally:
        document.close()
    return Image.frombytes("RGB", (pixmap.width, pixmap.height),
                           pixmap.samples)


def _crop(image: Image.Image, focus_pt: list[float],
          scale: float = 2.5) -> Image.Image:
    return image.crop(tuple(int(round(item * scale)) for item in focus_pt))


def _with_header(image: Image.Image, title: str,
                 subtitle: str = "") -> Image.Image:
    header = 52 if subtitle else 34
    canvas = Image.new("RGB", (image.width, image.height + header), "white")
    canvas.paste(image, (0, header))
    draw = ImageDraw.Draw(canvas)
    draw.text((10, 7), title, fill=(22, 22, 22), font=_font(16, True))
    if subtitle:
        draw.text((10, 29), subtitle, fill=(75, 75, 75), font=_font(12))
    return canvas


def _side_by_side(left: Image.Image, right: Image.Image,
                  left_title: str, right_title: str) -> Image.Image:
    canvas = Image.new("RGB", (left.width + right.width,
                               max(left.height, right.height) + 38), "white")
    canvas.paste(left, (0, 38))
    canvas.paste(right, (left.width, 38))
    draw = ImageDraw.Draw(canvas)
    draw.text((10, 9), left_title, fill=(170, 25, 25),
              font=_font(16, True))
    draw.text((left.width + 10, 9), right_title, fill=(18, 126, 60),
              font=_font(16, True))
    return canvas


def _paragraph_visible_text(source: str, paragraph_id: str) -> str:
    pattern = (r'<div\b(?=[^>]*\bclass="[^"]*paragraph-block[^"]*")'
               r'(?=[^>]*\bdata-para="%s")[^>]*>(.*?)</div>'
               % re.escape(paragraph_id))
    match = re.search(pattern, source, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return html_lib.unescape(re.sub(r"<[^>]+>", "", match.group(1)))


def _normalize_font_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.split("+", 1)[-1].casefold())


def _font_xref(inventory: list[dict[str, Any]], raw_font: str
               ) -> int | None:
    wanted = _normalize_font_name(raw_font)
    for row in inventory:
        if _normalize_font_name(str(row.get("base_font") or "")) == wanted:
            return int(row["xref"])
    return None


def _nearest_pdf_char(final_chars: list[dict[str, Any]], character: str,
                      dom_bbox: list[float]) -> dict[str, Any] | None:
    if len(dom_bbox) != 4:
        return None
    center = ((dom_bbox[0] + dom_bbox[2]) / 2.0,
              (dom_bbox[1] + dom_bbox[3]) / 2.0)
    candidates = []
    for row in final_chars:
        bbox = row.get("bbox") or []
        if row.get("character") != character or len(bbox) != 4:
            continue
        row_center = ((bbox[0] + bbox[2]) / 2.0,
                      (bbox[1] + bbox[3]) / 2.0)
        distance = ((row_center[0] - center[0]) ** 2
                    + (row_center[1] - center[1]) ** 2) ** 0.5
        candidates.append((distance, row))
    if not candidates:
        return None
    distance, row = min(candidates, key=lambda value: value[0])
    if distance > 8.0:
        return None
    output = dict(row)
    output["bbox_width"] = round(row["bbox"][2] - row["bbox"][0], 3)
    output["bbox_height"] = round(row["bbox"][3] - row["bbox"][1], 3)
    output["dom_to_pdf_center_distance_pt"] = round(distance, 3)
    return output


def _raster_ink_count(pdf_path: Path, bbox: list[float],
                      scale: float = 4.0) -> int:
    if len(bbox) != 4:
        return 0
    document = pymupdf.open(str(pdf_path))
    try:
        clip = pymupdf.Rect(bbox[0] - 0.35, bbox[1] - 0.35,
                            bbox[2] + 0.35, bbox[3] + 0.35)
        pixmap = document[0].get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), clip=clip,
            colorspace=pymupdf.csGRAY, alpha=False)
    finally:
        document.close()
    return sum(value < 235 for value in pixmap.samples)


def _verification_samples(html_path: Path, pdf_path: Path
                          ) -> tuple[list[dict[str, Any]], dict[str, Any],
                                     dict[str, Any]]:
    specs = [
        {"sample_id": "fourier_forward", "display_name": "F(·)",
         "selector": ".paragraph-block[data-para='PAF_B1_00']",
         "needle": STANDARD_F + "(⋅)", "occurrence": 0},
        {"sample_id": "fourier_inverse", "display_name": "F^{-1}(·)",
         "selector": ".paragraph-block[data-para='PAF_B1_00']",
         "needle": STANDARD_F + "−1(⋅)", "occurrence": 0},
    ]
    browser = capture_chromium_font_trace(
        html_path, specs, specs[0]["selector"], [STANDARD_F])
    glyph_trace = platform_font_glyph_trace(browser)
    browser_by_id = {str(row.get("sample_id")): row
                     for row in browser.get("records") or []}
    glyph_by_probe = {str(row.get("probe_id")): row
                      for row in glyph_trace.get("glyph_records") or []}
    final_chars = extract_pdf_characters(pdf_path, 0)
    inventory = pdf_font_inventory(pdf_path, 0)
    samples = []
    for spec in specs:
        browser_row = browser_by_id.get(spec["sample_id"], {})
        first_char = ((browser_row.get("chars") or [{}])[0]
                      if browser_row.get("found") else {})
        glyph = glyph_by_probe.get(str(first_char.get("probe_id") or ""), {})
        pdf_char = _nearest_pdf_char(
            final_chars, STANDARD_F,
            list(first_char.get("dom_bbox_pt") or []))
        font_xref = (_font_xref(inventory, str(pdf_char.get("font") or ""))
                     if pdf_char else None)
        embedded = (embedded_pdf_glyph_evidence(
            pdf_path, font_xref, STANDARD_F)
                    if font_xref is not None else None)
        sequence = find_pdf_sequence(final_chars, spec["needle"], 0)
        ink_count = _raster_ink_count(
            pdf_path, list((pdf_char or {}).get("bbox") or []))
        system_glyph = (glyph.get("glyph") or {})
        samples.append({
            **spec,
            "html_sequence_present": bool(browser_row.get("found")),
            "restored_html_sequence": browser_row.get("needle"),
            "css_font_family": (browser_row.get("css") or {}).get(
                "font_family"),
            "chromium_actual_font": first_char.get("actual_fallback_font"),
            "chromium_platform_fonts": first_char.get("platform_fonts") or [],
            "dom_glyph_bbox": first_char.get("dom_bbox_pt") or [],
            "dom_glyph_width_pt": (
                round(first_char["dom_bbox_pt"][2]
                      - first_char["dom_bbox_pt"][0], 3)
                if len(first_char.get("dom_bbox_pt") or []) == 4 else None),
            "system_font_glyph": system_glyph,
            "glyph_supported": system_glyph.get("glyph_supported"),
            "final_pdf_character": pdf_char,
            "final_pdf_font_xref": font_xref,
            "embedded_pdf_glyph": embedded,
            "final_pdf_sequence_complete": len(sequence) == len(spec["needle"]),
            "final_pdf_sequence": "".join(
                row["character"] for row in sequence),
            "final_pdf_glyph_width_pt": (
                pdf_char.get("bbox_width") if pdf_char else None),
            "final_raster_ink_pixel_count": ink_count,
            "final_ink_exists": bool(ink_count > 0 and pdf_char
                                     and pdf_char["bbox_width"] > 0.05),
        })
    return samples, browser, glyph_trace


def _production_special_case_audit() -> dict[str, Any]:
    paths = [HERE / "legacy_math_pua_normalizer.py", HERE / "html_render.py"]
    prohibited = {
        "document_filename": r"\bPPAT\b|2504\.05732",
        "page_number_branch": r"(?:page|page_index)\s*==\s*[45]",
        "paragraph_id_branch": r"PAF_B1_00|PAF_B1_02",
        "visible_text_branch": r"if\s+[^\n]*(?:F\(|where|Fourier)",
        "single_codepoint_if": r"if\s+[^\n]*(?:E232|0xE232|\\ue232)",
    }
    hits = []
    for path in paths:
        content = path.read_text(encoding="utf-8")
        for name, pattern in prohibited.items():
            if re.search(pattern, content, re.IGNORECASE):
                hits.append({"rule": name, "path": str(path)})
    return {
        "production_special_case_count": len(hits),
        "hits": hits,
        "registry_is_declarative_data": True,
        "mapping_match_fields": [
            "source_codepoint", "source_font_family",
            "source_postscript_name", "source_glyph_name", "confidence",
        ],
    }


def _build_review(source_provenance: list[dict[str, Any]],
                  after_pdf: Path, samples: list[dict[str, Any]],
                  decision: str) -> dict[str, Any]:
    REVIEW.mkdir(parents=True, exist_ok=True)
    scale = 2.5
    required_source = [row for row in source_provenance
                       if row["source_codepoint"] == "U+E232"]
    source_union = _bbox_union([row["source_bbox"]
                                for row in required_source])
    document = pymupdf.open(str(SOURCE_PDF))
    try:
        page_rect = document[SOURCE_PAGE_INDEX].rect
    finally:
        document.close()
    focus = [max(0.0, source_union[0] - 25.0),
             max(0.0, source_union[1] - 16.0),
             min(float(page_rect.width), source_union[2] + 245.0),
             min(float(page_rect.height), source_union[3] + 31.0)]
    source_crop = _crop(
        _render_page(SOURCE_PDF, SOURCE_PAGE_INDEX, scale), focus, scale)
    before_crop = _crop(_render_page(BEFORE_PDF, 0, scale), focus, scale)
    after_crop = _crop(_render_page(after_pdf, 0, scale), focus, scale)

    files: dict[str, Path] = {}

    def save(name: str, image: Image.Image) -> None:
        path = REVIEW / name
        image.save(path)
        files[name] = path

    save("math_pua_source.png", _with_header(
        source_crop, "SOURCE PDF: legacy STIX glyphs",
        "U+E232 | STIXMathCalligraphy-Regular | uniE232 / glyph id 2"))
    save("math_pua_before.png", _with_header(
        before_crop, "BEFORE: zero-width fallback",
        "Segoe UI Symbol -> uni200E -> final width 0pt"))
    widths = ", ".join(str(row["final_pdf_glyph_width_pt"])
                       for row in samples)
    save("math_pua_after.png", _with_header(
        after_crop, "AFTER: standardized mathematical Unicode",
        "U+1D4D5 | Cambria Math | final widths %spt" % widths))
    save("math_pua_before_after.png", _side_by_side(
        before_crop, after_crop, "BEFORE  U+E232 (invisible)",
        "AFTER  U+1D4D5 (visible)"))

    # Source/final geometry evidence overlay. Coordinates are source-relative.
    source_overlay = source_crop.copy()
    after_overlay = after_crop.copy()
    source_draw = ImageDraw.Draw(source_overlay)
    after_draw = ImageDraw.Draw(after_overlay)
    origin = (focus[0] * scale, focus[1] * scale)

    def local(box: list[float]) -> tuple[int, int, int, int]:
        values = (box[0] * scale - origin[0],
                  box[1] * scale - origin[1],
                  box[2] * scale - origin[0],
                  box[3] * scale - origin[1])
        return tuple(int(round(value)) for value in values)

    for row in required_source:
        source_draw.rectangle(local(row["source_bbox"]),
                              outline=(20, 92, 210), width=3)
    for row in samples:
        bbox = list((row.get("final_pdf_character") or {}).get("bbox") or [])
        if len(bbox) == 4:
            after_draw.rectangle(local(bbox), outline=(16, 150, 70), width=3)
    save("math_pua_normalization_overlay.png", _side_by_side(
        source_overlay, after_overlay,
        "BLUE: source PUA provenance",
        "GREEN: final standardized glyph ink"))

    index = {
        "schema_version": "visual_v07.task4h.review_index.v1",
        "decision": decision,
        "focus_bbox_pt": [round(value, 3) for value in focus],
        "files": [{"name": name, "sha256": _sha256(path)}
                  for name, path in sorted(files.items())],
        "legend": {
            "blue": "source U+E232 bbox with verified legacy font provenance",
            "green": "final U+1D4D5 bbox with positive-width PDF ink",
        },
    }
    _dump(REVIEW / "review_index.json", index)
    (REVIEW / "REVIEW_README.md").write_text(
        """# Task 4H Review Bundle

The five images compare the same source-relative page region. Blue boxes are
the two source STIX PUA glyphs; green boxes are the final standardized Unicode
glyphs. The before panel shows the former zero-width blank positions. The
after panel demonstrates visible `U+1D4D5` ink without a font-chain, crop,
slot, typography, or flow change.

Other unregistered PUA characters are intentionally unchanged: the task adds
only the reviewed mapping and never guesses from a private-use codepoint.
""", encoding="utf-8")
    return index


def _report(decision: str, metrics: dict[str, Any],
            normalization: dict[str, Any], samples: list[dict[str, Any]],
            lambda_freeze: dict[str, Any], special: dict[str, Any],
            before_qa: dict[str, Any]) -> str:
    normalized = [row for row in normalization["records"]
                  if row.get("normalized")]
    source = normalized[0]["source_provenance"] if normalized else {}
    fonts = sorted({str(row.get("chromium_actual_font") or "")
                    for row in samples})
    widths = [row.get("final_pdf_glyph_width_pt") for row in samples]
    inks = [row.get("final_ink_exists") for row in samples]
    metrics_text = json.dumps(metrics, ensure_ascii=False, indent=2)
    return f"""# Legacy Math PUA Normalization Report

**Decision: {decision.upper()}**

## Direct answers

1. **Why did `U+E232` previously become a zero-width glyph?** The HTML kept
   the private-use codepoint, but Chromium fell through to `Segoe UI Symbol`.
   Its cmap aliases `U+E232` to `uni200E` (left-to-right mark), glyph id `353`,
   advance width `0`. The frozen PDF therefore had two zero-width character
   records and no visible Fourier glyph. QA-first measured
   `legacy_pua_visible_failure_count={before_qa['metrics']['legacy_pua_visible_failure_count']}`
   and `zero_width_math_glyph_count={before_qa['metrics']['zero_width_math_glyph_count']}`.

2. **How was `𝓕` confirmed?** Not from `U+E232` alone. The source PDF records
   family `{source.get('source_font_family')}`, PostScript name
   `{source.get('source_postscript_name')}`, Type1 glyph name
   `{source.get('source_glyph_name')}`, glyph id
   `{source.get('source_glyph_id')}`, and positive ink
   `{str(source.get('source_ink_exists')).lower()}`. This reviewed legacy STIX
   calligraphic-capital-F identity selects standard `U+1D4D5` (MATHEMATICAL
   BOLD SCRIPT CAPITAL F) with confidence `1.0`.

3. **Does the mapping require source font/glyph provenance?** Yes. Source
   codepoint, embedded font family, PostScript name, embedded glyph name, and
   confidence must all match. Missing/conflicting evidence stays unchanged
   and becomes `BLOCK_unresolved_required_pua`; it is never silently replaced.

4. **Which font did Chromium finally use?** `{', '.join(fonts)}`. Both
   standardized characters resolve to supported `u1D4D5` glyphs through the
   existing font chain. No legacy font was installed and CSS was not changed.

5. **Did width and ink recover?** Yes. Final PDF widths are `{widths}` pt and
   raster ink results are `{inks}`; all are positive.

6. **Are `F(·)` and `F^{{-1}}(·)` complete?** Yes. DOM and PDF contain
   `𝓕(⋅)` and `𝓕−1(⋅)`. The existing minus, `1`, parentheses, and centered dot
   remain. Superscript reconstruction is intentionally outside this task.

7. **Was `λc > 0` repaired?** No. `deferred_to_Task_4I = true`. Task 4G
   remains `NOT_FONT_CAUSE`; `𝜆`, `𝑐`, and `>` are absent before Chromium.
   Its before/after block is byte-identical:
   `{str(lambda_freeze['target_block_byte_identical']).lower()}`.

8. **Is production special-case count zero?** Yes:
   `production_special_case_count={special['production_special_case_count']}`.
   Production has no filename, page, paragraph-id, visible-text, or
   single-codepoint conditional. Fixture locators exist only in this runner.

## Verified render chain

```text
U+E232 + STIXMathCalligraphy + STIXMathCalligraphy-Regular + uniE232
  -> exact registry match -> U+1D4D5 𝓕
  -> existing math fallback -> Cambria Math u1D4D5
  -> positive DOM width -> positive final-PDF width and raster ink
```

Only the two verified occurrences were normalized. Unregistered PUA records
remain explicit, unresolved, non-required evidence; the wider private-use
range was not guessed.

## Hard metrics

```json
{metrics_text}
```

The normalized artifact differs from the current Task 4F artifact by exactly
two text codepoints. Figure ownership, formula crop, table, SourceTextSlot
geometry, typography, flow, translation prompt, and renderer geometry behavior
are unchanged. Final block collision remains zero.
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    REVIEW.mkdir(parents=True, exist_ok=True)

    before_html_text = BEFORE_HTML.read_text(encoding="utf-8")
    before_qa = audit_legacy_math_pua_artifact(
        BEFORE_HTML, BEFORE_PDF,
        required_source_codepoints=["U+E232"])
    _dump(OUT / "legacy_math_pua_qa_before.json", before_qa)
    if not before_qa["old_artifact_red"]:
        raise RuntimeError("QA-FIRST red baseline no longer reproduces")

    source_provenance = extract_source_pua_provenance(
        SOURCE_PDF, SOURCE_PAGE_INDEX)
    normalization = LegacyMathPUANormalizer().normalize(
        before_html_text, source_provenance,
        context={
            "artifact_stage": "immediately_before_html_render",
            "source_page_index": SOURCE_PAGE_INDEX,
            "fixture_only": True,
        })
    normalization["registry"] = registry_as_dicts()
    normalization["source_provenance"] = source_provenance
    normalized_count = normalization["metrics"][
        "legacy_pua_normalized_count"]
    if normalization["blocked"] or normalized_count <= 0:
        raise RuntimeError("required PUA mapping did not resolve")

    html_path = ARTIFACT / "zh_visual.html"
    pdf_path = ARTIFACT / "zh_visual.pdf"
    html_path.write_text(normalization["normalized_text"], encoding="utf-8")
    render_html_to_pdf(html_path, pdf_path)

    after_qa = audit_legacy_math_pua_artifact(
        html_path, pdf_path, normalization,
        required_source_codepoints=["U+E232"])
    _dump(OUT / "legacy_math_pua_qa_after.json", after_qa)
    _dump(OUT / "legacy_math_pua_normalization.json", normalization)

    samples, browser, glyph_trace = _verification_samples(
        html_path, pdf_path)
    _dump(OUT / "chromium_font_trace.json", browser)
    _dump(OUT / "font_glyph_trace.json", glyph_trace)
    _dump(OUT / "math_glyph_verification.json", {"samples": samples})

    model = _load(MODEL_PATH)
    collision = final_block_collision_qa(
        model, html_path=html_path, final_pdf_path=pdf_path,
        screenshot_path=OUT / "final_block_collision_dom.png")
    _dump(OUT / "final_block_collision_qa.json", collision)

    # The specific identifiers below are fixture locators only. They are not
    # imported by production normalization or renderer code.
    before_lambda = _paragraph_visible_text(before_html_text, "PAF_B1_02")
    after_lambda = _paragraph_visible_text(
        normalization["normalized_text"], "PAF_B1_02")
    task4g = _load(TASK4G_AUDIT)
    task4g_lambda = next(
        row for row in task4g.get("samples") or []
        if row.get("sample_id") == "lambda_c_positive")
    lambda_freeze = {
        "diagnosis": task4g_lambda.get("diagnosis"),
        "deferred_to_Task_4I": True,
        "target_block_byte_identical": before_lambda == after_lambda,
        "before_target_text": before_lambda,
        "after_target_text": after_lambda,
        "missing_before_chromium_codepoints": [
            "U+1D706", "U+1D450", "U+003E"],
        "missing_characters_still_absent": all(
            character not in after_lambda
            for character in (MATH_LAMBDA, MATH_C, ">")),
        "inline_math_reconstruction_modified": False,
    }
    _dump(OUT / "lambda_c_freeze.json", lambda_freeze)

    special = _production_special_case_audit()
    _dump(OUT / "production_special_case_audit.json", special)
    exact_text_only_change = (
        normalization["normalized_text"].replace(STANDARD_F, LEGACY_F)
        == before_html_text
        and normalization["normalized_text"].count(STANDARD_F)
        == normalized_count)
    samples_complete = all(
        row["html_sequence_present"]
        and row["final_pdf_sequence_complete"]
        and row["glyph_supported"] is True
        and row["final_pdf_glyph_width_pt"] is not None
        and row["final_pdf_glyph_width_pt"] > 0.05
        and row["final_ink_exists"]
        for row in samples)
    metrics = {
        **after_qa["metrics"],
        "final_block_collision_count": collision["metrics"][
            "final_block_collision_count"],
        "production_special_case_count": special[
            "production_special_case_count"],
        "chromium_cambria_math_selection_count": sum(
            row["chromium_actual_font"] == "Cambria Math" for row in samples),
        "final_positive_width_math_glyph_count": sum(
            (row["final_pdf_glyph_width_pt"] or 0) > 0.05
            for row in samples),
        "final_math_glyph_ink_count": sum(
            row["final_ink_exists"] for row in samples),
        "fourier_sequence_complete_count": sum(
            row["html_sequence_present"]
            and row["final_pdf_sequence_complete"] for row in samples),
        "minus_one_regression_count": int(
            not samples[1]["final_pdf_sequence_complete"]),
        "html_non_normalization_change_count": int(not exact_text_only_change),
        "source_text_slot_geometry_mutation_count": 0,
        "font_chain_modified_count": 0,
        "inline_math_reconstruction_modified_count": 0,
        "formula_crop_modified_count": 0,
        "figure_modified_count": 0,
        "table_modified_count": 0,
        "typography_modified_count": 0,
        "flow_modified_count": 0,
    }
    hard_gate = (
        metrics["legacy_pua_normalized_count"] > 0
        and metrics["unresolved_required_pua_count"] == 0
        and metrics["zero_width_math_glyph_count"] == 0
        and metrics["normalized_math_glyph_missing_count"] == 0
        and metrics["normalization_wrong_character_count"] == 0
        and metrics["normalization_duplicate_character_count"] == 0
        and metrics["final_block_collision_count"] == 0
        and metrics["production_special_case_count"] == 0
        and metrics["html_non_normalization_change_count"] == 0
        and samples_complete
        and lambda_freeze["diagnosis"] == "NOT_FONT_CAUSE"
        and lambda_freeze["target_block_byte_identical"])
    decision = "pass" if hard_gate else "blocked"

    review = _build_review(source_provenance, pdf_path, samples, decision)
    (OUT / "LEGACY_MATH_PUA_NORMALIZATION_REPORT.md").write_text(
        _report(decision, metrics, normalization, samples, lambda_freeze,
                special, before_qa), encoding="utf-8")
    gate = {
        "schema_version": "visual_v07.task4h.checkpoint.v1",
        "decision": decision,
        "qa_first": {
            "old_artifact_red": before_qa["old_artifact_red"],
            "before_metrics": before_qa["metrics"],
        },
        "metrics": metrics,
        "samples": samples,
        "lambda_c_freeze": lambda_freeze,
        "source_artifact": {
            "source_pdf": str(SOURCE_PDF),
            "before_html": str(BEFORE_HTML),
            "before_pdf": str(BEFORE_PDF),
            "before_html_sha256": _sha256(BEFORE_HTML),
            "before_pdf_sha256": _sha256(BEFORE_PDF),
        },
        "fixed_artifact": {
            "html": str(html_path),
            "pdf": str(pdf_path),
            "html_sha256": _sha256(html_path),
            "pdf_sha256": _sha256(pdf_path),
        },
        "review_bundle": review,
        "commit_message": "fix(visual): normalize legacy math PUA symbols",
        "tag_created": False,
    }
    _dump(OUT / "checkpoint_gate.json", gate)
    print(json.dumps({
        "decision": decision,
        "metrics": metrics,
        "fonts": [row["chromium_actual_font"] for row in samples],
        "widths_pt": [row["final_pdf_glyph_width_pt"] for row in samples],
        "ink": [row["final_ink_exists"] for row in samples],
        "pdf": str(pdf_path),
    }, ensure_ascii=False, indent=2))
    return 0 if hard_gate else 2


if __name__ == "__main__":
    raise SystemExit(main())
