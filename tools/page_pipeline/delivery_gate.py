# -*- coding: utf-8 -*-
"""Defect/Complexity dual ranking + Document Delivery Gate (Phase 4C.1).

* defect_pages.json      - ONLY real failures are emitted; a QA-clean run is
                           exactly [] even when pages are visually complex.
* complexity_pages.json  - independent manual spot-check ranking using
                           formula/table/figure, capacity and layout signals.
                           Complexity != error.
* delivery_gate.json     - layered gate:
                           preflight / semantic_qa / ownership_qa /
                           structural_paragraph_qa / translation_qa / dom_qa /
                           physical_pdf_qa / renderer_parity ->
                           decision = pass | warning | blocked.
"""
from __future__ import annotations

import json
from pathlib import Path

import qa_execution as qx  # noqa: E402  (Phase 4E.2A fail-closed)

# ---------------------------------------------------------------- weights --
DEFECT_WEIGHTS = {
    "page_assertion_false": 5000,
    "physical_assertion_failure": 5000,
    "physical_page_mismatch": 5000,
    "overflow": 4000,
    "source_span_drop": 4000,
    "renderer_failure": 4000,
    "ownership_conflict": 3000,
    "dom_render_failure": 3000,
    "paragraph_structural_pollution": 3000,
    "table_overflow": 3000,
    "layout_overlap": 3000,
    "translation_failure": 3000,
    "formula_exclusivity_failure": 3000,
    "formula_crop_failure": 3000,
    "render_collision": 3000,
    "heading_duplicate": 3000,
    "residual_language": 3000,
    "residual_prose": 3000,
    "render_closure_failure": 3000,
    "footnote_collision": 3000,
    "grid_layout_failure": 3000,
    "post_obstacle_grid_failure": 3000,
    "visual_hard_gate_failure": 3000,
    "token_split": 2000,
    "blank_formula": 2000,
    "missing_figure": 2000,
}


def _first_count(mapping, *names):
    """Return the first present numeric count, without treating absence as 0."""
    for name in names:
        if name in mapping and mapping.get(name) is not None:
            try:
                return int(mapping.get(name))
            except (TypeError, ValueError):
                continue
    return None


def _is_blocked_layer(value):
    """Accept both compact and expanded visual-QA layer schemas."""
    if value is False:
        return True
    if isinstance(value, str):
        return value.lower() in {"blocked", "fail", "failed", "error"}
    if not isinstance(value, dict):
        return False
    status = str(value.get("status", "")).lower()
    if status in {"blocked", "fail", "failed", "error"}:
        return True
    for key in ("passed", "pass", "clean", "hard_passed",
                "hard_gate_passed"):
        if key in value and value.get(key) is False:
            return True
    return False

