# -*- coding: utf-8 -*-
"""Run fast-v02 against three frozen slow-page fixtures only.

No translation, fresh document run, full-document QA, or contact sheet is
reachable from this runner.  It starts from each frozen L0 HTML and the
frozen Task 4M ownership/slot ledgers, runs the generic fast Typography path,
prints exactly one formal fixture PDF, and executes only the requested local
geometry/collision/overflow gates.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for path in (HERE, REPO / "实现源码" / "pdf_translator"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from _chromium_pdf import render_html_to_pdf  # noqa: E402
from final_block_collision_qa import final_block_collision_qa  # noqa: E402
from local_typography_fill_qa import typography_fill_qa  # noqa: E402
from local_typography_fit import _with_base  # noqa: E402
from typography_fast_path import (  # noqa: E402
    TypographyBatchSession,
    fill_locked_html_fast,
    fit_locked_html_fast,
    write_fast_trace,
)
from typography_fast_path_qa import (  # noqa: E402
    audit_fast_pages,
    frozen_before_record,
)


TASK4M = REPO / "outputs" / "visual_v07_task4m"
VISUAL = TASK4M / "fresh_visual_pages"
SOURCE_CHAIN = TASK4M / "fresh_runs_v2"
OUT = REPO / "outputs" / "fast_v02"
REVIEW = OUT / "review_bundle"
SCRATCH = REPO / "tmp" / "pdfs" / "fast_v02"
BASELINE = REPO / "outputs" / "fast_v01" / "slow_page_ranking.json"
POPPLER = (Path.home() / ".cache" / "codex-runtimes"
           / "codex-primary-runtime" / "dependencies" / "native"
           / "poppler" / "Library" / "bin" / "pdftoppm.exe")

FIXTURES = (
    {"page_key": "ppat_p011", "doc": "ppat", "page": 11,
     "source_pdf": Path(r"C:\Users\74496\Desktop\PPAT.pdf")},
    {"page_key": "2504_p011", "doc": "2504", "page": 11,
     "source_pdf": Path(r"C:\Users\74496\Desktop\2504.05732v2.pdf")},
    {"page_key": "2504_p005", "doc": "2504", "page": 5,
     "source_pdf": Path(r"C:\Users\74496\Desktop\2504.05732v2.pdf")},
)


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


class _TagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[dict[str, str]] = []

    def handle_starttag(self, tag: str,
                        attrs: list[tuple[str, str | None]]) -> None:
        row = {str(name): str(value or "") for name, value in attrs}
        row["_tag"] = tag
        self.tags.append(row)


def _tags(html_text: str) -> list[dict[str, str]]:
    parser = _TagCollector()
    parser.feed(html_text)
    return parser.tags


def _style_box(tag: dict[str, str]) -> tuple[float, float, float, float] | None:
    style = str(tag.get("style") or "")
    values = {}
    for name in ("left", "top", "width", "height"):
        found = re.search(
            r"(?:^|;)\s*%s\s*:\s*([-+0-9.]+)pt" % name,
            style, re.IGNORECASE)
        if found is None:
            return None
        values[name] = round(float(found.group(1)), 3)
    return (values["left"], values["top"], values["width"],
            values["height"])


def _slot_signatures(html_text: str) -> dict[str, tuple[float, ...]]:
    result = {}
    for tag in _tags(html_text):
        classes = set(str(tag.get("class") or "").split())
        if ("paragraph-block" not in classes
                or tag.get("data-geometry-locked") != "true"):
            continue
        fragment = str(tag.get("data-flow-fragment") or "")
        box = _style_box(tag)
        if fragment and box is not None:
            result[fragment] = box
    return result


def _anchor_signatures(html_text: str) -> list[tuple[Any, ...]]:
    result = []
    for tag in _tags(html_text):
        classes = set(str(tag.get("class") or "").split())
        if not classes.intersection({
                "formula-seg", "figure-region", "translated-cell"}):
            continue
        result.append((
            tuple(sorted(classes)),
            str(tag.get("data-formula") or ""),
            str(tag.get("data-segment") or ""),
            str(tag.get("src") or ""),
            str(tag.get("style") or ""),
        ))
    return result


def _first_l0(page_dir: Path) -> Path:
    candidates = sorted(page_dir.glob("_local_typography_trial_*_L0.html"))
    if not candidates:
        raise FileNotFoundError("frozen L0 fixture missing: %s" % page_dir)
    return candidates[0]


def _render_png(pdf: Path, output: Path, *, page: int = 1) -> Path:
    if not POPPLER.exists():
        raise FileNotFoundError("bundled pdftoppm missing: %s" % POPPLER)
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = output.with_suffix("")
    command = [str(POPPLER), "-f", str(page), "-l", str(page),
               "-singlefile", "-r", "144", "-png", str(pdf),
               str(prefix)]
    subprocess.run(command, check=True, cwd=str(REPO),
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return output


def _font(size: int = 24) -> ImageFont.ImageFont:
    for path in (Path(r"C:\Windows\Fonts\arial.ttf"),
                 Path(r"C:\Windows\Fonts\msyh.ttc")):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _comparison(source: Path, old: Path, new: Path, output: Path,
                title: str) -> None:
    images = [Image.open(path).convert("RGB") for path in (source, old, new)]
    width = max(image.width for image in images)
    height = max(image.height for image in images)
    header = 62
    canvas = Image.new("RGB", (width * 3, height + header), "white")
    draw = ImageDraw.Draw(canvas)
    font = _font(22)
    labels = ("SOURCE", "OLD", "FAST-V02")
    for index, (image, label) in enumerate(zip(images, labels)):
        x = index * width + (width - image.width) // 2
        canvas.paste(image, (x, header))
        draw.text((index * width + 16, 12), "%s | %s" % (title, label),
                  fill="#111111", font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
    for image in images:
        image.close()


def _pixel_diff_count(left: Path, right: Path) -> int:
    with Image.open(left) as old_image, Image.open(right) as new_image:
        old = old_image.convert("RGB")
        new = new_image.convert("RGB")
        if old.size != new.size:
            return max(old.width * old.height, new.width * new.height)
        diff = ImageChops.difference(old, new)
        data = diff.tobytes()
        return sum(any(data[index:index + 3])
                   for index in range(0, len(data), 3))


def _baseline_rows() -> dict[str, dict[str, Any]]:
    payload = _load(BASELINE, {}) or {}
    return {row["page_key"]: row for row in payload.get(
        "required_hot_pages") or []}


def _run_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    page_key = fixture["page_key"]
    page_dir = VISUAL / page_key
    scratch = SCRATCH / page_key
    scratch.mkdir(parents=True, exist_ok=True)
    old_html = (page_dir / "zh_visual.html").read_text(encoding="utf-8")
    base_path = _first_l0(page_dir)
    base_html = base_path.read_text(encoding="utf-8")
    lock_trace = _load(page_dir / "source_text_slot_lock.json", {})
    initial_qa = (_load(page_dir / "visual_page_qa.json", {}) or {}).get(
        "initial_final_block_collision_qa") or {}
    candidate_qa = _load(
        page_dir / "local_typography_fill_candidates.json", {})

    def html_builder(state: str) -> str:
        return _with_base(state, base_path)

    started = time.perf_counter()
    with TypographyBatchSession(html_builder, scratch / "measure") as session:
        fitted_html, fast_lock = fit_locked_html_fast(
            base_html, lock_trace, initial_qa, session)
        final_html, fast_fill = fill_locked_html_fast(
            fitted_html, fast_lock, candidate_qa, session)
        fast_trace = write_fast_trace(
            scratch / "typography_fast_path.json", session,
            fast_lock, fast_fill)
    typography_seconds = time.perf_counter() - started

    final_html_with_base = _with_base(final_html, base_path)
    html_path = scratch / "fast_v02.html"
    pdf_path = scratch / "fast_v02.pdf"
    html_path.write_text(final_html_with_base, encoding="utf-8")
    print_started = time.perf_counter()
    render_html_to_pdf(html_path, pdf_path)
    formal_print_seconds = time.perf_counter() - print_started

    model_root = SOURCE_CHAIN / (
        "ppat_source_chain" if fixture["doc"] == "ppat"
        else "2504_source_chain")
    model_path = model_root / "pages" / ("p%03d" % fixture["page"]) \
        / "stitched_page_model.json"
    collision = final_block_collision_qa(
        _load(model_path, {}), html_path=html_path,
        final_pdf_path=pdf_path,
        screenshot_path=scratch / "final_collision.png")
    fill_audit = typography_fill_qa(candidate_qa, fast_fill, collision)

    old_slots = _slot_signatures(old_html)
    new_slots = _slot_signatures(final_html)
    old_anchors = _anchor_signatures(old_html)
    new_anchors = _anchor_signatures(final_html)
    slot_mutations = sum(old_slots.get(key) != value
                         for key, value in new_slots.items())
    slot_mutations += len(set(old_slots) ^ set(new_slots))
    hard_anchor_moved = int(old_anchors != new_anchors)

    source_png = scratch / "source.png"
    old_png = scratch / "old.png"
    new_png = scratch / "new.png"
    _render_png(fixture["source_pdf"], source_png, page=fixture["page"])
    _render_png(page_dir / "zh_visual.pdf", old_png)
    _render_png(pdf_path, new_png)
    pixel_diff_count = _pixel_diff_count(old_png, new_png)
    review_path = REVIEW / (page_key + "_source_old_new.png")
    _comparison(source_png, old_png, new_png, review_path, page_key)

    metrics = {
        "slot_geometry_mutation_count": slot_mutations,
        "final_block_collision_count": int(
            collision["metrics"]["final_block_collision_count"]),
        "hard_anchor_moved_count": hard_anchor_moved,
        "overflow_count": int((fill_audit.get("metrics") or {}).get(
            "typography_fill_overflow_count", 0)),
        "visual_pixel_diff_count": pixel_diff_count,
    }
    return {
        "page_key": page_key,
        "doc": fixture["doc"], "page": fixture["page"],
        "frozen_l0_html": str(base_path.resolve()),
        "old_final_html": str((page_dir / "zh_visual.html").resolve()),
        "old_final_pdf_sha256": _sha256(page_dir / "zh_visual.pdf"),
        "new_fixture_pdf_sha256": _sha256(pdf_path),
        "translation_api_call_count": 0,
        "typography_seconds": round(typography_seconds, 6),
        "formal_pdf_print_seconds": round(formal_print_seconds, 6),
        "formal_pdf_print_count": 1,
        "typography_fast_path": fast_trace,
        "metrics": metrics,
        "source_text_slot_geometry_unchanged": slot_mutations == 0,
        "hard_anchor_geometry_unchanged": hard_anchor_moved == 0,
        "fill_qa": fill_audit,
        "final_collision_qa": collision,
        "review_image": str(review_path.resolve()),
    }


def _write_report(before: list[dict[str, Any]], after: list[dict[str, Any]],
                  qa: dict[str, Any], performance: dict[str, Any]) -> None:
    lines = [
        "# fast-v02 - Typography Fast Path",
        "",
        "## Decision",
        "",
        "**FAST_V02 = %s**" % qa["decision"].upper(),
        "",
        "Only the frozen PPAT p011, 2504 p011, and 2504 p005 fixtures were "
        "used. No translation API, fresh document run, full-document QA, "
        "contact sheet, or candidate PDF was executed.",
        "",
        "## Architecture",
        "",
        "The old per-level L0 -> trial -> L1 -> trial loop was replaced by "
        "one persistent Chromium page. Round 1 batch-measures all role-policy "
        "Fit candidates. Round 2 validates the selected Fit state and "
        "batch-measures all eligible Fill candidates. The selected state is "
        "then printed once as the formal fixture PDF.",
        "",
        "SourceTextSlot coordinates and all formula, figure, and table anchor "
        "styles are treated as immutable signatures. No repack, block move, "
        "slot expansion, or page/text special case exists in production.",
        "",
        "## Performance and hard gates",
        "",
        "| fixture | old trials | old trial PDFs | new DOM rounds | browser "
        "launches | new trial PDFs | max correction | collision | overflow | "
        "slot mutation | anchor moved | pixel diff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    by_before = {row["page_key"]: row for row in before}
    timing = {row["page_key"]: row for row in performance.get("after") or []}
    for row in after:
        old = by_before[row["page_key"]]
        trace = row["typography_fast_path"]
        metrics = row["metrics"]
        lines.append(
            "| %(page)s | %(old_trials)d | %(old_pdfs)d | %(rounds)d | "
            "%(launches)d | %(new_pdfs)d | %(correction)d | %(collision)d | "
            "%(overflow)d | %(slot)d | %(anchor)d | %(pixel)d |" % {
                "page": row["page_key"],
                "old_trials": old["typography_trial_render_count"],
                "old_pdfs": old["typography_trial_pdf_print_count"],
                "rounds": trace["typography_measurement_round_count"],
                "launches": trace["browser_launch_count"],
                "new_pdfs": trace["typography_trial_pdf_print_count"],
                "correction": trace["typography_correction_count_max"],
                "collision": metrics["final_block_collision_count"],
                "overflow": metrics["overflow_count"],
                "slot": metrics["slot_geometry_mutation_count"],
                "anchor": metrics["hard_anchor_moved_count"],
                "pixel": metrics["visual_pixel_diff_count"],
            })
    lines += [
        "",
        "## Targeted timing",
        "",
        "| fixture | frozen full-page wall clock | fast Typography | one "
        "formal PDF print |",
        "|---|---:|---:|---:|",
    ]
    for row in after:
        old = by_before[row["page_key"]]
        current = timing[row["page_key"]]
        lines.append(
            "| %s | %.3fs | %.3fs | %.3fs |" % (
                row["page_key"], old["frozen_page_seconds"],
                current["typography_seconds"],
                current["formal_pdf_print_seconds"]))
    lines += [
        "",
        "The new numbers are targeted fixture timings, not a claim about a "
        "fresh full production page. They isolate the optimized Typography "
        "path and its one formal fixture print.",
        "",
        "## Result",
        "",
        "- typography_correction_count <= 1 for every block: %s"
        % ("PASS" if qa["metrics"][
            "typography_correction_count_gt1"] == 0 else "FAIL"),
        "- typography_trial_pdf_print_count = %d"
        % qa["metrics"]["typography_trial_pdf_print_count"],
        "- typography_measurement_round_count <= 2: %s"
        % ("PASS" if qa["metrics"][
            "after_typography_measurement_round_count_max"] <= 2 else "FAIL"),
        "- slot_geometry_mutation_count = %d"
        % qa["metrics"]["slot_geometry_mutation_count"],
        "- final_block_collision_count = %d"
        % qa["metrics"]["final_block_collision_count"],
        "- hard_anchor_moved_count = %d"
        % qa["metrics"]["hard_anchor_moved_count"],
        "- overflow_count = %d" % qa["metrics"]["overflow_count"],
        "- visual_pixel_diff_count = %d"
        % qa["metrics"]["visual_pixel_diff_count"],
        "- production_special_case_count = 0",
        "",
        "The three review images contain SOURCE | OLD | FAST-V02 at identical "
        "page scale. No full-paper run was started.",
    ]
    (OUT / "FAST_TYPOGRAPHY_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    REVIEW.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    baseline = _baseline_rows()
    before = []
    for fixture in FIXTURES:
        row = frozen_before_record(
            fixture["page_key"], VISUAL / fixture["page_key"])
        row["frozen_page_seconds"] = float(
            baseline[fixture["page_key"]]["elapsed_seconds"])
        before.append(row)
    before_qa = {
        "schema_version": "fast.v02.typography_fast_path_red.v1",
        "decision": "red_confirmed" if all(row["qa_red"] for row in before)
        else "blocked",
        "records": before,
        "metrics": {
            "legacy_typography_trial_render_count": sum(
                row["typography_trial_render_count"] for row in before),
            "legacy_typography_trial_pdf_print_count": sum(
                row["typography_trial_pdf_print_count"] for row in before),
        },
    }
    if before_qa["decision"] != "red_confirmed":
        raise RuntimeError("frozen slow-page QA did not reproduce legacy red")

    after = [_run_fixture(fixture) for fixture in FIXTURES]
    qa = audit_fast_pages(before, after)
    performance = {
        "schema_version": "fast.v02.performance_comparison.v1",
        "before": [{
            "page_key": row["page_key"],
            "frozen_page_seconds": row["frozen_page_seconds"],
            "typography_trial_render_count": row[
                "typography_trial_render_count"],
            "typography_trial_pdf_print_count": row[
                "typography_trial_pdf_print_count"],
        } for row in before],
        "after": [{
            "page_key": row["page_key"],
            "typography_seconds": row["typography_seconds"],
            "formal_pdf_print_seconds": row["formal_pdf_print_seconds"],
            "typography_measurement_round_count": row[
                "typography_fast_path"][
                    "typography_measurement_round_count"],
            "browser_launch_count": row["typography_fast_path"][
                "browser_launch_count"],
            "typography_trial_pdf_print_count": row[
                "typography_fast_path"][
                    "typography_trial_pdf_print_count"],
        } for row in after],
        "scope_note": (
            "New timing is targeted frozen-fixture Typography plus one formal "
            "fixture PDF print, not a full production-page wall clock."),
    }
    before_after = {
        "schema_version": "fast.v02.typography_before_after.v1",
        "before_qa": before_qa,
        "after_qa": qa,
        "pages": [{
            "page_key": row["page_key"],
            "source_text_slot_geometry_unchanged": row[
                "source_text_slot_geometry_unchanged"],
            "hard_anchor_geometry_unchanged": row[
                "hard_anchor_geometry_unchanged"],
            "metrics": row["metrics"],
            "old_final_pdf_sha256": row["old_final_pdf_sha256"],
            "new_fixture_pdf_sha256": row["new_fixture_pdf_sha256"],
            "typography_fast_path": row["typography_fast_path"],
            "review_image": row["review_image"],
        } for row in after],
    }
    _dump(OUT / "typography_before_after.json", before_after)
    _dump(OUT / "performance_comparison.json", performance)
    _write_report(before, after, qa, performance)
    print(json.dumps({"decision": qa["decision"],
                      "metrics": qa["metrics"]},
                     ensure_ascii=False, indent=2))
    return 0 if qa["decision"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
