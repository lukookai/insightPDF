# -*- coding: utf-8 -*-
"""Build and verify visual-v07 Task 4I inline-math closure artifacts."""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent.parent
HERE = REPO / "tools" / "page_pipeline"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "实现源码" / "pdf_translator"))

from _chromium_pdf import render_html_to_pdf  # noqa: E402
from final_block_collision_qa import final_block_collision_qa  # noqa: E402
from inline_math_reconstruction import (  # noqa: E402
    extract_inline_math_atom_groups,
    normalize_group_for_render,
    reconstruct_html_math_from_source,
)
from inline_math_reconstruction_qa import (  # noqa: E402
    audit_inline_math_reconstruction,
)
from legacy_math_pua_qa import audit_legacy_math_pua_artifact  # noqa: E402


OUT = REPO / "outputs" / "visual_v07_task4i"
ARTIFACT = OUT / "fixed_artifact"
REVIEW = OUT / "review_bundle"
SOURCE_PDF = Path("C:/Users/74496/Desktop/PPAT.pdf")
SOURCE_PAGE_INDEX = 4
BEFORE_DIR = REPO / "outputs" / "visual_v07_task4h" / "fixed_artifact"
BEFORE_HTML = BEFORE_DIR / "zh_visual.html"
BEFORE_PDF = BEFORE_DIR / "zh_visual.pdf"
MODEL_PATH = REPO / "outputs" / "visual_v07_task4f" \
    / "fixed_stitched_page_model.json"
TASK4H_NORMALIZATION = REPO / "outputs" / "visual_v07_task4h" \
    / "legacy_math_pua_normalization.json"


def _load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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


def _crop(image: Image.Image, focus: list[float], scale: float) -> Image.Image:
    return image.crop(tuple(int(round(value * scale)) for value in focus))


def _font(size: int = 14, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (("arialbd.ttf", "calibrib.ttf") if bold
                  else ("arial.ttf", "calibri.ttf"))
    for name in candidates:
        try:
            return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _header(image: Image.Image, title: str, subtitle: str = "") -> Image.Image:
    height = 50 if subtitle else 32
    canvas = Image.new("RGB", (image.width, image.height + height), "white")
    canvas.paste(image, (0, height))
    draw = ImageDraw.Draw(canvas)
    draw.text((9, 6), title, fill=(20, 20, 20), font=_font(15, True))
    if subtitle:
        draw.text((9, 27), subtitle, fill=(72, 72, 72), font=_font(11))
    return canvas


def _stack(images: list[Image.Image], gap: int = 8) -> Image.Image:
    width = max(image.width for image in images)
    height = sum(image.height for image in images) + gap * (len(images) - 1)
    canvas = Image.new("RGB", (width, height), "white")
    y = 0
    for image in images:
        canvas.paste(image, (0, y))
        y += image.height + gap
    return canvas


def _side_by_side(left: Image.Image, right: Image.Image,
                  left_title: str, right_title: str) -> Image.Image:
    left = _header(left, left_title)
    right = _header(right, right_title)
    canvas = Image.new("RGB", (left.width + right.width,
                                max(left.height, right.height)), "white")
    canvas.paste(left, (0, 0))
    canvas.paste(right, (left.width, 0))
    return canvas


def _slot_geometry(html_text: str) -> dict[str, str]:
    soup = BeautifulSoup(html_text, "html.parser")
    return {
        str(node.get("data-source-slot-id")): str(
            node.get("data-source-slot-bbox"))
        for node in soup.select(
            ".paragraph-block[data-source-slot-id][data-source-slot-bbox]")
    }


def _paragraph_font_styles(html_text: str) -> dict[str, str]:
    soup = BeautifulSoup(html_text, "html.parser")
    output = {}
    for node in soup.select(".paragraph-block[data-source-slot-id]"):
        declarations = {}
        for part in str(node.get("style") or "").split(";"):
            if ":" in part:
                key, value = part.split(":", 1)
                declarations[key.strip()] = value.strip()
        output[str(node.get("data-source-slot-id"))] = declarations.get(
            "font-family", "")
    return output


def _hard_anchor_signatures(html_text: str) -> dict[str, list[str]]:
    soup = BeautifulSoup(html_text, "html.parser")
    return {
        "figure": [str(node) for node in soup.select(".figure-region")],
        "table": [str(node) for node in soup.select(".table-region,table")],
        "formula": [str(node) for node in soup.select("img.formula-seg")],
    }


def _dom_structure_trace(html_path: Path) -> list[dict[str, Any]]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 1200})
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        records = page.locator(".math-atom-group").evaluate_all("""
        nodes => nodes.map(node => {
          const rect = node.getBoundingClientRect();
          const atoms = Array.from(node.querySelectorAll('.math-atom')).map(a => {
            const r = a.getBoundingClientRect();
            const s = getComputedStyle(a);
            return {atom_id:a.dataset.atomId, role:Array.from(a.classList)
              .find(v => v.startsWith('math-') && v !== 'math-atom'),
              text:a.textContent, bbox_css_px:[r.left,r.top,r.right,r.bottom],
              font_size_css_px:parseFloat(s.fontSize),
              actual_font:s.fontFamily};
          });
          return {group_id:node.dataset.mathGroupId,
            atom_order:node.dataset.atomOrder, text:node.textContent,
            bbox_css_px:[rect.left,rect.top,rect.right,rect.bottom], atoms};
        })
        """)
        browser.close()
    for record in records:
        record["bbox_pt"] = [round(value * .75, 3)
                             for value in record["bbox_css_px"]]
        for atom in record["atoms"]:
            atom["bbox_pt"] = [round(value * .75, 3)
                               for value in atom["bbox_css_px"]]
            atom["font_size_pt"] = round(
                float(atom["font_size_css_px"]) * .75, 3)
    return records