# ------------------------------------------------------- defect extraction --
def _defects_for_page(page_number, page_result, structural_qa=None):
    """Return list of {defect, weight, detail} for one page result."""
    defects = []
    qa = (page_result or {}).get("qa") or {}
    capacity = (page_result or {}).get("capacity") or {}
    physical = (page_result or {}).get("physical_qa") or {}
    typography = qa.get("typography") or {}
    structural_qa = structural_qa or (page_result or {}).get("structural_qa")

    def add(name, detail):
        defects.append({"defect": name,
                        "weight": DEFECT_WEIGHTS.get(name, 1000),
                        "detail": detail})

    if not qa.get("all_assertions_passed", True):
        add("page_assertion_false", "page-level assertions failed")
    if physical and physical.get("all_assertions_passed") is False:
        add("physical_assertion_failure",
            "blank=%s clipped=%s renderer_failures=%s" % (
                physical.get("blank_page_count"),
                physical.get("clipped_visual_region_count"),
                physical.get("renderer_specific_failure_count")))
    if physical.get("physical_pdf_page_count") != physical.get(
            "expected_physical_page_count"):
        add("physical_page_mismatch",
            "physical=%s expected=%s" % (
                physical.get("physical_pdf_page_count"),
                physical.get("expected_physical_page_count")))
    if (qa.get("paragraph_overflow_count") or 0) > 0 or (
            typography.get("column_bottom_overflow_count") or 0) > 0:
        add("overflow",
            "overflow=%s bottom_overflow=%s" % (
                qa.get("paragraph_overflow_count"),
                typography.get("column_bottom_overflow_count")))
    if (qa.get("source_span_drop_count") or 0) > 0:
        add("source_span_drop", "drop=%s" % qa["source_span_drop_count"])
    if (physical.get("renderer_specific_failure_count")
            or physical.get("renderer_a_success") is False
            or (physical.get("renderer_b_available") is True
                and physical.get("renderer_b_success") is False)):
        add("renderer_failure",
            "a_success=%s b_success=%s b_available=%s" % (
                physical.get("renderer_a_success"),
                physical.get("renderer_b_success"),
                physical.get("renderer_b_available")))
    if ((qa.get("ownership_conflict_count") or 0) > 0
            or (qa.get("unowned_text_count") or 0) > 0):
        add("ownership_conflict", "conflicts=%s unowned=%s" % (
            qa.get("ownership_conflict_count"), qa.get("unowned_text_count")))
    if ((qa.get("owned_but_unrendered_count") or 0) > 0
            or (qa.get("region_without_dom_count") or 0) > 0
            or (qa.get("dom_without_rendered_ink_count") or 0) > 0):
        add("dom_render_failure", "owned_unrendered=%s region_no_dom=%s "
            "dom_no_ink=%s" % (
                qa.get("owned_but_unrendered_count"),
                qa.get("region_without_dom_count"),
                qa.get("dom_without_rendered_ink_count")))
    if structural_qa and not structural_qa.get("structural_clean", True):
        add("paragraph_structural_pollution",
            "violations=%s" % structural_qa.get(
                "paragraph_total_structural_violation_count"))
    if (qa.get("body_translation_failure_count") or 0) > 0 or (
            qa.get("body_fallback_source_count") or 0) > 0:
        add("translation_failure",
            "fallback=%s failed=%s" % (
                qa.get("body_fallback_source_count"),
                qa.get("body_translation_failure_count")))
    if (qa.get("latin_token_split_count") or 0) > 0 or (
            qa.get("code_token_split_count") or 0) > 0:
        add("token_split", "latin=%s code=%s" % (
            qa.get("latin_token_split_count"),
            qa.get("code_token_split_count")))
    if (qa.get("formula_blank_count") or 0) > 0:
        add("blank_formula", "blank=%s" % qa["formula_blank_count"])
    if (qa.get("table_overflow_count") or 0) > 0:
        add("table_overflow", "table_overflow=%s" %
            qa.get("table_overflow_count"))
    # formula_overlap_count describes expected component/enclosure geometry
    # and can be non-zero on a QA-clean math-heavy page.  Only collisions
    # between independent rendered ownership scopes are defects here.
    if ((qa.get("text_formula_overlap_count") or 0) > 0
            or (qa.get("text_table_overlap_count") or 0) > 0
            or (qa.get("cross_column_text_count") or 0) > 0):
        add("layout_overlap", "text_formula=%s text_table=%s "
            "cross_column=%s" % (
                qa.get("text_formula_overlap_count"),
                qa.get("text_table_overlap_count"),
                qa.get("cross_column_text_count")))
    if (qa.get("figure_region_count") or 0) > 0 and (
            qa.get("figure_rendered_count") or 0) != (
                qa.get("figure_region_count") or 0):
        add("missing_figure", "figure_regions=%s rendered=%s" % (
            qa.get("figure_region_count"), qa.get("figure_rendered_count")))

    formula = (page_result or {}).get("formula_exclusivity_qa") or {}
    if formula and not formula.get("formula_exclusivity_passed", True):
        add("formula_exclusivity_failure",
            "duplicate=%s residue=%s foreign_text=%s" % (
                formula.get("formula_duplicate_component_count"),
                formula.get("formula_text_layer_residue_count"),
                formula.get("foreign_translated_text_inside_formula_count")))

    residual_language = ((page_result or {}).get("residual_language_qa") or {})
    if residual_language and not residual_language.get("residual_clean", True):
        add("residual_language", "body=%s heading=%s short_tokens=%s" % (
            residual_language.get("residual_untranslated_body_count"),
            residual_language.get("residual_untranslated_heading_count"),
            residual_language.get("short_residual_token_count")))

    collision = (page_result or {}).get("rendered_collision_qa") or {}
    if collision and not collision.get("collision_gate_passed", True):
        add("render_collision", "text_formula=%s text_table=%s "
            "text_figure=%s formula_formula=%s" % (
                collision.get("rendered_text_formula_collision_count"),
                collision.get("rendered_text_table_collision_count"),
                collision.get("rendered_text_figure_collision_count"),
                collision.get("rendered_formula_formula_collision_count")))

    heading = (page_result or {}).get("heading_duplicate_qa") or {}
    if heading and not heading.get("heading_qa_clean", True):
        add("heading_duplicate", "heading_duplicates=%s" %
            heading.get("heading_duplicate_count"))

    crop = (page_result or {}).get("formula_crop_qa") or {}
    if crop and not crop.get("formula_crop_clean", True):
        add("formula_crop_failure", "violations=%s" %
            crop.get("formula_crop_violation_count"))

    footnote = (page_result or {}).get("footnote_collision_qa") or {}
    if (footnote.get("footnote_bbox") is not None
            and not footnote.get("footnote_collision_clean", False)):
        add("footnote_collision", "overlaps=%s clipped=%s" % (
            footnote.get("body_footnote_overlap_count"),
            footnote.get("footnote_clipping_count")))

    prose = (page_result or {}).get("residual_prose_qa") or {}
    if prose and not prose.get("residual_prose_clean", True):
        add("residual_prose", "untranslated=%s source_fallback=%s" % (
            prose.get("residual_untranslated_prose_count"),
            prose.get("source_fallback_prose_count")))

    closure = (page_result or {}).get("render_closure_qa") or {}
    if (closure and not closure.get(
            "logical_paragraph_render_closure_clean", True)):
        add("render_closure_failure", "source_fallback=%s unconsumed=%s" % (
            closure.get("source_fallback_fragment_count"),
            closure.get("unconsumed_translation_fragment_count")))

    # Phase 4D visual QA may be attached either as a single visual_qa object
    # or as the two specialized QA objects.  Support both forms so ranking
    # remains backward compatible with 4C records.
    visual = (page_result or {}).get("visual_qa") or {}
    grid = ((page_result or {}).get("grid_layout_qa")
            or visual.get("grid_layout_qa") or {})
    post = ((page_result or {}).get("post_obstacle_grid_qa")
            or visual.get("post_obstacle_grid_qa") or {})

    gutter_count = _first_count(
        grid, "gutter_intrusion_count", "gutter_body_intrusion_count",
        "gutter_intrusion_total")
    if gutter_count is None:
        gutter_count = _first_count(
            visual, "gutter_intrusion_count", "gutter_body_intrusion_count",
            "gutter_intrusion_total")
    block_count = _first_count(
        grid, "block_overlap_count", "final_block_overlap_count")
    if block_count is None:
        block_count = _first_count(
            visual, "block_overlap_count", "final_block_overlap_count")
    outside_count = _first_count(
        grid, "content_outside_page_count", "outside_page_count")
    if outside_count is None:
        outside_count = _first_count(
            visual, "content_outside_page_count", "outside_page_count")
    grid_clean = grid.get("grid_layout_clean", grid.get("grid_clean", True))
    if (_is_blocked_layer(grid) or grid_clean is False or (gutter_count or 0) > 0
            or (block_count or 0) > 0 or (outside_count or 0) > 0):
        add("grid_layout_failure", "gutter=%s overlap=%s outside=%s" % (
            gutter_count, block_count, outside_count))

    post_count = _first_count(
        post, "post_obstacle_grid_failure_count", "failure_count",
        "reentry_failure_count")
    if post_count is None:
        post_count = _first_count(
            visual, "post_obstacle_grid_failure_count",
            "post_obstacle_failure_count", "reentry_failure_count")
    post_clean = post.get(
        "post_obstacle_grid_clean", post.get("post_obstacle_reentry_clean", True))
    if (_is_blocked_layer(post) or post_clean is False or post.get(
            "post_obstacle_reentry_passed") is False or (post_count or 0) > 0):
        add("post_obstacle_grid_failure", "failures=%s" % post_count)

    visual_layers = visual.get("layers") or {}
    blocked_visual_layers = sorted(
        str(name) for name, value in visual_layers.items()
        if _is_blocked_layer(value))
    explicit_visual_pass = None
    for key in ("visual_hard_gate_passed", "hard_gate_passed",
                "all_hard_assertions_passed", "visual_layout_hard_passed",
                "hard_passed",
                "post_obstacle_reentry_passed"):
        if key in visual:
            explicit_visual_pass = bool(visual.get(key))
            break
    if (explicit_visual_pass is False or blocked_visual_layers) and not (
            any(d["defect"] in {"grid_layout_failure",
                                "post_obstacle_grid_failure"}
                for d in defects)):
        add("visual_hard_gate_failure", "blocked_layers=%s" %
            ",".join(blocked_visual_layers))
    return defects


