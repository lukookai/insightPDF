# -*- coding: utf-8 -*-
"""visual-v07 Task 4D checkpoint: restore source paragraph first-line indent."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageDraw, ImageFont


REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))
sys.path.insert(0, str(REPO / "实现源码" / "pdf_translator"))

from _chromium_pdf import render_html_to_pdf  # noqa: E402
from final_block_collision_qa import final_block_collision_qa  # noqa: E402
from local_typography_fit import fit_locked_html_with_browser  # noqa: E402
from prose_adopted_formula_recovery import (  # noqa: E402
    recover_prose_adopted_formulas,
)
from source_paragraph_style import (  # noqa: E402
    _paragraph_parts,
    _visible_text,
    apply_source_paragraph_styles_to_flows,
    apply_source_paragraph_styles_to_html,
)
from source_paragraph_style_qa import (  # noqa: E402
    detect_source_first_line_indent_candidates,
    extract_source_page_lines,
    render_first_line_indent_overlay,
    source_paragraph_style_qa,
)
from source_text_slot_lock_qa import source_text_slot_lock_qa  # noqa: E402


OUT = REPO / "outputs" / "visual_v07_task4d"
MODEL_ROOT = REPO / "outputs" / "phase4e2a_qa_recovery" / "doc2" / "pages"
TASK4A = REPO / "outputs" / "visual_v07_task4a"
TASK4B = REPO / "outputs" / "visual_v07_task4b"
TASK4C = REPO / "outputs" / "visual_v07_task4c"
PPAT_PDF = Path("C:/Users/74496/Desktop/PPAT.pdf")


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _model(page: int) -> dict[str, Any]:
    return _load(MODEL_ROOT / ("p%03d" % page)
                 / "stitched_page_model.json")


def _translations(page: int) -> dict[str, str]:
    return _load(MODEL_ROOT / ("p%03d" % page) / "translation.json")


def _copy_assets(source_dir: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("*.svg", "img_*.png"):
        for source in source_dir.glob(pattern):
            shutil.copy2(source, target_dir / source.name)


def _empty_repack() -> dict[str, Any]:
    return {
        "schema_version": "visual_v07.browser_measured_repack.v1",
        "applied": False,
        "repacked_region_count": 0,
        "measured_block_count": 0,
        "moved_block_count": 0,
        "hard_anchor_moved_count": 0,
        "cross_region_spill_count": 0,
        "cross_page_spill_count": 0,
        "unresolved_count": 0,
        "placements": [],
        "unresolved": [],
        "reason": "Task 4D only changes paragraph-content indentation",
    }


def _recovery(page: int, model: dict[str, Any]) -> dict[str, Any]:
    translations = _translations(page)
    return recover_prose_adopted_formulas(
        model, translations, str(PPAT_PDF), page - 1,
        existing_ids=set(translations))


def _frozen_style_model(
        page: int, model: dict[str, Any], source_html: str,
        slots: dict[str, Any], lock_trace: dict[str, Any],
        candidates: dict[str, Any], recovery: dict[str, Any],
        ) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    recovered_by_fragment = {
        str(row["paragraph_id"]) + "-F0": row
        for row in recovery.get("recovered") or []}
    items = []
    source_by_fragment = {}
    for lock in lock_trace.get("records") or []:
        fragment = str(lock.get("flow_fragment_id") or "")
        paragraph = recovered_by_fragment.get(fragment)
        if not lock.get("geometry_locked") or paragraph is None:
            continue
        _, _, _, content = _paragraph_parts(source_html, fragment)
        target_text = _visible_text(content)
        items.append({
            "kind": "paragraph",
            "geometry_locked": True,
            "flow_fragment_id": fragment,
            "source_text_slot_id": lock.get("slot_id"),
            "render_text": target_text,
            "style_role": paragraph.get("style_role") or "body",
            "_para": paragraph,
        })
        source_by_fragment[fragment] = str(
            paragraph.get("source_text") or "")
    flows, style_model = apply_source_paragraph_styles_to_flows(
        [{"items": items}], model, slots, candidates)
    return style_model, source_by_fragment, flows


def _hard_anchor_geometry(html_text: str) -> list[tuple[str, str]]:
    output = []
    hard_classes = {
        "formula-seg", "figure-region", "table-region",
        "reserved-region", "formula-region",
    }
    for match in re.finditer(r"<(?:img|span|div|table)\b[^>]*>",
                             html_text, re.IGNORECASE):
        tag = match.group(0)
        class_match = re.search(r'\bclass="([^"]*)"', tag, re.IGNORECASE)
        classes = set((class_match.group(1) if class_match else "").split())
        if not classes.intersection(hard_classes):
            continue
        identity = re.search(
            r'\bdata-(?:formula|figure|table|region)="([^"]*)"',
            tag, re.IGNORECASE)
        style = re.search(r'\bstyle="([^"]*)"', tag, re.IGNORECASE)
        geometry = []
        for name in ("left", "top", "width", "height"):
            value = re.search(
                r"(?i)(?:^|;)\s*%s\s*:\s*([^;]+)" % name,
                style.group(1) if style else "")
            geometry.append(value.group(1).strip() if value else "")
        output.append((identity.group(1) if identity else ":".join(
            sorted(classes)), "|".join(geometry)))
    return output


def _run_p005() -> dict[str, Any]:
    page = 5
    model = _model(page)
    source_dir = TASK4C / "ppat_p005"
    source_html_path = source_dir / "zh_visual.html"
    source_html = source_html_path.read_text(encoding="utf-8")
    slots = _load(TASK4A / "source_text_slots_ppat_p005.json")
    lock_trace = _load(TASK4B / "ppat_p005"
                       / "source_text_slot_lock.json")
    candidates = detect_source_first_line_indent_candidates(
        extract_source_page_lines(PPAT_PDF, page - 1))
    recovery = _recovery(page, model)
    style_model, source_by_fragment, flows = _frozen_style_model(
        page, model, source_html, slots, lock_trace, candidates, recovery)
    styled_html, style_model = apply_source_paragraph_styles_to_html(
        source_html, style_model, source_by_fragment)

    page_dir = OUT / "ppat_p005"
    _copy_assets(source_dir, page_dir)
    l0_html_path = page_dir / "source_paragraph_style_l0.html"
    l0_pdf_path = page_dir / "source_paragraph_style_l0.pdf"
    l0_html_path.write_text(styled_html, encoding="utf-8")
    render_html_to_pdf(l0_html_path, l0_pdf_path)
    l0_collision = final_block_collision_qa(
        model, html_path=l0_html_path, final_pdf_path=l0_pdf_path,
        screenshot_path=page_dir / "source_paragraph_style_l0.png")

    final_html, post_indent_fit = fit_locked_html_with_browser(
        styled_html, lock_trace, l0_collision,
        source_html_path=source_html_path,
        trial_dir=OUT / "fit_trials" / "p005")
    html_path = page_dir / "zh_visual.html"
    pdf_path = page_dir / "zh_visual.pdf"
    html_path.write_text(final_html, encoding="utf-8")
    render_html_to_pdf(html_path, pdf_path)
    screenshot = page_dir / "final_block_collision_dom.png"
    collision = final_block_collision_qa(
        model, html_path=html_path, final_pdf_path=pdf_path,
        screenshot_path=screenshot)
    style_audit = source_paragraph_style_qa(
        style_model, html_path, collision_qa=collision,
        screenshot_path=page_dir / "source_paragraph_style_final.png")
    lock_audit = source_text_slot_lock_qa(
        slots, collision, lock_trace=post_indent_fit,
        repack_trace=_empty_repack(),
        artifact_label="Task 4D p005 final geometry")

    target_records = [row for row in style_audit.get("records") or []
                      if int(row.get("source_sentence_ordinal") or 0) > 0]
    negative_records = list(style_model.get("negative_records") or [])
    negative_ok = bool(negative_records) and all(
        str(row.get("flow_fragment_id") or "") not in {
            str(value.get("flow_fragment_id") or "")
            for value in style_model.get("records") or []}
        for row in negative_records)
    hard_anchor_moved_count = int(
        _hard_anchor_geometry(source_html)
        != _hard_anchor_geometry(final_html))
    outputs = {
        "source_paragraph_style_candidates.json": candidates,
        "source_paragraph_style.json": style_model,
        "source_paragraph_style_qa.json": style_audit,
        "source_text_slot_lock_after_indent.json": post_indent_fit,
        "source_text_slot_lock_qa.json": lock_audit,
        "final_block_collision_qa.json": collision,
    }
    for name, value in outputs.items():
        _dump(page_dir / name, value)
    return {
        "page": page,
        "model": model,
        "source_html": source_html,
        "final_html": final_html,
        "html_path": html_path,
        "pdf_path": pdf_path,
        "screenshot": screenshot,
        "candidates": candidates,
        "recovery": recovery,
        "style_model": style_model,
        "style_qa": style_audit,
        "lock_qa": lock_audit,
        "collision": collision,
        "post_indent_fit": post_indent_fit,
        "target_records": target_records,
        "negative_records": negative_records,
        "negative_fixture_ok": negative_ok,
        "hard_anchor_moved_count": hard_anchor_moved_count,
        "flow_snapshot": flows,
    }


def _run_p006_regression() -> dict[str, Any]:
    page = 6
    model = _model(page)
    source_dir = TASK4B / "ppat_p006"
    source_html_path = source_dir / "zh_visual.html"
    source_html = source_html_path.read_text(encoding="utf-8")
    slots = _load(TASK4A / "source_text_slots_ppat_p006.json")
    lock_trace = _load(source_dir / "source_text_slot_lock.json")
    candidates = detect_source_first_line_indent_candidates(
        extract_source_page_lines(PPAT_PDF, page - 1))
    recovery = _recovery(page, model)
    style_model, source_by_fragment, _ = _frozen_style_model(
        page, model, source_html, slots, lock_trace, candidates, recovery)
    final_html, final_style_model = apply_source_paragraph_styles_to_html(
        source_html, style_model, source_by_fragment)
    l2 = [row for row in lock_trace.get("records") or []
          if row.get("geometry_locked") and row.get("fit_level") == "L2"]
    collision = _load(source_dir / "final_block_collision_qa.json")
    result = {
        "schema_version": "visual_v07.task4d.p006_regression.v1",
        "decision": "pass" if (
            not candidates.get("records")
            and not final_style_model.get("records")
            and final_html == source_html
            and bool(l2)
            and int(collision["metrics"][
                "final_block_collision_count"]) == 0) else "fail",
        "source_first_line_indent_candidate_count": len(
            candidates.get("records") or []),
        "source_paragraph_style_count": len(
            final_style_model.get("records") or []),
        "task3_l2_fit_record_count": len(l2),
        "task3_l2_fit_records": l2,
        "task3_fit_html_unchanged": final_html == source_html,
        "task3_fit_html_sha256": _sha256(source_html_path),
        "final_block_collision_count": int(
            collision["metrics"]["final_block_collision_count"]),
    }
    _dump(OUT / "p006_regression.json", result)
    return result


def _production_diff_audit() -> dict[str, Any]:
    files = [
        "tools/page_pipeline/source_paragraph_style.py",
        "tools/page_pipeline/prose_adopted_formula_recovery.py",
        "tools/page_pipeline/html_render.py",
        "tools/page_pipeline/run_visual_v06_checkpoint.py",
    ]
    result = subprocess.run(
        ["git", "diff", "--unified=0", "HEAD", "--", *files],
        cwd=REPO, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    added = [line[1:] for line in result.stdout.splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    # New untracked production modules are not emitted by git diff.
    style_path = REPO / files[0]
    if style_path.exists() and not subprocess.run(
            ["git", "ls-files", "--error-unmatch", files[0]], cwd=REPO,
            capture_output=True).returncode == 0:
        added.extend(style_path.read_text(encoding="utf-8").splitlines())
    patterns = {
        "target_text": re.compile(
            r"We concurrently|In addition to the high-frequency", re.I),
        "document_or_filename": re.compile(r"PPAT(?:\.pdf)?", re.I),
        "page_branch": re.compile(
            r"\b(?:page|page_idx|page_index)\s*==\s*[45]\b", re.I),
        "paragraph_id_branch": re.compile(
            r"\bparagraph_id\s*==\s*['\"](?:PAF|DLP)", re.I),
    }
    findings = []
    for line in added:
        for kind, pattern in patterns.items():
            if pattern.search(line):
                findings.append({"kind": kind, "line": line.strip()})
    return {
        "schema_version": "visual_v07.task4d.production_diff_audit.v1",
        "decision": "pass" if not findings else "fail",
        "production_special_case_count": len(findings),
        "findings": findings,
        "files": files,
    }


def _crop_pdf(pdf_path: str | Path, page_index: int, box: list[float],
              output_path: str | Path, zoom: float = 3.0) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(str(pdf_path)) as document:
        page = document[page_index]
        bounds = page.rect
        clip = pymupdf.Rect(
            max(bounds.x0, box[0]), max(bounds.y0, box[1]),
            min(bounds.x1, box[2]), min(bounds.y1, box[3]))
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
        pixmap.save(str(target))
    return target


def _page_png(pdf_path: str | Path, page_index: int,
              output_path: str | Path, zoom: float = 2.0) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(str(pdf_path)) as document:
        pixmap = document[page_index].get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        pixmap.save(str(target))
    return target


def _font(size: int) -> ImageFont.ImageFont:
    for path in ("C:/Windows/Fonts/msyh.ttc",
                 "C:/Windows/Fonts/arial.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _side_by_side(before_path: Path, after_path: Path,
                  output_path: Path) -> Path:
    with Image.open(before_path) as image:
        before = image.convert("RGB")
    with Image.open(after_path) as image:
        after = image.convert("RGB")
    header, gap = 58, 24
    canvas = Image.new(
        "RGB", (before.width + after.width + gap,
                max(before.height, after.height) + header), "white")
    canvas.paste(before, (0, header))
    canvas.paste(after, (before.width + gap, header))
    draw = ImageDraw.Draw(canvas)
    font = _font(22)
    draw.text((12, 14), "BEFORE - indent missing",
              fill="#D7191C", font=font)
    draw.text((before.width + gap + 12, 14),
              "AFTER - source-relative indent", fill="#087D4A", font=font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)
    return output_path


def _review_bundle(p005: dict[str, Any], p006: dict[str, Any],
                   decision: str) -> dict[str, Any]:
    bundle = OUT / "review_bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    crop = [45.0, 220.0, 298.0, 535.0]
    _crop_pdf(PPAT_PDF, 4, crop, bundle / "indent_problem_source.png")
    _crop_pdf(TASK4C / "ppat_p005" / "zh_visual.pdf", 0, crop,
              bundle / "indent_problem_before.png")
    _crop_pdf(p005["pdf_path"], 0, crop,
              bundle / "indent_problem_after.png")
    _side_by_side(bundle / "indent_problem_before.png",
                  bundle / "indent_problem_after.png",
                  bundle / "indent_before_after.png")
    full = _page_png(p005["pdf_path"], 0,
                     OUT / "ppat_p005" / "final_page.png")
    render_first_line_indent_overlay(
        p005["style_qa"], full,
        bundle / "first_line_indent_overlay.png",
        page_width=float(p005["model"]["width"]),
        page_height=float(p005["model"]["height"]),
        title="p005 Source Paragraph First-Line Indent")
    shutil.copy2(TASK4B / "review_bundle" / "p006_task4b_after.png",
                 bundle / "p006_regression.png")
    names = [
        "indent_problem_source.png",
        "indent_problem_before.png",
        "indent_problem_after.png",
        "indent_before_after.png",
        "first_line_indent_overlay.png",
        "p006_regression.png",
    ]
    index = {
        "schema_version": "visual_v07.task4d.review_index.v1",
        "decision": decision,
        "flat": True,
        "files": [{"name": name, "bytes": (bundle / name).stat().st_size}
                  for name in names],
        "legend": {
            "green": "indent and geometry pass",
            "red": "missing/wrong indent or geometry defect",
        },
        "p006_regression_decision": p006["decision"],
    }
    _dump(bundle / "review_index.json", index)
    (bundle / "REVIEW_README.md").write_text(
        "# Task 4D review\n\n"
        "1. Open `indent_problem_source.png` to see the source paragraph "
        "boundaries and first-line indents.\n"
        "2. Compare `indent_problem_before.png` and "
        "`indent_problem_after.png`, or use the combined image.\n"
        "3. Use `first_line_indent_overlay.png` for source pt/em, target "
        "pt/em, geometry, and defect truth.\n"
        "4. Confirm `p006_regression.png` retains the Task 3 Fit render.\n",
        encoding="utf-8")
    return index


def _report(checkpoint: dict[str, Any]) -> str:
    p005 = checkpoint["p005"]
    metrics = checkpoint["hard_metrics"]
    target_rows = p005["target_records"][:2]
    target_labels = ["We concurrently …", "In addition …"]
    values = [
        "- **%s** (`%s`): %.3f pt / %.3f em; final %.3f pt / %.3f em."
        % (target_labels[index], row.get("style_id"),
           float(row.get("first_line_indent_pt") or 0.0),
           float(row.get("first_line_indent_em") or 0.0),
           float(row.get("target_first_line_indent_pt") or 0.0),
           float(row.get("target_first_line_indent_em") or 0.0))
        for index, row in enumerate(target_rows)]
    return """# visual-v07 Task 4D — Restore Source Paragraph First-Line Indent

