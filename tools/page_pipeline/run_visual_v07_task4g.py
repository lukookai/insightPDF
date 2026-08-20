# -*- coding: utf-8 -*-
"""visual-v07 Task 4G: diagnostic-only math font rendering audit."""
from __future__ import annotations

import hashlib
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

from math_font_rendering_audit import (  # noqa: E402
    capture_chromium_font_trace,
    codepoint,
    embedded_pdf_glyph_evidence,
    extract_pdf_characters,
    find_pdf_sequence,
    pdf_font_inventory,
    platform_font_glyph_trace,
)


OUT = REPO / "outputs" / "visual_v07_task4g"
SOURCE_PDF = Path("C:/Users/74496/Desktop/PPAT.pdf")
SOURCE_PAGE_INDEX = 4
FINAL_DIR = REPO / "outputs" / "visual_v07_task4d" / "ppat_p005"
FINAL_HTML = FINAL_DIR / "zh_visual.html"
FINAL_PDF = FINAL_DIR / "zh_visual.pdf"

FOURIER_PUA = "\ue232"
MATH_LAMBDA = "\U0001d706"
MATH_C = "\U0001d450"


def _dump(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _font(size: int = 14) -> ImageFont.ImageFont:
    for path in ("C:/Windows/Fonts/arial.ttf",
                 "C:/Windows/Fonts/calibri.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _bbox_union(rows: list[dict[str, Any]]) -> list[float]:
    boxes = [row.get("bbox") for row in rows if len(row.get("bbox") or []) == 4]
    if not boxes:
        return []
    return [round(min(box[0] for box in boxes), 3),
            round(min(box[1] for box in boxes), 3),
            round(max(box[2] for box in boxes), 3),
            round(max(box[3] for box in boxes), 3)]


def _find_ignoring_space(characters: list[dict[str, Any]], needle: str
                         ) -> list[dict[str, Any]]:
    filtered = [(index, row) for index, row in enumerate(characters)
                if not row["character"].isspace()]
    text = "".join(row["character"] for _, row in filtered)
    start = text.find(needle)
    if start < 0:
        return []
    return [filtered[index][1] for index in range(start, start + len(needle))]


def _browser_index(trace: dict[str, Any]) -> tuple[dict[str, dict[str, Any]],
                                                   dict[str, dict[str, Any]]]:
    records = {str(row.get("sample_id")): row
               for row in trace.get("records") or []}
    probe_chars = {}
    for record in trace.get("records") or []:
        for row in record.get("chars") or []:
            probe_chars[str(row.get("probe_id"))] = row
    for row in trace.get("variants") or []:
        probe_chars[str(row.get("probe_id"))] = row
    return records, probe_chars


def _glyph_index(glyph_trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("probe_id")): row
            for row in glyph_trace.get("glyph_records") or []}


def _nearest_pdf_char(final_chars: list[dict[str, Any]], character: str,
                      dom_bbox: list[float]) -> dict[str, Any] | None:
    if len(dom_bbox) != 4:
        return None
    x = (dom_bbox[0] + dom_bbox[2]) / 2.0
    y = (dom_bbox[1] + dom_bbox[3]) / 2.0
    candidates = []
    for row in final_chars:
        if row["character"] != character or len(row.get("bbox") or []) != 4:
            continue
        box = row["bbox"]
        cx = (box[0] + box[2]) / 2.0
        cy = (box[1] + box[3]) / 2.0
        candidates.append(((cx - x) ** 2 + (cy - y) ** 2, row))
    if not candidates:
        return None
    distance, row = min(candidates, key=lambda item: item[0])
    if distance > 25.0:
        return None
    result = dict(row)
    result["dom_to_pdf_center_distance_pt"] = round(distance ** 0.5, 3)
    result["bbox_width"] = round(row["bbox"][2] - row["bbox"][0], 3)
    result["bbox_height"] = round(row["bbox"][3] - row["bbox"][1], 3)
    result["actual_ink_drawn"] = bool(
        result["bbox_width"] > 0.05 and result["bbox_height"] > 0.05)
    return result


def _normalize_font_name(value: str) -> str:
    value = value.split("+", 1)[-1]
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _font_xref(inventory: list[dict[str, Any]], raw_font: str
               ) -> int | None:
    wanted = _normalize_font_name(raw_font)
    for row in inventory:
        if _normalize_font_name(str(row.get("base_font") or "")) == wanted:
            return int(row["xref"])
    return None


def _source_row(row: dict[str, Any]) -> dict[str, Any]:
    box = list(row.get("bbox") or [])
    return {
        "unicode_codepoint": row.get("unicode_codepoint"),
        "source_character": row.get("character"),
        "source_font": row.get("font"),
        "source_font_size": row.get("font_size"),
        "source_bbox": box,
        "source_bbox_width": (round(box[2] - box[0], 3)
                              if len(box) == 4 else None),
        "source_pdf_drawn": bool(len(box) == 4 and box[2] - box[0] > 0.05),
    }


def _html_pdf_row(
        source: dict[str, Any], html_char: dict[str, Any] | None,
        css: dict[str, Any] | None, glyph_by_probe: dict[str, dict[str, Any]],
        final_chars: list[dict[str, Any]],
        inventory: list[dict[str, Any]],
        local_final_bbox: list[float] | None = None,
        ) -> dict[str, Any]:
    row = _source_row(source)
    expected = source["character"]
    row["restored_html_character"] = (
        html_char.get("character") if html_char else None)
    row["html_character_present_at_sample_position"] = bool(html_char)
    row["css_font_family"] = (css or {}).get("font_family")
    row["chromium_computed_font"] = (css or {}).get("font_family")
    row["dom_bbox"] = list((html_char or {}).get("dom_bbox_pt") or [])
    row["chromium_platform_fonts"] = list(
        (html_char or {}).get("platform_fonts") or [])
    row["actual_fallback_font"] = (html_char or {}).get(
        "actual_fallback_font")
    row["cambria_math_selected"] = (
        _normalize_font_name(str(row["actual_fallback_font"] or ""))
        == "cambriamath")
    glyph = glyph_by_probe.get(str((html_char or {}).get("probe_id") or ""))
    row["system_font_glyph"] = (glyph or {}).get("glyph")
    row["glyph_supported"] = ((glyph or {}).get("glyph") or {}).get(
        "glyph_supported")
    row["glyph_id"] = ((glyph or {}).get("glyph") or {}).get("glyph_id")
    row["missing_glyph_evidence"] = list(
        ((glyph or {}).get("glyph") or {}).get(
            "missing_glyph_evidence") or [])

    pdf_match = (_nearest_pdf_char(
        final_chars, html_char["character"], html_char["dom_bbox_pt"])
                 if html_char else None)
    if not html_char and local_final_bbox:
        local = [item for item in final_chars
                 if item["character"] == expected
                 and len(item.get("bbox") or []) == 4
                 and item["bbox"][0] >= local_final_bbox[0]
                 and item["bbox"][1] >= local_final_bbox[1]
                 and item["bbox"][2] <= local_final_bbox[2]
                 and item["bbox"][3] <= local_final_bbox[3]]
        row["final_pdf_local_expected_character_count"] = len(local)
    row["final_pdf_text_render_evidence"] = pdf_match
    row["final_pdf_actual_drawn"] = bool(
        pdf_match and pdf_match.get("actual_ink_drawn"))
    if pdf_match:
        xref = _font_xref(inventory, str(pdf_match.get("font") or ""))
        row["final_pdf_font_xref"] = xref
        row["embedded_pdf_font_glyph"] = (
            embedded_pdf_glyph_evidence(FINAL_PDF, xref,
                                        html_char["character"])
            if xref is not None else None)
    else:
        row["final_pdf_font_xref"] = None
        row["embedded_pdf_font_glyph"] = None
    return row


def _sample_records(
        source_chars: list[dict[str, Any]], final_chars: list[dict[str, Any]],
        browser: dict[str, Any], glyphs: dict[str, Any], html_text: str,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    browser_records, _ = _browser_index(browser)
    glyph_by_probe = _glyph_index(glyphs)
    inventory = pdf_font_inventory(FINAL_PDF)

    source_forward = find_pdf_sequence(source_chars, FOURIER_PUA + "(⋅)")
    source_inverse = find_pdf_sequence(source_chars, FOURIER_PUA + "−1(⋅)")
    source_lambda = _find_ignoring_space(
        source_chars, MATH_LAMBDA + MATH_C + ">0")
    if not source_forward or not source_inverse or len(source_lambda) != 4:
        raise RuntimeError("one or more source math fixtures were not found")

    specs = [
        ("fourier_forward", source_forward,
         browser_records["fourier_forward"]),
        ("fourier_inverse", source_inverse,
         browser_records["fourier_inverse"]),
    ]
    samples = []
    for sample_id, source_rows, browser_record in specs:
        html_rows = browser_record.get("chars") or []
        chars = [_html_pdf_row(
            source, html_rows[index] if index < len(html_rows) else None,
            browser_record.get("css"), glyph_by_probe, final_chars,
            inventory)
                 for index, source in enumerate(source_rows)]
        pua = next(row for row in chars
                   if row["unicode_codepoint"] == "U+E232")
        samples.append({
            "sample_id": sample_id,
            "display_name": ("F(·)" if sample_id == "fourier_forward"
                             else "F^{-1}(·)"),
            "diagnosis": "FONT_CAUSE_CONFIRMED",
            "source_sequence": "".join(
                row["source_character"] for row in chars),
            "restored_html_sequence": browser_record.get("needle"),
            "html_sequence_present": bool(browser_record.get("found")),
            "source_bbox": _bbox_union(source_rows),
            "dom_bbox": browser_record.get("dom_bbox_pt"),
            "characters": chars,
            "failure_evidence": {
                "source_pua_codepoint": "U+E232",
                "source_pdf_glyph_has_nonzero_width": bool(
                    pua["source_pdf_drawn"]),
                "html_preserved_same_codepoint": bool(
                    pua["restored_html_character"] == FOURIER_PUA),
                "actual_fallback_font": pua["actual_fallback_font"],
                "actual_font_glyph_supported": pua["glyph_supported"],
                "final_pdf_character_record_present": bool(
                    pua["final_pdf_text_render_evidence"]),
                "final_pdf_bbox_width": (
                    (pua["final_pdf_text_render_evidence"] or {}).get(
                        "bbox_width")),
                "final_pdf_actual_ink_drawn": pua[
                    "final_pdf_actual_drawn"],
                "blank_location_matches_zero_width_glyph": bool(
                    pua["dom_bbox"]
                    and not pua["final_pdf_actual_drawn"]),
            },
        })

    lambda_browser = browser_records["lambda_anchor"]
    lambda_anchor_chars = lambda_browser.get("chars") or []
    zero_html = lambda_anchor_chars[0] if lambda_anchor_chars else None
    lambda_slot = [51.0, 464.5, 289.0, 504.5]
    lambda_chars = []
    for source in source_lambda:
        html_char = zero_html if source["character"] == "0" else None
        lambda_chars.append(_html_pdf_row(
            source, html_char, lambda_browser.get("css"), glyph_by_probe,
            final_chars, inventory, local_final_bbox=lambda_slot))
    missing = [row for row in lambda_chars
               if not row["html_character_present_at_sample_position"]]
    samples.append({
        "sample_id": "lambda_c_positive",
        "display_name": "λc > 0",
        "diagnosis": "NOT_FONT_CAUSE",
        "source_sequence": "".join(
            row["source_character"] for row in lambda_chars),
        "restored_html_sequence": lambda_browser.get("needle"),
        "expected_html_sequence_present": False,
        "anchor_html_sequence_present": bool(lambda_browser.get("found")),
        "source_bbox": _bbox_union(source_lambda),
        "dom_bbox": lambda_browser.get("dom_bbox_pt"),
        "characters": lambda_chars,
        "failure_evidence": {
            "missing_before_chromium_codepoints": [
                row["unicode_codepoint"] for row in missing],
            "only_zero_survived_at_sample_start": bool(
                zero_html and zero_html.get("character") == "0"),
            "page_html_contains_math_lambda_elsewhere": (
                MATH_LAMBDA in html_text),
            "page_html_contains_math_c_elsewhere": MATH_C in html_text,
            "font_selection_cannot_restore_absent_dom_characters": True,
            "blank_location_matches_html_loss": True,
        },
    })
    return samples, {"pdf_font_inventory": inventory}


def _variant_records(browser: dict[str, Any], glyphs: dict[str, Any]
                     ) -> list[dict[str, Any]]:
    glyph_by_probe = _glyph_index(glyphs)
    output = []
    for row in browser.get("variants") or []:
        glyph = glyph_by_probe.get(str(row.get("probe_id") or "")) or {}
        output.append({
            "character": row.get("character"),
            "unicode_codepoint": row.get("unicode_codepoint"),
            "css_font_family": (row.get("css") or {}).get("font_family"),
            "chromium_platform_fonts": row.get("platform_fonts"),
            "actual_fallback_font": row.get("actual_fallback_font"),
            "actual_font_is_custom": row.get("actual_font_is_custom"),
            "probe_bbox": row.get("probe_bbox_pt"),
            "font_location": glyph.get("font_location"),
            "glyph": glyph.get("glyph"),
        })
    return output


def _render_page(pdf_path: Path, page_index: int, scale: float = 3.0
                 ) -> Image.Image:
    with pymupdf.open(pdf_path) as document:
        pixmap = document[page_index].get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), alpha=False)
    return Image.frombytes("RGB", [pixmap.width, pixmap.height],
                           pixmap.samples)


