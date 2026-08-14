# -*- coding: utf-8 -*-
"""PDF-bbox spacing, density, hierarchy and vertical-rhythm metrics."""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from typing import Any, Iterable

try:
    from .extract_typography import bbox_union, percentile, stats
except ImportError:  # direct script execution
    from extract_typography import bbox_union, percentile, stats  # type: ignore


BODY_ROLES = {"body", "body_bold_lead", "abstract_body", "reference"}
HEADING_ROLES = {"section_heading", "subsection_heading", "subsubsection_heading"}


def _visible_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _group_lines(lines: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        grouped[str(line.get("paragraph_id") or "unmapped")].append(line)
    for rows in grouped.values():
        rows.sort(key=lambda row: (float((row.get("bbox") or [0, 0])[1]),
                                   float((row.get("bbox") or [0])[0])))
    return grouped


def _logical_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge PDF fragments that share one visual baseline.

    PyMuPDF may expose a bold lead, a protected Latin token, and the CJK
    continuation as separate line records even though the browser placed all
    three on one line.  Typography line counts and baseline gaps must count
    that visual line once.
    """
    grouped: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        baseline = line.get("baseline")
        if baseline is None:
            bbox = line.get("bbox") or [0, 0, 0, 0]
            baseline = bbox[3]
        grouped[round(float(baseline), 1)].append(line)
    result = []
    for _, rows in sorted(grouped.items()):
        spans = sorted(
            [span for row in rows for span in row.get("spans", [])],
            key=lambda span: ((span.get("bbox") or [0])[0],
                              (span.get("bbox") or [0, 0])[1]),
        )
        boxes = [row["bbox"] for row in rows if row.get("bbox")]
        baselines = [float(row["baseline"]) for row in rows
                     if row.get("baseline") is not None]
        result.append({
            "paragraph_id": rows[0].get("paragraph_id"),
            "role": rows[0].get("role"),
            "text": "".join(span.get("text") or "" for span in spans),
            "bbox": bbox_union(boxes),
            "baseline": statistics.median(baselines) if baselines else None,
            "spans": spans,
        })
    return result


def _block_record(pid: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
    logical_lines = _logical_lines(lines)
    bbox = bbox_union(line["bbox"] for line in logical_lines if line.get("bbox"))
    baselines = sorted(set(float(line["baseline"]) for line in logical_lines
                           if line.get("baseline") is not None))
    gaps = [b - a for a, b in zip(baselines, baselines[1:]) if .5 < b - a < 80]
    text = "".join(line.get("text") or "" for line in logical_lines)
    widths = [(line["bbox"][2] - line["bbox"][0]) for line in logical_lines if line.get("bbox")]
    sizes = [float(span.get("size") or 0) for line in logical_lines
             for span in line.get("spans", []) if span.get("size")]
    return {
        "paragraph_id": pid,
        "role": lines[0].get("role") or "body",
        "text": text,
        "bbox": bbox,
        "line_count": len(logical_lines),
        "character_count": _visible_length(text),
        "characters_per_line": round(_visible_length(text) / max(len(logical_lines), 1), 4),
        "average_occupied_width_pt": round(statistics.mean(widths), 4) if widths else None,
        "block_height_pt": round(bbox[3] - bbox[1], 4) if bbox else None,
        "font_size_median_pt": percentile(sizes, .5),
        "baseline_gaps_pt": [round(value, 4) for value in gaps],
        "baseline_gap_median_pt": percentile(gaps, .5),
    }


def build_blocks(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_block_record(pid, rows) for pid, rows in _group_lines(lines).items()]


def density_metrics(page: dict[str, Any]) -> dict[str, Any]:
    source_blocks = {block["paragraph_id"]: block
                     for block in build_blocks(page["source"]["lines"])}
    current_blocks = {block["paragraph_id"]: block
                      for block in build_blocks(page["current_zh"]["lines"])}
    records = []
    for pid in sorted(set(source_blocks) & set(current_blocks)):
        source, current = source_blocks[pid], current_blocks[pid]
        if source["role"] in {"table_header", "table_body"}:
            continue
        source_height = source.get("block_height_pt") or 0
        current_height = current.get("block_height_pt") or 0
        records.append({
            "paragraph_id": pid,
            "role": current["role"],
            "source_line_count": source["line_count"],
            "zh_line_count": current["line_count"],
            "source_characters_per_line": source["characters_per_line"],
            "zh_characters_per_line": current["characters_per_line"],
            "source_average_occupied_width_pt": source["average_occupied_width_pt"],
            "zh_average_occupied_width_pt": current["average_occupied_width_pt"],
            "source_block_height_pt": source_height,
            "zh_block_height_pt": current_height,
            "line_count_expansion_ratio": round(
                current["line_count"] / max(source["line_count"], 1), 4),
            "height_expansion_ratio": round(
                current_height / source_height, 4) if source_height else None,
        })
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["role"]].append(record)
    by_role = {}
    for role, rows in grouped.items():
        by_role[role] = {
            "sample_count": len(rows),
            "source_characters_per_line": stats(
                row["source_characters_per_line"] for row in rows),
            "zh_characters_per_line": stats(
                row["zh_characters_per_line"] for row in rows),
            "line_count_expansion_ratio": stats(
                row["line_count_expansion_ratio"] for row in rows),
            "height_expansion_ratio": stats(
                row["height_expansion_ratio"] for row in rows
                if row["height_expansion_ratio"] is not None),
        }
    return {"paragraph_records": records, "by_role": by_role,
            "density_measured": bool(records)}


def line_height_metrics(page: dict[str, Any]) -> dict[str, Any]:
    blocks = build_blocks(page["current_zh"]["lines"])
    records = []
    for block in blocks:
        size, gap = block.get("font_size_median_pt"), block.get("baseline_gap_median_pt")
        if not size or not gap:
            continue
        records.append({
            "paragraph_id": block["paragraph_id"],
            "role": block["role"],
            "font_size_pt": size,
            "baseline_gap_pt": gap,
            "line_height_ratio": round(gap / size, 4),
            "line_count": block["line_count"],
        })
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["role"]].append(record)
    return {
        "records": records,
        "by_role": {
            role: {
                "sample_count": len(rows),
                "font_size_pt": stats(row["font_size_pt"] for row in rows),
                "baseline_gap_pt": stats(row["baseline_gap_pt"] for row in rows),
                "line_height_ratio": stats(row["line_height_ratio"] for row in rows),
            } for role, rows in grouped.items()
        },
        "final_pdf_baselines_measured": bool(records),
    }


def _column_for(block: dict[str, Any], page: dict[str, Any]) -> int | None:
    bbox = block.get("bbox")
    if not bbox:
        return None
    grid = page.get("page_grid") or {}
    columns = grid.get("columns") or grid.get("column_tracks") or []
    cx = (bbox[0] + bbox[2]) / 2.0
    for index, column in enumerate(columns):
        if float(column["x0"]) - 2 <= cx <= float(column["x1"]) + 2:
            return int(column.get("column_id", index))
    return -1


def _object_boxes(page: dict[str, Any]) -> list[dict[str, Any]]:
    objects = []
    for record in page.get("dom_snapshot", {}).get("display_formula", []):
        objects.append({"kind": "display_formula", "role": "display_formula",
                        "bbox": record.get("bbox_pt")})
    for table in page.get("tables", []):
        objects.append({"kind": "table", "role": "table", "bbox": table["bbox"]})
    model_regions = page.get("model_regions") or []
    for region in model_regions:
        if region.get("type") == "figure" and region.get("bbox"):
            objects.append({"kind": "figure", "role": "figure", "bbox": region["bbox"]})
    return objects


def vertical_rhythm(page: dict[str, Any]) -> dict[str, Any]:
    blocks = [block for block in build_blocks(page["current_zh"]["lines"])
              if block.get("bbox") and block["paragraph_id"] != "unmapped"]
    for block in blocks:
        block["column"] = _column_for(block, page)
    records = []
    for column in sorted({block["column"] for block in blocks if block["column"] is not None}):
        rows = sorted((block for block in blocks if block["column"] == column),
                      key=lambda block: (block["bbox"][1], block["bbox"][0]))
        for first, second in zip(rows, rows[1:]):
            gap = second["bbox"][1] - first["bbox"][3]
            if gap < -2 or gap > 160:
                continue
            if first["role"] in HEADING_ROLES and second["role"] in BODY_ROLES:
                relation = "heading_to_body"
            elif first["role"] in BODY_ROLES and second["role"] in BODY_ROLES:
                relation = "paragraph_to_paragraph"
            elif first["role"] == "caption" and second["role"] in BODY_ROLES:
                relation = "caption_to_body"
            elif first["role"] == "list_item" and second["role"] == "list_item":
                relation = "list_item_gap"
            else:
                relation = f"{first['role']}_to_{second['role']}"
            records.append({
                "relation": relation,
                "from_paragraph_id": first["paragraph_id"],
                "to_paragraph_id": second["paragraph_id"],
                "column": column,
                "gap_pt": round(gap, 4),
            })

    # Geometry-locked object relations.  Measurements use final PDF text
    # bboxes and final HTML object bboxes (PDF points).
    objects = _object_boxes(page)
    for obj in objects:
        ob = obj["bbox"]
        above = [block for block in blocks if block["bbox"][3] <= ob[1] + 1
                 and min(block["bbox"][2], ob[2]) - max(block["bbox"][0], ob[0]) > 10]
        below = [block for block in blocks if block["bbox"][1] >= ob[3] - 1
                 and min(block["bbox"][2], ob[2]) - max(block["bbox"][0], ob[0]) > 10]
        if above:
            first = max(above, key=lambda block: block["bbox"][3])
            relation = ("body_to_display_formula" if obj["kind"] == "display_formula"
                        else f"{first['role']}_to_{obj['kind']}")
            records.append({"relation": relation,
                            "from_paragraph_id": first["paragraph_id"],
                            "to_object": obj["kind"],
                            "gap_pt": round(ob[1] - first["bbox"][3], 4)})
        if below:
            second = min(below, key=lambda block: block["bbox"][1])
            if obj["kind"] == "display_formula":
                relation = "display_formula_to_body"
            elif obj["kind"] == "figure" and second["role"] == "caption":
                relation = "figure_to_caption"
            elif obj["kind"] == "table" and second["role"] == "caption":
                relation = "table_to_caption"
            else:
                relation = f"{obj['kind']}_to_{second['role']}"
            records.append({"relation": relation, "from_object": obj["kind"],
                            "to_paragraph_id": second["paragraph_id"],
                            "gap_pt": round(second["bbox"][1] - ob[3], 4)})

    grouped: dict[str, list[float]] = defaultdict(list)
    for record in records:
        grouped[record["relation"]].append(record["gap_pt"])
    return {
        "records": records,
        "by_relation": {relation: stats(values) for relation, values in grouped.items()},
        "vertical_rhythm_measured": bool(records),
        "truth_source": "final PDF text bboxes + final HTML locked-object bboxes",
    }


def list_metrics(page: dict[str, Any]) -> dict[str, Any]:
    records = []
    for pid, lines in _group_lines(page["current_zh"]["lines"]).items():
        if not lines or lines[0].get("role") != "list_item":
            continue
        unique_lines = _logical_lines(lines)
        first_chars = [char for span in unique_lines[0].get("spans", [])
                       for char in span.get("chars", []) if char.get("c") and not char["c"].isspace()]
        if not first_chars:
            continue
        bullet = first_chars[0]
        text_char = next((char for char in first_chars[1:] if char["c"] not in "•·●○"), None)
        continuation_x = None
        continuation_xs = []
        for continuation_line in unique_lines[1:]:
            continuation = [char for span in continuation_line.get("spans", [])
                            for char in span.get("chars", [])
                            if char.get("c") and not char["c"].isspace()]
            if continuation:
                continuation_xs.append(continuation[0]["bbox"][0])
        if continuation_xs:
            continuation_x = min(continuation_xs)
        bullet_x = bullet["bbox"][0]
        text_x = text_char["bbox"][0] if text_char else None
        records.append({
            "paragraph_id": pid,
            "bullet": bullet["c"],
            "bullet_x_pt": round(bullet_x, 4),
            "text_x_pt": round(text_x, 4) if text_x is not None else None,
            "continuation_line_x_pt": round(continuation_x, 4) if continuation_x is not None else None,
            "bullet_to_text_gap_pt": round(text_x - bullet["bbox"][2], 4)
            if text_x is not None else None,
            "hanging_indent_pt": round(continuation_x - bullet_x, 4)
            if continuation_x is not None else None,
            "font_size_pt": percentile(
                [span.get("size") for line in unique_lines for span in line.get("spans", [])], .5),
            "baseline_gap_pt": percentile(
                [b["baseline"] - a["baseline"] for a, b in zip(unique_lines, unique_lines[1:])
                 if a.get("baseline") is not None and b.get("baseline") is not None], .5),
        })
    return {
        "records": records,
        "hanging_indent_pt": stats(record["hanging_indent_pt"] for record in records
                                   if record["hanging_indent_pt"] is not None),
        "bullet_to_text_gap_pt": stats(record["bullet_to_text_gap_pt"] for record in records
                                       if record["bullet_to_text_gap_pt"] is not None),
    }


def heading_metrics(page: dict[str, Any]) -> dict[str, Any]:
    blocks = build_blocks(page["current_zh"]["lines"])
    records = []
    for block in blocks:
        if block["role"] not in HEADING_ROLES:
            continue
        lines = _group_lines(page["current_zh"]["lines"]).get(block["paragraph_id"], [])
        chars = [char for span in (lines[0].get("spans", []) if lines else [])
                 for char in span.get("chars", []) if char.get("c") and not char["c"].isspace()]
        visible = "".join(char["c"] for char in chars)
        match = re.match(r"(?:[A-Z]|\d+)(?:\.\d+){0,3}", visible)
        number_gap = None
        number = None
        if match and chars:
            number = match.group()
            end_index = len(match.group()) - 1
            if end_index + 1 < len(chars):
                number_gap = chars[end_index + 1]["bbox"][0] - chars[end_index]["bbox"][2]
        font_names = [span.get("font") for line in lines for span in line.get("spans", [])]
        records.append({
            "paragraph_id": block["paragraph_id"],
            "role": block["role"],
            "text": block["text"][:120],
            "number": number,
            "number_title_gap_pt": round(number_gap, 4) if number_gap is not None else None,
            "font_size_pt": block["font_size_median_pt"],
            "font_weight_signal": "bold_or_type3" if any(
                font and ("Type3" in font or "Bold" in font or "Medi" in font)
                for font in font_names) else "regular",
            "left_x_pt": round(block["bbox"][0], 4) if block.get("bbox") else None,
            "baseline_pt": round(lines[0]["baseline"], 4) if lines and lines[0].get("baseline") else None,
        })
    return {"records": records,
            "number_title_gap_pt": stats(record["number_title_gap_pt"] for record in records
                                         if record["number_title_gap_pt"] is not None)}


def frontmatter_metrics(page: dict[str, Any]) -> dict[str, Any]:
    if page.get("page") != 1:
        return {"applicable": False}
    blocks = build_blocks(page["current_zh"]["lines"])
    by_role: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        by_role[block["role"]].append(block)

    def union(role: str) -> list[float] | None:
        return bbox_union(block["bbox"] for block in by_role.get(role, []) if block.get("bbox"))

    boxes = {role: union(role) for role in (
        "document_title", "author", "affiliation", "abstract_heading",
        "abstract_body", "footnote", "body")}
    title_lines = sum(block["line_count"] for block in by_role["document_title"])
    title_widths = [line["bbox"][2] - line["bbox"][0]
                    for line in page["current_zh"]["lines"]
                    if line.get("role") == "document_title" and line.get("bbox")]
    author_baselines = sorted(set(line["baseline"] for line in page["current_zh"]["lines"]
                              if line.get("role") == "author" and line.get("baseline") is not None)
                              )
    author_line_gaps = [b - a for a, b in zip(author_baselines, author_baselines[1:])
                        if 2 < b - a < 40]
    content_center = page["geometry"]["current"]["width_pt"] / 2.0
    title_box = boxes["document_title"]
    title_center_error = abs((title_box[0] + title_box[2]) / 2 - content_center) if title_box else None

    def gap(top: str, bottom: str) -> float | None:
        a, b = boxes.get(top), boxes.get(bottom)
        return round(b[1] - a[3], 4) if a and b else None

    # Author and affiliation are different rows, so comparing their absolute
    # y coordinates would only re-label the inter-block gap as a "baseline
    # delta".  Compare the baseline offset within each glyph box instead.
    def normalized_offsets(role: str, script: str) -> list[float]:
        values = []
        for line in page["current_zh"]["lines"]:
            if line.get("role") != role:
                continue
            for span in line.get("spans", []):
                text = span.get("text") or ""
                has_script = (re.search(r"[A-Za-z]", text) if script == "Latin"
                              else re.search(r"[\u3400-\u9fff]", text))
                origin, bbox, size = span.get("origin"), span.get("bbox"), span.get("size")
                if has_script and origin and bbox and size:
                    values.append((float(origin[1]) - float(bbox[1])) / float(size))
        return values

    author_offsets = normalized_offsets("author", "Latin")
    affiliation_offsets = normalized_offsets("affiliation", "CJK")
    author_offset = percentile(author_offsets, .5)
    affiliation_offset = percentile(affiliation_offsets, .5)
    normalized_delta = (abs(author_offset - affiliation_offset)
                        if author_offset is not None and affiliation_offset is not None
                        else None)

    return {
        "applicable": True,
        "role_bboxes": boxes,
        "title_line_count": title_lines,
        "title_max_line_width_pt": round(max(title_widths), 4) if title_widths else None,
        "title_width_to_page_ratio": round(max(title_widths) / page["geometry"]["current"]["width_pt"], 4)
        if title_widths else None,
        "title_center_error_pt": round(title_center_error, 4) if title_center_error is not None else None,
        "author_line_gap_pt": stats(author_line_gaps),
        "title_to_author_gap_pt": gap("document_title", "author"),
        "author_to_affiliation_gap_pt": gap("author", "affiliation"),
        "affiliation_to_abstract_heading_gap_pt": gap("affiliation", "abstract_heading"),
        "abstract_heading_to_body_gap_pt": gap("abstract_heading", "abstract_body"),
        "abstract_heading_body_font_difference_pt": (
            round((percentile([block["font_size_median_pt"] for block in by_role["abstract_heading"]], .5) or 0)
                  - (percentile([block["font_size_median_pt"] for block in by_role["abstract_body"]], .5) or 0), 4)
            if by_role["abstract_heading"] and by_role["abstract_body"] else None),
        "latin_author_baseline_offset_em": stats(author_offsets),
        "cjk_affiliation_baseline_offset_em": stats(affiliation_offsets),
        "latin_author_cjk_affiliation_normalized_baseline_delta_em": (
            round(normalized_delta, 4) if normalized_delta is not None else None),
        "latin_author_cjk_affiliation_baseline_consistent": (
            normalized_delta <= .12 if normalized_delta is not None else None),
        "baseline_comparison_note": (
            "Compared PDF span.origin baseline offset within each glyph bbox; "
            "absolute row-to-row y distance is reported separately as author_to_affiliation_gap_pt."
        ),
    }


def table_metrics(page: dict[str, Any]) -> dict[str, Any]:
    lines = page["current_zh"]["lines"]
    header = [span for line in lines if line.get("role") == "table_header"
              for span in line.get("spans", [])]
    body = [span for line in lines if line.get("role") == "table_body"
            for span in line.get("spans", [])]
    header_size = percentile([span["size"] for span in header], .5)
    body_size = percentile([span["size"] for span in body], .5)
    numeric = [span for span in body if re.fullmatch(r"[\d.,%+\-]+", span.get("text", "").strip())]
    cjk = [span for span in body if re.search(r"[\u3400-\u9fff]", span.get("text", ""))]
    tables = page.get("tables") or []
    all_cell_geometry = [cell for table in tables for cell in table.get("cell_geometry", [])]
    cells_by_id = {str(cell.get("cell_id")): cell for cell in all_cell_geometry}
    numeric_baselines = []
    cjk_baselines = []
    numeric_model_deltas = []
    cjk_model_deltas = []
    final_insets = []
    row_baselines: dict[int, dict[str, list[float]]] = defaultdict(
        lambda: {"numeric": [], "cjk": []})
    for line in lines:
        if line.get("role") not in {"table_header", "table_body"}:
            continue
        cell = cells_by_id.get(str(line.get("paragraph_id")))
        origins = [float(span["origin"][1]) for span in line.get("spans", [])
                   if span.get("origin")]
        actual_baseline = percentile(origins, .5)
        text = line.get("text") or ""
        is_numeric = bool(re.fullmatch(r"[\d.,%+\-]+", text.strip()))
        is_cjk = bool(re.search(r"[\u3400-\u9fff]", text))
        if cell and actual_baseline is not None and cell.get("baseline_pt") is not None:
            delta = actual_baseline - float(cell["baseline_pt"])
            if is_numeric:
                numeric_model_deltas.append(delta)
            if is_cjk:
                cjk_model_deltas.append(delta)
            row = int(cell.get("row") or 0)
            if is_numeric:
                row_baselines[row]["numeric"].append(actual_baseline)
            if is_cjk:
                row_baselines[row]["cjk"].append(actual_baseline)
        if cell and line.get("bbox"):
            lb, tb = cell.get("layout_bbox"), line["bbox"]
            if lb:
                final_insets.append({
                    "alignment": cell.get("alignment") or "left",
                    "left": float(tb[0]) - float(lb[0]),
                    "right": float(lb[2]) - float(tb[2]),
                    "top": float(tb[1]) - float(lb[1]),
                    "bottom": float(lb[3]) - float(tb[3]),
                })
        for span in line.get("spans", []):
            target = numeric_baselines if re.fullmatch(
                r"[\d.,%+\-]+", span.get("text", "").strip()) else cjk_baselines
            if span.get("origin"):
                target.append(float(span["origin"][1]))
    row_baseline_deltas = []
    for values in row_baselines.values():
        if values["numeric"] and values["cjk"]:
            row_baseline_deltas.append(
                statistics.median(values["numeric"]) - statistics.median(values["cjk"]))

    def inset_stats(alignment: str | None, edge: str) -> dict[str, Any]:
        return stats(row[edge] for row in final_insets
                     if alignment is None or row["alignment"] == alignment)
    return {
        "table_count": len(tables),
        "header_font_size_pt": stats(span["size"] for span in header),
        "body_font_size_pt": stats(span["size"] for span in body),
        "header_body_size_ratio": round(header_size / body_size, 4)
        if header_size and body_size else None,
        "cjk_text_font_size_pt": stats(span["size"] for span in cjk),
        "numeric_font_size_pt": stats(span["size"] for span in numeric),
        "numeric_fonts": sorted({span.get("font") for span in numeric if span.get("font")}),
        "cjk_fonts": sorted({span.get("font") for span in cjk if span.get("font")}),
        "numeric_baseline_pt": stats(numeric_baselines),
        "cjk_baseline_pt": stats(cjk_baselines),
        "numeric_baseline_delta_to_frozen_cell_pt": stats(numeric_model_deltas),
        "cjk_baseline_delta_to_frozen_cell_pt": stats(cjk_model_deltas),
        "numeric_to_cjk_same_row_baseline_delta_pt": stats(row_baseline_deltas),
        "declared_css_padding_pt": {
            "horizontal": 0.0, "vertical": 0.0,
            "source": "final HTML universal reset and absolutely positioned .translated-cell",
        },
        "effective_final_pdf_content_inset_pt": {
            "left_aligned_left": inset_stats("left", "left"),
            "left_aligned_right": inset_stats("left", "right"),
            "centered_left": inset_stats("center", "left"),
            "centered_right": inset_stats("center", "right"),
            "top": inset_stats(None, "top"),
            "bottom": inset_stats(None, "bottom"),
            "note": "PDF glyph bbox to frozen cell edge; centered values include alignment whitespace, not CSS padding.",
        },
        "horizontal_padding_pt": {"declared": 0.0, "effective_insets_above": True},
        "vertical_padding_pt": {"declared": 0.0, "effective_insets_above": True},
        "alignment_counts": dict(__import__('collections').Counter(
            cell["alignment"] for cell in all_cell_geometry)),
        "structure": tables,
        "geometry_frozen": True,
    }


def caption_metrics(page: dict[str, Any]) -> dict[str, Any]:
    blocks = [block for block in build_blocks(page["current_zh"]["lines"])
              if block["role"] == "caption"]
    prefix_gaps = []
    for line in page["current_zh"]["lines"]:
        if line.get("role") != "caption":
            continue
        chars = [char for span in line.get("spans", []) for char in span.get("chars", [])
                 if char.get("c") and not char["c"].isspace()]
        colon_index = next((i for i, char in enumerate(chars)
                            if char["c"] in {"：", ":"}), None)
        if colon_index is not None and colon_index + 1 < len(chars):
            prefix_gaps.append(chars[colon_index + 1]["bbox"][0]
                               - chars[colon_index]["bbox"][2])
    return {
        "records": [{
            "paragraph_id": block["paragraph_id"],
            "font_size_pt": block["font_size_median_pt"],
            "baseline_gap_pt": block["baseline_gap_median_pt"],
            "line_count": block["line_count"],
            "width_pt": round(block["bbox"][2] - block["bbox"][0], 4) if block.get("bbox") else None,
            "bbox": block.get("bbox"),
        } for block in blocks],
        "prefix_text_gap_pt": stats(prefix_gaps),
    }


def column_density_metrics(page: dict[str, Any]) -> dict[str, Any]:
    grid = page.get("page_grid") or {}
    columns = grid.get("columns") or []
    by_column = []
    for index, column in enumerate(columns):
        x0, x1 = float(column["x0"]), float(column["x1"])
        lines = [line for line in page["current_zh"]["lines"]
                 if line.get("role") in BODY_ROLES | {"list_item"}
                 and line.get("bbox")
                 and x0 - 2 <= (line["bbox"][0] + line["bbox"][2]) / 2 <= x1 + 2]
        chars = sum(_visible_length(line.get("text") or "") for line in lines)
        if lines:
            y0 = min(line["bbox"][1] for line in lines)
            y1 = max(line["bbox"][3] for line in lines)
        else:
            y0 = y1 = 0.0
        by_column.append({
            "column": int(column.get("column_id", index)),
            "line_count": len(lines), "character_count": chars,
            "occupied_height_pt": round(y1 - y0, 4),
            "characters_per_100pt_height": round(chars / max(y1-y0, 1) * 100, 4),
        })
    density_difference = None
    if len(by_column) == 2:
        density_difference = round(abs(by_column[0]["characters_per_100pt_height"]
                                       - by_column[1]["characters_per_100pt_height"]), 4)
    return {"columns": by_column, "density_difference_per_100pt": density_difference}


def analyze_page_spacing(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "density": density_metrics(page),
        "line_height": line_height_metrics(page),
        "vertical_rhythm": vertical_rhythm(page),
        "list": list_metrics(page),
        "heading": heading_metrics(page),
        "frontmatter": frontmatter_metrics(page),
        "table": table_metrics(page),
        "caption": caption_metrics(page),
        "column_density": column_density_metrics(page),
    }
