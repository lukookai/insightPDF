# -*- coding: utf-8 -*-
"""Run existing 4C/4D.1 hard gates on isolated balanced mock pages."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PIPELINE = REPO / "tools" / "page_pipeline"
for path in (PIPELINE, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from physical_pdf_qa import physical_pdf_qa  # noqa: E402
from layout_grid_qa import layout_grid_qa  # noqa: E402
from rendered_region_collision_qa import rendered_region_collision_qa  # noqa: E402
from formula_exclusivity_qa import formula_exclusivity_qa  # noqa: E402
from formula_crop_qa import formula_crop_qa  # noqa: E402
from structural_paragraph_qa import structural_paragraph_qa  # noqa: E402
from residual_source_language_qa import residual_source_language_qa  # noqa: E402
from front_matter import classify_front_matter  # noqa: E402


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _flow_enclosures(flows: list[dict[str, Any]]) -> dict[str, list[float]]:
    result = {}
    for flow in flows or []:
        for item in flow.get("items", []):
            if item.get("kind") != "formula":
                continue
            dy = float(item.get("flow_y") or 0) - float(item.get("anchor_y") or 0)
            for member in item.get("row_members", []) or []:
                bbox = [float(v) for v in member["bbox"]]
                result[member["formula_id"]] = [bbox[0], bbox[1] + dy,
                                                 bbox[2], bbox[3] + dy]
    return result


def _pdf_object_boxes(pdf_path: str | Path) -> dict[str, list[list[float]]]:
    doc = pymupdf.open(str(pdf_path))
    page = doc[0]
    result = {
        "images": [[round(rect.x0, 3), round(rect.y0, 3), round(rect.x1, 3), round(rect.y1, 3)]
                   for info in page.get_images(full=True) for rect in page.get_image_rects(info[0])],
        "page_size": [round(page.rect.width, 3), round(page.rect.height, 3)],
    }
    doc.close()
    return result


def _pdf_font_inventory(pdf_path: str | Path) -> list[dict[str, Any]]:
    """Audit the sandbox output's actual embedded PDF fonts."""
    counts = Counter()
    doc = pymupdf.open(str(pdf_path))
    try:
        for block in doc[0].get_text("rawdict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = "".join(char.get("c") or "" for char in span.get("chars", []))
                    script = ("CJK" if any("\u3400" <= char <= "\u9fff" for char in text)
                              else "Latin" if any(char.isascii() and char.isalpha() for char in text)
                              else "other")
                    counts[(span.get("font") or "", script,
                            round(float(span.get("size") or 0), 4))] += 1
    finally:
        doc.close()
    return [{"actual_pdf_font": font, "script": script, "font_size_pt": size,
             "span_count": count}
            for (font, script, size), count in counts.most_common()]


def _dom_geometry(snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        kind: [{"id": row.get("formula_id") or row.get("region_id") or row.get("segment_id"),
                "bbox": [round(v, 3) for v in row.get("bbox_pt", [])]}
               for row in snapshot.get(kind, [])]
        for kind in ("inline_formula", "display_formula", "figure", "table_cell")
    }


def _geometry_delta(current: dict[str, Any], mock: dict[str, Any]) -> dict[str, Any]:
    deltas = []
    counts = {}
    for kind in ("inline_formula", "display_formula", "figure", "table_cell"):
        left, right = current.get(kind, []), mock.get(kind, [])
        counts[kind] = {"current": len(left), "mock": len(right)}
        for index, (a, b) in enumerate(zip(left, right)):
            if a.get("id") != b.get("id") and kind != "table_cell":
                deltas.append({"kind": kind, "index": index,
                               "reason": "id_changed", "current": a, "mock": b})
                continue
            delta = max((abs(float(x) - float(y)) for x, y in zip(a["bbox"], b["bbox"])), default=0.0)
            if delta > .08:
                deltas.append({"kind": kind, "index": index,
                               "id": a.get("id"), "max_bbox_delta_pt": round(delta, 4),
                               "current_bbox": a["bbox"], "mock_bbox": b["bbox"]})
    return {
        "counts": counts,
        "geometry_change_count": len(deltas),
        "geometry_change_details": deltas[:80],
        "formula_bbox_unchanged": not any(d["kind"] in {"inline_formula", "display_formula"} for d in deltas),
        "figure_bbox_unchanged": not any(d["kind"] == "figure" for d in deltas),
        "table_cell_bbox_unchanged": not any(d["kind"] == "table_cell" for d in deltas),
    }


def run_mock_page_qa(*, page_number: int, mock_pdf: str | Path,
                     mock_html: str | Path, mock_manifest: dict[str, Any],
                     current_manifest: dict[str, Any], model_path: str | Path,
                     baseline_qa_path: str | Path, grid_path: str | Path,
                     out_dir: str | Path) -> dict[str, Any]:
    out_dir = Path(out_dir)
    page_dir = out_dir / f"p{page_number:03d}"
    page_dir.mkdir(parents=True, exist_ok=True)
    model = _load(model_path)
    baseline = _load(baseline_qa_path)
    grid = _load(grid_path)
    flows = baseline.get("flows") or []
    frontmatter = None
    if page_number == 1:
        # The accepted 4D.1C LayoutGridQA explicitly exempts the frozen
        # Title/Author/Affiliation full-width bands.  Omitting this model
        # falsely labels the unchanged author row as a gutter intrusion.
        frontmatter = classify_front_matter(
            str(REPO / "runs" / "diag_src_2504.pdf"), page_number - 1, grid)
    physical = physical_pdf_qa(mock_pdf, model,
                               expected_physical_page_count=1, out_dir=page_dir,
                               output_filename="physical_pdf_qa.json")
    layout = layout_grid_qa(mock_pdf, grid=grid, out_dir=page_dir,
                            page_label=f"p{page_number:03d}_mock",
                            frontmatter=frontmatter)
    collision = rendered_region_collision_qa(
        mock_pdf, mock_html, page_model=model, out_dir=page_dir,
        page_label=f"p{page_number:03d}_mock", flows=flows)
    exclusivity = formula_exclusivity_qa(
        model, mock_pdf, out_dir=page_dir,
        flow_enclosures=_flow_enclosures(flows))
    crop = formula_crop_qa(model, mock_pdf, out_dir=page_dir)
    structural = structural_paragraph_qa(model, page_height=model.get("height"))
    _dump(page_dir / "paragraph_structural_qa.json", structural)
    residual = residual_source_language_qa(model, mock_pdf, out_dir=page_dir)
    geometry = _geometry_delta(_dom_geometry(current_manifest),
                               _dom_geometry(mock_manifest["dom_snapshot"]))
    # Figure/table/formula counts must also match even if zip() has no delta.
    count_mismatch = any(value["current"] != value["mock"]
                         for value in geometry["counts"].values())
    geometry["object_count_mismatch"] = count_mismatch
    geometry["geometry_gate_passed"] = geometry["geometry_change_count"] == 0 and not count_mismatch

    hard = {
        "physical_pdf_qa": bool(physical.get("all_assertions_passed")),
        "layout_grid_qa": bool(layout.get("hard_gate_passed")),
        "rendered_region_collision_qa": bool(collision.get("collision_gate_passed")),
        "formula_exclusivity_qa": bool(exclusivity.get("formula_exclusivity_passed")),
        "formula_crop_qa": bool(crop.get("formula_crop_clean")),
        "paragraph_structural_qa": bool(structural.get("structural_clean")),
        "residual_language_qa": bool(residual.get("residual_clean")),
        "geometry_freeze_qa": bool(geometry.get("geometry_gate_passed")),
    }
    result = {
        "page": page_number,
        "mock_pdf": str(Path(mock_pdf).resolve()),
        "hard_gates": hard,
        "hard_gate_regression_count": sum(not value for value in hard.values()),
        "all_existing_hard_gates_passed": all(hard.values()),
        "physical_pdf_qa": physical,
        "layout_grid_qa": layout,
        "rendered_region_collision_qa": collision,
        "formula_exclusivity_qa": exclusivity,
        "formula_crop_qa": crop,
        "paragraph_structural_qa": structural,
        "residual_language_qa": residual,
        "geometry_freeze_qa": geometry,
        "mock_actual_pdf_font_inventory": _pdf_font_inventory(mock_pdf),
        "acceptance_counts": {
            "physical_page_count": physical.get("physical_pdf_page_count"),
            "page_count_change": int(physical.get("physical_pdf_page_count") != 1),
            "gutter_intrusion": layout.get("gutter_intrusion_count", 0),
            "text_formula_collision": collision.get("rendered_text_formula_collision_count", 0),
            "formula_crop_failure": crop.get("formula_crop_violation_count", 0),
            "table_failure": collision.get("rendered_text_table_collision_count", 0),
            "figure_missing": int(geometry["counts"]["figure"]["current"]
                                  != geometry["counts"]["figure"]["mock"]),
        },
    }
    _dump(page_dir / "mock_page_qa.json", result)
    return result


def aggregate_mock_qa(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter()
    for record in records:
        counts.update(record["acceptance_counts"])
    regression = sum(record["hard_gate_regression_count"] for record in records)
    return {
        "schema_version": "phase4d2a.typography_mock_qa.v1",
        "analysis_pages": [record["page"] for record in records],
        "mock_page_count": len(records),
        "existing_4c_gate_regression": regression,
        "existing_4d1_gate_regression": regression,
        "gutter_intrusion": counts["gutter_intrusion"],
        "text_formula_collision": counts["text_formula_collision"],
        "formula_crop_failure": counts["formula_crop_failure"],
        "table_failure": counts["table_failure"],
        "figure_missing": counts["figure_missing"],
        "page_count_change": counts["page_count_change"],
        "all_existing_hard_gates_passed": regression == 0,
        "records": records,
    }