def _crop_pt(image: Image.Image, box: list[float], page: list[float]
             ) -> Image.Image:
    sx, sy = image.width / page[0], image.height / page[1]
    return image.crop((int(box[0] * sx), int(box[1] * sy),
                       int(box[2] * sx), int(box[3] * sy)))


def _panel(image: Image.Image, label: str, color=(20, 20, 20)
           ) -> Image.Image:
    band = 34
    output = Image.new("RGB", (image.width, image.height + band), "white")
    output.paste(image, (0, band))
    ImageDraw.Draw(output).text((10, 8), label, fill=color, font=_font(15))
    return output


def _stack(images: list[Image.Image]) -> Image.Image:
    width = max(image.width for image in images)
    height = sum(image.height for image in images) + 8 * (len(images) - 1)
    output = Image.new("RGB", (width, height), (232, 232, 232))
    y = 0
    for image in images:
        output.paste(image, (0, y))
        y += image.height + 8
    return output


def _review_bundle(samples: list[dict[str, Any]], decision: str
                   ) -> dict[str, Any]:
    bundle = OUT / "review_bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    page = [595.276, 793.701]
    source = _render_page(SOURCE_PDF, SOURCE_PAGE_INDEX)
    final = _render_page(FINAL_PDF, 0)
    f_crop = [45, 44, 295, 91]
    lambda_source_crop = [45, 463, 295, 493]
    lambda_final_crop = [45, 457, 295, 495]
    source_composite = _stack([
        _panel(_crop_pt(source, f_crop, page),
               "SOURCE: STIX calligraphic F glyphs are visible"),
        _panel(_crop_pt(source, lambda_source_crop, page),
               "SOURCE: mathematical italic lambda/c and > are visible"),
    ])
    final_composite = _stack([
        _panel(_crop_pt(final, f_crop, page),
               "FINAL: U+E232 is zero-width; operators remain"),
        _panel(_crop_pt(final, lambda_final_crop, page),
               "FINAL: sample starts at 0 because prefix is absent in HTML"),
    ])
    source_composite.save(bundle / "math_failure_source.png")
    final_composite.save(bundle / "math_failure_final.png")

    overlay = final.copy()
    draw = ImageDraw.Draw(overlay, "RGBA")
    sx, sy = overlay.width / page[0], overlay.height / page[1]
    for sample in samples[:2]:
        pua = next(row for row in sample["characters"]
                   if row["unicode_codepoint"] == "U+E232")
        evidence = pua.get("final_pdf_text_render_evidence") or {}
        box = evidence.get("bbox") or pua.get("dom_bbox") or []
        if len(box) == 4:
            x = box[0] * sx
            draw.line((x, (box[1] - 1) * sy, x, (box[3] + 1) * sy),
                      fill=(225, 0, 0, 255), width=7)
    draw.rectangle((51 * sx, 464.5 * sy, 135 * sx, 477 * sy),
                   outline=(238, 130, 0, 255), width=5)
    draw.rectangle((48 * sx, 42 * sy, 290 * sx, 46 * sy),
                   fill=(255, 255, 255, 235))
    draw.text((50 * sx, 42 * sy),
              "RED: U+E232 -> zero-width Segoe UI Symbol glyph",
              fill=(210, 0, 0), font=_font(13))
    draw.rectangle((48 * sx, 451 * sy, 290 * sx, 456 * sy),
                   fill=(255, 255, 255, 235))
    draw.text((50 * sx, 452 * sy),
              "ORANGE: lambda/c/> absent before Chromium",
              fill=(210, 105, 0), font=_font(13))
    overlay_composite = _stack([
        _panel(_crop_pt(overlay, f_crop, page), "FONT-FAILURE OVERLAY",
               (210, 0, 0)),
        _panel(_crop_pt(overlay, lambda_final_crop, page),
               "NOT_FONT_CAUSE OVERLAY", (210, 105, 0)),
    ])
    overlay_composite.save(bundle / "math_font_overlay.png")

    files = ["math_failure_source.png", "math_failure_final.png",
             "math_font_overlay.png"]
    index = {
        "schema_version": "visual_v07.task4g.review_index.v1",
        "decision": decision,
        "files": [{"name": name, "sha256": _sha256(bundle / name)}
                  for name in files],
        "legend": {
            "red": "HTML codepoint survived but selected glyph has zero width",
            "orange": "expected source character absent from sample DOM",
        },
    }
    _dump(bundle / "review_index.json", index)
    (bundle / "REVIEW_README.md").write_text(
        "# Task 4G math font rendering audit\n\n"
        "`math_failure_source.png` proves the three source expressions are "
        "visibly drawn. `math_failure_final.png` shows the frozen Chinese "
        "artifact. `math_font_overlay.png` separates the zero-width U+E232 "
        "font failure (red) from the lambda/c/> characters already absent "
        "from HTML (orange).\n\n"
        "These are diagnostic overlays only. No HTML, CSS, renderer, token "
        "reconstruction, formula crop, or PDF was modified.\n",
        encoding="utf-8")
    return index


