# -*- coding: utf-8 -*-
"""PageCapacityQA (Phase 4E.1B) -- report-only.

Measures per-column available-flow intervals vs required text height from the
existing PageModel / grid / flows (no re-render), and detects physical page
expansion in preserve_source_pagination mode.
"""
from __future__ import annotations

import json
from pathlib import Path

from page_capacity import column_capacity, classify_complexity


def _obstacle_boxes(model):
    out = []
    for r in model.get("regions", []):
        if r.get("type") in ("figure", "table", "image", "formula"):
            b = r.get("bbox")
            if b:
                out.append([float(v) for v in b])
    return out


def _required_text_height(flows, column):
    total = 0.0
    for flow in flows or []:
        if flow.get("column") != column:
            continue
        for item in flow.get("items", []):
            if item.get("kind") == "paragraph":
                total += float(item.get("est_height", 0.0) or 0.0)
    return total


def page_capacity_profile(page_no, model, grid, flows, physical_pages,
                          semantic_role="normal_body") -> dict:
    cols = (grid or {}).get("columns") or []
    obstacles = _obstacle_boxes(model)
    capacities = []
    for col in cols:
        req = _required_text_height(flows, col.get("column_id"))
        capacities.append(column_capacity(col, grid, obstacles, req))
    min_ratio = min((c["capacity_ratio"] for c in capacities), default=1.0)
    unresolved = physical_pages > 1
    return {
        "page": page_no,
        "layout_class": classify_complexity(model.get("regions", []),
                                            semantic_role),
        "physical_pages": physical_pages,
        "capacity_unresolved": unresolved,
        "min_capacity_ratio": min_ratio,
        "columns": capacities,
    }


def page_capacity_qa(pages: list[int], models: dict[int, dict],
                     grids: dict[int, dict], flows: dict[int, list],
                     physical: dict[int, int],
                     semantic_roles: dict[int, str] | None = None,
                     out_dir=None) -> dict:
    semantic_roles = semantic_roles or {}
    per_page = {}
    unresolved_count = 0
    expansion = 0
    for pno in pages:
        prof = page_capacity_profile(pno, models.get(pno, {}),
                                     grids.get(pno, {}),
                                     flows.get(pno, []),
                                     physical.get(pno, 1),
                                     semantic_roles.get(pno, "normal_body"))
        per_page[pno] = prof
        if prof["capacity_unresolved"]:
            unresolved_count += 1
            expansion += physical.get(pno, 1) - 1
    result = {
        "schema_version": "phase4e1b.page_capacity_qa.v1",
        "source_logical_page_count": len(pages),
        "final_physical_page_count": sum(physical.get(p, 1) for p in pages),
        "unexpected_page_expansion_count": expansion,
        "capacity_unresolved_count": unresolved_count,
        "capacity_unresolved_pages": [p for p, pr in per_page.items()
                                      if pr["capacity_unresolved"]],
        "per_page": per_page,
        "hard": {"unexpected_page_expansion_count": expansion,
                 "capacity_unresolved_count": unresolved_count},
        "decision": ("pass" if expansion == 0 and unresolved_count == 0
                     else "fail"),
    }
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "page_capacity_qa.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