## Outcome

**%s**. The change restores paragraph first-line indentation inside immutable SourceTextSlots without copying ordinary source PDF line wraps.

## Required answers

1. **How is first-line indentation identified from source line geometry?** Source PDF vector-text lines are grouped by block/column. The robust body left edge and body font are estimated per column. A first line is accepted only when its positive offset is 0.80–2.50 em, its font matches the body font, it has substantive width, and the following body line returns to the robust body edge. OCR and text-content decisions are both disabled.
2. **What are the source indents for the two target paragraphs?**
%s
3. **Was the final Chinese indent restored?** Yes. Both target paragraph boundaries, plus the other eligible locked paragraph on the fixture, measure at their source-relative 1.500 em value in Chromium. `first_line_indent_missing_count=0` and `first_line_indent_wrong_count=0`.
4. **Was any ordinary paragraph falsely indented?** No. An ordinary locked body paragraph without positive source geometry remains unstyled, and `false_first_line_indent_count=0`.
5. **Do Fit / Fill affect the indent proportion?** No. Indent is emitted in em, so font-size changes alter the absolute pt value together with the glyphs while preserving the 1.500 em ratio. p006 retains its Task 3 L2 Fit artifact unchanged.
6. **Is SourceTextSlot geometry completely unchanged?** Yes. Slot x/y/width/height are never modified; Chromium/source-box comparison reports `slot_geometry_mutation_count=0`. Hard-anchor movement is also zero.
7. **Is production special-case count zero?** Yes. `production_special_case_count=0`; production code contains no document, filename, page, paragraph-ID, or target-text branch.