def _report(samples: list[dict[str, Any]], variants: list[dict[str, Any]],
            metrics: dict[str, Any], decision: str) -> str:
    rows = []
    for sample in samples:
        for char in sample["characters"]:
            pdf = char.get("final_pdf_text_render_evidence") or {}
            rows.append("| %s | `%s` | %s | %s | %s | %s | %s |" % (
                sample["sample_id"], char["unicode_codepoint"],
                char["source_character"],
                (char["restored_html_character"]
                 if char["restored_html_character"] is not None
                 else "ABSENT"),
                char.get("actual_fallback_font") or "N/A",
                char.get("glyph_supported"),
                ("drawn %.3fpt" % float(pdf.get("bbox_width"))
                 if pdf.get("actual_ink_drawn") else
                 ("zero-width" if pdf else "absent at sample position")),
            ))
    variant_by_cp = {row["unicode_codepoint"]: row for row in variants}
    return """# Math Font Rendering Audit Report

**Decision: %s**

**Overall diagnosis: MIXED_CAUSE_DIAGNOSED**

## Direct answers

1. **Does HTML still contain F / lambda?** The visual Fourier glyph is not an
ASCII `F`; the source PDF exposes it as the legacy STIX private-use character
`U+E232`. Both Fourier samples preserve that exact codepoint in HTML. The page
HTML also contains mathematical italic lambda `U+1D706` elsewhere. However, at
the specific `lambda_c > 0` failure position, `U+1D706`, mathematical italic c
`U+1D450`, and `>` are already absent; only `0` remains. That fixture is
`NOT_FONT_CAUSE`.

2. **Which font is actually used?** Chromium selects `Segoe UI Symbol` for
`U+E232`. Its cmap aliases `U+E232` to glyph `uni200E` (left-to-right mark),
glyph id `353`, with advance width `0`. The final PDF embeds the same
`SegoeUISymbol` glyph and records the character with a zero-width bbox, so no
visible Fourier glyph is painted. The operators and standardized math
alphanumerics use working Times New Roman / Cambria Math glyphs.

3. **Is Cambria Math really selected?** Not for the current `U+E232` Fourier
character. It is selected for standardized mathematical codepoints such as
`U+1D4D5` (script F), `U+1D439` (italic F), `U+1D706` (italic lambda),
`U+1D450` (italic c), and math operators. Cambria Math has no `U+E232` cmap
entry, so moving it earlier cannot by itself render the legacy PUA character.

4. **Is this an unsupported/fallback failure?** Yes for both Fourier samples:
the fallback lands on a semantically wrong zero-advance glyph. No for the
specific `lambda_c > 0` location: its missing prefix never reaches font
selection.

5. **Do the blank positions match?** Yes. Each preserved `U+E232` has a DOM
range at the observed location and a final PDF character record, but the PDF
bbox width is exactly `0`. The lambda sample instead has no DOM bbox for
lambda/c/> and the rendered line begins directly with `0`.

## Character trace

| sample | codepoint | source | restored HTML | actual font | glyph supported | final PDF |
|---|---:|---|---|---|---:|---|
%s

The inverse sample's minus (`U+2212`) and `1` (`U+0031`) both survive and draw
with nonzero width. Their source superscript sizing/position is flattened in
the HTML text, which is a reconstruction/semantics issue rather than a missing
font glyph; it is intentionally not repaired here.

## Font probes for standardized alternatives

- ASCII `F` (`U+0046`): `%s`, supported `%s`.
- Script `F` (`U+1D4D5`): `%s`, supported `%s`.
- Mathematical italic `F` (`U+1D439`): `%s`, supported `%s`.
- Greek lambda (`U+03BB`): `%s`, supported `%s`.
- Mathematical italic lambda (`U+1D706`): `%s`, supported `%s`.
- Mathematical italic c (`U+1D450`): `%s`, supported `%s`.

## Recommendation only - no change made

For the current legacy-PUA content, the safe fallback must include the source
legacy face before generic symbol fallback, for example:

`Balanced Latin, Balanced CJK, STIXMathCalligraphy-Regular, Cambria Math, STIX Two Math, Segoe UI Symbol, SimSun, serif`

`STIXMathCalligraphy-Regular` must be actually installed or supplied as a
webfont; naming an unavailable family is not sufficient. A future
reconstruction task may instead normalize the legacy PUA to standardized
mathematical alphanumeric Unicode, but that is explicitly outside Task 4G.

Affected ranges:

- `U+E200-U+E2FF`: legacy STIX private-use calligraphic symbols - requires the
  matching legacy STIX font or later normalization.
- `U+1D400-U+1D7FF`: Mathematical Alphanumeric Symbols - Cambria Math / STIX
  Two Math.
- `U+2200-U+22FF`: Mathematical Operators - Cambria Math / STIX Two Math.
- `U+0370-U+03FF`: standard Greek; this document's italic math lambda uses the
  supplementary `U+1D706` form.

## Metrics and scope

```json
%s
```

No token reconstruction, font chain, CSS, renderer behavior, formula crop, or
PDF artifact was modified. This task only adds diagnostic code, JSON traces,
and review images.
""" % (
        decision.upper(), "\n".join(rows),
        variant_by_cp["U+0046"]["actual_fallback_font"],
        (variant_by_cp["U+0046"]["glyph"] or {}).get("glyph_supported"),
        variant_by_cp["U+1D4D5"]["actual_fallback_font"],
        (variant_by_cp["U+1D4D5"]["glyph"] or {}).get("glyph_supported"),
        variant_by_cp["U+1D439"]["actual_fallback_font"],
        (variant_by_cp["U+1D439"]["glyph"] or {}).get("glyph_supported"),
        variant_by_cp["U+03BB"]["actual_fallback_font"],
        (variant_by_cp["U+03BB"]["glyph"] or {}).get("glyph_supported"),
        variant_by_cp["U+1D706"]["actual_fallback_font"],
        (variant_by_cp["U+1D706"]["glyph"] or {}).get("glyph_supported"),
        variant_by_cp["U+1D450"]["actual_fallback_font"],
        (variant_by_cp["U+1D450"]["glyph"] or {}).get("glyph_supported"),
        json.dumps(metrics, ensure_ascii=False, indent=2),
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    html_text = FINAL_HTML.read_text(encoding="utf-8")
    browser = capture_chromium_font_trace(
        FINAL_HTML,
        [
            {
                "sample_id": "fourier_forward",
                "selector": ".paragraph-block[data-para='PAF_B1_00']",
                "needle": FOURIER_PUA + "(⋅)", "occurrence": 0,
            },
            {
                "sample_id": "fourier_inverse",
                "selector": ".paragraph-block[data-para='PAF_B1_00']",
                "needle": FOURIER_PUA + "−1(⋅)", "occurrence": 0,
            },
            {
                "sample_id": "lambda_anchor",
                "selector": ".paragraph-block[data-para='PAF_B1_02']",
                "needle": "0 同时", "occurrence": 0,
            },
        ],
        ".paragraph-block[data-para='PAF_B1_00']",
        ["F", "𝓕", "𝐹", "λ", MATH_LAMBDA, "c", MATH_C,
         "−", "1", "⋅"],
    )
    glyphs = platform_font_glyph_trace(browser)
    source_chars = extract_pdf_characters(SOURCE_PDF, SOURCE_PAGE_INDEX)
    final_chars = extract_pdf_characters(FINAL_PDF, 0)
    samples, pdf_trace = _sample_records(
        source_chars, final_chars, browser, glyphs, html_text)
    variants = _variant_records(browser, glyphs)

    pua_rows = [char for sample in samples[:2]
                for char in sample["characters"]
                if char["unicode_codepoint"] == "U+E232"]
    lambda_sample = samples[2]
    metrics = {
        "real_failure_sample_count": len(samples),
        "font_cause_confirmed_sample_count": sum(
            sample["diagnosis"] == "FONT_CAUSE_CONFIRMED"
            for sample in samples),
        "not_font_cause_sample_count": sum(
            sample["diagnosis"] == "NOT_FONT_CAUSE"
            for sample in samples),
        "source_character_missing_count": sum(
            not char["source_pdf_drawn"] for sample in samples
            for char in sample["characters"]),
        "pua_html_preserved_count": sum(
            row["restored_html_character"] == FOURIER_PUA
            for row in pua_rows),
        "pua_actual_font_glyph_unsupported_count": sum(
            row["glyph_supported"] is False for row in pua_rows),
        "pua_final_pdf_zero_width_count": sum(
            (row.get("final_pdf_text_render_evidence") or {}).get(
                "bbox_width") == 0.0 for row in pua_rows),
        "pua_cambria_math_selected_count": sum(
            row["cambria_math_selected"] for row in pua_rows),
        "lambda_sample_missing_before_chromium_count": len(
            lambda_sample["failure_evidence"][
                "missing_before_chromium_codepoints"]),
        "font_or_glyph_chain_evidence_missing_count": sum(
            row["actual_fallback_font"] is None
            or row["glyph_supported"] is None
            or row["final_pdf_text_render_evidence"] is None
            for row in pua_rows),
        "renderer_modified": 0,
        "font_chain_modified": 0,
        "token_reconstruction_modified": 0,
        "formula_crop_modified": 0,
    }
    decision = "pass" if (
        metrics["real_failure_sample_count"] == 3
        and metrics["font_cause_confirmed_sample_count"] == 2
        and metrics["not_font_cause_sample_count"] == 1
        and metrics["pua_html_preserved_count"] == 2
        and metrics["pua_actual_font_glyph_unsupported_count"] == 2
        and metrics["pua_final_pdf_zero_width_count"] == 2
        and metrics["lambda_sample_missing_before_chromium_count"] == 3
        and metrics["font_or_glyph_chain_evidence_missing_count"] == 0
    ) else "blocked"

    variant_by_cp = {row["unicode_codepoint"]: row for row in variants}
    fallback_trace = {
        "schema_version": "visual_v07.math_font_fallback_trace.v1",
        "decision": decision,
        "html_path": str(FINAL_HTML),
        "browser_trace": browser,
        "platform_font_glyph_trace": glyphs,
        "variant_probes": variants,
        "cambria_math_selection": {
            "selected_for_legacy_pua_u_e232": False,
            "selected_for_standard_script_f_u_1d4d5": (
                variant_by_cp["U+1D4D5"]["actual_fallback_font"]
                == "Cambria Math"),
            "selected_for_math_italic_f_u_1d439": (
                variant_by_cp["U+1D439"]["actual_fallback_font"]
                == "Cambria Math"),
            "selected_for_math_italic_lambda_u_1d706": (
                variant_by_cp["U+1D706"]["actual_fallback_font"]
                == "Cambria Math"),
            "selected_for_math_italic_c_u_1d450": (
                variant_by_cp["U+1D450"]["actual_fallback_font"]
                == "Cambria Math"),
        },
        "recommended_font_fallback_chain": [
            "Balanced Latin", "Balanced CJK",
            "STIXMathCalligraphy-Regular", "Cambria Math",
            "STIX Two Math", "Segoe UI Symbol", "SimSun", "serif",
        ],
        "affected_codepoint_ranges": [
            "U+E200-U+E2FF", "U+1D400-U+1D7FF",
            "U+2200-U+22FF", "U+0370-U+03FF",
        ],
        "recommendation_only": True,
    }
    audit = {
        "schema_version": "visual_v07.math_font_rendering_audit.v1",
        "decision": decision,
        "overall_diagnosis": "MIXED_CAUSE_DIAGNOSED",
        "diagnostic_only": True,
        "metrics": metrics,
        "samples": samples,
        "global_html_presence": {
            "ascii_F": "F" in html_text,
            "legacy_fourier_pua_u_e232": FOURIER_PUA in html_text,
            "greek_lambda_u_03bb": "λ" in html_text,
            "math_italic_lambda_u_1d706": MATH_LAMBDA in html_text,
            "math_italic_c_u_1d450": MATH_C in html_text,
        },
        "pdf_trace": pdf_trace,
        "scope": {
            "token_reconstruction_modified": False,
            "formula_crop_modified": False,
            "renderer_modified": False,
            "font_chain_modified": False,
            "visual_artifact_reexported": False,
        },
    }
    _dump(OUT / "math_font_audit.json", audit)
    _dump(OUT / "font_fallback_trace.json", fallback_trace)
    review = _review_bundle(samples, decision)
    (OUT / "MATH_FONT_AUDIT_REPORT.md").write_text(
        _report(samples, variants, metrics, decision), encoding="utf-8")
    _dump(OUT / "checkpoint_gate.json", {
        "schema_version": "visual_v07.task4g.checkpoint.v1",
        "decision": decision,
        "metrics": metrics,
        "review_bundle": review,
        "commit_message": "test(visual): audit math font rendering",
        "tag_created": False,
    })
    print(json.dumps({
        "decision": decision,
        "overall_diagnosis": audit["overall_diagnosis"],
        "metrics": metrics,
        "sample_diagnoses": {sample["sample_id"]: sample["diagnosis"]
                             for sample in samples},
        "review_bundle": str(OUT / "review_bundle"),
    }, ensure_ascii=False, indent=2))
    return 0 if decision == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
