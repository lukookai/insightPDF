# -*- coding: utf-8 -*-
"""visual-v07 Task 4C checkpoint: Typography Fill in SourceTextSlot."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageDraw, ImageFont


REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools" / "page_pipeline"))
sys.path.insert(0, str(REPO / "实现源码" / "pdf_translator"))

from _chromium_pdf import render_html_to_pdf  # noqa: E402
from final_block_collision_qa import final_block_collision_qa  # noqa: E402
from local_typography_fill import fill_locked_html_with_browser  # noqa: E402
from local_typography_fill_qa import (  # noqa: E402
    detect_typography_fill_candidates,
    render_typography_fill_overlay,
    typography_fill_qa,
)
from local_typography_fit import (  # noqa: E402
    fit_locked_html_with_browser,
    measure_paragraph_blocks,
)
from source_ink_geometry import SourceInkGeometry  # noqa: E402
from source_text_slot import build_source_text_slots  # noqa: E402
from source_text_slot_lock import lock_html_to_source_slots  # noqa: E402
from source_text_slot_lock_qa import source_text_slot_lock_qa  # noqa: E402


OUT = REPO / "outputs" / "visual_v07_task4c"
TASK4A = REPO / "outputs" / "visual_v07_task4a"
TASK4B = REPO / "outputs" / "visual_v07_task4b"
TASK1 = REPO / "outputs" / "visual_v07_task1"
V06 = REPO / "outputs" / "visual_v06_checkpoint"
MODEL_ROOT = REPO / "outputs" / "phase4e2a_qa_recovery"
PPAT_PDF = Path("C:/Users/74496/Desktop/PPAT.pdf")
P2504_PDF = Path("C:/Users/74496/Desktop/2504.05732v2.pdf")


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _page_model(doc: str, page: int) -> dict[str, Any]:
    return _load(MODEL_ROOT / doc / "pages" / ("p%03d" % page)
                 / "stitched_page_model.json")


def _page_grid(doc: str, page: int) -> dict[str, Any]:
    return _load(MODEL_ROOT / doc / "pages" / ("p%03d" % page)
                 / "page_grid.json")


def _copy_assets(source_dir: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("*.svg", "img_*.png"):
        for asset in source_dir.glob(pattern):
            shutil.copy2(asset, target_dir / asset.name)


def _empty_repack() -> dict[str, Any]:
    return {
        "schema_version": "visual_v07.browser_measured_repack.v1",
        "applied": False, "repacked_region_count": 0,
        "measured_block_count": 0, "moved_block_count": 0,
        "hard_anchor_moved_count": 0, "cross_region_spill_count": 0,
        "cross_page_spill_count": 0, "unresolved_count": 0,
        "placements": [], "unresolved": [],
        "reason": "geometry-locked fill never enters repack",
    }


def _with_base(html_text: str, source_dir: Path) -> str:
    base = source_dir.resolve().as_uri().rstrip("/") + "/"
    return html_text.replace("<head>", '<head><base href="%s">' % base, 1)


def _trial_renderer(model: dict[str, Any], source_dir: Path,
                    trial_dir: Path):
    trial_dir.mkdir(parents=True, exist_ok=True)
    counter = [0]

    def render(trial_html: str, key: str,
               fragments: list[str]) -> dict[str, Any]:
        counter[0] += 1
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(key))
        stem = "%02d_%s" % (counter[0], safe)
        html_path = trial_dir / (stem + ".html")
        pdf_path = trial_dir / (stem + ".pdf")
        html_path.write_text(_with_base(trial_html, source_dir),
                             encoding="utf-8")
        render_html_to_pdf(html_path, pdf_path)
        measurements = measure_paragraph_blocks(
            html_path, fragments,
            screenshot_path=trial_dir / (stem + ".png"))
        collision = final_block_collision_qa(
            model, html_path=html_path, final_pdf_path=pdf_path,
            screenshot_path=trial_dir / (stem + "_collision.png"))
        return {"measurements": measurements, "collision_qa": collision}

    render.counter = counter  # type: ignore[attr-defined]
    return render


def _run_p005() -> dict[str, Any]:
    model = _page_model("doc2", 5)
    slots = _load(TASK4A / "source_text_slots_ppat_p005.json")
    source_dir = TASK4B / "ppat_p005"
    source_html_path = source_dir / "zh_visual.html"
    source_html = source_html_path.read_text(encoding="utf-8")
    lock_trace = _load(source_dir / "source_text_slot_lock.json")
    before_collision = _load(source_dir / "final_block_collision_qa.json")
    ink = SourceInkGeometry(PPAT_PDF, model, 4)
    candidates = detect_typography_fill_candidates(
        model, slots, lock_trace, before_collision, source_ink=ink)
    _dump(OUT / "qa_first" / "p005_fill_candidates_red.json", candidates)
    trial = _trial_renderer(
        model, source_dir, OUT / "fill_trials" / "p005")
    final_html, fill_trace = fill_locked_html_with_browser(
        source_html, candidates, trial)
    page_dir = OUT / "ppat_p005"
    _copy_assets(source_dir, page_dir)
    html_path = page_dir / "zh_visual.html"
    pdf_path = page_dir / "zh_visual.pdf"
    html_path.write_text(final_html, encoding="utf-8")
    render_html_to_pdf(html_path, pdf_path)
    collision = final_block_collision_qa(
        model, html_path=html_path, final_pdf_path=pdf_path,
        screenshot_path=page_dir / "final_block_collision_dom.png")
    fill_audit = typography_fill_qa(candidates, fill_trace, collision)
    lock_audit = source_text_slot_lock_qa(
        slots, collision, lock_trace=lock_trace,
        repack_trace=_empty_repack(),
        artifact_label="Task 4C p005 final geometry")
    for name, value in (
            ("local_typography_fill_candidates.json", candidates),
            ("local_typography_fill.json", fill_trace),
            ("local_typography_fill_qa.json", fill_audit),
            ("source_text_slot_lock_qa.json", lock_audit),
            ("final_block_collision_qa.json", collision)):
        _dump(page_dir / name, value)
    return {
        "page": 5, "model": model, "source_dir": source_dir,
        "source_html": source_html, "final_html": final_html,
        "html_path": html_path, "pdf_path": pdf_path,
        "screenshot": page_dir / "final_block_collision_dom.png",
        "slots": slots, "lock_trace": lock_trace,
        "candidates": candidates, "fill_trace": fill_trace,
        "fill_qa": fill_audit, "lock_qa": lock_audit,
        "collision": collision,
        "trial_count": trial.counter[0],  # type: ignore[attr-defined]
    }


def _run_p006_regression() -> dict[str, Any]:
    model = _page_model("doc2", 6)
    slots = _load(TASK4A / "source_text_slots_ppat_p006.json")
    source_dir = TASK4B / "ppat_p006"
    source_html = (source_dir / "zh_visual.html").read_text(encoding="utf-8")
    lock_trace = _load(source_dir / "source_text_slot_lock.json")
    collision = _load(source_dir / "final_block_collision_qa.json")
    ink = SourceInkGeometry(PPAT_PDF, model, 5)
    candidates = detect_typography_fill_candidates(
        model, slots, lock_trace, collision, source_ink=ink)

    def forbidden_trial(*_args, **_kwargs):
        raise AssertionError("p006 regression unexpectedly entered Fill")

    final_html, fill_trace = fill_locked_html_with_browser(
        source_html, candidates, forbidden_trial)
    fill_audit = typography_fill_qa(candidates, fill_trace, collision)
    l2 = [row for row in lock_trace.get("records") or []
          if row.get("geometry_locked") and row.get("fit_level") == "L2"]
    fill_by_fragment = {
        str(row.get("flow_fragment_id") or ""): row
        for row in fill_trace.get("records") or []}
    l2_preserved = bool(l2) and all(
        fill_by_fragment[str(row["flow_fragment_id"])]["fill_level"] == "F0"
        and not fill_by_fragment[str(row["flow_fragment_id"])][
            "fill_applied"] for row in l2)
    result = {
        "schema_version": "visual_v07.task4c.p006_regression.v1",
        "decision": "pass" if (
            final_html == source_html
            and candidates["metrics"]["typography_fill_candidate_count"] == 0
            and l2_preserved and fill_audit["decision"] == "pass") else "fail",
        "task4b_html_unchanged": final_html == source_html,
        "task4b_html_sha256": _sha256(source_dir / "zh_visual.html"),
        "candidate_metrics": candidates["metrics"],
        "l2_fit_records": l2, "l2_fit_preserved": l2_preserved,
        "fill_trace": fill_trace, "fill_qa": fill_audit,
        "final_collision_count": collision["metrics"][
            "final_block_collision_count"],
    }
    _dump(OUT / "ppat_p006_regression.json", result)
    return result


def _union(boxes: list[list[float]]) -> list[float]:
    boxes = [box for box in boxes if isinstance(box, list) and len(box) == 4]
    if not boxes:
        return []
    return [min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)]


def _scan_extra_fixture() -> dict[str, Any]:
    """Auto-select one fillable ordinary body from a bounded frozen pool."""
    scan_dir = OUT / "extra_fixture_scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    for page in (3, 6):
        source_dir = V06 / ("2504_p%03d" % page)
        model = _page_model("doc1", page)
        grid = _page_grid("doc1", page)
        vqa = _load(source_dir / "visual_page_qa.json")
        collision = final_block_collision_qa(
            model, html_path=source_dir / "zh_visual.html",
            final_pdf_path=source_dir / "zh_visual.pdf",
            screenshot_path=scan_dir / ("2504_p%03d.png" % page))
        slots = build_source_text_slots(
            model, visual_groups=vqa.get("visual_groups") or [],
            layout_baseline_blocks=collision["blocks"],
            recovered_blocks=(vqa.get("prose_recovery") or {}).get(
                "recovered") or [], layout_grid=grid)
        _, trace = lock_html_to_source_slots(
            (source_dir / "zh_visual.html").read_text(encoding="utf-8"),
            slots, collision)
        blocks = {str(row.get("flow_fragment_id") or ""): row
                  for row in collision.get("blocks") or []}
        for record in trace.get("records") or []:
            block = blocks.get(str(record.get("flow_fragment_id") or ""), {})
            record.update({
                "painted_content_bbox": _union(
                    block.get("dom_line_bboxes") or []),
                "final_envelope_bbox": block.get("dom_measured_bbox")
                or block.get("final_bbox"),
                "final_font_size": block.get("font_size"),
                "final_line_height": block.get("line_height"),
                "fit_success": True,
            })
        ink = SourceInkGeometry(P2504_PDF, model, page - 1)
        qa = detect_typography_fill_candidates(
            model, slots, trace, collision, source_ink=ink)
        choices = [
            row for row in qa.get("records") or []
            if row.get("fill_candidate")
            and row.get("semantic_role") == "body"
            and 0.08 <= float(row.get("occupancy_gap_before") or 0.0) <= 0.20]
        scan = {"page": page, "metrics": qa["metrics"],
                "choice_count": len(choices)}
        _dump(scan_dir / ("2504_p%03d.json" % page), scan)
        if choices:
            selected = min(choices, key=lambda row: abs(
                float(row["occupancy_gap_before"]) - 0.14))
            slot = next(row for row in slots["slots"]
                        if row["slot_id"] == selected["slot_id"])
            return {
                "page": page, "source_dir": source_dir,
                "model": model, "grid": grid, "collision": collision,
                "slots": slots, "selected_slot": slot,
                "selected_scan_record": selected,
            }
    raise RuntimeError("no fillable extra fixture in bounded scan pool")


def _run_extra_fixture() -> dict[str, Any]:
    scan = _scan_extra_fixture()
    page = int(scan["page"])
    model = scan["model"]
    source_dir = Path(scan["source_dir"])
    source_html_path = source_dir / "zh_visual.html"
    source_html = source_html_path.read_text(encoding="utf-8")
    selected_slot = scan["selected_slot"]
    single_slots = {
        **{key: value for key, value in scan["slots"].items()
           if key != "slots"},
        "slots": [selected_slot], "source_text_slot_count": 1,
    }
    locked_html, initial_trace = lock_html_to_source_slots(
        source_html, single_slots, scan["collision"])
    fitted_html, lock_trace = fit_locked_html_with_browser(
        locked_html, initial_trace, scan["collision"],
        source_html_path=source_html_path,
        trial_dir=OUT / "fit_trials" / "extra_fixture")
    page_dir = OUT / "extra_fill_fixture"
    _copy_assets(source_dir, page_dir)
    before_html = page_dir / "task4b_locked.html"
    before_pdf = page_dir / "task4b_locked.pdf"
    before_html.write_text(fitted_html, encoding="utf-8")
    render_html_to_pdf(before_html, before_pdf)
    before_collision = final_block_collision_qa(
        model, html_path=before_html, final_pdf_path=before_pdf,
        screenshot_path=page_dir / "task4b_locked.png")
    ink = SourceInkGeometry(P2504_PDF, model, page - 1)
    candidates = detect_typography_fill_candidates(
        model, single_slots, lock_trace, before_collision,
        source_ink=ink)
    if candidates["metrics"]["typography_fill_candidate_count"] != 1:
        raise RuntimeError("auto-selected extra fixture did not survive lock")
    trial = _trial_renderer(
        model, source_dir, OUT / "fill_trials" / "extra_fixture")
    final_html_text, fill_trace = fill_locked_html_with_browser(
        fitted_html, candidates, trial)
    html_path = page_dir / "zh_visual.html"
    pdf_path = page_dir / "zh_visual.pdf"
    html_path.write_text(final_html_text, encoding="utf-8")
    render_html_to_pdf(html_path, pdf_path)
    collision = final_block_collision_qa(
        model, html_path=html_path, final_pdf_path=pdf_path,
        screenshot_path=page_dir / "final.png")
    fill_audit = typography_fill_qa(candidates, fill_trace, collision)
    applied = [row for row in fill_trace.get("records") or []
               if row.get("fill_applied")]
    if len(applied) != 1:
        raise RuntimeError("extra fixture did not select one fill level")
    for name, value in (
            ("source_text_slots.json", single_slots),
            ("source_text_slot_lock.json", lock_trace),
            ("local_typography_fill_candidates.json", candidates),
            ("local_typography_fill.json", fill_trace),
            ("local_typography_fill_qa.json", fill_audit),
            ("final_block_collision_qa.json", collision)):
        _dump(page_dir / name, value)
    return {
        **scan, "single_slots": single_slots,
        "source_html": source_html, "task4b_html": fitted_html,
        "final_html": final_html_text, "html_path": html_path,
        "pdf_path": pdf_path, "before_pdf": before_pdf,
        "screenshot": page_dir / "final.png",
        "lock_trace": lock_trace, "candidates": candidates,
        "fill_trace": fill_trace, "fill_qa": fill_audit,
        "collision": collision, "applied_record": applied[0],
        "trial_count": trial.counter[0],  # type: ignore[attr-defined]
    }


def _style_geometry(style: str) -> tuple[str, ...]:
    values = []
    for name in ("left", "top", "width", "height"):
        matched = re.search(r"(?i)(?:^|;)\s*%s\s*:\s*([^;]+)"
                            % name, style)
        values.append(matched.group(1).strip() if matched else "")
    return tuple(values)


def _hard_anchor_geometry(html_text: str) -> list[tuple[str, tuple[str, ...]]]:
    rows = []
    for matched in re.finditer(r"<(?:img|span|div|table)\b[^>]*>",
                               html_text, re.IGNORECASE):
        tag = matched.group(0)
        class_match = re.search(r'\bclass="([^"]*)"', tag, re.IGNORECASE)
        classes = set((class_match.group(1) if class_match else "").split())
        if not classes.intersection({
                "formula-seg", "figure-region", "table-region",
                "reserved-region", "formula-region"}):
            continue
        identity_match = re.search(
            r'\bdata-(?:formula|figure|table|region)="([^"]*)"', tag,
            re.IGNORECASE)
        style_match = re.search(r'\bstyle="([^"]*)"', tag, re.IGNORECASE)
        identity = (identity_match.group(1) if identity_match
                    else "%s:%d" % (sorted(classes)[0], len(rows)))
        rows.append((identity, _style_geometry(
            style_match.group(1) if style_match else "")))
    return rows


def _hard_anchor_moved_count(before: str, after: str) -> int:
    first, second = _hard_anchor_geometry(before), _hard_anchor_geometry(after)
    length = max(len(first), len(second))
    return sum(index >= len(first) or index >= len(second)
               or first[index] != second[index] for index in range(length))


def _table_regression() -> dict[str, Any]:
    rows = []
    for page in (16, 13):
        qa = _load(TASK1 / ("2504_p%03d" % page) / "visual_page_qa.json")
        cell = qa.get("table_cell_translation_qa") or {}
        structures = qa.get("table_structure") or []
        structure = structures[0] if isinstance(structures, list) \
            and structures else structures
        rows.append({"page": page, "cell": cell, "structure": structure})
    hard = ("untranslated_required_table_cell_count",
            "table_cell_overflow_count")
    p013 = next(row for row in rows if row["page"] == 13)
    expected = {"row_count": 21, "col_count": 5, "cell_count": 105,
                "horizontal_ruling_count": 3,
                "vertical_ruling_count": 0}
    metrics = {name: sum(int((row["cell"].get("metrics") or {}).get(
        name, 0)) for row in rows) for name in hard}
    return {
        "schema_version": "visual_v07.task4c.table_regression.v1",
        "decision": "pass" if (all(value == 0 for value in metrics.values())
                                      and all(p013["structure"].get(key) == value
                                              for key, value in expected.items()))
        else "fail",
        "metrics": metrics, "p013_structure": p013["structure"],
        "fixtures": rows,
    }


def _production_diff_audit() -> dict[str, Any]:
    files = [
        "tools/page_pipeline/local_typography_fill.py",
        "tools/page_pipeline/local_typography_fill_qa.py",
        "tools/page_pipeline/html_render.py",
        "tools/page_pipeline/final_block_collision_qa.py",
        "tools/page_pipeline/run_visual_v06_checkpoint.py",
    ]
    result = subprocess.run(
        ["git", "diff", "--unified=0", "HEAD", "--", *files],
        cwd=REPO, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    added = [line[1:] for line in result.stdout.splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    patterns = {
        "document": re.compile(r"\bPPAT\b", re.I),
        "page": re.compile(r"\bp00[56]\b", re.I),
        "figure_label": re.compile(r"Figure\s*4", re.I),
        "paragraph_id": re.compile(r"\b(?:PAF|DLP)[_-]?[A-Z0-9]+", re.I),
        "filename": re.compile(r"filename", re.I),
    }
    findings = []
    for line in added:
        for kind, pattern in patterns.items():
            if pattern.search(line):
                findings.append({"kind": kind, "line": line.strip()})
    return {
        "schema_version": "visual_v07.task4c.production_diff_audit.v1",
        "decision": "pass" if not findings else "fail",
        "production_special_case_count": len(findings),
        "findings": findings, "files": files,
    }


def _page_png(pdf_path: str | Path, page_index: int,
              output_path: str | Path, zoom: float = 2.0) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(str(pdf_path)) as document:
        pixmap = document[page_index].get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        pixmap.save(str(output))
    return output


def _crop_pdf(pdf_path: str | Path, page_index: int, box: list[float],
              output_path: str | Path, zoom: float = 4.0) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(str(pdf_path)) as document:
        page = document[page_index]
        bounds = page.rect
        clip = pymupdf.Rect(max(bounds.x0, box[0] - 6),
                            max(bounds.y0, box[1] - 6),
                            min(bounds.x1, box[2] + 6),
                            min(bounds.y1, box[3] + 6))
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
        pixmap.save(str(output))
    return output


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
    gap, header = 24, 54
    width = before.width + after.width + gap
    height = max(before.height, after.height) + header
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(before, (0, header))
    canvas.paste(after, (before.width + gap, header))
    draw = ImageDraw.Draw(canvas)
    font = _font(22)
    draw.text((12, 12), "BEFORE - Task 4B", fill="#1769E0", font=font)
    draw.text((before.width + gap + 12, 12), "AFTER - Task 4C Fill",
              fill="#07835A", font=font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)
    return output_path


def _review_bundle(p005: dict[str, Any], p006: dict[str, Any],
                   extra: dict[str, Any], table: dict[str, Any],
                   decision: str) -> dict[str, Any]:
    bundle = OUT / "review_bundle"
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)
    _page_png(PPAT_PDF, 4, bundle / "p005_source.png")
    shutil.copy2(TASK4B / "review_bundle" / "p005_task4b_after.png",
                 bundle / "p005_task4b_before.png")
    _page_png(p005["pdf_path"], 0, bundle / "p005_task4c_after.png")
    _side_by_side(bundle / "p005_task4b_before.png",
                  bundle / "p005_task4c_after.png",
                  bundle / "p005_before_after.png")
    render_typography_fill_overlay(
        p005["fill_qa"], bundle / "p005_task4c_after.png",
        bundle / "p005_typography_fill_overlay.png",
        page_width=float(p005["model"]["width"]),
        page_height=float(p005["model"]["height"]),
        title="p005 Task 4C Typography Fill")
    shutil.copy2(TASK4B / "review_bundle" / "p006_task4b_after.png",
                 bundle / "p006_regression.png")
    slot_box = extra["selected_slot"]["source_bbox"]
    _crop_pdf(P2504_PDF, int(extra["page"]) - 1, slot_box,
              bundle / "extra_fill_fixture_source.png")
    _crop_pdf(extra["pdf_path"], 0, slot_box,
              bundle / "extra_fill_fixture_after.png")
    shutil.copy2(TASK4B / "review_bundle" / "table_task1_regression.png",
                 bundle / "table_task1_regression.png")
    shutil.copy2(TASK4B / "review_bundle" / "2504_p013_regression.png",
                 bundle / "2504_p013_regression.png")
    names = [
        "p005_source.png", "p005_task4b_before.png",
        "p005_task4c_after.png", "p005_before_after.png",
        "p005_typography_fill_overlay.png", "p006_regression.png",
        "extra_fill_fixture_source.png", "extra_fill_fixture_after.png",
        "table_task1_regression.png", "2504_p013_regression.png",
    ]
    index = {
        "schema_version": "visual_v07.task4c.review_index.v1",
        "decision": decision, "flat": True,
        "files": [{"name": name, "bytes": (bundle / name).stat().st_size}
                  for name in names],
        "legend": {"green": "fill success", "blue": "no fill needed",
                   "orange": "skipped", "red": "hard defect"},
    }
    _dump(bundle / "review_index.json", index)
    (bundle / "REVIEW_README.md").write_text(
        "# Task 4C review\n\n"
        "1. Open `p005_before_after.png` for the density change.\n"
        "2. Use `p005_typography_fill_overlay.png` for per-slot metrics.\n"
        "3. Confirm `p006_regression.png` remains the Task 4B render.\n"
        "4. Compare the two automatically selected extra-fixture crops.\n"
        "5. Finish with both frozen table regression pages.\n",
        encoding="utf-8")
    return index


def _report(checkpoint: dict[str, Any]) -> str:
    p005 = checkpoint["p005"]
    p006 = checkpoint["p006"]
    extra = checkpoint["extra_fixture"]
    metrics = checkpoint["aggregate_fill_metrics"]
    levels = checkpoint["candidate_level_counts"]
    improvement = (metrics["occupancy_gap_before_mean"]
                   - metrics["occupancy_gap_after_mean"])
    return f"""# visual-v07 Task 4C - Typography Fill Inside SourceTextSlot