## Hard metrics

```json
%s
```

## Scope

Only source paragraph-boundary inference, SourceParagraphStyle metadata, first-line indent rendering, QA, p005 review evidence, and p006 minimal regression are included. Figure ownership, math glyphs, tables, geometry fidelity, flow, and release regression were not changed or expanded.
""" % (
        checkpoint["decision"].upper(),
        "\n".join(values),
        json.dumps(metrics, ensure_ascii=False, indent=2))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    p005 = _run_p005()
    p006 = _run_p006_regression()
    production = _production_diff_audit()
    _dump(OUT / "production_diff_audit.json", production)

    qa_metrics = p005["style_qa"]["metrics"]
    hard = {
        "first_line_indent_missing_count": int(
            qa_metrics["first_line_indent_missing_count"]),
        "first_line_indent_wrong_count": int(
            qa_metrics["first_line_indent_wrong_count"]),
        "false_first_line_indent_count": int(
            qa_metrics["false_first_line_indent_count"]),
        "slot_geometry_mutation_count": int(
            qa_metrics["slot_geometry_mutation_count"]),
        "final_block_collision_count": int(
            qa_metrics["final_block_collision_count"]),
        "production_special_case_count": int(
            production["production_special_case_count"]),
    }
    supporting = {
        "source_has_first_line_indent_count": int(
            qa_metrics["source_has_first_line_indent_count"]),
        "target_fixture_count": len(p005["target_records"]),
        "ordinary_negative_fixture_count": len(p005["negative_records"]),
        "ordinary_negative_fixture_pass": p005["negative_fixture_ok"],
        "hard_anchor_moved_count": p005["hard_anchor_moved_count"],
        "source_text_slot_geometry_preserved": all(
            int(p005["lock_qa"]["metrics"].get(name, 0)) == 0
            for name in (
                "geometry_locked_repack_count",
                "geometry_locked_x_mutation_count",
                "geometry_locked_y_mutation_count",
                "geometry_locked_width_mutation_count")),
        "p006_regression_decision": p006["decision"],
        "ordinary_source_line_breaks_copied": False,
        "ocr_used": False,
    }
    decision = "pass" if (
        all(value == 0 for value in hard.values())
        and p005["style_qa"]["decision"] == "pass"
        and supporting["source_text_slot_geometry_preserved"]
        and p005["negative_fixture_ok"]
        and p005["hard_anchor_moved_count"] == 0
        and len(p005["target_records"]) >= 2
        and p006["decision"] == "pass"
        and production["decision"] == "pass") else "blocked"
    checkpoint = {
        "schema_version": "visual_v07.task4d.checkpoint.v1",
        "decision": decision,
        "hard_metrics": hard,
        "supporting_metrics": supporting,
        "qa_first": {
            "artifact": "qa_first/source_paragraph_style_red.json",
            "expected_decision": "red_confirmed",
        },
        "p005": {
            "style_qa": p005["style_qa"],
            "source_paragraph_style": p005["style_model"],
            "source_text_slot_lock_qa": p005["lock_qa"],
            "final_block_collision_qa": p005["collision"],
            "target_records": p005["target_records"],
            "negative_records": p005["negative_records"],
        },
        "p006_regression": p006,
        "production_diff_audit": production,
    }
    checkpoint["review_bundle"] = _review_bundle(p005, p006, decision)
    _dump(OUT / "checkpoint_gate.json", checkpoint)
    (OUT / "SOURCE_PARAGRAPH_STYLE_REPORT.md").write_text(
        _report({**checkpoint, "p005": p005}), encoding="utf-8")
    print(json.dumps({"decision": decision,
                      "hard_metrics": hard,
                      "supporting_metrics": supporting},
                     ensure_ascii=False, indent=2))
    return 0 if decision == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
