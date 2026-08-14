# -*- coding: utf-8 -*-
"""FormulaRenderCompletenessQA (Phase 4E.1B-C2) -- report-only.

A FormulaModel existing is NOT proof of a rendered formula.  This QA compares
the SOURCE formula ink (source PDF raster at the formula bbox) with the
RENDERED ink (final zh.pdf raster at the flowed bbox) and reports any formula
whose source had visible ink but whose final render is blank / cropped.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pymupdf

INK_DARK = 150


def _raster_ink_ratio(pdf_path, bbox, page_index=0, zoom=1.0):
    doc = pymupdf.open(str(pdf_path))
    page = doc[page_index]
    clip = pymupdf.Rect(*bbox)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
    samples = pix.samples
    dark = 0
    total = pix.width * pix.height
    if total == 0:
        doc.close()
        return 0.0, 0
    for i in range(0, len(samples), pix.n):
        if samples[i] < INK_DARK and samples[i + 1] < INK_DARK \
                and samples[i + 2] < INK_DARK:
            dark += 1
    doc.close()
    return dark / total, dark


def _flow_shifts(flows):
    """formula_id -> (dx, dy) from the flow (outer container shift only)."""
    out = {}
    for flow in flows or []:
        for item in flow.get("items", []):
            if item.get("kind") != "formula":
                continue
            dy = float(item.get("flow_y", 0)) - float(item.get("anchor_y", 0))
            for m in item.get("row_members", []):
                out[m.get("formula_id")] = (0.0, dy)
    return out


def _iter_traces(trace_records) -> Iterable[dict]:
    """Yield formula traces from page/document/flat trace containers."""
    if not trace_records:
        return
    if isinstance(trace_records, list):
        for row in trace_records:
            if isinstance(row, dict):
                yield row
        return
    if not isinstance(trace_records, dict):
        return
    if isinstance(trace_records.get("traces"), list):
        yield from trace_records["traces"]
        return
    if isinstance(trace_records.get("pages"), list):
        for page in trace_records["pages"]:
            yield from _iter_traces(page)


def _trace_completeness(trace_records) -> dict:
    """Build the C2 completeness contract from lifecycle traces."""
    traces = list(_iter_traces(trace_records))
    blank = []
    cropped = []
    orphan = []
    owned_unrendered = []
    duplicate = []
    source_to_svg_loss = []
    svg_to_pdf_loss = []
    renderer_disagreement = []
    equation_orphans = []
    condition_orphans = []
    double_render = []

    for trace in traces:
        tid = trace.get("formula_trace_id")
        fid = trace.get("source_formula_id")
        page = trace.get("page")
        base = {"formula_trace_id": tid, "formula_id": fid, "page": page}
        source = trace.get("source") or {}
        svg = trace.get("svg") or {}
        placement = trace.get("placement") or {}
        final = trace.get("final_pdf") or {}
        ownership = trace.get("ownership") or {}
        source_ink = int(source.get("ink_pixel_count") or 0)
        svg_ink = int(final.get("svg_ink_pixels") or sum(
            int(row.get("rasterized_ink_pixel_count") or 0)
            for row in svg.get("segments") or []))
        final_mu = int(final.get("final_pymupdf_ink_pixels") or 0)
        final_pdfium = int(final.get("final_pdfium_ink_pixels") or 0)
        blank_count = int(final.get("blank_placement_count") or 0)
        crop_count = int(final.get("crop_placement_count") or 0)
        embed_missing = int(placement.get("embed_missing_count") or 0)
        duplicate_embed = int(placement.get("duplicate_embed_count") or 0)
        double_text_svg = int(
            trace.get("formula_double_render_text_svg_count") or 0)

        if source_ink > 0 and svg_ink <= 0:
            source_to_svg_loss.append({**base, "source_ink_pixels": source_ink,
                                       "svg_ink_pixels": svg_ink})
        if svg_ink > 0 and (blank_count > 0 or final_mu <= 0
                            or final_pdfium <= 0):
            svg_to_pdf_loss.append({**base, "svg_ink_pixels": svg_ink,
                                     "pymupdf_ink_pixels": final_mu,
                                     "pdfium_ink_pixels": final_pdfium})
        if blank_count:
            blank.append({**base, "blank_placement_count": blank_count})
        if crop_count or int(svg.get("viewbox_crop_count") or 0):
            cropped.append({
                **base,
                "final_crop_placement_count": crop_count,
                "svg_viewbox_crop_count": int(
                    svg.get("viewbox_crop_count") or 0),
            })
        if embed_missing:
            orphan.append({**base, "embed_missing_count": embed_missing})
        if embed_missing or blank_count:
            owned_unrendered.append({
                **base,
                "embed_missing_count": embed_missing,
                "blank_placement_count": blank_count,
            })
        if duplicate_embed:
            duplicate.append({**base,
                              "duplicate_embed_count": duplicate_embed})
        if final.get("renderer_disagreement"):
            renderer_disagreement.append(base)
        if int(ownership.get("equation_number_orphan_count") or 0):
            equation_orphans.append({
                **base,
                "count": int(ownership["equation_number_orphan_count"]),
            })
        if int(ownership.get("condition_text_orphan_count") or 0):
            condition_orphans.append({
                **base,
                "count": int(ownership["condition_text_orphan_count"]),
            })
        if double_text_svg:
            double_render.append({**base, "count": double_text_svg})

    hard = {
        "formula_trace_gap_count": max(
            0, int((trace_records or {}).get("formula_model_count", len(traces)))
            - len(traces)) if isinstance(trace_records, dict) else 0,
        "formula_blank_count": len(blank),
        "formula_crop_count": len(cropped),
        "formula_orphan_count": len(orphan),
        "formula_owned_but_unrendered_count": len(owned_unrendered),
        "formula_owned_unrendered_count": len(owned_unrendered),
        "formula_duplicate_count": len(duplicate),
        "formula_duplicate_render_count": len(duplicate),
        "formula_fragment_unowned_count": int(
            (trace_records or {}).get("formula_fragment_unowned_count", 0))
            if isinstance(trace_records, dict) else 0,
        "formula_fragment_double_owned_count": int(
            (trace_records or {}).get("formula_fragment_double_owned_count", 0))
            if isinstance(trace_records, dict) else 0,
        "formula_equation_number_orphan_count": sum(
            row["count"] for row in equation_orphans),
        "equation_number_orphan_count": sum(
            row["count"] for row in equation_orphans),
        "formula_condition_text_orphan_count": sum(
            row["count"] for row in condition_orphans),
        "condition_text_orphan_count": sum(
            row["count"] for row in condition_orphans),
        "formula_renderer_disagreement_count": len(renderer_disagreement),
        "formula_double_render_text_svg_count": sum(
            row["count"] for row in double_render),
        "source_to_svg_loss_count": len(source_to_svg_loss),
        "formula_source_to_svg_zero_loss_count": len(source_to_svg_loss),
        "svg_to_final_pdf_loss_count": len(svg_to_pdf_loss),
        "formula_svg_to_pdf_zero_loss_count": len(svg_to_pdf_loss),
    }
    decision = "pass" if all(value == 0 for value in hard.values()) else "fail"
    return {
        "schema_version": "phase4e1b.c2.formula_render_completeness_qa.v2",
        "formula_model_count": int(
            (trace_records or {}).get("formula_model_count", len(traces)))
            if isinstance(trace_records, dict) else len(traces),
        "formula_expected_count": int(
            (trace_records or {}).get("formula_model_count", len(traces)))
            if isinstance(trace_records, dict) else len(traces),
        "formula_rendered_count": len(traces) - len(owned_unrendered),
        "formula_trace_count": len(traces),
        "formula_trace_coverage": round(
            len(traces) / max(int(
                (trace_records or {}).get("formula_model_count", len(traces)))
                if isinstance(trace_records, dict) else len(traces), 1), 4),
        "source_to_svg_zero_loss": len(source_to_svg_loss) == 0,
        "svg_to_final_pdf_zero_loss": len(svg_to_pdf_loss) == 0,
        "formula_render_success_rate": round(
            (len(traces) - len(owned_unrendered)) / max(len(traces), 1), 4),
        "formula_ink_recovery_ratio": round(
            (len(traces) - len(source_to_svg_loss) - len(svg_to_pdf_loss))
            / max(len(traces), 1), 4),
        "hard": hard,
        **hard,
        "blank_details": blank,
        "crop_details": cropped,
        "orphan_details": orphan,
        "owned_but_unrendered_details": owned_unrendered,
        "duplicate_details": duplicate,
        "source_to_svg_loss_details": source_to_svg_loss,
        "svg_to_final_pdf_loss_details": svg_to_pdf_loss,
        "renderer_disagreement_details": renderer_disagreement,
        "equation_number_orphan_details": equation_orphans,
        "condition_text_orphan_details": condition_orphans,
        "double_render_text_svg_details": double_render,
        "records": traces,
        "decision": decision,
    }


def formula_render_completeness_qa(source_pdf=None, final_pdf=None,
                                   page_model=None, flows=None,
                                   out_dir=None, page_label="page", *,
                                   source_page_index=None,
                                   trace_records=None) -> dict:
    if trace_records is not None:
        result = _trace_completeness(trace_records)
        if out_dir:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            (Path(out_dir) / "formula_render_completeness_qa.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8")
        return result

    if page_model is None:
        raise ValueError("page_model is required when trace_records is absent")
    if source_page_index is None:
        source_page_index = max(0, int(page_model.get("page") or 1) - 1)
    shifts = _flow_shifts(flows)
    formulas = [r for r in page_model.get("regions", [])
                if r.get("type") == "formula"]
    records = []
    blank = []
    cropped = []
    orphan = 0
    ink_total = ink_ok = 0
    for r in formulas:
        fm = r.get("payload") or {}
        b = fm.get("layout_bbox") or r.get("bbox")
        if not b:
            continue
        source_box = [float(v) for v in b]
        dx, dy = shifts.get(fm.get("formula_id"), (0.0, 0.0))
        rendered_box = [source_box[0] + dx, source_box[1] + dy,
                        source_box[2] + dx, source_box[3] + dy]
        src_ratio, src_dark = _raster_ink_ratio(
            source_pdf, source_box, page_index=source_page_index, zoom=4.0)
        ren_ratio, ren_dark = _raster_ink_ratio(
            final_pdf, rendered_box, page_index=0, zoom=4.0)
        ink_total += 1
        source_nonzero = src_ratio > 0.001
        rendered_nonzero = ren_ratio > 0.001
        if source_nonzero and rendered_nonzero:
            ink_ok += 1
        ncomp = len(fm.get("components") or [])
        is_blank = source_nonzero and not rendered_nonzero
        is_crop = source_nonzero and rendered_nonzero and \
            ren_ratio < 0.4 * src_ratio and src_ratio > 0.02
        if is_blank:
            blank.append({"formula_id": fm.get("formula_id"),
                          "source_ink": round(src_ratio, 4),
                          "rendered_ink": round(ren_ratio, 4),
                          "source_bbox": [round(v, 1) for v in source_box],
                          "rendered_bbox": [round(v, 1) for v in rendered_box]})
            orphan += ncomp
        if is_crop:
            cropped.append({"formula_id": fm.get("formula_id"),
                            "source_ink": round(src_ratio, 4),
                            "rendered_ink": round(ren_ratio, 4)})
        records.append({
            "formula_id": fm.get("formula_id"),
            "placement": fm.get("placement"),
            "source_ink_ratio": round(src_ratio, 4),
            "rendered_ink_ratio": round(ren_ratio, 4),
            "source_ink_nonzero": source_nonzero,
            "rendered_ink_nonzero": rendered_nonzero,
            "component_count": ncomp,
            "blank": is_blank,
            "crop": is_crop,
        })
    result = {
        "schema_version": "phase4e1b.formula_render_completeness_qa.v1.1",
        "source_page_index": source_page_index,
        "formula_count": len(formulas),
        "formula_blank_count": len(blank),
        "formula_crop_count": len(cropped),
        "formula_orphan_component_count": orphan,
        "formula_ink_recovery_ratio": round(ink_ok / max(ink_total, 1), 4),
        "formula_render_success_rate": round(ink_ok / max(ink_total, 1), 4),
        "blank_details": blank[:8],
        "records": records,
        "hard": {"formula_blank_count": len(blank),
                 "formula_crop_count": len(cropped),
                 "formula_orphan_component_count": orphan},
        "decision": ("pass" if not blank and not cropped and orphan == 0
                     else "fail"),
    }
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "formula_render_completeness_qa.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
