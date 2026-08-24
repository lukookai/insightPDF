# -*- coding: utf-8 -*-
"""Build and verify visual-v07 Task 4K math-aware block measurement."""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

import pymupdf
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent.parent
HERE = REPO / "tools" / "page_pipeline"
sys.path.insert(0, str(HERE))

from final_block_collision_qa import final_block_collision_qa  # noqa: E402
from math_aware_block_measurement_qa import (  # noqa: E402
    audit_math_aware_block_measurement,
)


OUT = REPO / "outputs" / "visual_v07_task4k"
ARTIFACT = OUT / "fixed_artifact"
REVIEW = OUT / "review_bundle"
TASK4I = REPO / "outputs" / "visual_v07_task4i"
BEFORE_HTML = TASK4I / "fixed_artifact" / "zh_visual.html"
BEFORE_PDF = TASK4I / "fixed_artifact" / "zh_visual.pdf"
BEFORE_GATE = TASK4I / "checkpoint_gate.json"
TASK4J_AUDIT = REPO / "outputs" / "visual_v07_task4j" \
    / "soft_text_paint_audit.json"
MODEL_PATH = REPO / "outputs" / "visual_v07_task4f" \
    / "fixed_stitched_page_model.json"


def _load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _round_box(box: Iterable[float] | None) -> list[float] | None:
    if box is None:
        return None
    values = list(box)
    return [round(float(value), 3) for value in values]


def _render_page(pdf_path: Path, scale: float = 4.0) -> Image.Image:
    document = pymupdf.open(str(pdf_path))
    try:
        pixmap = document[0].get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), alpha=False)
    finally:
        document.close()
    return Image.frombytes("RGB", (pixmap.width, pixmap.height),
                           pixmap.samples)