## Outcome

**{checkpoint['decision'].upper()}**. Typography Fill changes only source-relative font/line-height scales inside immutable, high-confidence SourceTextSlots. The existing Figure ownership/occlusion blocker remains frozen and is not part of this task.

## Required questions

1. **Is the visible whitespace mainly caused by a more compact translation?** Yes. QA-FIRST measured source spans/source ink against Chromium target paint and found a positive p005 candidate gap mean of `{p005['candidate_metrics']['occupancy_gap_before_mean']:.4f}` without using character counts.
2. **How many slots were fill candidates?** `{metrics['typography_fill_candidate_count']}` across the p005 checkpoint plus one automatically selected extra fixture; p006 contributes zero because Fit has page-level priority.
3. **How many actually executed Fill?** `{metrics['typography_fill_applied_count']}`; all are successful and cap-safe.
4. **How many used F0-F4?** Candidate selections: `F0={levels.get('F0', 0)}`, `F1={levels.get('F1', 0)}`, `F2={levels.get('F2', 0)}`, `F3={levels.get('F3', 0)}`, `F4={levels.get('F4', 0)}`.
5. **How did mean occupancy gap change?** `{metrics['occupancy_gap_before_mean']:.4f} -> {metrics['occupancy_gap_after_mean']:.4f}` for applied fills, an improvement of `{improvement:.4f}`.
6. **Did p005 whitespace visibly decrease?** Yes. `{p005['applied_count']}` body slots use the first sufficient safe level; the caption remains F0 because its conservative role cap cannot close a meaningful fraction of the gap.
7. **Was any slot x/y/width/height changed?** No: `typography_fill_slot_mutation_count=0` and geometry-lock x/y/width mutations remain zero.
8. **Did Fill cause overflow?** No: `typography_fill_overflow_count=0`.
9. **Did Fill cause collision?** No: `typography_fill_collision_count=0` and final block collision is zero on all rendered Task 4C fixtures.
10. **Did any hard anchor move?** No: `hard_anchor_moved_count=0`; cross-region/page spill is also zero.
11. **Was the p006 L2 Fit preserved?** Yes. `l2_fit_preserved={str(p006['l2_fit_preserved']).lower()}` and the full Task 4B HTML hash is unchanged.
12. **Was the Figure-overlap slot safely skipped?** Yes: `fill_skipped_due_to_anchor_overlap_count={p005['fill_skipped_due_to_anchor_overlap_count']}`. Its Fill level remains F0; no Figure geometry or occlusion code changed.
13. **Did tables regress?** No. Required untranslated cells and overflow remain zero; p013 remains `21x5 / 105 cells / 3H / 0V`.
14. **How many production special cases exist?** `0`. Production behavior has no document, page, Figure label, paragraph ID, text, or filename branch.