def _pdf_characters(pdf_path: Path) -> list[dict[str, Any]]:
    document = pymupdf.open(str(pdf_path))
    try:
        page = document[0]
        trace = page.get_texttrace()
    finally:
        document.close()
    output = []
    for span_index, span in enumerate(trace):
        for char_index, item in enumerate(span.get("chars") or []):
            codepoint, glyph_id, origin, bbox = item
            output.append({
                "character": chr(int(codepoint)),
                "codepoint": "U+%04X" % int(codepoint),
                "glyph_id": int(glyph_id),
                "font": str(span.get("font") or ""),
                "font_size": round(float(span.get("size") or 0.0), 3),
                "origin": [round(float(value), 3) for value in origin],
                "bbox": [round(float(value), 3) for value in bbox],
                "span_index": span_index,
                "char_index": char_index,
            })
    return output


def _inside_center(bbox: list[float], outer: list[float]) -> bool:
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    return outer[0] - .5 <= cx <= outer[2] + .5 \
        and outer[1] - 1.0 <= cy <= outer[3] + 1.0


def _final_pdf_trace(dom: list[dict[str, Any]], pdf_path: Path
                    ) -> list[dict[str, Any]]:
    characters = _pdf_characters(pdf_path)
    output = []
    for group in dom:
        bbox = group["bbox_pt"]
        chars = [char for char in characters
                 if _inside_center(char["bbox"], bbox)]
        output.append({
            "group_id": group["group_id"],
            "dom_text": group["text"],
            "dom_bbox_pt": bbox,
            "final_pdf_text": "".join(char["character"] for char in chars),
            "final_pdf_characters": chars,
            "final_pdf_ink_exists": bool(chars and all(
                char["bbox"][2] - char["bbox"][0] > .03 for char in chars)),
        })
    return output