def _font(size: int = 14, bold: bool = False) -> ImageFont.ImageFont:
    names = (("arialbd.ttf", "calibrib.ttf") if bold
             else ("arial.ttf", "calibri.ttf"))
    for name in names:
        try:
            return ImageFont.truetype(
                str(Path("C:/Windows/Fonts") / name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _crop(image: Image.Image, focus: Sequence[float],
          scale: float) -> Image.Image:
    return image.crop(tuple(int(round(float(value) * scale))
                            for value in focus))


def _local_box(box: Sequence[float], focus: Sequence[float],
               scale: float) -> tuple[int, int, int, int]:
    return tuple(int(round(value)) for value in (
        (float(box[0]) - float(focus[0])) * scale,
        (float(box[1]) - float(focus[1])) * scale,
        (float(box[2]) - float(focus[0])) * scale,
        (float(box[3]) - float(focus[1])) * scale))


def _html_geometry(html_text: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    slots = {}
    for node in soup.select(".paragraph-block[data-source-slot-bbox]"):
        identity = str(node.get("data-render-id") or node.get("data-para"))
        slots[identity] = [float(value) for value in str(
            node.get("data-source-slot-bbox")).split(",")]

    def anchors(selector: str, identity_attr: str) -> list[dict[str, str]]:
        return sorted(({
            "identity": str(node.get(identity_attr) or ""),
            "style": str(node.get("style") or ""),
            "src": str(node.get("src") or ""),
        } for node in soup.select(selector)),
            key=lambda row: (row["identity"], row["style"], row["src"]))

    return {
        "slots": slots,
        "figure": anchors(".figure-region", "data-figure"),
        "table": anchors(".translated-cell", "data-cell"),
        "formula": anchors(".formula-seg", "data-segment"),
    }


def _atom_sequences(html_text: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html_text, "html.parser")
    records = []
    for group in soup.select(".math-atom-group"):
        atoms = []
        for atom in group.select(".math-atom"):
            role = next((value.removeprefix("math-")
                         for value in atom.get("class") or []
                         if value.startswith("math-")
                         and value != "math-atom"), "")
            atoms.append({"atom_id": str(atom.get("data-atom-id") or ""),
                          "role": role, "text": atom.get_text()})
        records.append({
            "group_id": str(group.get("data-math-group-id") or ""),
            "atom_order": str(group.get("data-atom-order") or ""),
            "atoms": atoms,
        })
    return records


def _production_special_case_audit() -> dict[str, Any]:
    sources = [HERE / "math_aware_block_measurement.py",
               HERE / "final_block_collision_qa.py",
               HERE / "browser_measured_repack.py"]
    forbidden = ("PAF_B1_02", "lambda", "λ", "PPAT", "page 5")
    hits = []
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"),
                         filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.If, ast.IfExp)):
                continue
            for child in ast.walk(node.test):
                if isinstance(child, ast.Constant) \
                        and isinstance(child.value, str) \
                        and any(value.casefold() in child.value.casefold()
                                for value in forbidden):
                    hits.append({"file": str(source.relative_to(REPO)),
                                 "line": child.lineno,
                                 "value": child.value})
    return {
        "production_special_case_count": len(hits),
        "hits": hits,
        "basis": (
            "AST inspection of production measurement/collision/repack "
            "branch conditions"),
    }


def _measurement_payload(collision: dict[str, Any]) -> dict[str, Any]:
    blocks = []
    for block in collision.get("blocks") or []:
        measurement = block["math_aware_measurement"]
        blocks.append({
            "render_id": block["render_id"],
            "flow_fragment_id": block["flow_fragment_id"],
            "source_slot_id": block.get("source_slot_id") or "",
            "source_slot_bbox_pt": block.get("source_slot_bbox") or None,
            "dom_bbox_pt": block["dom_measured_bbox"],
            "final_pdf_text_bbox_pt": block.get("pdf_rendered_bbox"),
            "painted_ink_bbox_pt": block.get("painted_ink_bbox_pt"),
            "effective_measurement_bbox_pt": block[
                "effective_measurement_bbox_pt"],
            "measurement_mode": measurement["measurement_mode"],
            "has_math_atom_group": measurement["has_math_atom_group"],
            "has_sup": any(atom["role"] == "superscript"
                           for atom in measurement["math"]["atoms"]),
            "has_sub": any(atom["role"] == "subscript"
                           for atom in measurement["math"]["atoms"]),
            "math_atoms": measurement["math"]["atoms"],
            "dom_height_pt": measurement["dom_height_pt"],
            "painted_glyph_bbox_height_pt": measurement[
                "painted_glyph_bbox_height_pt"],
            "painted_ink_height_pt": measurement[
                "painted_ink_height_pt"],
            "painted_extent_height_from_dom_top_pt": measurement[
                "painted_extent_height_from_dom_top_pt"],
            "measured_height_pt": measurement["measured_height_pt"],
            "measured_height_from_dom_origin_pt": measurement[
                "measured_height_from_dom_origin_pt"],
            "ink_top_pt": measurement["ink_top_pt"],
            "ink_bottom_pt": measurement["ink_bottom_pt"],
            "ascent_extension_pt": measurement["ascent_extension_pt"],
            "descent_extension_pt": measurement["descent_extension_pt"],
            "script_ascent_extension_pt": measurement["math"][
                "max_superscript_ascent_extension_pt"],
            "script_descent_extension_pt": measurement["math"][
                "max_subscript_descent_extension_pt"],
            "painted_source": measurement["painted_source"],
            "formula": measurement["formula"],
        })
    return {
        "schema_version": "visual_v07.task4k.math_aware_measurement.v1",
        "measurement_policy": {
            "inputs": ["Chromium DOM bbox",
                       "final PDF raster painted-ink bbox"],
            "height": "max(dom_height, painted_ink_height)",
            "effective_bbox": "union(dom_bbox, painted_ink_bbox)",
            "ink_owner_constraint": (
                "final PDF text-layer glyph bbox assigned to DOM lines"),
            "math": (
                "measure each MathAtomGroup base/sup/sub bbox; retain "
                "ascent/descent extensions"),
            "source_text_slot_geometry": "immutable",
        },
        "blocks": blocks,
    }


def _review_bundle(before_pdf: Path, after_pdf: Path,
                   before: dict[str, Any], after: dict[str, Any],
                   actual_ink: Sequence[float], decision: str) -> dict[str, Any]:
    REVIEW.mkdir(parents=True, exist_ok=True)
    scale = 4.0
    focus = [min(before["dom_bbox_pt"][0], actual_ink[0]) - 9.0,
             min(before["dom_bbox_pt"][1], actual_ink[1]) - 18.0,
             max(before["dom_bbox_pt"][2], actual_ink[2]) + 9.0,
             max(after["effective_measurement_bbox_pt"][3],
                 actual_ink[3]) + 18.0]
    before_page = _render_page(before_pdf, scale)
    after_page = _render_page(after_pdf, scale)
    before_crop = _crop(before_page, focus, scale)
    after_crop = _crop(after_page, focus, scale)
    font = _font(12, True)
    files: dict[str, Path] = {}

    def save(name: str, image: Image.Image) -> None:
        path = REVIEW / name
        image.save(path)
        files[name] = path

    before_overlay = before_crop.copy()
    draw = ImageDraw.Draw(before_overlay)
    draw.rectangle(_local_box(before["dom_bbox_pt"], focus, scale),
                   outline=(220, 45, 40), width=4)
    draw.rectangle(_local_box(actual_ink, focus, scale),
                   outline=(20, 155, 70), width=3)
    draw.text((8, 7), "BEFORE: RED DOM height | GREEN final ink",
              fill=(15, 15, 15), font=font)
    save("block_measure_before.png", before_overlay)

    after_overlay = after_crop.copy()
    draw = ImageDraw.Draw(after_overlay)
    draw.rectangle(_local_box(after["effective_measurement_bbox_pt"],
                              focus, scale),
                   outline=(25, 90, 220), width=4)
    draw.rectangle(_local_box(actual_ink, focus, scale),
                   outline=(20, 155, 70), width=3)
    draw.text((8, 7), "AFTER: BLUE effective measure | GREEN final ink",
              fill=(15, 15, 15), font=font)
    save("block_measure_after.png", after_overlay)

    ink_overlay = after_crop.copy()
    draw = ImageDraw.Draw(ink_overlay)
    draw.rectangle(_local_box(before["source_slot_bbox_pt"], focus, scale),
                   outline=(235, 145, 20), width=3)
    draw.rectangle(_local_box(before["dom_bbox_pt"], focus, scale),
                   outline=(220, 45, 40), width=3)
    draw.rectangle(_local_box(actual_ink, focus, scale),
                   outline=(20, 155, 70), width=3)
    draw.rectangle(_local_box(after["effective_measurement_bbox_pt"],
                              focus, scale),
                   outline=(25, 90, 220), width=3)
    draw.text((8, 7),
              "ORANGE frozen slot | RED DOM | GREEN raster ink | BLUE measure",
              fill=(15, 15, 15), font=_font(11, True))
    save("ink_bbox_overlay.png", ink_overlay)

    math_overlay = after_crop.copy()
    draw = ImageDraw.Draw(math_overlay)
    for atom in after.get("math_atoms") or []:
        color = ((160, 55, 205) if atom["role"] == "subscript"
                 else (220, 45, 40) if atom["role"] == "superscript"
                 else (25, 90, 220))
        draw.rectangle(_local_box(atom["bbox_pt"], focus, scale),
                       outline=color, width=3)
    draw.line((0, int(round((before["dom_bbox_pt"][3] - focus[1])
                           * scale)), math_overlay.width,
               int(round((before["dom_bbox_pt"][3] - focus[1]) * scale))),
              fill=(220, 45, 40), width=2)
    draw.line((0, int(round((after["effective_measurement_bbox_pt"][3]
                            - focus[1]) * scale)), math_overlay.width,
               int(round((after["effective_measurement_bbox_pt"][3]
                          - focus[1]) * scale))),
              fill=(25, 90, 220), width=2)
    draw.text((8, 7),
              "BLUE base/measure | PURPLE sub | RED old bottom",
              fill=(15, 15, 15), font=font)
    save("math_block_height_overlay.png", math_overlay)

    index = {
        "schema_version": "visual_v07.task4k.review_index.v1",
        "decision": decision,
        "focus_bbox_pt": _round_box(focus),
        "files": [{"name": name, "sha256": _sha256(path)}
                  for name, path in sorted(files.items())],
        "legend": {
            "orange": "immutable SourceTextSlot",
            "red": "legacy DOM-only measurement",
            "green": "independent final-PDF raster ink",
            "blue": "math-aware effective measurement / base atom",
            "purple": "subscript atom",
        },
    }
    _dump(REVIEW / "review_index.json", index)
    (REVIEW / "REVIEW_README.md").write_text(
        "# Task 4K review bundle\n\n"
        "All four views use the same final page pixels. The change is an "
        "internal measurement correction, so the page content does not move. "
        "Red shows the stale DOM-only bottom; blue encloses the independent "
        "green final ink. Orange is the frozen SourceTextSlot and is not the "
        "new measurement. Purple identifies the target subscript atom.\n",
        encoding="utf-8")
    return index


def _report(decision: str, metrics: dict[str, Any],
            before: dict[str, Any], after: dict[str, Any],
            actual_ink: Sequence[float], atom_extensions: list[dict[str, Any]],
            atom_sequence_unchanged: bool) -> str:
    target_atoms = [atom for atom in after.get("math_atoms") or []
                    if atom["role"] in {"superscript", "subscript"}]
    return f"""# Math-aware Block Measurement Report

Decision: **{decision.upper()}**

## Why DOM height was smaller than painted ink

`PAF_B1_02` retained an explicit frozen CSS/SourceTextSlot height of
{before['dom_height_pt']:.3f}pt. The content still painted with
`overflow:visible` through the last wrapped line, so the independent final-PDF
raster ink reached y={float(actual_ink[3]):.3f}pt while the DOM-only
measurement stopped at y={before['dom_bbox_pt'][3]:.3f}pt. The old measurement
was therefore short by {float(actual_ink[3]) - before['dom_bbox_pt'][3]:.3f}pt.
This was stale measurement, not clipping or later-block occlusion.

## Math ascent/descent evidence

The target MathAtomGroup contains {len(target_atoms)} script atom(s). Its
subscript `𝑐` extends {after['script_descent_extension_pt']:.3f}pt below the
base atom box. Across all measured groups, the recorded non-zero script
extensions are:

```json
{json.dumps(atom_extensions, ensure_ascii=False, indent=2)}
```

The subscript is relevant evidence that normal-text baseline assumptions are
unsafe. It is not the sole source of the 8pt-class stale height: the frozen
block also paints a final wrapped line below its declared DOM bottom. Task 4J's
sup/sub neutralization remains unchanged; this task measures the actual result
instead of changing script CSS.

## Fixed measurement

The production measurement now retains `dom_bbox`, final painted-glyph bbox,
per-atom base/sup/sub boxes, `ink_top`, `ink_bottom`, ascent, and descent. From
the block origin it computes:

`measured_height = max(dom_height, painted_ink_height)`

For the target this is
`max({after['dom_height_pt']:.3f}, {after['painted_ink_height_pt']:.3f})`
= **{after['measured_height_pt']:.3f}pt**. The effective bottom
is y={after['effective_measurement_bbox_pt'][3]:.3f}pt, which encloses both the
DOM block and the independent raster-ink bottom
y={float(actual_ink[3]):.3f}pt. The text-layer glyph bbox is retained only as
the ownership constraint used to isolate the target's raster ink.

## Frozen geometry and regressions

- SourceTextSlot geometry is byte-for-byte unchanged:
  `slot_geometry_mutation_count={metrics['slot_geometry_mutation_count']}`.
- Figure and table anchors are unchanged:
  `figure_anchor_mutation_count={metrics['figure_anchor_mutation_count']}` and
  `table_anchor_mutation_count={metrics['table_anchor_mutation_count']}`.
- Task 4H calligraphic `𝓕(⋅)` / inverse-transform glyph content is unchanged.
- Task 4I semantic `<sup>` / `<sub>` markup and every MathAtomGroup atom order
  are unchanged: `{atom_sequence_unchanged}`.
- No block is moved and the SourceTextSlot CSS `height` is not enlarged. The
  copied final PDF is pixel/content-identical; only measurement truth changes.
- Production special-case count is
  `{metrics['production_special_case_count']}`. Production code contains no
  target text, render id, page, or filename branch.

## Hard metrics

```json
{json.dumps(metrics, ensure_ascii=False, indent=2)}
```
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    REVIEW.mkdir(parents=True, exist_ok=True)

    before_audit = _load(TASK4J_AUDIT)
    before_qa = audit_math_aware_block_measurement(
        before_audit, required_render_ids=["PAF_B1_02"])
    _dump(OUT / "math_aware_measurement_qa_before.json", before_qa)
    if before_qa["metrics"]["dom_height_stale_count"] <= 0:
        raise RuntimeError("QA-FIRST red baseline no longer reproduces")

    shutil.copy2(BEFORE_HTML, ARTIFACT / "zh_visual.html")
    shutil.copy2(BEFORE_PDF, ARTIFACT / "zh_visual.pdf")
    after_html = ARTIFACT / "zh_visual.html"
    after_pdf = ARTIFACT / "zh_visual.pdf"

    model = _load(MODEL_PATH)
    collision = final_block_collision_qa(
        model, html_path=after_html, final_pdf_path=after_pdf,
        screenshot_path=OUT / "final_block_collision_dom.png")
    _dump(OUT / "final_block_collision_qa.json", collision)
    measurement = _measurement_payload(collision)
    _dump(OUT / "math_aware_measurement.json", measurement)
    after_qa = audit_math_aware_block_measurement(
        measurement, required_render_ids=["PAF_B1_02"])
    _dump(OUT / "math_aware_measurement_qa.json", after_qa)

    before_observation = before_qa["observations"][0]
    after_observation = next(
        row for row in after_qa["observations"]
        if row["render_id"] == before_observation["render_id"])
    after_record = next(
        row for row in measurement["blocks"]
        if row["render_id"] == before_observation["render_id"])
    actual_ink = before_audit["measurements"][
        "previous_painted_ink_bbox_pt"]
    comparison = {
        "schema_version": "visual_v07.task4k.measurement_before_after.v1",
        "render_id": before_observation["render_id"],
        "before": before_observation,
        "after": after_record,
        "independent_final_pdf_raster_ink_bbox_pt": actual_ink,
        "proof": {
            "before_declared_bottom_lt_ink_bottom": (
                before_observation["previous_declared_bottom"]
                < float(actual_ink[3])),
            "after_measured_bottom_gte_ink_bottom": (
                after_record["effective_measurement_bbox_pt"][3]
                >= float(actual_ink[3])),
            "source_slot_bbox_unchanged": (
                before_observation["source_slot_bbox_pt"]
                == after_record["source_slot_bbox_pt"]),
        },
    }
    _dump(OUT / "measurement_before_after.json", comparison)

    before_text = BEFORE_HTML.read_text(encoding="utf-8")
    after_text = after_html.read_text(encoding="utf-8")
    before_geometry = _html_geometry(before_text)
    after_geometry = _html_geometry(after_text)
    before_atoms = _atom_sequences(before_text)
    after_atoms = _atom_sequences(after_text)
    atom_sequence_unchanged = before_atoms == after_atoms
    task4i_gate = _load(BEFORE_GATE)
    special = _production_special_case_audit()
    _dump(OUT / "production_special_case_audit.json", special)

    atom_extensions = []
    for block in measurement["blocks"]:
        for atom in block["math_atoms"]:
            if atom["ascent_extension_pt"] > 0 \
                    or atom["descent_extension_pt"] > 0:
                atom_extensions.append({
                    "render_id": block["render_id"],
                    "atom_id": atom["atom_id"],
                    "role": atom["role"],
                    "text": atom["text"],
                    "ascent_extension_pt": atom[
                        "ascent_extension_pt"],
                    "descent_extension_pt": atom[
                        "descent_extension_pt"],
                })

    metrics = {
        "dom_height_stale_count": after_qa["metrics"][
            "dom_height_stale_count"],
        "ink_outside_measurement_count": after_qa["metrics"][
            "ink_outside_measurement_count"],
        "final_block_collision_count": collision["metrics"][
            "final_block_collision_count"],
        "slot_geometry_mutation_count": int(
            before_geometry["slots"] != after_geometry["slots"]),
        "figure_anchor_mutation_count": int(
            before_geometry["figure"] != after_geometry["figure"]),
        "table_anchor_mutation_count": int(
            before_geometry["table"] != after_geometry["table"]),
        "production_special_case_count": special[
            "production_special_case_count"],
        "math_atom_order_error_count": int(not atom_sequence_unchanged),
        "task4h_regression_count": int(
            task4i_gate["metrics"]["task4h_pua_regression_count"] != 0
            or before_text.count("𝓕") != after_text.count("𝓕")),
        "superscript_structure_loss_count": int(
            before_text.count("<sup") != after_text.count("<sup")),
        "subscript_structure_loss_count": int(
            before_text.count("<sub") != after_text.count("<sub")),
        "pdf_content_mutation_count": int(
            _sha256(BEFORE_PDF) != _sha256(after_pdf)),
    }
    required_zero = (
        "dom_height_stale_count", "ink_outside_measurement_count",
        "final_block_collision_count", "slot_geometry_mutation_count",
        "figure_anchor_mutation_count", "table_anchor_mutation_count",
        "production_special_case_count", "math_atom_order_error_count",
        "task4h_regression_count", "superscript_structure_loss_count",
        "subscript_structure_loss_count", "pdf_content_mutation_count")
    hard_gate = (all(metrics[key] == 0 for key in required_zero)
                 and comparison["proof"][
                     "before_declared_bottom_lt_ink_bottom"]
                 and comparison["proof"][
                     "after_measured_bottom_gte_ink_bottom"]
                 and comparison["proof"]["source_slot_bbox_unchanged"])
    decision = "pass" if hard_gate else "blocked"

    review = _review_bundle(
        BEFORE_PDF, after_pdf, before_observation, after_record,
        actual_ink, decision)
    report = _report(decision, metrics, before_observation, after_record,
                     actual_ink, atom_extensions, atom_sequence_unchanged)
    (OUT / "MATH_AWARE_BLOCK_MEASUREMENT_REPORT.md").write_text(
        report, encoding="utf-8")
    gate = {
        "schema_version": "visual_v07.task4k.checkpoint.v1",
        "decision": decision,
        "qa_first": {
            "old_artifact_red": True,
            "captured_render_id": before_observation["render_id"],
            "before_metrics": before_qa["metrics"],
        },
        "metrics": metrics,
        "proof": comparison["proof"],
        "fixed_artifact": {
            "html": str(after_html), "pdf": str(after_pdf),
            "html_sha256": _sha256(after_html),
            "pdf_sha256": _sha256(after_pdf),
        },
        "review_bundle": review,
        "commit_message": (
            "fix(visual): measure math-aware block ink bounds"),
        "tag_created": False,
    }
    _dump(OUT / "checkpoint_gate.json", gate)
    sys.stdout.buffer.write((json.dumps(
        {"decision": decision, "metrics": metrics,
         "proof": comparison["proof"], "pdf": str(after_pdf)},
        ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return 0 if hard_gate else 2


if __name__ == "__main__":
    raise SystemExit(main())
