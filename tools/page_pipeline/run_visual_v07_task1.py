# -*- coding: utf-8 -*-
"""visual-v07 Task 1 checkpoint: table-cell translation closure only."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent
for path in (HERE, REPO / "tools" / "table_html_render",
             REPO / "实现源码" / "pdf_translator"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import run_visual_v06_checkpoint as v06  # noqa: E402
from table_cell_translation_qa import table_cell_translation_qa  # noqa: E402


OUT = REPO / "outputs" / "visual_v07_task1"
REVIEW = OUT / "review_bundle"


def _load(path: str | Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _dump(path: str | Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _page_model(doc_key: str, page: int) -> Path:
    return v06.DOCS[doc_key]["src"] / "pages" / ("p%03d" % page) \
        / "stitched_page_model.json"


def _translation_path(doc_key: str, page: int) -> Path:
    return v06.DOCS[doc_key]["src"] / "pages" / ("p%03d" % page) \
        / "translation.json"


def _table_count(model: dict) -> int:
    return sum(1 for region in model.get("regions") or []
               if region.get("type") == "table" and region.get("payload"))


def _find_problem_fixture() -> tuple[str, int, dict]:
    """Find the frozen v06 table page with the strongest old-red result."""
    candidates = []
    frozen = REPO / "outputs" / "visual_v06_checkpoint"
    for pdf in sorted(frozen.glob("*_p*/zh_visual.pdf")):
        match = re.fullmatch(r"(.+)_p(\d+)", pdf.parent.name)
        if not match or match.group(1) not in v06.DOCS:
            continue
        doc_key, page = match.group(1), int(match.group(2))
        model = _load(_page_model(doc_key, page), {})
        if not _table_count(model):
            continue
        qa = table_cell_translation_qa(
            model, final_pdf_path=pdf,
            translations=_load(_translation_path(doc_key, page), {}))
        untranslated = qa["metrics"]["untranslated_required_table_cell_count"]
        cell_count = sum(len(region["payload"].get("cells") or [])
                         for region in model.get("regions") or []
                         if region.get("type") == "table"
                         and region.get("payload"))
        # The reported problem is the table whose cell population is broadly
        # untranslated, not merely the largest table by absolute cell count.
        score = untranslated / max(cell_count, 1)
        candidates.append((score, untranslated, doc_key, page, qa))
    if not candidates:
        raise RuntimeError("no frozen visual-v06 table fixture found")
    _, _, doc_key, page, qa = max(candidates,
                                  key=lambda item: (item[0], item[1]))
    return doc_key, page, qa


def _find_additional_fixture(excluded: set[tuple[str, int]]) -> tuple[str, int] | None:
    """Automatically choose one existing table fixture beyond A + p013."""
    candidates = []
    for doc_key, info in sorted(v06.DOCS.items()):
        for page_dir in sorted((info["src"] / "pages").glob("p[0-9][0-9][0-9]")):
            page = int(page_dir.name[1:])
            if (doc_key, page) in excluded:
                continue
            model = _load(page_dir / "stitched_page_model.json", {})
            if _table_count(model):
                candidates.append((doc_key, page))
    return candidates[0] if candidates else None


def _render_pdf_page(pdf_path: str | Path, out_path: str | Path,
                     page_index: int = 0, scale: float = 2.0) -> None:
    doc = pymupdf.open(str(pdf_path))
    try:
        pix = doc[page_index].get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                        alpha=False)
        pix.save(str(out_path))
    finally:
        doc.close()


def _before_after(left_path: Path, right_path: Path, out_path: Path) -> None:
    left = Image.open(left_path).convert("RGB")
    right = Image.open(right_path).convert("RGB")
    header = 42
    canvas = Image.new("RGB", (left.width + right.width,
                                max(left.height, right.height) + header), "white")
    canvas.paste(left, (0, header))
    canvas.paste(right, (left.width, header))
    draw = ImageDraw.Draw(canvas)
    draw.text((16, 12), "visual-v06 BEFORE", fill=(160, 0, 0))
    draw.text((left.width + 16, 12), "visual-v07 TASK 1 AFTER",
              fill=(0, 120, 30))
    canvas.save(out_path)


def _overlay(after_path: Path, qa: dict, out_path: Path,
             page_width: float, page_height: float) -> None:
    image = Image.open(after_path).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    sx, sy = image.width / page_width, image.height / page_height
    try:
        font = ImageFont.truetype("arial.ttf", 11)
    except OSError:
        font = ImageFont.load_default()
    for cell in qa.get("cells") or []:
        bbox = cell.get("bbox") or []
        if len(bbox) != 4:
            continue
        if cell.get("overflow") or cell.get("wrong_owner"):
            color = (255, 140, 0, 230)       # orange
        elif cell.get("target_missing"):
            color = (150, 40, 190, 230)      # purple
        elif cell.get("untranslated_required"):
            color = (220, 30, 30, 230)       # red
        else:
            color = (20, 175, 70, 230)       # green
        xy = [bbox[0] * sx, bbox[1] * sy, bbox[2] * sx, bbox[3] * sy]
        draw.rectangle(xy, outline=color, width=3)
        label = "r%d c%d req=%s %s" % (
            cell["row_index"], cell["col_index"],
            "T" if cell["translation_required"] else "F",
            cell.get("render_source") or "unknown")
        tx, ty = xy[0] + 2, max(0, xy[1] - 13)
        text_box = draw.textbbox((tx, ty), label, font=font)
        draw.rectangle(text_box, fill=(255, 255, 255, 205))
        draw.text((tx, ty), label, fill=color, font=font)
    image.save(out_path)


def _source_structure(model: dict) -> list[dict]:
    structures = []
    for region in model.get("regions") or []:
        table = region.get("payload")
        if region.get("type") != "table" or not table:
            continue
        structures.append({
            "table_id": region.get("region_id"),
            "row_count": len(table.get("rows") or []),
            "col_count": len(table.get("columns") or []),
            "cell_count": len(table.get("cells") or []),
            "horizontal_ruling_count": sum(
                rule.get("orientation") == "horizontal"
                for rule in table.get("rules") or []),
            "vertical_ruling_count": sum(
                rule.get("orientation") == "vertical"
                for rule in table.get("rules") or []),
        })
    return structures


def _production_diff_audit() -> dict:
    production_files = [
        REPO / "tools" / "page_pipeline" / "table_cell_translation.py",
        REPO / "tools" / "table_html_render" / "render_zh.py",
    ]
    forbidden = ["Table 5", "Score 1", "Description Relevance",
                 "2504.05732v2.pdf", "page == 16", "page_idx == 15"]
    findings = []
    for path in production_files:
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                findings.append({"file": str(path.relative_to(REPO)),
                                 "pattern": needle})
    return {
        "schema_version": "visual_v07.production_diff_audit.v1",
        "audited_files": [str(path.relative_to(REPO))
                          for path in production_files],
        "production_special_case_count": len(findings),
        "findings": findings,
        "decision": "pass" if not findings else "fail",
    }


def _write_review_bundle(problem: tuple[str, int], p013: tuple[str, int],
                         results: dict[tuple[str, int], dict], old_red: dict) -> None:
    REVIEW.mkdir(parents=True, exist_ok=True)
    problem_doc, problem_page = problem
    problem_result = results[problem]
    before_pdf = REPO / "outputs" / "visual_v06_checkpoint" \
        / ("%s_p%03d" % problem) / "zh_visual.pdf"
    after_pdf = OUT / ("%s_p%03d" % problem) / "zh_visual.pdf"
    source_pdf = Path(v06.DOCS[problem_doc]["pdf"])
    source_png = REVIEW / "table_problem_source.png"
    before_png = REVIEW / "table_problem_v06_before.png"
    after_png = REVIEW / "table_problem_task1_after.png"
    _render_pdf_page(source_pdf, source_png, problem_page - 1)
    _render_pdf_page(before_pdf, before_png)
    _render_pdf_page(after_pdf, after_png)
    _before_after(before_png, after_png,
                  REVIEW / "table_problem_before_after.png")
    with pymupdf.open(str(after_pdf)) as doc:
        width, height = float(doc[0].rect.width), float(doc[0].rect.height)
    _overlay(after_png, problem_result["table_cell_translation_qa"],
             REVIEW / "table_cell_translation_overlay.png", width, height)

    p013_doc, p013_page = p013
    _render_pdf_page(v06.DOCS[p013_doc]["pdf"],
                     REVIEW / "2504_p013_source.png", p013_page - 1)
    _render_pdf_page(OUT / ("%s_p%03d" % p013) / "zh_visual.pdf",
                     REVIEW / "2504_p013_task1_after.png")

    entries = [
        {"file": "table_problem_source.png", "kind": "source",
         "fixture": "%s:p%d" % problem},
        {"file": "table_problem_v06_before.png", "kind": "frozen_before",
         "old_red_untranslated": old_red["metrics"]
         ["untranslated_required_table_cell_count"]},
        {"file": "table_problem_task1_after.png", "kind": "task1_after",
         "decision": problem_result["table_cell_translation_qa"]["decision"]},
        {"file": "table_problem_before_after.png", "kind": "comparison"},
        {"file": "table_cell_translation_overlay.png", "kind": "qa_overlay",
         "legend": {"green": "translated cell verified",
                    "red": "required cell still source",
                    "purple": "target missing",
                    "orange": "overflow or wrong owner"}},
        {"file": "2504_p013_source.png", "kind": "source",
         "fixture": "%s:p%d" % p013},
        {"file": "2504_p013_task1_after.png", "kind": "task1_after",
         "fixture": "%s:p%d" % p013},
    ]
    _dump(REVIEW / "review_index.json", {
        "schema_version": "visual_v07.task1.review_index.v1",
        "flat": True, "entries": entries,
    })
    (REVIEW / "REVIEW_README.md").write_text(
        "# visual-v07 Task 1 review bundle\n\n"
        "This flat bundle compares the automatically detected visual-v06 "
        "table-cell failure with the Task 1 result.\n\n"
        "Overlay legend: green = verified translated cell; red = required "
        "cell still source; purple = target missing; orange = overflow or "
        "wrong owner. Every box is labelled with row, column, requirement, "
        "and render source.\n", encoding="utf-8")


def _write_report(problem: tuple[str, int], fixtures: list[tuple[str, int]],
                  results: dict[tuple[str, int], dict], old_red: dict,
                  structure: dict, audit: dict) -> None:
    aggregate = {
        "required_table_cell_count": 0,
        "translated_table_cell_count": 0,
        "untranslated_required_table_cell_count": 0,
        "table_cell_target_missing_count": 0,
        "table_cell_source_residual_count": 0,
        "table_cell_duplicate_render_count": 0,
        "table_cell_source_target_double_render_count": 0,
        "table_cell_overflow_count": 0,
        "table_cell_wrong_owner_count": 0,
    }
    api_calls = translated = cache_hits = 0
    provider_cells = []
    page_qas = []
    for fixture in fixtures:
        result = results[fixture]
        qa = result["table_cell_translation_qa"]
        page_qas.append({"doc": fixture[0], "page": fixture[1], **qa})
        for key in aggregate:
            aggregate[key] += int(qa["metrics"].get(key, 0))
        tm = result["table_translation"]
        api_calls += int(tm["table_translation_api_calls"])
        translated += int(tm["translated_cell_count"])
        cache_hits += int(tm["cache_hit_cell_count"])
        provider_cells.extend(
            {"doc": fixture[0], "page": fixture[1],
             "cell_id": cell["cell_id"]}
            for cell in qa["cells"]
            if cell["translation_route"] == "translation_provider")
    decision = "pass" if all(
        value == 0 for key, value in aggregate.items()
        if key not in ("required_table_cell_count",
                       "translated_table_cell_count")) \
        and audit["production_special_case_count"] == 0 \
        and structure["decision"] == "pass" else "blocked"
    _dump(OUT / "table_cell_translation_qa.json", {
        "schema_version": "visual_v07.task1.aggregate_qa.v1",
        "decision": decision, "metrics": aggregate, "pages": page_qas,
        "table_translation_api_calls": api_calls,
        "translated_cell_count": translated,
        "cache_hit_cell_count": cache_hits,
    })
    problem_label = "%s:p%d" % problem
    fixture_labels = ["%s:p%d" % fixture for fixture in fixtures]
    p013_ok = any(
        after == {"table_id": after["table_id"], "row_count": 21,
                  "col_count": 5, "cell_count": 105,
                  "horizontal_ruling_count": 3,
                  "vertical_ruling_count": 0}
        for entry in structure["fixtures"] if entry["page"] == 13
        for after in entry["after"])
    report = f"""# visual-v07 Task 1 — Table Cell Translation Closure