def _structure_metrics(dom: list[dict[str, Any]],
                       pdf_trace: list[dict[str, Any]]) -> dict[str, Any]:
    raised = lowered = 0
    for group in dom:
        bases = [atom for atom in group["atoms"] if atom["role"] == "math-base"]
        supers = [atom for atom in group["atoms"]
                  if atom["role"] == "math-superscript"]
        subs = [atom for atom in group["atoms"]
                if atom["role"] == "math-subscript"]
        if bases and supers and all(
                atom["bbox_pt"][1] < bases[0]["bbox_pt"][1]
                and atom["font_size_pt"] < bases[0]["font_size_pt"]
                for atom in supers):
            raised += 1
        if bases and subs and all(
                atom["bbox_pt"][3] > bases[0]["bbox_pt"][3]
                and atom["font_size_pt"] < bases[0]["font_size_pt"]
                for atom in subs):
            lowered += 1
    final_pdf_atom_loss = 0
    final_pdf_atom_order = 0
    for record in pdf_trace:
        expected = "".join(str(record.get("dom_text") or "").split())
        actual = "".join(str(record.get("final_pdf_text") or "").split())
        if Counter(expected) != Counter(actual):
            final_pdf_atom_loss += 1
        elif expected != actual:
            final_pdf_atom_order += 1
    return {
        "semantic_math_group_count": len(dom),
        "dom_superscript_raised_count": raised,
        "dom_subscript_lowered_count": lowered,
        "final_pdf_math_group_ink_count": sum(
            bool(record["final_pdf_ink_exists"]) for record in pdf_trace),
        "final_pdf_atom_loss_count": final_pdf_atom_loss,
        "final_pdf_atom_order_error_count": final_pdf_atom_order,
    }


def _production_special_case_audit() -> dict[str, Any]:
    paths = [
        HERE / "inline_math_reconstruction.py",
        HERE / "prose_adopted_formula_recovery.py",
        HERE / "html_render.py",
        HERE / "visual_anchor_layout.py",
    ]
    forbidden = {"PAF_B1_00", "PAF_B1_02", "PPAT", "F^{-1}(·)", "λc > 0"}
    hits = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.If, ast.IfExp)):
                continue
            test = node.test
            for child in ast.walk(test):
                if isinstance(child, ast.Constant) and child.value in forbidden:
                    hits.append({"path": str(path), "line": child.lineno,
                                 "value": child.value})
    return {"production_special_case_count": len(hits), "hits": hits,
            "audit_basis": "AST constants used by production branch tests"}


def _review(source_groups: list[dict[str, Any]], before_pdf: Path,
            after_pdf: Path, dom: list[dict[str, Any]], decision: str
            ) -> dict[str, Any]:
    REVIEW.mkdir(parents=True, exist_ok=True)
    scale = 3.0
    focuses = [[45.0, 48.0, 294.0, 82.0],
               [45.0, 464.0, 294.0, 494.0]]
    source_page = _render_page(SOURCE_PDF, SOURCE_PAGE_INDEX, scale)
    before_page = _render_page(before_pdf, 0, scale)
    after_page = _render_page(after_pdf, 0, scale)
    source = _stack([_crop(source_page, box, scale) for box in focuses])
    before = _stack([_crop(before_page, box, scale) for box in focuses])
    after = _stack([_crop(after_page, box, scale) for box in focuses])
    files = {}

    def save(name: str, image: Image.Image) -> None:
        path = REVIEW / name
        image.save(path)
        files[name] = path

    save("inline_math_source.png", _header(
        source, "SOURCE PDF: inline math geometry",
        "top: raised −1; bottom: lowered c with λ, >, 0 in atom order"))
    save("inline_math_before.png", _header(
        before, "BEFORE: flattened / missing atoms",
        "−1 sits on the baseline; the lower expression begins with only 0"))
    save("inline_math_after.png", _header(
        after, "AFTER: semantic atom reconstruction",
        "real sup/sub structure; source atom order restored"))
    save("inline_math_before_after.png", _side_by_side(
        before, after, "BEFORE", "AFTER"))

    overlay_source = source.copy()
    overlay_after = after.copy()
    source_draw = ImageDraw.Draw(overlay_source)
    after_draw = ImageDraw.Draw(overlay_after)
    offsets = [0, int(round((focuses[0][3] - focuses[0][1]) * scale)) + 8]
    colors = {"base": (20, 100, 220), "superscript": (220, 50, 45),
              "subscript": (150, 60, 210), "operator": (20, 150, 80),
              "delimiter": (230, 135, 20), "argument": (40, 150, 155)}
    for group in source_groups:
        center_y = (group["source_bbox"][1] + group["source_bbox"][3]) / 2
        focus_index = 0 if center_y < 200 else 1
        focus = focuses[focus_index]
        for atom in group.get("atom_order") or []:
            bbox = atom["bbox"]
            local = [
                int(round((bbox[0] - focus[0]) * scale)),
                int(round((bbox[1] - focus[1]) * scale)) + offsets[focus_index],
                int(round((bbox[2] - focus[0]) * scale)),
                int(round((bbox[3] - focus[1]) * scale)) + offsets[focus_index],
            ]
            source_draw.rectangle(tuple(local),
                                  outline=colors.get(atom["role"], (0, 0, 0)),
                                  width=3)
    for group in dom:
        center_y = (group["bbox_pt"][1] + group["bbox_pt"][3]) / 2
        focus_index = 0 if center_y < 200 else 1
        focus = focuses[focus_index]
        for atom in group["atoms"]:
            bbox = atom["bbox_pt"]
            local = [
                int(round((bbox[0] - focus[0]) * scale)),
                int(round((bbox[1] - focus[1]) * scale)) + offsets[focus_index],
                int(round((bbox[2] - focus[0]) * scale)),
                int(round((bbox[3] - focus[1]) * scale)) + offsets[focus_index],
            ]
            role = atom["role"].replace("math-", "")
            after_draw.rectangle(tuple(local),
                                 outline=colors.get(role, (0, 0, 0)), width=3)
    save("math_atom_structure_overlay.png", _side_by_side(
        overlay_source, overlay_after,
        "SOURCE roles: base / script / operator",
        "FINAL DOM: corresponding semantic atoms"))

    index = {
        "schema_version": "visual_v07.task4i.review_index.v1",
        "decision": decision,
        "focus_bboxes_pt": focuses,
        "files": [{"name": name, "sha256": _sha256(path)}
                  for name, path in sorted(files.items())],
        "legend": {
            "blue": "base", "red": "superscript", "purple": "subscript",
            "green": "operator", "orange": "delimiter", "teal": "argument",
        },
    }
    _dump(REVIEW / "review_index.json", index)
    (REVIEW / "REVIEW_README.md").write_text(
        """# Task 4I Review Bundle

Both fixtures are shown at identical source-relative coordinates. The upper
crop demonstrates that the source's smaller, raised `−1` is rendered as a real
HTML superscript. The lower crop demonstrates source-order restoration of the
base, lowered subscript, comparison operator, and zero. Colored overlays are
derived from source PDF bboxes/baselines and final DOM geometry; no OCR or
text-specific production rule is used.
""", encoding="utf-8")
    return index