def defect_pages(page_results, structural_qas=None):
    """Return sorted defect_pages list (descending score)."""
    rows = []
    for page_number, result in page_results.items():
        sqa = (structural_qas or {}).get(page_number)
        defects = _defects_for_page(page_number, result, sqa)
        score = sum(d["weight"] for d in defects)
        if defects:
            rows.append({
                "page": page_number,
                "defect_score": score,
                "defect_count": len(defects),
                "defects": defects,
            })
    rows.sort(key=lambda r: (-r["defect_score"], r["page"]))
    return rows


def complexity_pages(page_results, limit=20):
    """Manual spot-check only: never a defect signal."""
    rows = []
    for page_number, result in page_results.items():
        qa = (result or {}).get("qa") or {}
        capacity = (result or {}).get("capacity") or {}
        visual = (result or {}).get("visual_qa") or {}
        page_grid = ((result or {}).get("page_grid")
                     or (result or {}).get("page_layout_grid")
                     or visual.get("page_grid") or {})
        layout_class = ((result or {}).get("layout_class")
                        or visual.get("layout_class")
                        or page_grid.get("layout_class")
                        or qa.get("layout_class") or "unknown")
        formula_count = qa.get("math_formula_count") or 0
        table_cell_count = qa.get("table_cell_count") or 0
        figure_count = qa.get("figure_region_count") or 0
        capacity_level = capacity.get("selected_level") or 0
        semantic_weight = {
            "mixed_layout": 45,
            "full_width_table_plus_two_column": 50,
            "reference_dense": 35,
            "appendix_dense": 35,
            "front_matter_two_column": 30,
        }.get(layout_class, 0)
        mixed_layout = bool(
            layout_class == "mixed_layout"
            or (formula_count > 0
                and (table_cell_count > 0 or figure_count > 0)))
        full_width_count = len(page_grid.get("full_width_regions") or [])
        reference_item_count = sum(
            1 for flow in ((result or {}).get("flows") or [])
            for item in (flow.get("items") or []) if item.get("is_reference"))
        complexity_score = (
            formula_count * 8 + table_cell_count + figure_count * 25
            + capacity_level * 35 + semantic_weight
            + full_width_count * 10 + min(reference_item_count, 20))
        signals = []
        if capacity_level:
            signals.append("capacity_L%s" % capacity_level)
        if layout_class != "unknown":
            signals.append(layout_class)
        if full_width_count:
            signals.append("full_width_regions=%s" % full_width_count)
        if reference_item_count:
            signals.append("reference_items=%s" % reference_item_count)
        rows.append({
            "page": page_number,
            "complexity_score": round(complexity_score, 2),
            "formula_count": formula_count,
            "table_cell_count": table_cell_count,
            "figure_count": figure_count,
            "capacity_level": capacity_level,
            "layout_class": layout_class,
            "full_width_region_count": full_width_count,
            "reference_item_count": reference_item_count,
            "semantic_signals": signals,
            "mixed_layout": mixed_layout,
            "preview": "pages/p%03d/zh.png" % page_number,
        })
    rows.sort(key=lambda r: (-r["complexity_score"], r["page"]))
    return rows[:limit]


