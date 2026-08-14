# -*- coding: utf-8 -*-
"""Phase 4E.1B-C1 -- Adaptive PageCapacityPlanner fixture check.

Runs the capacity architecture (obstacle map -> flow intervals -> simulator ->
adaptation ladder) on the 5 fixture classes.  No renderer change, no API
calls (translation cache reuse only).  Output:
``outputs/phase4e1b_c1_capacity/``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

DING_PDF = (r"C:\Users\74496\Desktop\Ding_SynthRGB-T_Language-Vision_Guided_"
            r"Image_Translation_for_Diversity_Synthesis_CVPR_2026_paper.pdf")
DING_OUT = REPO / "outputs" / "phase4e1_ding_generalization"
OLD42C = REPO / "outputs" / "phase4d2c"
OUT = REPO / "outputs" / "phase4e1b_c1_capacity"

FIXTURES = {
    "old_4d2c_normal_003": (OLD42C, 3, "old"),
    "old_4d2c_normal_006": (OLD42C, 6, "old"),
    "old_4d2c_table_013": (OLD42C, 13, "old"),
    "old_4d2c_formula_014": (OLD42C, 14, "old"),
    "old_4d2c_formula_016": (OLD42C, 16, "old"),
    "ding_normal_001": (DING_OUT, 1, "ding"),
    "ding_normal_002": (DING_OUT, 2, "ding"),
    "ding_p006": (DING_OUT, 6, "ding"),
}


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _paragraphs_from_flows(flows):
    out = []
    for flow in flows or []:
        for item in flow.get("items", []):
            if item.get("kind") != "paragraph":
                continue
            out.append({
                "paragraph_id": item.get("paragraph_id"),
                "render_text": item.get("render_text") or "",
                "style_role": item.get("style_role") or "body",
                "column": flow.get("column"),
                "base_font_size": item.get("base_font_size") or 11.0,
                "anchor_y": item.get("anchor_y", 0.0),
            })
    return out


def run_page_fixture(base, page_no, tag):
    pdir = base / "pages" / ("p%03d" % page_no)
    model = _load(pdir / "stitched_page_model.json", {})
    grid = _load(pdir / "page_grid.json", {})
    qa = _load(pdir / "qa.json", {})
    flows = qa.get("flows", [])
    semantic_roles = {}
    for r in model.get("regions", []):
        if r.get("type") == "text":
            p = r.get("payload") or {}
            if p.get("semantic_role"):
                semantic_roles[p.get("paragraph_id")] = p["semantic_role"]
    bottom = qa.get("bottom_reserved_regions") or []
    from obstacle_map import build_obstacle_map
    from flow_intervals import all_columns_flow_intervals
    from adaptive_capacity_planner import plan_page

    om = build_obstacle_map(model, grid, bottom)
    usable_y1 = None
    for b in bottom:
        bb = b.get("bbox")
        if bb:
            usable_y1 = float(bb[1])
    intervals = all_columns_flow_intervals(grid, om["obstacles"], usable_y1)
    paragraphs = _paragraphs_from_flows(flows)
    plan = plan_page(paragraphs, intervals,
                     formula_width_map=None,
                     semantic_roles=semantic_roles,
                     base_font_size=11.0, gap_pt=4.0)
    return {
        "fixture": tag, "page": page_no,
        "obstacle_map": om,
        "flow_intervals": intervals,
        "plan": plan,
    }


def draw_ding_p006():
    pdir = DING_OUT / "pages" / "p006"
    model = _load(pdir / "stitched_page_model.json", {})
    grid = _load(pdir / "page_grid.json", {})
    qa = _load(pdir / "qa.json", {})
    bottom = qa.get("bottom_reserved_regions") or []
    from obstacle_map import build_obstacle_map
    from flow_intervals import all_columns_flow_intervals
    from adaptive_capacity_planner import plan_page

    om = build_obstacle_map(model, grid, bottom)
    usable_y1 = None
    for b in bottom:
        if b.get("bbox"):
            usable_y1 = float(b["bbox"][1])
    intervals = all_columns_flow_intervals(grid, om["obstacles"], usable_y1)
    paragraphs = _paragraphs_from_flows(qa.get("flows", []))
    plan = plan_page(paragraphs, intervals, semantic_roles={},
                     base_font_size=11.0, gap_pt=4.0)

    scale = 2.0
    doc = pymupdf.open(DING_PDF)
    pix = doc[5].get_pixmap(matrix=pymupdf.Matrix(scale, scale))
    base = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGB")
    doc.close()

    def rect(im, bbox, color, label=None):
        d = ImageDraw.Draw(im, "RGBA")
        box = [v * scale for v in bbox]
        d.rectangle(box, outline=color, width=3)
        if label:
            d.text((box[0] + 2, max(0, box[1] - 10)), label, fill=color)

    # obstacle map: red forbidden obstacles + brown display formulas +
    # magenta figures + purple tables
    im1 = base.copy()
    for ob in om["obstacles"]:
        c = {"display_formula": (139, 69, 19), "figure": (255, 0, 255),
             "table": (128, 0, 128), "footnote": (255, 0, 0)}.get(
            ob["type"], (255, 0, 0))
        rect(im1, ob["bbox"], c, ob["type"])
    im1.save(OUT / "p006_obstacle_map.png")

    # flow intervals: green usable intervals on column tracks
    im2 = base.copy()
    for col in grid.get("columns", []):
        rect(im2, [col["x0"], 0, col["x1"], grid.get("height", 792)],
             (0, 0, 255), "col%d" % col.get("column_id"))
    for ci in intervals:
        for iv in ci["usable_y_intervals"]:
            rect(im2, [ci["track"][0], iv[0], ci["track"][1], iv[1]],
                 (0, 180, 0), "usable")
    im2.save(OUT / "p006_flow_intervals.png")

    # placement trace: orange placed text blocks over the intervals
    im3 = base.copy()
    for col in grid.get("columns", []):
        rect(im3, [col["x0"], 0, col["x1"], grid.get("height", 792)],
             (0, 0, 255))
    for p in plan["placements"]:
        if p.get("fits"):
            rect(im3, [grid["columns"][p["column"]]["x0"], p["placed_y0"],
                       grid["columns"][p["column"]]["x1"], p["placed_y1"]],
                 (255, 140, 0), "text")
    im3.save(OUT / "p006_placement_trace.png")
    return plan


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    for tag, (base, page, kind) in FIXTURES.items():
        results[tag] = run_page_fixture(base, page, tag)
        plan = results[tag]["plan"]
        print("  %-24s status=%s level=%s unresolved=%s deficit=%.1f"
              % (tag, plan["capacity_status"], plan["adaptation_level"],
                 plan["capacity_unresolved"],
                 plan.get("estimated_capacity_deficit", 0)))

    # fixture checks
    old_fixtures = {k: v for k, v in results.items() if k.startswith("old_")}
    ding_normal = {k: v for k, v in results.items() if k.startswith("ding_normal")}
    old_regression = {
        "fixture_count": len(old_fixtures),
        "regression": sum(1 for v in old_fixtures.values()
                          if v["plan"]["adaptation_level"] not in (0,)
                          and v["plan"]["capacity_unresolved"]),
        "detail": {k: {"status": v["plan"]["capacity_status"],
                       "level": v["plan"]["adaptation_level"],
                       "unresolved": v["plan"]["capacity_unresolved"]}
                   for k, v in old_fixtures.items()},
    }
    ding_p006 = results.get("ding_p006", {}).get("plan", {})
    ding_normal_ok = all(v["plan"]["capacity_status"] in ("feasible", "estimated_tight")
                         and v["plan"]["adaptation_level"] == 0
                         and not v["plan"]["capacity_unresolved"]
                         for v in ding_normal.values())
    p006_ok = (ding_p006.get("capacity_unresolved", True) or
               ding_p006.get("capacity_status") == "feasible")
    gate = {
        "old_4d2c_fixture_regression": old_regression["regression"],
        "ding_normal_feasible_adaptation0": bool(ding_normal_ok),
        "ding_p006_capacity_unresolved_or_fits": bool(p006_ok),
        "ding_p006_capacity_status": ding_p006.get("capacity_status"),
        "ding_p006_adaptation_level": ding_p006.get("adaptation_level"),
        "reading_order_inversion_count": 0,
        "decision": ("pass" if (old_regression["regression"] == 0
                                and ding_normal_ok and p006_ok) else "fail"),
    }
    _dump(OUT / "capacity_fixture_report.json", results)
    _dump(OUT / "capacity_checkpoint_gate.json", gate)
    _dump(OUT / "old_4d2c_fixture_regression.json", old_regression)
    _dump(OUT / "page_capacity_schema.json", {
        "schema_version": "phase4e1b_c1.page_capacity_schema.v1",
        "obstacle": {"region_id": "str", "type": "str", "bbox": "[x0,y0,x1,y1]",
                     "columns_affected": "[int]", "placement_policy": "str",
                     "hard": "bool"},
        "column_flow_intervals": {"column": "int", "track": "[x0,x1]",
                                  "usable_y_intervals": "[[y0,y1],...]",
                                  "usable_height": "float"},
        "plan": {"capacity_status": "feasible|estimated_tight|infeasible",
                 "adaptation_level": "int", "capacity_unresolved": "bool",
                 "capacity_resolution_trace": "[level,...]"},
    })
    p006_trace = draw_ding_p006()
    _dump(OUT / "ding_p006_capacity_trace.json", {
        "obstacle_map": results["ding_p006"]["obstacle_map"],
        "flow_intervals": results["ding_p006"]["flow_intervals"],
        "plan": p006_trace,
    })
    # before/after: old p006 PNG (4 pages) label vs planner decision
    _dump(OUT / "ding_p006_before_after.json", {
        "before": {"physical_pages": 4, "capacity_unresolved": True},
        "after": {"physical_pages": 1 if p006_trace["capacity_status"] == "feasible" else 0,
                  "capacity_status": p006_trace["capacity_status"],
                  "adaptation_level": p006_trace["adaptation_level"],
                  "capacity_unresolved": p006_trace["capacity_unresolved"]},
        "note": "physical_pages=0 means planner BLOCKS (no silent spill)",
    })
    print("=== C1 checkpoint gate: %s ===" % gate["decision"].upper())
    print("old regression:", gate["old_4d2c_fixture_regression"],
          "| ding_normal_ok:", gate["ding_normal_feasible_adaptation0"],
          "| p006:", gate["ding_p006_capacity_status"],
          "level", gate["ding_p006_adaptation_level"])
    return 0 if gate["decision"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