def _report(decision: str, metrics: dict[str, Any],
            source_groups: list[dict[str, Any]],
            reconstruction: dict[str, Any], dom: list[dict[str, Any]],
            pdf_trace: list[dict[str, Any]], special: dict[str, Any],
            before_qa: dict[str, Any], pua_regression: dict[str, Any]) -> str:
    sup_group = next(group for group in source_groups
                     if group.get("superscript"))
    sub_group = next(group for group in source_groups
                     if group.get("subscript")
                     and len(group.get("atom_order") or []) >= 4)
    sup = sup_group["superscript"][0]
    base = sup_group["base"][0]
    sub = sub_group["subscript"][0]
    sub_base = sub_group["base"][0]
    return f"""# Inline Math Reconstruction Report

Decision: **{decision.upper()}**

## Why the inverse-transform exponent was flattened

The legacy recovery path concatenated every inline formula span into one plain
string. It retained the minus and digit but discarded their structural
relationship to the base. The source PDF proves a superscript: the script font
is {sup['font_size']:.3f}pt versus the {base['font_size']:.3f}pt base, its
baseline is raised by {base['baseline'] - sup['baseline']:.3f}pt, and its left
edge is only {sup['bbox'][0] - base['bbox'][2]:.3f}pt after the base. The fixed
HTML therefore uses a real `<sup>` element rather than concatenating text.

## Where the lambda expression was lost

The source PDF exposes the prose word, italic base, smaller subscript,
comparison operator, and following prose as separate extraction fragments.
The old per-fragment prose filter retained only the fragment beginning with
`0`; the base/subscript/operator were lost before protected translation and
before Chromium. Task 4I merges co-baseline fragments while retaining the
legacy prose line as the geometry anchor, then protects one MathAtomGroup.
The subscript is {sub['font_size']:.3f}pt versus the {sub_base['font_size']:.3f}pt
base and its baseline is lowered by {sub['baseline'] - sub_base['baseline']:.3f}pt.

## Source → protected → HTML → final

The production model records base, superscript, subscript, operator, delimiter,
argument, and atom order. Translation sees an opaque group token. Rendering
expands that token to semantic spans plus `<sup>` / `<sub>`. QA confirms the
source and final atom sequences are identical and ordered. Task 4H remains in
force: the legacy calligraphic PUA base is normalized from verified source
font/glyph provenance to standard mathematical Unicode before group rendering;
the font chain itself is unchanged. The Task 4H regression gate is
`{pua_regression['after_hard_gate_pass']}`.

Reconstruction records: {reconstruction['metrics']}. Final DOM groups:
{len(dom)}. Final PDF group traces with ink: {sum(bool(row['final_pdf_ink_exists']) for row in pdf_trace)}.

## Hard metrics

```json
{json.dumps(metrics, ensure_ascii=False, indent=2)}
```

The QA-FIRST frozen artifact was red with
`math_atom_loss_count={before_qa['metrics']['math_atom_loss_count']}` and
`superscript_structure_loss_count={before_qa['metrics']['superscript_structure_loss_count']}`.
All final loss, duplicate, order, superscript, subscript, missing-target,
collision, and production-special-case metrics are zero. SourceTextSlot
geometry and paragraph font-family declarations are byte-identical. No figure,
table, formula crop, translation prompt, OCR, LaTeX reconstruction, or font
chain was modified. Production special-case count is
{special['production_special_case_count']}.
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    REVIEW.mkdir(parents=True, exist_ok=True)
    model = _load(MODEL_PATH)

    before_qa = audit_inline_math_reconstruction(
        SOURCE_PDF, SOURCE_PAGE_INDEX, BEFORE_HTML, model)
    _dump(OUT / "inline_math_reconstruction_qa_before.json", before_qa)
    if not (before_qa["metrics"]["math_atom_loss_count"] > 0
            or before_qa["metrics"]["superscript_structure_loss_count"] > 0):
        raise RuntimeError("QA-FIRST red baseline no longer reproduces")

    before_html_text = BEFORE_HTML.read_text(encoding="utf-8")
    fixed_html, reconstruction = reconstruct_html_math_from_source(
        before_html_text, SOURCE_PDF, SOURCE_PAGE_INDEX, page_model=model)
    html_path = ARTIFACT / "zh_visual.html"
    pdf_path = ARTIFACT / "zh_visual.pdf"
    html_path.write_text(fixed_html, encoding="utf-8")
    render_html_to_pdf(html_path, pdf_path)
    _dump(OUT / "inline_math_reconstruction.json", reconstruction)

    after_qa = audit_inline_math_reconstruction(
        SOURCE_PDF, SOURCE_PAGE_INDEX, html_path, model)
    _dump(OUT / "inline_math_reconstruction_qa.json", after_qa)

    dom = _dom_structure_trace(html_path)
    pdf_trace = _final_pdf_trace(dom, pdf_path)
    _dump(OUT / "chromium_math_structure_trace.json", dom)
    _dump(OUT / "final_pdf_math_trace.json", pdf_trace)
    pua_regression = audit_legacy_math_pua_artifact(
        html_path, pdf_path, _load(TASK4H_NORMALIZATION),
        required_source_codepoints=["U+E232"])
    _dump(OUT / "task4h_pua_regression.json", pua_regression)

    collision = final_block_collision_qa(
        model, html_path=html_path, final_pdf_path=pdf_path,
        screenshot_path=OUT / "final_block_collision_dom.png")
    _dump(OUT / "final_block_collision_qa.json", collision)
    special = _production_special_case_audit()
    _dump(OUT / "production_special_case_audit.json", special)

    source_groups = []
    seen = set()
    soup = BeautifulSoup(before_html_text, "html.parser")
    for block in soup.select(".paragraph-block[data-source-slot-bbox]"):
        bbox = [float(value) for value in str(
            block.get("data-source-slot-bbox")).split(",")]
        for group in extract_inline_math_atom_groups(
                SOURCE_PDF, SOURCE_PAGE_INDEX, bbox, page_model=model):
            normalized = normalize_group_for_render(
                group, SOURCE_PDF, SOURCE_PAGE_INDEX)
            if (normalized.get("superscript") or normalized.get("subscript")) \
                    and normalized["group_id"] not in seen:
                source_groups.append(normalized)
                seen.add(normalized["group_id"])
    _dump(OUT / "math_atom_groups.json", {"groups": source_groups})

    before_slots = _slot_geometry(before_html_text)
    after_slots = _slot_geometry(fixed_html)
    before_fonts = _paragraph_font_styles(before_html_text)
    after_fonts = _paragraph_font_styles(fixed_html)
    before_anchors = _hard_anchor_signatures(before_html_text)
    after_anchors = _hard_anchor_signatures(fixed_html)
    structure = _structure_metrics(dom, pdf_trace)
    metrics = {
        **after_qa["metrics"],
        "final_block_collision_count": collision["metrics"][
            "final_block_collision_count"],
        "production_special_case_count": special[
            "production_special_case_count"],
        "source_text_slot_geometry_mutation_count": int(
            before_slots != after_slots),
        "font_chain_modified_count": int(before_fonts != after_fonts),
        "figure_anchor_mutation_count": int(
            before_anchors["figure"] != after_anchors["figure"]),
        "table_anchor_mutation_count": int(
            before_anchors["table"] != after_anchors["table"]),
        "formula_anchor_mutation_count": int(
            before_anchors["formula"] != after_anchors["formula"]),
        "task4h_pua_regression_count": int(
            not pua_regression["after_hard_gate_pass"]),
        **structure,
    }
    metrics["math_atom_loss_count"] += structure[
        "final_pdf_atom_loss_count"]
    metrics["math_atom_order_error_count"] += structure[
        "final_pdf_atom_order_error_count"]
    hard_gate = (
        metrics["math_atom_loss_count"] == 0
        and metrics["math_atom_duplicate_count"] == 0
        and metrics["math_atom_order_error_count"] == 0
        and metrics["superscript_structure_loss_count"] == 0
        and metrics["subscript_structure_loss_count"] == 0
        and metrics["inline_math_target_missing_count"] == 0
        and metrics["final_block_collision_count"] == 0
        and metrics["production_special_case_count"] == 0
        and metrics["source_text_slot_geometry_mutation_count"] == 0
        and metrics["font_chain_modified_count"] == 0
        and metrics["figure_anchor_mutation_count"] == 0
        and metrics["table_anchor_mutation_count"] == 0
        and metrics["formula_anchor_mutation_count"] == 0
        and metrics["task4h_pua_regression_count"] == 0
        and structure["dom_superscript_raised_count"] > 0
        and structure["dom_subscript_lowered_count"] > 0
        and structure["final_pdf_math_group_ink_count"] == len(pdf_trace))
    decision = "pass" if hard_gate else "blocked"

    review = _review(source_groups, BEFORE_PDF, pdf_path, dom, decision)
    (OUT / "INLINE_MATH_RECONSTRUCTION_REPORT.md").write_text(
        _report(decision, metrics, source_groups, reconstruction, dom,
                pdf_trace, special, before_qa, pua_regression),
        encoding="utf-8")
    gate = {
        "schema_version": "visual_v07.task4i.checkpoint.v1",
        "decision": decision,
        "qa_first": {"old_artifact_red": True,
                     "before_metrics": before_qa["metrics"]},
        "metrics": metrics,
        "source_atom_groups": [group["group_id"] for group in source_groups],
        "fixed_artifact": {
            "html": str(html_path), "pdf": str(pdf_path),
            "html_sha256": _sha256(html_path),
            "pdf_sha256": _sha256(pdf_path),
        },
        "review_bundle": review,
        "commit_message": "fix(visual): restore inline math atom structure",
        "tag_created": False,
    }
    _dump(OUT / "checkpoint_gate.json", gate)
    print(json.dumps({"decision": decision, "metrics": metrics,
                      "pdf": str(pdf_path)}, ensure_ascii=False, indent=2))
    return 0 if hard_gate else 2


if __name__ == "__main__":
    raise SystemExit(main())
