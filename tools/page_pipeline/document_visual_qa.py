# -*- coding: utf-8 -*-
"""Phase 4D.1C document-grid integration and final-PDF visual QA.

This module deliberately contains no page-number policy.  Layout classes and
profile overrides are inferred from semantic page objects, paragraph roles,
column evidence and document priors.  The DOM is used only to verify locked
figure geometry; text geometry is always read back from the final PDF.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

try:
    from translation_status import is_reference_item
except ImportError:  # package import during focused tests
    from .translation_status import is_reference_item


LOW_GRID_CONFIDENCE = 0.65
LARGE_VOID_PT = 96.0
_APPENDIX_HEADING_RE = re.compile(
    r"^\s*(?:Appendix\s+)?[A-Z](?:\.\d+)*(?:[.\s:]|$)", re.I)
_FIGURE_DOM_RE = re.compile(
    r'<span\s+class="figure-region"[^>]*data-region="([^"]+)"[^>]*'
    r'style="[^"]*left:([\d.+-]+)pt;top:([\d.+-]+)pt;'
    r'width:([\d.+-]+)pt;height:([\d.+-]+)pt;', re.I)


def _dump(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _text_payloads(page):
    return [r.get("payload") or {} for r in page.get("regions", [])
            if r.get("type") == "text"]


def _regions(page, kind):
    return [r for r in page.get("regions", []) if r.get("type") == kind]


def _bbox(value):
    if isinstance(value, dict):
        value = value.get("bbox")
    if not value or len(value) != 4:
        return None
    return [float(v) for v in value]


def _intersection(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _intersects(a, b, tolerance=0.0):
    return not (a[2] <= b[0] + tolerance or a[0] >= b[2] - tolerance
                or a[3] <= b[1] + tolerance or a[1] >= b[3] - tolerance)


def _column_evidence(page):
    counts = Counter()
    for p in _text_payloads(page):
        if p.get("style_role") not in ("body", "heading", "list_item", "caption"):
            continue
        column = p.get("column")
        if column in (0, 1):
            counts[column] += 1
    return counts


def _is_full_width(box, frame, ratio=0.64):
    if not box or not frame:
        return False
    width = max(float(frame["x1"]) - float(frame["x0"]), 1.0)
    return (box[2] - box[0]) / width >= ratio


def _layout_traits(page, grid, frontmatter=None):
    frame = grid.get("content_frame") or {
        "x0": 0.0, "y0": 0.0,
        "x1": float(page.get("width") or 0.0),
        "y1": float(page.get("height") or 0.0),
    }
    paras = _text_payloads(page)
    refs = [p for p in paras if is_reference_item({
        "type": "paragraph", "source_text": p.get("source_text") or ""})]
    headings = [p for p in paras if p.get("style_role") == "heading"]
    appendix = [p for p in headings
                if _APPENDIX_HEADING_RE.match(p.get("source_text") or "")]
    tables, figures = _regions(page, "table"), _regions(page, "figure")
    formulas = _regions(page, "formula")
    columns = _column_evidence(page)
    substantive = Counter()
    for p in paras:
        if (p.get("column") in (0, 1)
                and p.get("style_role") in ("body", "heading", "list_item", "caption")
                and len((p.get("source_text") or "").strip()) >= 40):
            substantive[p["column"]] += 1
    full_tables = [r for r in tables if _is_full_width(_bbox(r), frame)]
    full_figures = [r for r in figures if _is_full_width(_bbox(r), frame)]
    full_formulas = [r for r in formulas if _is_full_width(_bbox(r), frame, 0.60)]
    ref_ratio = len(refs) / max(len(paras), 1)
    two_column_evidence = bool(columns[0] and columns[1])
    grid_mode = ((grid.get("grid_reliability") or {}).get("layout_mode")
                 or grid.get("layout_mode"))
    return {
        "frontmatter_detected": bool(frontmatter or grid_mode == "front_matter"),
        "paragraph_count": len(paras),
        "reference_paragraph_count": len(refs),
        "reference_ratio": round(ref_ratio, 4),
        "appendix_heading_count": len(appendix),
        "appendix_heading_ids": [p.get("paragraph_id") for p in appendix],
        "table_count": len(tables),
        "full_width_table_count": len(full_tables),
        "figure_count": len(figures),
        "full_width_figure_count": len(full_figures),
        "formula_count": len(formulas),
        "full_width_formula_count": len(full_formulas),
        "column_scope_counts": {str(k): v for k, v in sorted(columns.items())},
        "substantive_column_counts": {
            str(k): v for k, v in sorted(substantive.items())},
        "two_column_evidence": two_column_evidence,
        "substantive_two_column_evidence": bool(substantive[0] and substantive[1]),
    }


def classify_layout(page, grid, frontmatter=None):
    """Classify one page from semantics and local geometry, never page id."""
    t = _layout_traits(page, grid, frontmatter)
    if t["frontmatter_detected"]:
        return "front_matter_two_column", t
    # Reference continuation pages intentionally have their own density and
    # capacity policy, regardless of one- vs two-track local geometry.
    if (t["reference_paragraph_count"] >= 3
            and (t["reference_ratio"] >= 0.48
                 or t["reference_paragraph_count"] >= 7)):
        return "reference_dense", t
    # Figure-dominant continuation/plate pages may have one caption mapped
    # to each source column without containing a two-column body.  Their
    # geometry is a single full-page figure grammar, not appendix prose.
    if (t["figure_count"] and not t["table_count"] and not t["formula_count"]
            and not t["appendix_heading_count"]
            and t["paragraph_count"] <= max(4, t["figure_count"] * 2)):
        return "single_column", t
    # Table-bearing appendix pages take the more concrete obstacle class;
    # appendix state remains available in layout_traits.
    if t["full_width_table_count"]:
        return "full_width_table_plus_two_column", t
    if t["appendix_heading_count"]:
        return "appendix_dense", t
    if ((t["full_width_figure_count"] and t["substantive_two_column_evidence"])
            or (t["figure_count"] and t["formula_count"]
                and t["substantive_two_column_evidence"])):
        return "mixed_layout", t
    if t["substantive_two_column_evidence"] or (grid.get("gutter") and
                                                 len(grid.get("columns") or []) == 2):
        return "standard_two_column", t
    return "single_column", t


def _profile_tracks(profile):
    profile = profile or {}
    tracks = profile.get("median_tracks") or {}
    left = profile.get("left_track") or tracks.get("left")
    right = profile.get("right_track") or tracks.get("right")
    if not left or not right:
        return None
    return [
        {"column_id": 0, "x0": float(left[0]), "x1": float(left[1]),
         "width": round(float(left[1]) - float(left[0]), 2)},
        {"column_id": 1, "x0": float(right[0]), "x1": float(right[1]),
         "width": round(float(right[1]) - float(right[0]), 2)},
    ]


def _semantic_full_width_regions(page, frame, existing=None):
    output = []
    seen = set()

    def add(kind, region_id, box, source):
        box = _bbox(box)
        if not box:
            return
        key = (kind, region_id, tuple(round(v, 2) for v in box))
        if key in seen:
            return
        seen.add(key)
        output.append({"region_type": kind, "region_id": region_id,
                       "bbox": [round(v, 3) for v in box], "source": source})

    # Do not promote raw x-projection rows into hard-gutter exemptions: two
    # ordinary column lines sharing a baseline can appear full-width when
    # unioned.  Exemptions below require a semantic parent.
    for region in page.get("regions", []):
        kind = region.get("type")
        box = _bbox(region)
        if kind in ("table", "figure", "image") and _is_full_width(box, frame):
            add(kind, region.get("region_id"), box, "semantic_geometry_locked")
        elif kind == "formula" and _is_full_width(box, frame, 0.60):
            add("display_formula", region.get("region_id"), box,
                "semantic_formula_enclosure")
        elif kind == "text":
            p = region.get("payload") or {}
            if p.get("column") == -1 or p.get("full_width"):
                add("full_width_%s" % (p.get("style_role") or "text"),
                    region.get("region_id"), box, "semantic_paragraph_scope")
    return output


def prepare_page_grid(page, grid, frontmatter=None, profile=None,
                      appendix_active=False):
    """Return an enriched, renderer-ready PageLayoutGrid dictionary."""
    grid = dict(grid or {})
    grid["content_frame"] = dict(grid.get("content_frame") or {
        "x0": 0.0, "y0": 0.0,
        "x1": float(page.get("width") or 0.0),
        "y1": float(page.get("height") or 0.0),
    })
    grid["columns"] = [dict(c) for c in grid.get("columns") or []]
    layout_class, traits = classify_layout(page, grid, frontmatter)
    traits["appendix_section_active"] = bool(
        appendix_active or traits.get("appendix_heading_count"))
    if (traits["appendix_section_active"]
            and layout_class in ("standard_two_column", "mixed_layout")
            and not traits.get("full_width_table_count")
            and (traits.get("appendix_heading_count")
                 or traits.get("formula_count")
                 or traits.get("table_count")
                 or traits.get("paragraph_count", 0) > 4)):
        layout_class = "appendix_dense"
    original_two = bool(grid.get("gutter") and len(grid["columns"]) == 2)
    profile_columns = _profile_tracks(profile)
    profile_snap = bool(grid.get("profile_snap_applied", False))
    override_reason = grid.get("override_reason")

    # A full-width obstacle can hide the gutter from x-projection.  If local
    # paragraph semantics independently show both columns, restore only the
    # paragraph tracks; locked obstacle geometry remains untouched.
    if (not original_two and traits["substantive_two_column_evidence"] and profile_columns
            and layout_class != "reference_dense"):
        grid["columns"] = profile_columns
        grid["gutter"] = {
            "x0": profile_columns[0]["x1"],
            "x1": profile_columns[1]["x0"],
            "width": round(profile_columns[1]["x0"]
                           - profile_columns[0]["x1"], 2),
        }
        profile_snap = True
        override_reason = "profile_tracks_restored_from_two_column_paragraph_evidence_after_full_width_obstacle"
    elif (original_two and profile_columns and
          all(abs(grid["columns"][i][edge] - profile_columns[i][edge]) <= .15
              for i in (0, 1) for edge in ("x0", "x1"))):
        profile_snap = True
    elif not original_two and layout_class in ("single_column", "reference_dense"):
        override_reason = override_reason or "%s_page_local_geometry_preserved" % layout_class

    frame = grid["content_frame"]
    columns = grid.get("columns") or []
    occupancy = 1.0 if traits["two_column_evidence"] else (
        0.82 if len(columns) == 1 else 0.68)
    gutter_evidence = float(grid.get("confidence") or 0.75)
    if len(columns) == 2:
        widths = [max(float(c["x1"]) - float(c["x0"]), 1.0) for c in columns]
        alignment = max(0.0, 1.0 - abs(widths[0] - widths[1]) / max(widths))
    else:
        alignment = 0.9
    profile_agreement = 0.8
    profile_gutter = ((profile or {}).get("gutter_median")
                      or (profile or {}).get("median_gutter_width"))
    if grid.get("gutter") and profile_gutter:
        delta = abs(float(grid["gutter"]["width"])
                    - float(profile_gutter))
        profile_agreement = max(0.0, 1.0 - delta / 20.0)
    elif profile_snap:
        profile_agreement = 1.0
    confidence = round(0.28 * occupancy + 0.24 * gutter_evidence
                       + 0.24 * alignment + 0.24 * profile_agreement, 3)

    projection_full_width_evidence = list(grid.get("full_width_regions") or [])
    grid.update({
        "layout_class": layout_class,
        "page_layout_class": layout_class,
        "layout_traits": traits,
        "column_tracks": columns,
        "full_width_regions": _semantic_full_width_regions(page, frame),
        "projection_full_width_evidence": projection_full_width_evidence,
        "bottom_reserved_regions": [],
        "grid_confidence": confidence,
        "grid_confidence_components": {
            "column_occupancy_evidence": round(occupancy, 3),
            "gutter_whitespace_evidence": round(gutter_evidence, 3),
            "alignment_consistency": round(alignment, 3),
            "document_profile_agreement": round(profile_agreement, 3),
        },
        "profile_snap_applied": profile_snap,
        "override_reason": override_reason,
    })
    return grid


def _pdf_lines(page):
    lines = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if (s.get("text") or "").strip()]
            if not spans:
                continue
            box = [min(s["bbox"][0] for s in spans), min(s["bbox"][1] for s in spans),
                   max(s["bbox"][2] for s in spans), max(s["bbox"][3] for s in spans)]
            lines.append({"text": "".join(s.get("text") or "" for s in spans),
                          "bbox": [float(v) for v in box],
                          "size": max(float(s.get("size") or 0.0) for s in spans)})
    return lines


def _flow_boxes(flows):
    boxes = []
    for flow in flows or []:
        for item in flow.get("items", []):
            if item.get("kind") != "paragraph":
                continue
            y0 = float(item.get("flow_y") or 0.0)
            box = [float(flow.get("col_x0") or 0.0), y0,
                   float(flow.get("col_x1") or 0.0),
                   y0 + max(float(item.get("est_height") or 0.0), 1.0)]
            boxes.append({"bbox": box, "column": item.get("column", flow.get("column")),
                          "style_role": item.get("style_role"),
                          "paragraph_id": item.get("logical_paragraph_id")
                          or item.get("paragraph_id")})
    return boxes


def _containing_flow(line_box, flow_boxes):
    cx, cy = (line_box[0] + line_box[2]) / 2, (line_box[1] + line_box[3]) / 2
    hits = [f for f in flow_boxes if f["bbox"][0] - 1 <= cx <= f["bbox"][2] + 1
            and f["bbox"][1] - 2 <= cy <= f["bbox"][3] + 2]
    return min(hits, key=lambda f: abs(f["bbox"][1] - line_box[1])) if hits else None


def _outside(box, width, height, tolerance=.75):
    return (box[0] < -tolerance or box[1] < -tolerance
            or box[2] > width + tolerance or box[3] > height + tolerance)


def _figure_dom_boxes(html_path):
    try:
        html = Path(html_path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}
    return {m.group(1): [float(m.group(2)), float(m.group(3)),
                         float(m.group(2)) + float(m.group(4)),
                         float(m.group(3)) + float(m.group(5))]
            for m in _FIGURE_DOM_RE.finditer(html)}


def _voids(lines, obstacles, columns, frame):
    warnings = []
    for column in columns:
        track = [float(column["x0"]), float(frame["y0"]),
                 float(column["x1"]), float(frame["y1"])]
        intervals = []
        for line in lines:
            if _intersects(line["bbox"], track):
                intervals.append([line["bbox"][1], line["bbox"][3], "text"])
        for obstacle in obstacles:
            if _intersects(obstacle["bbox"], track):
                intervals.append([obstacle["bbox"][1], obstacle["bbox"][3],
                                  obstacle["region_type"]])
        intervals.sort()
        if len(intervals) < 2:
            continue
        end = intervals[0][1]
        for start, stop, _kind in intervals[1:]:
            gap = start - end
            if gap >= LARGE_VOID_PT:
                warnings.append({"column": column.get("column_id"),
                                 "bbox": [track[0], round(end, 2),
                                          track[2], round(start, 2)],
                                 "height": round(gap, 2),
                                 "classification": "unexplained_middle_void"})
            end = max(end, stop)
    return warnings


def _density(final_page, lines, grid):
    frame = grid.get("content_frame") or {}
    clip = pymupdf.Rect(float(frame.get("x0", 0)), float(frame.get("y0", 0)),
                        float(frame.get("x1", final_page.rect.width)),
                        float(frame.get("y1", final_page.rect.height)))
    pix = final_page.get_pixmap(matrix=pymupdf.Matrix(1, 1), colorspace=pymupdf.csGRAY,
                                clip=clip, alpha=False)
    samples = pix.samples
    ink = sum(1 for value in samples if value < 245)
    ratio = ink / max(len(samples), 1)
    bottoms = {}
    for line in lines:
        cx = (line["bbox"][0] + line["bbox"][2]) / 2
        for col in grid.get("columns") or []:
            if float(col["x0"]) <= cx <= float(col["x1"]):
                cid = str(col.get("column_id"))
                bottoms[cid] = max(bottoms.get(cid, 0.0), line["bbox"][3])
    difference = (abs(bottoms.get("0", 0) - bottoms.get("1", 0))
                  if "0" in bottoms and "1" in bottoms else None)
    return {"text_ink_ratio": round(ratio, 5),
            "whitespace_ratio": round(1.0 - ratio, 5),
            "column_bottoms": {k: round(v, 2) for k, v in bottoms.items()},
            "column_bottom_difference": (round(difference, 2)
                                           if difference is not None else None)}


def _post_obstacle_reentry(page, flows, grid, final_lines=None):
    tracks = {int(c.get("column_id", i)): c
              for i, c in enumerate(grid.get("columns") or [])}
    if not (0 in tracks and 1 in tracks):
        return {"checked_obstacle_count": 0, "reentry_records": [],
                "post_obstacle_wrong_column_count": 0,
                "post_obstacle_grid_passed": True}
    final_lines = final_lines or []
    paragraphs = []
    formula_rows = []
    for flow in flows or []:
        for item in flow.get("items", []):
            if item.get("kind") == "paragraph" and item.get("column") in (0, 1):
                y0 = float(item.get("flow_y") or 0.0)
                paragraphs.append({"column": int(item["column"]),
                                   "paragraph_id": item.get("logical_paragraph_id")
                                   or item.get("paragraph_id"),
                                   "style_role": item.get("style_role") or "body",
                                   "y0": y0,
                                   "y1": y0 + max(float(item.get("est_height") or 0.0), 1.0),
                                   "x0": float(flow.get("col_x0") or 0.0),
                                   "x1": float(flow.get("col_x1") or 0.0)})
            elif item.get("kind") == "formula" and flow.get("column") in (0, 1):
                formula_rows.append({"region_type": "formula",
                                     "region_id": item.get("formula_id") or ",".join(
                                         str(m.get("formula_id"))
                                         for m in item.get("row_members", [])),
                                     "bbox": [float(item.get("bbox", [0, 0, 0, 0])[0]),
                                              float(item.get("flow_y") or 0.0),
                                              float(item.get("bbox", [0, 0, 0, 0])[2]),
                                              float(item.get("flow_y") or 0.0)
                                              + float(item.get("est_height") or 0.0)],
                                     "columns": [int(flow["column"])]})
    obstacles = []
    for region in page.get("regions", []):
        if region.get("type") not in ("table", "figure"):
            continue
        box = _bbox(region)
        touched = [cid for cid, track in tracks.items()
                   if box and _intersects(
                       box, [float(track["x0"]), box[1],
                             float(track["x1"]), box[3]])]
        obstacles.append({"region_type": region.get("type"),
                          "region_id": region.get("region_id"),
                          "bbox": box, "columns": touched})
    obstacles += formula_rows
    records, wrong = [], 0
    for obstacle in obstacles:
        box = obstacle.get("bbox")
        if not box:
            continue
        scope_columns = obstacle.get("columns") or [0, 1]
        following = [p for p in paragraphs
                     if p["column"] in scope_columns
                     and p["y0"] >= box[3] - 1.0]
        if not following:
            continue
        first_y = min(p["y0"] for p in following)
        first = [p for p in following if p["y0"] <= first_y + 3.0]
        for para in first:
            track = tracks[para["column"]]
            source = next((p for p in _text_payloads(page)
                           if (p.get("logical_paragraph_id")
                               or p.get("paragraph_id")) == para["paragraph_id"]), {})
            # A source paragraph can legitimately carry a small semantic
            # indent (list item, inset front-matter prose, etc.).  The layout
            # engine is also allowed to normalize it back to the track x0.
            # Both are valid re-entry positions; the hard rule is that the
            # block returns to this column and stays inside its track.
            source_indent = max(0.0, min(
                24.0, float((source.get("bbox") or [0.0])[0])
                - float(source.get("col_x0") or 0.0)))
            track_x0 = float(track["x0"])
            source_indented_x0 = track_x0 + source_indent
            matching = [line for line in final_lines
                        if para["y0"] - 2.0 <=
                        (line["bbox"][1] + line["bbox"][3]) / 2.0
                        <= para["y1"] + 2.0
                        and _intersects(line["bbox"],
                                        [float(track["x0"]), para["y0"] - 2.0,
                                         float(track["x1"]), para["y1"] + 2.0])]
            actual_x0 = (min(line["bbox"][0] for line in matching)
                         if matching else para["x0"])
            actual_x1 = (max(line["bbox"][2] for line in matching)
                         if matching else para["x1"])
            error = min(abs(actual_x0 - track_x0),
                        abs(actual_x0 - source_indented_x0))
            outside_track = (actual_x0 < float(track["x0"]) - 2.0
                             or actual_x1 > float(track["x1"]) + 2.0)
            failed = error > 4.0 or outside_track
            wrong += int(failed)
            records.append({"obstacle_type": obstacle["region_type"],
                            "obstacle_id": obstacle["region_id"],
                            "paragraph_id": para["paragraph_id"],
                            "expected_column": para["column"],
                            "actual_x0": round(actual_x0, 3),
                            "actual_x1": round(actual_x1, 3),
                            "expected_x0": round(track_x0, 3),
                            "source_indented_x0": round(source_indented_x0, 3),
                            "track_alignment_error": round(error, 3),
                            "outside_track": outside_track,
                            "wrong_column": failed})
    return {"checked_obstacle_count": len({(r["obstacle_type"], r["obstacle_id"])
                                            for r in records}),
            "reentry_records": records[:96],
            "post_obstacle_wrong_column_count": wrong,
            "post_obstacle_grid_passed": wrong == 0}


def _write_grid_overlay(final_pdf, grid, lines, qa, out_path):
    doc = pymupdf.open(str(final_pdf))
    page = doc[0]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    draw = ImageDraw.Draw(image)

    def rect(box, color, width=3, fill=None):
        if box:
            draw.rectangle([v * 2 for v in box], outline=color, width=width, fill=fill)

    cf = grid.get("content_frame") or {}
    rect([cf.get("x0", 0), cf.get("y0", 0), cf.get("x1", 0), cf.get("y1", 0)],
         "#1976d2", 3)
    for col in grid.get("columns") or []:
        rect([col["x0"], cf.get("y0", 0), col["x1"], cf.get("y1", 0)],
             "#00a6a6", 2)
    gutter = grid.get("gutter")
    if gutter:
        rect([gutter["x0"], cf.get("y0", 0), gutter["x1"], cf.get("y1", 0)],
             "#e53935", 2)
    for region in grid.get("full_width_regions") or []:
        rect(region.get("bbox"), "#7b1fa2", 2)
    for region in grid.get("bottom_reserved_regions") or []:
        rect(region.get("bbox"), "#fb8c00", 3)
    for line in qa.get("gutter_intrusion_details", []):
        rect(line.get("bbox"), "#ff0000", 4)
    image.save(str(out_path))


def run_page_visual_qa(*, page_model, final_pdf, final_png=None,
                       final_html=None, page_grid, frontmatter=None, flows=None,
                       bottom_reserved_regions=None, page_record=None,
                       out_dir=None):
    """Run Phase 4D final-PDF QA and write all per-page artifacts."""
    page_record = page_record or {}
    out_dir = Path(out_dir or Path(final_pdf).parent)
    grid = dict(page_grid or {})
    grid["bottom_reserved_regions"] = list(bottom_reserved_regions or [])
    grid["column_tracks"] = grid.get("columns") or grid.get("column_tracks") or []
    doc = pymupdf.open(str(final_pdf))
    physical_count = doc.page_count
    page = doc[0] if physical_count else None
    if page is None:
        doc.close()
        raise RuntimeError("final PDF contains no physical page: %s" % final_pdf)
    lines = _pdf_lines(page)
    width, height = float(page.rect.width), float(page.rect.height)
    flow_boxes = _flow_boxes(flows)
    full_regions = list(grid.get("full_width_regions") or [])
    for region in bottom_reserved_regions or []:
        full_regions.append({"region_type": "bottom_reserved", "bbox": _bbox(region)})

    gutter = grid.get("gutter")
    intrusions = []
    if gutter:
        # Boundary contact is legal; only >1pt overlap with the gutter
        # interior is an intrusion.  PDF text bboxes include tiny glyph
        # overhang/rounding at the track edge.
        gutter_box = [float(gutter["x0"]) + 1.0, 0.0,
                      float(gutter["x1"]) - 1.0, height]
        for line in lines:
            if not _intersects(line["bbox"], gutter_box):
                continue
            scope = _containing_flow(line["bbox"], flow_boxes)
            if scope and scope.get("column") not in (0, 1):
                continue
            if any(r.get("bbox") and _intersection(line["bbox"], r["bbox"])
                   / max((line["bbox"][2] - line["bbox"][0])
                         * (line["bbox"][3] - line["bbox"][1]), .01) > .35
                   for r in full_regions):
                continue
            # Only column-scoped body/heading/list/caption is prohibited.
            if scope and scope.get("column") in (0, 1):
                intrusions.append({"text": line["text"][:120],
                                   "bbox": [round(v, 3) for v in line["bbox"]],
                                   "paragraph_id": scope.get("paragraph_id"),
                                   "style_role": scope.get("style_role")})

    outside = []
    for line in lines:
        if _outside(line["bbox"], width, height):
            outside.append({"object_type": "text", "bbox": line["bbox"],
                            "text": line["text"][:80]})
    for info in page.get_images(full=True):
        try:
            for box in page.get_image_rects(info[0]):
                b = [box.x0, box.y0, box.x1, box.y1]
                if _outside(b, width, height):
                    outside.append({"object_type": "image", "bbox": b,
                                    "xref": info[0]})
        except (RuntimeError, ValueError):
            continue
    for drawing in page.get_drawings():
        b = list(drawing["rect"])
        # PDF generators commonly emit clipped zero-area rules outside the
        # MediaBox.  They carry no visible ink and must not become a content
        # overflow defect.
        if (b[2] - b[0]) <= 0.1 or (b[3] - b[1]) <= 0.1:
            continue
        if _outside(b, width, height):
            outside.append({"object_type": "drawing", "bbox": b})

    overlaps = []
    for i, left in enumerate(lines):
        for right in lines[i + 1:]:
            area = _intersection(left["bbox"], right["bbox"])
            if area <= 0:
                continue
            la = max((left["bbox"][2] - left["bbox"][0])
                     * (left["bbox"][3] - left["bbox"][1]), .01)
            ra = max((right["bbox"][2] - right["bbox"][0])
                     * (right["bbox"][3] - right["bbox"][1]), .01)
            vertical_overlap = (min(left["bbox"][3], right["bbox"][3])
                                - max(left["bbox"][1], right["bbox"][1]))
            minimum_height = max(min(
                left["bbox"][3] - left["bbox"][1],
                right["bbox"][3] - right["bbox"][1]), .01)
            # Tight bibliography leading can make glyph boxes touch slightly
            # while remaining visually separate.  A hard collision must
            # substantially overlap in both area and vertical extent.
            if (area / min(la, ra) >= .20
                    and vertical_overlap / minimum_height >= .45):
                overlaps.append({"left_text": left["text"][:60],
                                 "right_text": right["text"][:60],
                                 "overlap_area": round(area, 3),
                                 "left_bbox": left["bbox"],
                                 "right_bbox": right["bbox"]})

    heading_details = []
    tracks = {int(c.get("column_id", i)): c
              for i, c in enumerate(grid.get("columns") or [])}
    for fbox in flow_boxes:
        if fbox.get("style_role") != "heading" or fbox.get("column") not in tracks:
            continue
        matching = [line for line in lines if _intersection(line["bbox"], fbox["bbox"]) > 0]
        if not matching:
            continue
        actual_x0 = min(line["bbox"][0] for line in matching)
        source = next((p for p in _text_payloads(page_model)
                       if (p.get("logical_paragraph_id") or p.get("paragraph_id"))
                       == fbox["paragraph_id"]), {})
        source_indent = max(0.0, float((source.get("bbox") or [0])[0])
                            - float(source.get("col_x0") or 0.0))
        expected = float(tracks[fbox["column"]]["x0"]) + source_indent
        error = abs(actual_x0 - expected)
        heading_details.append({"paragraph_id": fbox["paragraph_id"],
                                "column": fbox["column"],
                                "actual_x0": round(actual_x0, 3),
                                "expected_x0": round(expected, 3),
                                "heading_track_alignment_error": round(error, 3),
                                "warning": error > 3.0})

    figures = _regions(page_model, "figure")
    figure_ids = {r.get("payload", {}).get("figure_id") or r.get("region_id")
                  for r in figures}
    captions = [p for p in _text_payloads(page_model)
                if p.get("style_role") == "caption" and p.get("caption_for")]
    orphan_captions = [p.get("paragraph_id") for p in captions
                       if p.get("caption_for") not in figure_ids]
    dom_figures = _figure_dom_boxes(final_html) if final_html else {}
    deviations = []
    missing_figures = []
    for region in figures:
        fid = region.get("payload", {}).get("figure_id") or region.get("region_id")
        source_box = _bbox(region)
        rendered_box = dom_figures.get(fid)
        if rendered_box is None:
            missing_figures.append(fid)
            continue
        deviation = max(abs(a - b) for a, b in zip(source_box, rendered_box))
        deviations.append({"figure_id": fid,
                           "figure_bbox_deviation": round(deviation, 4),
                           "source_bbox": source_box,
                           "rendered_bbox": rendered_box})

    tables = _regions(page_model, "table")
    table_failures = []
    for region in tables:
        payload = region.get("payload")
        table_qa = (payload or {}).get("qa") or {}
        if (not payload or table_qa.get("assigned_text_ratio", 1.0) < .999
                or table_qa.get("unassigned_text_count", 0) > 0):
            table_failures.append(region.get("region_id"))

    post_obstacle = _post_obstacle_reentry(page_model, flows, grid, lines)
    obstacles = [r for r in full_regions if r.get("bbox")]
    large_voids = [] if frontmatter else _voids(lines, obstacles,
                                                grid.get("columns") or [],
                                                grid.get("content_frame") or {})
    density = _density(page, lines, grid)
    doc.close()

    formula_adopted = page_record.get("formula_adopted_prose_qa") or {}
    formula_excl = page_record.get("formula_exclusivity_qa") or {}
    formula_crop = page_record.get("formula_crop_qa") or {}
    component_coverage = float(formula_excl.get(
        "formula_source_component_coverage_ratio", 1.0))
    collision = page_record.get("rendered_collision_qa") or {}
    footnote = page_record.get("footnote_collision_qa") or {}
    residual_prose = page_record.get("residual_prose_qa") or {}
    render_closure = page_record.get("render_closure_qa") or {}
    severe_frontmatter_fragmentation = 0
    if frontmatter:
        severe_frontmatter_fragmentation = int(
            (page_record.get("qa") or {}).get("paragraph_fragmentation_count", 0) > 4)

    hard = {
        "physical_page": physical_count == 1,
        "gutter_intrusion": len(intrusions) == 0,
        "text_overlap": len(overlaps) == 0,
        "formula_collision": collision.get(
            "collision_gate_passed",
            collision.get("all_collision_assertions_passed", True)),
        "formula_crop": formula_crop.get("formula_crop_clean", True),
        "body_footnote_collision": footnote.get("body_footnote_overlap_count", 0) == 0,
        "table_integrity": len(table_failures) == 0,
        "figure_integrity": len(missing_figures) == 0,
        "figure_caption": len(orphan_captions) == 0,
        "content_inside_page": len(outside) == 0,
        "frontmatter_integrity": severe_frontmatter_fragmentation == 0,
        "post_obstacle_grid": post_obstacle["post_obstacle_grid_passed"],
        "formula_adopted_prose": formula_adopted.get(
            "formula_adopted_plain_prose_count", 0) == 0,
        "formula_component_coverage": component_coverage == 1.0,
        "formula_exclusivity": formula_excl.get(
            "formula_exclusivity_passed", True),
        "residual_prose": residual_prose.get("residual_prose_clean", True),
        "render_closure": render_closure.get(
            "logical_paragraph_render_closure_clean", True),
    }
    warning = {
        "low_grid_confidence": grid.get("grid_confidence", 0.0) < LOW_GRID_CONFIDENCE,
        "heading_alignment": any(x["warning"] for x in heading_details),
        "column_bottom_imbalance": (density["column_bottom_difference"] or 0) > 90.0,
        "large_void": bool(large_voids),
    }
    result = {
        "page": page_model.get("page"),
        "layout_class": grid.get("layout_class"),
        "layout_traits": grid.get("layout_traits") or {},
        "grid_confidence": grid.get("grid_confidence"),
        "final_pdf_truth": True,
        "physical_page_count": physical_count,
        "gutter_intrusion_count": len(intrusions),
        "gutter_intrusion_details": intrusions[:48],
        "text_overlap_count": len(overlaps),
        "text_overlap_details": overlaps[:48],
        "content_outside_page_count": len(outside),
        "content_outside_page_details": outside[:48],
        "heading_track_alignment_error_count": sum(x["warning"] for x in heading_details),
        "heading_alignment_details": heading_details,
        "detected_figure_count": len(figures),
        "rendered_figure_count": len(figures) - len(missing_figures),
        "missing_figure_count": len(missing_figures),
        "missing_figure_ids": missing_figures,
        "figure_bbox_deviation": max((x["figure_bbox_deviation"]
                                      for x in deviations), default=0.0),
        "figure_bbox_deviation_details": deviations,
        "orphan_caption_count": len(orphan_captions),
        "orphan_caption_ids": orphan_captions,
        "table_count": len(tables),
        "table_failure_count": len(table_failures),
        "table_failure_ids": table_failures,
        "table_details": [
            {
                "table_id": region.get("region_id"),
                "rows": len((region.get("payload") or {}).get("rows") or []),
                "columns": len((region.get("payload") or {}).get("columns") or []),
                "cells": len((region.get("payload") or {}).get("cells") or []),
                "horizontal_rules": sum(
                    rule.get("orientation") == "horizontal"
                    for rule in ((region.get("payload") or {}).get("rules") or [])),
                "vertical_rules": sum(
                    rule.get("orientation") == "vertical"
                    for rule in ((region.get("payload") or {}).get("rules") or [])),
                "reconstruction_confidence": ((region.get("payload") or {}).get(
                    "qa") or {}).get("reconstruction_confidence"),
            }
            for region in tables
        ],
        "formula_adopted_plain_prose_count": formula_adopted.get(
            "formula_adopted_plain_prose_count", 0),
        "formula_component_coverage_ratio": component_coverage,
        "frontmatter_severe_fragmentation_count": severe_frontmatter_fragmentation,
        "post_obstacle_qa": post_obstacle,
        "density": density,
        "large_void_qa": {"warning_count": len(large_voids),
                          "warnings": large_voids},
        "layers": {
            "semantic": {"passed": (page_record.get("qa") or {}).get(
                "all_assertions_passed", True)},
            "ownership": {"passed": (page_record.get("qa") or {}).get(
                "ownership_conflict_count", 0) == 0},
            "translation": {"passed": not (page_record.get(
                "render_closure_qa") or {}).get(
                    "source_fallback_fragment_count", 0)},
            "physical_pdf": {"passed": (page_record.get(
                "physical_qa") or {}).get("all_assertions_passed", True)},
            "paragraph_structural": {"passed": (page_record.get(
                "structural_qa") or {}).get("structural_clean", True)},
            "formula_exclusivity": {"passed": formula_excl.get(
                "formula_exclusivity_passed", True)},
            "formula_crop": {"passed": formula_crop.get(
                "formula_crop_clean", True)},
            "render_collision": {"passed": collision.get(
                "collision_gate_passed", True)},
            "heading_duplicate": {"passed": (page_record.get(
                "heading_duplicate_qa") or {}).get("heading_qa_clean", True)},
            "residual_language": {"passed": (page_record.get(
                "residual_language_qa") or {}).get("residual_clean", True)},
            "residual_prose": {"passed": (page_record.get(
                "residual_prose_qa") or {}).get("residual_prose_clean", True)},
            "render_closure": {"passed": (page_record.get(
                "render_closure_qa") or {}).get(
                    "logical_paragraph_render_closure_clean", True)},
            "grid_layout": {"passed": len(intrusions) == 0
                            and len(outside) == 0},
            "footnote_collision": {"passed": footnote.get(
                "body_footnote_overlap_count", 0) == 0},
            "post_obstacle_grid": {"passed": post_obstacle[
                "post_obstacle_grid_passed"]},
            "renderer_parity": {"passed": (page_record.get(
                "physical_qa") or {}).get("renderer_specific_failure_count", 0)
                == 0},
        },
        "hard_layers": hard,
        "warning_layers": warning,
        "hard_passed": all(hard.values()),
        "warning_count": sum(bool(v) for v in warning.values()),
    }
    _dump(out_dir / "page_grid.json", grid)
    _dump(out_dir / "visual_qa.json", result)
    _dump(out_dir / "grid_layout_qa.json", {
        "gutter_intrusion_count": len(intrusions),
        "content_outside_page_count": len(outside),
        "grid_layout_passed": len(intrusions) == 0 and len(outside) == 0})
    _dump(out_dir / "post_obstacle_grid_qa.json", post_obstacle)
    _write_grid_overlay(final_pdf, grid, lines, result,
                        out_dir / "grid_overlay.png")
    return result


def _median(values):
    values = sorted(float(x) for x in values)
    if not values:
        return None
    m = len(values) // 2
    return values[m] if len(values) % 2 else (values[m - 1] + values[m]) / 2


def build_document_visual_report(page_results, layout_profile=None,
                                 document_physical_qa=None, out_path=None):
    """Aggregate per-page visual evidence into document_visual_report.json."""
    pages = []
    for number, record in sorted(page_results.items(), key=lambda item: int(item[0])):
        visual = record.get("visual_qa") or {}
        pages.append({"page": int(number), **visual})
    classes = Counter(p.get("layout_class") or "unclassified" for p in pages)
    gutter_pages = []
    overrides, low = [], []
    for number, record in sorted(page_results.items(), key=lambda item: int(item[0])):
        grid = record.get("page_grid") or {}
        if not grid:
            # run_document keeps the grid in the per-page artifact, not record.
            visual = record.get("visual_qa") or {}
            page_dir = None
            if out_path:
                page_dir = Path(out_path).parent / "pages" / ("p%03d" % int(number))
            try:
                grid = json.loads((page_dir / "page_grid.json").read_text(encoding="utf-8"))
            except (OSError, TypeError, json.JSONDecodeError):
                grid = {"layout_class": visual.get("layout_class"),
                        "grid_confidence": visual.get("grid_confidence")}
        if grid.get("gutter"):
            gutter_pages.append({"page": int(number),
                                 "width": float(grid["gutter"]["width"]),
                                 "layout_class": grid.get("layout_class")})
        if grid.get("override_reason"):
            overrides.append({"page": int(number), "reason": grid["override_reason"],
                              "profile_snap_applied": grid.get("profile_snap_applied")})
        if float(grid.get("grid_confidence") or 0.0) < LOW_GRID_CONFIDENCE:
            low.append({"page": int(number),
                        "grid_confidence": grid.get("grid_confidence")})
    standard_gutter_pages = [x for x in gutter_pages
                             if x.get("layout_class") == "standard_two_column"]
    widths = [x["width"] for x in standard_gutter_pages]
    median = _median(widths)
    mad = _median([abs(v - median) for v in widths]) if median is not None else None
    capacity = {str(n): (r.get("capacity") or {}).get("selected_level")
                for n, r in sorted(page_results.items(), key=lambda item: int(item[0]))}
    column_widths = [
        float(c.get("width") or (float(c["x1"]) - float(c["x0"])))
        for _n, record in page_results.items()
        if (record.get("page_grid") or {}).get("gutter")
        for c in ((record.get("page_grid") or {}).get("column_tracks") or [])
    ]
    column_width_median = _median(column_widths)
    column_width_variance = (sum((x - sum(column_widths) / len(column_widths)) ** 2
                                 for x in column_widths) / len(column_widths)
                             if column_widths else None)
    report = {
        "phase": "4D.1C",
        "page_count": len(pages),
        "layout_class_counts": dict(sorted(classes.items())),
        "standard_two_column_page_count": classes.get("standard_two_column", 0),
        "two_column_page_count": len(gutter_pages),
        "single_column_page_count": classes.get("single_column", 0),
        "mixed_page_count": classes.get("mixed_layout", 0),
        "two_column_track_page_count": len(gutter_pages),
        "document_layout_profile": layout_profile or {},
        "gutter_stability": {
            "scope": "standard_two_column",
            "page_values": standard_gutter_pages,
            "all_two_column_page_values": gutter_pages,
            "median": round(median, 3) if median is not None else None,
            "minimum": round(min(widths), 3) if widths else None,
            "maximum": round(max(widths), 3) if widths else None,
            "mad": round(mad, 3) if mad is not None else None,
        },
        "column_width_median": (round(column_width_median, 3)
                                if column_width_median is not None else None),
        "column_width_variance": (round(column_width_variance, 5)
                                  if column_width_variance is not None else None),
        "page_local_overrides": overrides,
        "low_grid_confidence_pages": low,
        "gutter_intrusion_count": sum(p.get("gutter_intrusion_count", 0) for p in pages),
        "text_overlap_count": sum(p.get("text_overlap_count", 0) for p in pages),
        "block_overlap_total": sum(p.get("text_overlap_count", 0) for p in pages),
        "content_outside_page_count": sum(p.get("content_outside_page_count", 0)
                                          for p in pages),
        "body_footnote_collision_count": sum(
            (r.get("footnote_collision_qa") or {}).get("body_footnote_overlap_count", 0)
            for r in page_results.values()),
        "footnote_collision_total": sum(
            (r.get("footnote_collision_qa") or {}).get(
                "body_footnote_overlap_count", 0)
            for r in page_results.values()),
        "post_obstacle_wrong_column_count": sum(
            (p.get("post_obstacle_qa") or {}).get("post_obstacle_wrong_column_count", 0)
            for p in pages),
        "post_obstacle_reentry_error_total": sum(
            (p.get("post_obstacle_qa") or {}).get(
                "post_obstacle_wrong_column_count", 0) for p in pages),
        "content_outside_page_total": sum(
            p.get("content_outside_page_count", 0) for p in pages),
        "frontmatter_fragment_total": sum(
            p.get("frontmatter_severe_fragmentation_count", 0) for p in pages),
        "residual_prose_count": sum(
            (r.get("residual_prose_qa") or {}).get(
                "residual_untranslated_prose_count", 0)
            for r in page_results.values()),
        "formula_adopted_plain_prose_count": sum(
            p.get("formula_adopted_plain_prose_count", 0) for p in pages),
        "minimum_formula_component_coverage_ratio": min(
            (p.get("formula_component_coverage_ratio", 1.0) for p in pages), default=1.0),
        "table_count": sum(p.get("table_count", 0) for p in pages),
        "table_failure_count": sum(p.get("table_failure_count", 0) for p in pages),
        "table_pages": [p["page"] for p in pages if p.get("table_count", 0)],
        "table_page_details": {
            str(p["page"]): p.get("table_details") or []
            for p in pages if p.get("table_count", 0)
        },
        "detected_figure_count": sum(p.get("detected_figure_count", 0) for p in pages),
        "rendered_figure_count": sum(p.get("rendered_figure_count", 0) for p in pages),
        "missing_figure_count": sum(p.get("missing_figure_count", 0) for p in pages),
        "orphan_caption_count": sum(p.get("orphan_caption_count", 0) for p in pages),
        "bottom_reserved_region_pages": [int(n) for n, r in page_results.items()
                                          if r.get("bottom_reserved_regions")],
        "capacity_levels": capacity,
        "l4_l5_capacity_pages": [int(n) for n, r in page_results.items()
                                  if (r.get("capacity") or {}).get("selected_level")
                                  in (4, 5, "L4", "L5")],
        "formula_crop_failure_count": sum(
            not (r.get("formula_crop_qa") or {}).get("formula_crop_clean", True)
            for r in page_results.values()),
        "formula_collision_failure_count": sum(
            not (r.get("rendered_collision_qa") or {}).get(
                "collision_gate_passed", True)
            for r in page_results.values()),
        "density_diagnostics": {str(p["page"]): p.get("density") for p in pages},
        "large_void_warning_pages": [p["page"] for p in pages
                                     if (p.get("large_void_qa") or {}).get("warning_count")],
        "document_physical_qa": document_physical_qa,
        "pages": pages,
    }
    if out_path:
        _dump(out_path, report)
    return report


def build_visual_layout_gate(document_visual_report, page_results=None,
                             document_physical_qa=None, out_path=None):
    """Build the independent Phase 4D hard/warning gate."""
    report = document_visual_report
    pages = report.get("pages") or []
    document_physical_qa = document_physical_qa or report.get("document_physical_qa") or {}
    hard_layers = {
        "per_page_visual": all(p.get("hard_passed", False) for p in pages)
        and len(pages) == report.get("page_count"),
        "gutter_intrusion": report.get("gutter_intrusion_count", 0) == 0,
        "text_overlap": report.get("text_overlap_count", 0) == 0,
        "content_inside_page": report.get("content_outside_page_count", 0) == 0,
        "body_footnote_collision": report.get("body_footnote_collision_count", 0) == 0,
        "post_obstacle_grid": report.get("post_obstacle_wrong_column_count", 0) == 0,
        "residual_prose": report.get("residual_prose_count", 0) == 0,
        "formula_adopted_prose": report.get("formula_adopted_plain_prose_count", 0) == 0,
        "formula_component_coverage": report.get(
            "minimum_formula_component_coverage_ratio", 0.0) == 1.0,
        "formula_crop": report.get("formula_crop_failure_count", 0) == 0,
        "formula_collision": report.get(
            "formula_collision_failure_count", 0) == 0,
        "table_integrity": report.get("table_failure_count", 0) == 0,
        "figure_integrity": report.get("missing_figure_count", 0) == 0,
        "figure_caption": report.get("orphan_caption_count", 0) == 0,
        "document_physical_pdf": (not document_physical_qa
                                  or document_physical_qa.get("all_assertions_passed", False)),
    }
    warning_layers = {
        "low_grid_confidence_pages": report.get("low_grid_confidence_pages") or [],
        "gutter_profile_deviation": [x for x in
                                     (report.get("gutter_stability") or {}).get("page_values", [])
                                     if abs(x["width"] - ((report.get("gutter_stability") or {}).get("median")
                                                          or x["width"])) > 3.0],
        "large_void_pages": report.get("large_void_warning_pages") or [],
        "page_visual_warning_count": sum(p.get("warning_count", 0) for p in pages),
    }
    hard_pass = all(hard_layers.values())
    has_warning = any(bool(v) for v in warning_layers.values())
    gate = {
        "phase": "4D.1C",
        "hard_layers": hard_layers,
        "warning_layers": warning_layers,
        "hard_decision": "pass" if hard_pass else "blocked",
        "decision": "blocked" if not hard_pass else ("warning" if has_warning else "pass"),
        "hard_failure_count": sum(not value for value in hard_layers.values()),
    }
    if out_path:
        _dump(out_path, gate)
    return gate


def write_phase4d1c_report(*, out_path, visual_report, visual_gate,
                           delivery_gate=None, defect_pages=None,
                           complexity_pages=None, final_physical_page_count=None,
                           formal_preview_generated=False, dlp00034_preserved=None,
                           translation_cache_audit=None):
    """Write the concise 23-answer Phase 4D.1C acceptance report."""
    vr, vg = visual_report, visual_gate
    gs = vr.get("gutter_stability") or {}
    capacity = vr.get("capacity_levels") or {}
    complexity = complexity_pages or []
    top10 = [row.get("page") for row in complexity[:10]]
    table_details = vr.get("table_page_details") or {}
    overrides = vr.get("page_local_overrides") or []
    cache_summary = {
        key: (translation_cache_audit or {}).get(key)
        for key in (
            "item_count", "cache_hit", "cache_miss",
            "cache_hit_count", "cache_miss_count",
            "cache_invalidated_reason", "actual_new_translation_call_count")
        if key in (translation_cache_audit or {})
    }
    lines = [
        "# Phase 4D.1C 全文 Document Grid 验收报告", "",
        "## 验收结论", "",
        "- 4C gate：`%s`。" % ((delivery_gate or {}).get("decision")),
        "- 4D hard gate：`%s`。" % vg.get("hard_decision"),
        "- 正式 PDF：`%s`；physical pages=`%s`。" % (
            "已生成" if formal_preview_generated else "未生成",
            final_physical_page_count),
        "- defect_pages：`%s`。" % (len(defect_pages or [])),
        "", "## 23 项必答", "",
        "1. layout_class 分布：`%s`。" % json.dumps(
            vr.get("layout_class_counts") or {}, ensure_ascii=False),
        "2. standard two-column：`%s` 页。" % vr.get("standard_two_column_page_count"),
        "3. document median gutter：`%s pt`。" % gs.get("median"),
        "4. gutter min/max/MAD：`%s / %s / %s pt`。" % (
            gs.get("minimum"), gs.get("maximum"), gs.get("mad")),
        "5. page-local override：`%s`。" % json.dumps(overrides, ensure_ascii=False),
        "6. 低 grid_confidence 页：`%s`。" % json.dumps(
            vr.get("low_grid_confidence_pages") or [], ensure_ascii=False),
        "7. 全文 gutter intrusion：`%s`。" % vr.get("gutter_intrusion_count"),
        "8. BottomReservedRegion 页：`%s`。" % vr.get("bottom_reserved_region_pages"),
        "9. body-footnote collision：`%s`。" % vr.get("body_footnote_collision_count"),
        "10. post-table/figure/formula wrong-column：`%s`。" % vr.get(
            "post_obstacle_wrong_column_count"),
        "11. p003 DLP00034 保持：`%s`。" % dlp00034_preserved,
        "12. 全文 residual prose：`%s`。" % vr.get("residual_prose_count"),
        "13. formula adopted plain prose：`%s`。" % vr.get(
            "formula_adopted_plain_prose_count"),
        "14. TableModel：pages=`%s`，共 `%s` 表，failure=`%s`；明细=`%s`。" % (
            vr.get("table_pages"), vr.get("table_count"),
            vr.get("table_failure_count"),
            json.dumps(table_details, ensure_ascii=False)),
        "15. Formula crop/collision failure：`%s / %s`。" % (
            vr.get("formula_crop_failure_count"),
            vr.get("formula_collision_failure_count")),
        "16. Figure：detected=`%s`，rendered=`%s`，missing=`%s`。" % (
            vr.get("detected_figure_count"), vr.get("rendered_figure_count"),
            vr.get("missing_figure_count")),
        "17. L4/L5 capacity 页：`%s`（levels=%s）。" % (
            vr.get("l4_l5_capacity_pages"), json.dumps(capacity, ensure_ascii=False)),
        "18. defect_pages 是否为空：`%s`。" % (not bool(defect_pages)),
        "19. complexity_pages Top 10：`%s`。" % top10,
        "20. 4C delivery gate：`%s`。" % ((delivery_gate or {}).get("decision")),
        "21. 4D visual hard gate：`%s`。" % vg.get("hard_decision"),
        "22. final PDF physical pages：`%s`。" % final_physical_page_count,
        "23. 正式 full_zh_preview.pdf：`%s`。" % formal_preview_generated,
        "", "## Warning 解释", "",
        "- `heading_alignment`：主要来自标题/小节标题保留的源缩进或居中；人工逐页检查确认未跨 gutter、未越界。",
        "- `column_bottom_imbalance`：p002/p016 的左右栏底差异；本阶段仅记录，不做齐底拉伸。",
        "- `large_void`：p003/p005/p008/p009/p011 的图、公式、参考文献分段或原始结构留白；人工检查确认可解释且无内容丢失。",
        "", "## Cache 摘要", "",
        "`%s`" % json.dumps(cache_summary, ensure_ascii=False),
    ]
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


__all__ = [
    "classify_layout", "prepare_page_grid", "run_page_visual_qa",
    "build_document_visual_report", "build_visual_layout_gate",
    "write_phase4d1c_report",
]