Decision: **{decision.upper()}**

## Scope and checkpoint

- Problem fixture (automatically detected from frozen visual-v06): `{problem_label}`.
- Executed fixtures: {', '.join(f'`{label}`' for label in fixture_labels)}.
- QA-first old red: required={old_red['metrics']['required_table_cell_count']}, untranslated={old_red['metrics']['untranslated_required_table_cell_count']}, target missing={old_red['metrics']['table_cell_target_missing_count']}, source residual={old_red['metrics']['table_cell_source_residual_count']}.
- Final aggregate: required={aggregate['required_table_cell_count']}, translated={aggregate['translated_table_cell_count']}, untranslated={aggregate['untranslated_required_table_cell_count']}.

## Required answers

1. **Why did captions translate while cells did not?** Captions are logical paragraphs and consumed `translation.json`; table cells live in the TableModel payload, remained `pending`, and the locked renderer therefore selected `source_text`.
2. **Where were cells excluded?** At required-translation item collection/consumption in the visual checkpoint: paragraph targets were loaded, but LogicalCells were neither classified as required nor back-filled before `build_zh_html`.
3. **Did LogicalCell already exist?** Yes. Existing cell ids, row/column ownership, `layout_bbox`, baselines, and span text were reused.
4. **Was existing table geometry reused?** Yes. No OCR or reconstruction was performed; table bbox, rows, columns, rulings, and anchors were not changed.
5. **Which cells called the API?** {json.dumps(provider_cells, ensure_ascii=False)}
6. **Table API call count:** {api_calls}.
7. **Cache-hit cell count:** {cache_hits}.
8. **Table-cell source residual:** {aggregate['table_cell_source_residual_count']}.
9. **Cell overflow:** {aggregate['table_cell_overflow_count']}.
10. **p013 21×5 / 105 / 3H / 0V preserved:** {'yes' if p013_ok else 'no'}.
11. **Production special cases:** {audit['production_special_case_count']}.