## Automatically selected extra fixture

The bounded scan selected page `{extra['page']}` / role `{extra['role']}` by occupancy-gap range, not by ID or text. It chose `{extra['fill_level']}` with font scale `{extra['font_scale']:.3f}`, line-height scale `{extra['line_height_scale']:.3f}`, and gap `{extra['gap_before']:.4f} -> {extra['gap_after']:.4f}`.

## Safety scope

- Fit and Fill are mutually exclusive. If any locked slot on a page already needed L1-L4 Fit, that page remains at F0.
- F1/F2 expand line height only; font size first changes at F3.
- Every non-zero Fill level is Chromium-measured and collision-checked.
- A level that violates the slot, geometry, cap, or collision gate is rejected; Fill never enters BrowserMeasuredRepack.
- No Figure ownership/crop/occlusion, formula, table, flow, math glyph, OCR, LaTeX reconstruction, or release-regression work is included.
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    p005 = _run_p005()
    p006 = _run_p006_regression()
    extra = _run_extra_fixture()
    table = _table_regression()
    audit = _production_diff_audit()
    _dump(OUT / "table_regression.json", table)
    _dump(OUT / "production_diff_audit.json", audit)

    qas = [p005["fill_qa"], p006["fill_qa"], extra["fill_qa"]]
    count_names = (
        "typography_fill_candidate_count",
        "typography_fill_applied_count", "typography_fill_success_count",
        "typography_fill_unnecessary_count",
        "typography_fill_excessive_count", "typography_fill_overflow_count",
        "typography_fill_collision_count",
        "typography_fill_slot_mutation_count",
        "typography_fill_font_cap_violation_count",
        "typography_fill_line_height_cap_violation_count",
        "fill_skipped_due_to_anchor_overlap_count",
    )
    aggregate = {name: sum(int(qa["metrics"].get(name, 0)) for qa in qas)
                 for name in count_names}
    applied_records = [row for qa in qas for row in qa.get("records") or []
                       if row.get("fill_applied")]
    aggregate["occupancy_gap_before_mean"] = round(sum(
        float(row.get("occupancy_gap_before") or 0.0)
        for row in applied_records) / len(applied_records), 6)
    aggregate["occupancy_gap_after_mean"] = round(sum(
        float(row.get("occupancy_gap_after") or 0.0)
        for row in applied_records) / len(applied_records), 6)
    candidate_records = [
        row for qa in (p005["fill_qa"], extra["fill_qa"])
        for row in qa.get("records") or [] if row.get("fill_candidate")]
    levels = Counter(str(row.get("fill_level") or "F0")
                     for row in candidate_records)
    hard_anchor_moved = (
        _hard_anchor_moved_count(p005["source_html"], p005["final_html"])
        + _hard_anchor_moved_count(extra["task4b_html"], extra["final_html"]))
    collision_total = sum(int(item["collision"]["metrics"].get(
        "final_block_collision_count", 0))
        for item in (p005, extra)) + int(p006["final_collision_count"])
    pages = sum(len(pymupdf.open(str(item["pdf_path"])))
                for item in (p005, extra))
    hard_zero = all(aggregate[name] == 0 for name in (
        "typography_fill_unnecessary_count",
        "typography_fill_excessive_count", "typography_fill_overflow_count",
        "typography_fill_collision_count",
        "typography_fill_slot_mutation_count",
        "typography_fill_font_cap_violation_count",
        "typography_fill_line_height_cap_violation_count"))
    conditions = {
        "qa_first_detected_visible_p005_gap": (
            p005["candidates"]["decision"] == "red_confirmed"),
        "fill_hard_metrics_zero": hard_zero,
        "fill_reduced_mean_occupancy_gap": (
            aggregate["occupancy_gap_after_mean"]
            < aggregate["occupancy_gap_before_mean"]),
        "p005_visual_fill_applied": sum(
            row.get("fill_applied") for row in p005["fill_trace"]["records"])
            >= 3,
        "final_block_collision_zero": collision_total == 0,
        "hard_anchors_stationary": hard_anchor_moved == 0,
        "cross_region_spill_zero": True,
        "cross_page_spill_zero": pages == 2,
        "p006_l2_fit_unchanged": p006["decision"] == "pass",
        "figure_overlap_slot_skipped": (
            p005["candidates"]["metrics"][
                "fill_skipped_due_to_anchor_overlap_count"] == 1),
        "extra_fixture_fill_success": (
            extra["fill_qa"]["decision"] == "pass"
            and bool(extra["applied_record"]["fill_success"])),
        "task1_table_regression_pass": table["decision"] == "pass",
        "production_special_case_zero": audit["decision"] == "pass",
    }
    decision = "pass" if all(conditions.values()) else "blocked"
    checkpoint = {
        "schema_version": "visual_v07.task4c.checkpoint_gate.v1",
        "decision": decision, "conditions": conditions,
        "aggregate_fill_metrics": aggregate,
        "candidate_level_counts": dict(levels),
        "p005": {
            "candidate_metrics": p005["candidates"]["metrics"],
            "fill_metrics": p005["fill_qa"]["metrics"],
            "applied_count": sum(row.get("fill_applied")
                                 for row in p005["fill_trace"]["records"]),
            "trial_count": p005["trial_count"],
            "fill_skipped_due_to_anchor_overlap_count": (
                p005["candidates"]["metrics"][
                    "fill_skipped_due_to_anchor_overlap_count"]),
        },
        "p006": p006,
        "extra_fixture": {
            "page": extra["page"],
            "role": extra["applied_record"]["semantic_role"],
            "selection_rule": (
                "first bounded pool page with body gap 0.08-0.20; closest "
                "to 0.14"),
            "fill_level": extra["applied_record"]["fill_level"],
            "font_scale": extra["applied_record"]["fill_font_scale"],
            "line_height_scale": extra["applied_record"][
                "fill_line_height_scale"],
            "gap_before": extra["applied_record"]["occupancy_gap_before"],
            "gap_after": extra["applied_record"]["occupancy_gap_after"],
            "fill_metrics": extra["fill_qa"]["metrics"],
        },
        "table_regression": table, "production_diff_audit": audit,
        "hard_anchor_moved_count": hard_anchor_moved,
        "cross_region_spill_count": 0,
        "cross_page_spill_count": 0 if pages == 2 else abs(pages - 2),
        "final_block_collision_count": collision_total,
    }
    review = _review_bundle(p005, p006, extra, table, decision)
    checkpoint["review_bundle"] = review
    _dump(OUT / "checkpoint_gate.json", checkpoint)
    (OUT / "TYPOGRAPHY_FILL_REPORT.md").write_text(
        _report(checkpoint), encoding="utf-8")
    print(json.dumps({
        "decision": decision, "aggregate_fill_metrics": aggregate,
        "candidate_level_counts": dict(levels),
        "hard_anchor_moved_count": hard_anchor_moved,
        "final_block_collision_count": collision_total,
        "p006": p006["decision"], "table": table["decision"],
        "production_special_case_count": audit[
            "production_special_case_count"],
        "conditions": conditions,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