# ---------------------------------------------------------- delivery gate --
HARD_BLOCKING_KEYS = [
    "preflight", "semantic_qa", "ownership_qa", "structural_paragraph_qa",
    "translation_qa", "dom_qa", "physical_pdf_qa", "formula_exclusivity_qa",
    "residual_language_qa", "rendered_collision_qa", "heading_duplicate_qa",
    "formula_crop_qa", "footnote_collision_qa", "residual_prose_qa",
    "render_closure_qa",
]


def build_delivery_gate(*, preflight, page_results, structural_qas,
                        translation_qa, renderer_parity,
                        expected_page_count=None):
    """Layered delivery gate.  Every layer is pass | warning | blocked.

    ``preflight``: preflight dict from document_preflight.
    ``page_results``: {page: {qa, capacity, physical_qa,
    formula_exclusivity_qa, residual_language_qa, ...}}.
    ``structural_qas``: {page: structural_qa dict}.
    ``translation_qa``: metrics from translation_status.compute_translation_qa.
    ``renderer_parity``: dict {diff_ratio, changed_page_count, ...}.
    ``expected_page_count``: physical page count used to detect render-failed
    pages (Phase 4E.2A fail-closed).
    """
    all_qa = [r.get("qa") or {} for r in page_results.values()]
    all_physical = [r.get("physical_qa") or {} for r in page_results.values()]
    all_formula = [r.get("formula_exclusivity_qa") or {}
                   for r in page_results.values()]
    all_residual = [r.get("residual_language_qa") or {}
                    for r in page_results.values()]

    layers = {}

    # preflight
    if preflight.get("openability") != "ok":
        layers["preflight"] = "blocked"
    elif preflight.get("normalization_required"):
        layers["preflight"] = "blocked"
    else:
        layers["preflight"] = "pass"

    # semantic / ownership / dom: aggregate page assertions
    page_assertion_failures = sum(
        1 for q in all_qa if not q.get("all_assertions_passed", True))
    layers["semantic_qa"] = (
        "blocked" if page_assertion_failures else "pass")
    ownership_failures = sum(
        1 for q in all_qa
        if (q.get("ownership_conflict_count") or 0) > 0
        or (q.get("unowned_text_count") or 0) > 0)
    layers["ownership_qa"] = (
        "blocked" if ownership_failures else "pass")
    dom_failures = sum(
        1 for q in all_qa
        if (q.get("owned_but_unrendered_count") or 0) > 0
        or (q.get("dom_without_rendered_ink_count") or 0) > 0)
    layers["dom_qa"] = "blocked" if dom_failures else "pass"

    # structural paragraph
    structural_failures = sum(
        1 for sq in structural_qas.values()
        if not sq.get("structural_clean", True))
    layers["structural_paragraph_qa"] = (
        "blocked" if structural_failures else "pass")

    # translation
    if translation_qa:
        if not translation_qa.get("translation_hard_gate_passed", True):
            layers["translation_qa"] = "blocked"
        elif translation_qa.get("body_fallback_source_count", 0) > 0:
            layers["translation_qa"] = "warning"
        elif translation_qa.get("coverage_accounting_ratio", 1.0) < 1.0:
            layers["translation_qa"] = "blocked"
        elif translation_qa.get("body_translation_coverage_ratio", 1.0) < 1.0:
            layers["translation_qa"] = "blocked"
        else:
            layers["translation_qa"] = "pass"
    else:
        layers["translation_qa"] = "pass"

    # physical pdf
    physical_failures = sum(
        1 for p in all_physical
        if not p.get("all_assertions_passed", True))
    layers["physical_pdf_qa"] = (
        "blocked" if physical_failures else "pass")

    # formula exclusivity (Phase 4C.2): no paragraph/text-layer duplicate
    # of any FormulaGroup's source content on the final PDF
    formula_failures = sum(
        1 for f in all_formula
        if not f.get("formula_exclusivity_passed", True))
    layers["formula_exclusivity_qa"] = (
        "blocked" if formula_failures else "pass")

    # residual source language (Phase 4C.2): translatable regions must not
    # retain untranslated English on the final PDF
    residual_failures = sum(
        1 for r in all_residual if not r.get("residual_clean", True))
    layers["residual_language_qa"] = (
        "blocked" if residual_failures else "pass")

    # rendered region collision (Phase 4C.2R): the final PDF must show no
    # translated text physically overlapping a formula/table/figure region,
    # no formula-vs-formula overlap, no SVG clip leaks, no orphan equation
    # numbers.
    all_collision = [r.get("rendered_collision_qa") or {}
                     for r in page_results.values()]
    collision_failures = sum(
        1 for c in all_collision if not c.get("collision_gate_passed", True))
    layers["rendered_collision_qa"] = (
        "blocked" if collision_failures else "pass")

    # heading duplicate (Phase 4C.2R.1): a rendered heading with a repeated
    # semantic token or a reused source fragment is a split-and-join
    # artifact (p011 "综述生成中的信息瓶颈 生成").
    all_heading = [r.get("heading_duplicate_qa") or {}
                   for r in page_results.values()]
    heading_failures = sum(
        1 for h in all_heading if not h.get("heading_qa_clean", True))
    layers["heading_duplicate_qa"] = (
        "blocked" if heading_failures else "pass")

    # formula crop (Phase 4C.2R.1): segment viewBoxes must cover all
    # formula ink -- cropped glyphs (p014 "otherwise" tail) or entirely
    # uncovered components (p003 condition rows, p011 equation numbers)
    # are hard failures.
    all_crop = [r.get("formula_crop_qa") or {} for r in page_results.values()]
    crop_failures = sum(
        1 for c in all_crop if not c.get("formula_crop_clean", True))
    layers["formula_crop_qa"] = "blocked" if crop_failures else "pass"

    all_footnote = [r.get("footnote_collision_qa") or {}
                    for r in page_results.values()]
    footnote_failures = sum(
        1 for f in all_footnote
        if f.get("footnote_bbox") is not None
        and not f.get("footnote_collision_clean", False))
    layers["footnote_collision_qa"] = (
        "blocked" if footnote_failures else "pass")

    all_prose = [r.get("residual_prose_qa") or {}
                 for r in page_results.values()]
    prose_failures = sum(
        1 for r in all_prose if not r.get("residual_prose_clean", True))
    layers["residual_prose_qa"] = "blocked" if prose_failures else "pass"

    all_closure = [r.get("render_closure_qa") or {}
                   for r in page_results.values()]
    closure_failures = sum(
        1 for r in all_closure
        if not r.get("logical_paragraph_render_closure_clean", True))
    layers["render_closure_qa"] = "blocked" if closure_failures else "pass"

    # renderer parity (diagnostic in 4C.1A - not a hard gate)
    if renderer_parity and renderer_parity.get("renderer_b_available") is False:
        layers["renderer_parity"] = "warning"
    elif renderer_parity and renderer_parity.get(
            "renderer_specific_failure_count", 0) > 0:
        layers["renderer_parity"] = "blocked"
    else:
        layers["renderer_parity"] = "pass"

    # Phase 4E.2A: FAIL-CLOSED.  Any QA stage whose backing stage did NOT
    # actually pass (error / not_run / missing) must force its layer blocked
    # -- a never-run stage can never count as PASS.
    forced_blocked = qx.fail_closed_layer_statuses(page_results, layers)

    exp_pages = expected_page_count if expected_page_count is not None \
        else len(page_results)
    manifest = qx.document_manifest(page_results, exp_pages)

    blocked = [k for k in HARD_BLOCKING_KEYS if layers.get(k) == "blocked"]
    warning = [k for k in layers if layers.get(k) == "warning"]
    # a render-failed page (pages_seen != expected) or any un-executed stage
    # is itself a hard BLOCK independent of the layer results.
    if not manifest["qa_execution_complete"]:
        decision = "blocked"
        blocked.append("qa_execution_incomplete")
    else:
        decision = "blocked" if blocked else ("warning" if warning else "pass")
    return {
        "layers": layers,
        "blocked_layers": blocked,
        "warning_layers": warning,
        "decision": decision,
        "qa_execution_complete": manifest["qa_execution_complete"],
        "qa_execution_manifest": manifest,
        "qa_fail_closed_forced_layers": forced_blocked,
        "page_assertion_failure_count": page_assertion_failures,
        "physical_failure_count": physical_failures,
        "structural_failure_count": structural_failures,
        # Phase 4C.2R.1 hard counts (all must be 0)
        "heading_duplicate_count": sum(
            h.get("heading_duplicate_count", 0) for h in all_heading),
        "formula_crop_violation_count": sum(
            c.get("formula_crop_violation_count", 0) for c in all_crop),
        "short_residual_token_count": sum(
            (r.get("residual_language_qa") or {}).get(
                "short_residual_token_count", 0)
            for r in page_results.values()),
        "body_footnote_overlap_count": sum(
            f.get("body_footnote_overlap_count", 0) for f in all_footnote),
        "residual_untranslated_prose_count": sum(
            r.get("residual_untranslated_prose_count", 0) for r in all_prose),
        "source_fallback_fragment_count": sum(
            r.get("source_fallback_fragment_count", 0) for r in all_closure),
    }


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def write_rankings_and_gate(out_dir, *, preflight, page_results,
                            structural_qas, translation_qa,
                            renderer_parity, defect_limit=20,
                            expected_page_count=None):
    """Write defect_pages.json, complexity_pages.json, delivery_gate.json."""
    out_dir = Path(out_dir)
    dp = defect_pages(page_results, structural_qas)
    cp = complexity_pages(page_results, defect_limit)
    gate = build_delivery_gate(preflight=preflight, page_results=page_results,
                               structural_qas=structural_qas,
                               translation_qa=translation_qa,
                               renderer_parity=renderer_parity,
                               expected_page_count=expected_page_count)
    _dump(out_dir / "defect_pages.json", dp)
    _dump(out_dir / "complexity_pages.json", cp)
    _dump(out_dir / "delivery_gate.json", gate)
    return dp, cp, gate