## Hard metrics

```json
{json.dumps(aggregate, ensure_ascii=False, indent=2)}
```

The task stopped at table-cell translation closure. Body collision, geometry fidelity, flow changes, and visual-v07 Task 2 were not attempted.
"""
    (OUT / "TABLE_CELL_TRANSLATION_REPORT.md").write_text(report,
                                                           encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    problem_doc, problem_page, old_red = _find_problem_fixture()
    problem = (problem_doc, problem_page)
    _dump(OUT / "old_red_evidence.json", old_red)
    if old_red["metrics"]["untranslated_required_table_cell_count"] <= 0:
        print("STOP: new QA did not reproduce frozen visual-v06 red")
        return 2

    p013 = ("2504", 13)
    fixtures = [problem]
    if p013 not in fixtures:
        fixtures.append(p013)
    additional = _find_additional_fixture(set(fixtures))
    if additional is not None:
        fixtures.append(additional)

    # Reuse the frozen visual runner, changing only its output root and the
    # newly integrated cell-translation closure.
    v06.OUT = OUT
    partitions = {}
    for doc_key in sorted({fixture[0] for fixture in fixtures}):
        pages = sorted(int(path.name[1:])
                       for path in (v06.DOCS[doc_key]["src"] / "pages").iterdir()
                       if path.is_dir() and path.name.startswith("p"))
        partitions[doc_key] = v06.build_document_partition(doc_key, pages)["targets"]

    results = {}
    for doc_key, page in fixtures:
        out_dir = OUT / ("%s_p%03d" % (doc_key, page))
        out_dir.mkdir(parents=True, exist_ok=True)
        print("[visual-v07-task1] %s p%03d" % (doc_key, page), flush=True)
        results[(doc_key, page)] = v06.render_visual_page(
            doc_key, page, out_dir, fragment_targets=partitions.get(doc_key),
            dry_run=False)

    structure_fixtures = []
    for fixture in fixtures:
        source = _source_structure(_load(_page_model(*fixture), {}))
        after = results[fixture].get("table_structure") or []
        structure_fixtures.append({
            "doc": fixture[0], "page": fixture[1],
            "source": source, "after": after,
            "preserved": source == after,
        })
    structure = {
        "schema_version": "visual_v07.table_structure_regression.v1",
        "decision": ("pass" if all(entry["preserved"]
                                   for entry in structure_fixtures)
                     else "fail"),
        "fixtures": structure_fixtures,
        "additional_fixture_selected": (
            {"doc": additional[0], "page": additional[1]}
            if additional else None),
    }
    _dump(OUT / "table_structure_regression.json", structure)
    audit = _production_diff_audit()
    _dump(OUT / "production_diff_audit.json", audit)
    _write_review_bundle(problem, p013, results, old_red)
    _write_report(problem, fixtures, results, old_red, structure, audit)
    aggregate = _load(OUT / "table_cell_translation_qa.json", {})
    print("decision:", aggregate.get("decision"))
    return 0 if aggregate.get("decision") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
