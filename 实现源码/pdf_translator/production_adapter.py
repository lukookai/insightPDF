from __future__ import annotations

import bisect
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from pdf_translator.translation.runner import is_technical_identifier, split_for_api


_NON_TRANSLATABLE_TYPES = {"abandon", "abandon_cell", "table", "image"}
_MODEL_TEXT_LABELS = {
    "plain text",
    "title",
    "figure_caption",
    "table_caption",
    "formula_caption",
}
_MODEL_PRIORITY = {
    "isolate_formula": 0,
    "formula_caption": 1,
    "table": 2,
    "figure": 3,
    "table_caption": 4,
    "figure_caption": 5,
    "title": 6,
    "abandon": 7,
    "plain text": 8,
}


def extract_with_production_rules(
    pdf: Path,
    output_json: Path,
    *,
    production_repo: Path,
    work_dir: Path,
) -> dict:
    """Run pdf2json's real ``extract_all_pages`` in an isolated process.

    The production repository writes relative temporary files and imports
    sibling modules by their top-level names.  A subprocess preserves that
    behavior without changing this application's process-wide cwd/sys.path.
    """

    pdf = Path(pdf).resolve()
    output_json = Path(output_json).resolve()
    production_repo = Path(production_repo).resolve()
    work_dir = Path(work_dir).resolve()
    extractor = production_repo / "extract_all_pages.py"
    if not extractor.is_file():
        raise FileNotFoundError(f"生产 pdf2json 提取器不存在：{extractor}")
    work_dir.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    bootstrap = (
        "from extract_all_pages import extract_all_pages; import sys; "
        "extract_all_pages(sys.argv[1], save_json_path=sys.argv[2], "
        "enable_image=False)"
    )
    environment = os.environ.copy()
    old_path = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(production_repo), old_path) if part
    )
    subprocess.run(
        [sys.executable, "-c", bootstrap, str(pdf), str(output_json)],
        cwd=work_dir,
        env=environment,
        check=True,
    )
    return json.loads(output_json.read_text(encoding="utf-8"))


def _intersection(left: list[float], right: list[float]) -> float:
    return max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0, min(left[3], right[3]) - max(left[1], right[1])
    )


def _coverage(box: list[float], region: list[float]) -> float:
    area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
    return _intersection(box, region) / area


def _union(boxes: list[list[float]]) -> list[float]:
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text.replace("\u00ad", ""))


def _fallback_word_boundary_repair(text: str) -> str:
    text = text.replace("\u00ad", "")
    text = re.sub(r"(?<=[a-z)])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[,.;:!?])(?=[A-Za-z0-9(“])", " ", text)
    text = re.sub(r"(?<=[A-Za-z])-(?=[a-z]{2,}\b)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _word_items_in_box(page, box: list[float]) -> list[dict]:
    """Return native PDF words substantially contained by ``box``."""

    items = []
    for raw in page.get_text("words", sort=True):
        word_box = [float(value) for value in raw[:4]]
        word_area = max(
            1.0,
            (word_box[2] - word_box[0]) * (word_box[3] - word_box[1]),
        )
        if _intersection(word_box, box) / word_area < 0.55:
            continue
        items.append(
            {
                "bbox": word_box,
                "text": str(raw[4]),
                "block": int(raw[5]),
                "line": int(raw[6]),
            }
        )
    return items


def _text_geometry_from_words(words: list[dict]) -> dict:
    if not words:
        return {"text": "", "bbox": [], "line_boxes": []}
    text = " ".join(item["text"] for item in words)
    text = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    line_groups: dict[tuple[int, int], list[list[float]]] = {}
    for item in words:
        line_groups.setdefault((item["block"], item["line"]), []).append(
            item["bbox"]
        )
    return {
        "text": text,
        "bbox": [round(value, 3) for value in _union([item["bbox"] for item in words])],
        "line_boxes": [
            [round(value, 3) for value in _union(boxes)]
            for boxes in line_groups.values()
        ],
    }


def _split_title_identity_geometry(
    words: list[dict],
    title_region: list[float],
) -> tuple[dict, dict] | None:
    """Split a production block that merged a title with its identity band.

    Production pdf2json can join adjacent native blocks.  DocLayout supplies
    the semantic boundary; native PDF word coordinates recover the text and
    the exact redaction boxes on either side of that boundary.
    """

    title_words = []
    remainder_words = []
    for item in words:
        word_box = item["bbox"]
        word_area = max(
            1.0,
            (word_box[2] - word_box[0]) * (word_box[3] - word_box[1]),
        )
        if _intersection(word_box, title_region) / word_area >= 0.55:
            title_words.append(item)
        else:
            remainder_words.append(item)
    if len(title_words) < 4 or len(remainder_words) < 2:
        return None
    return (
        _text_geometry_from_words(title_words),
        _text_geometry_from_words(remainder_words),
    )


def _split_first_page_title_identity(page, item: dict, regions: list[dict]) -> list[dict]:
    """Split only top-of-first-page blocks crossing a detected title edge."""

    if item["bbox"][1] > page.rect.height * 0.2:
        return [item]
    title_regions = [
        region
        for region in regions
        if region.get("label") == "title"
        and region["bbox"][1] < page.rect.height * 0.18
        and 0.15 <= _coverage(item["bbox"], region["bbox"]) <= 0.8
        and item["bbox"][3] > region["bbox"][3] + 8.0
    ]
    if not title_regions:
        return [item]
    title_region = max(
        title_regions,
        key=lambda region: (_coverage(item["bbox"], region["bbox"]), region.get("confidence", 0)),
    )
    split = _split_title_identity_geometry(
        _word_items_in_box(page, item["bbox"]),
        title_region["bbox"],
    )
    if not split:
        return [item]

    title_geometry, identity_geometry = split
    title = dict(item)
    title.update(
        id=f"{item['id']}-title",
        bbox=title_geometry["bbox"],
        source_text=title_geometry["text"],
        production_source_text=title_geometry["text"],
        source_line_boxes=title_geometry["line_boxes"],
        role="title",
        translate=True,
        preserve_reason="",
        word_boundary_recovered=True,
        model_role="title",
        model_coverage=round(_coverage(title_geometry["bbox"], title_region["bbox"]), 3),
    )
    identity = dict(item)
    identity.update(
        id=f"{item['id']}-identity",
        bbox=identity_geometry["bbox"],
        source_text=identity_geometry["text"],
        production_source_text=identity_geometry["text"],
        source_line_boxes=identity_geometry["line_boxes"],
        role="identity",
        translate=False,
        preserve_reason="title_page_identity_band",
        word_boundary_recovered=True,
        model_role="plain text",
        model_coverage=0.0,
    )
    return [title, identity]


def _is_first_page_identity(page_number: int, item: dict) -> bool:
    if page_number != 1 or item.get("role") == "title":
        return False
    box = item["bbox"]
    if box[1] < 105 or box[1] > 215:
        return False
    text = item.get("source_text", "")
    explicit = re.search(
        r"\b(university|institute|laborator(?:y|ies)|department|school|college|"
        r"academy|author|correspondence|affiliation)\b|@",
        text,
        re.IGNORECASE,
    )
    capitalized_names = re.findall(r"\b[A-Z][a-z]{1,}\b", text)
    return bool(explicit or len(capitalized_names) >= 4)


def recover_word_boundaries(page, source_text: str, target_bbox: list[float]) -> dict:
    """Recover spaces removed by production line merging using PDF words.

    Production rules intentionally merge visual lines, but concatenate several
    line endings without a space.  We match the whitespace-free production
    string back to PyMuPDF's word stream and retain the production bbox/type.
    """

    words = []
    stream_parts = []
    starts = []
    cursor = 0
    for raw in page.get_text("words", sort=True):
        token = str(raw[4])
        compact = _compact(token)
        if not compact:
            continue
        starts.append(cursor)
        stream_parts.append(compact)
        words.append(
            {
                "bbox": [float(value) for value in raw[:4]],
                "text": token,
                "block": int(raw[5]),
                "line": int(raw[6]),
            }
        )
        cursor += len(compact)
    stream = "".join(stream_parts)
    target = _compact(source_text)
    if not target or not stream:
        return {
            "text": _fallback_word_boundary_repair(source_text),
            "line_boxes": [],
            "matched": False,
        }

    occurrences = []
    offset = stream.find(target)
    while offset >= 0:
        end = offset + len(target)
        first = max(0, bisect.bisect_right(starts, offset) - 1)
        last = min(len(words), bisect.bisect_left(starts, end))
        selected = words[first:last]
        if selected:
            selected_box = _union([item["bbox"] for item in selected])
            occurrences.append(
                (_coverage(selected_box, target_bbox), selected, selected_box)
            )
        offset = stream.find(target, offset + 1)
    if not occurrences:
        return {
            "text": _fallback_word_boundary_repair(source_text),
            "line_boxes": [],
            "matched": False,
        }

    _, selected, _ = max(occurrences, key=lambda item: item[0])
    recovered = " ".join(item["text"] for item in selected)
    recovered = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", recovered)
    recovered = re.sub(r"\s+", " ", recovered).strip()
    line_groups: dict[tuple[int, int], list[list[float]]] = {}
    for item in selected:
        line_groups.setdefault((item["block"], item["line"]), []).append(item["bbox"])
    line_boxes = [
        [round(value, 3) for value in _union(boxes)]
        for boxes in line_groups.values()
    ]
    return {"text": recovered, "line_boxes": line_boxes, "matched": True}


def _model_match(box: list[float], regions: list[dict]) -> tuple[str | None, float]:
    matches = []
    for region in regions:
        coverage = _coverage(box, region["bbox"])
        if coverage >= 0.28:
            matches.append(
                (
                    _MODEL_PRIORITY.get(region["label"], 99),
                    -coverage,
                    region["label"],
                    coverage,
                )
            )
    if not matches:
        return None, 0.0
    _, _, label, coverage = min(matches)
    return label, round(coverage, 3)


def _role_from_production(content_type: str) -> str:
    if content_type == "ref_text":
        return "references_entry"
    if content_type == "cell":
        return "table_text"
    if content_type in {"abandon", "abandon_cell"}:
        return "abandon"
    if content_type == "table":
        return "table"
    if content_type == "image":
        return "figure"
    return "body"


def _apply_doclayout(block: dict, label: str | None, coverage: float) -> None:
    block["model_role"] = label
    block["model_coverage"] = coverage
    if not label:
        return
    if label == "isolate_formula" and coverage >= 0.5:
        block.update(role="formula", translate=False, preserve_reason="doclayout_formula")
    elif label == "figure" and coverage >= 0.5 and block["role"] != "abandon":
        block.update(role="figure_text", translate=True, preserve_reason="")
    elif label == "table" and coverage >= 0.5 and block["role"] != "abandon":
        block.update(role="table_text", translate=True, preserve_reason="")
    elif label in {"figure_caption", "table_caption", "formula_caption"}:
        block.update(role=label, translate=True, preserve_reason="")
    elif label == "title" and block.get("translate"):
        block.update(role="title", preserve_reason="")
    elif (
        block["role"] == "abandon"
        and label in _MODEL_TEXT_LABELS
        and coverage >= 0.55
        and len(re.findall(r"[A-Za-z]", block["source_text"])) >= 3
    ):
        block.update(role="body", translate=True, preserve_reason="")


def _doclayout_regions(page, detector) -> list[dict]:
    if detector is None:
        return []
    import numpy as np

    pix = page.get_pixmap(matrix=__import__("fitz").Matrix(2, 2), alpha=False)
    samples = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n
    )[:, :, :3]
    regions = detector.predict(samples)
    for region in regions:
        region["bbox"] = [round(value / 2.0, 3) for value in region["bbox"]]
    return regions


def _native_supplements(page, blocks: list[dict], model_regions: list[dict]) -> list[dict]:
    supplements = []
    existing = [block["bbox"] for block in blocks if block.get("source_text")]
    for raw_index, raw in enumerate(page.get_text("dict", sort=True).get("blocks", []), 1):
        if raw.get("type") != 0:
            continue
        text = " ".join(
            "".join(span.get("text", "") for span in line.get("spans", []))
            for line in raw.get("lines", [])
        )
        text = re.sub(r"\s+", " ", text).strip()
        if len(re.findall(r"[A-Za-z]", text)) < 3:
            continue
        bbox = [round(float(value), 3) for value in raw["bbox"]]
        # One native PyMuPDF block is often split into two or more production
        # paragraphs.  Treat their combined coverage as already represented;
        # otherwise the model fallback would duplicate the whole native block.
        combined_coverage = min(
            1.0,
            sum(_intersection(bbox, other) for other in existing)
            / max(1.0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])),
        )
        if combined_coverage >= 0.72:
            continue
        label, coverage = _model_match(bbox, model_regions)
        if label not in _MODEL_TEXT_LABELS or coverage < 0.45:
            continue
        role = "title" if label == "title" else label.replace("plain text", "body")
        supplements.append(
            {
                "id": f"model-supplement-{raw_index}",
                "bbox": bbox,
                "source_text": text,
                "translated_text": "",
                "role": role,
                "production_content_type": "model_supplement",
                "translate": True,
                "preserve_reason": "",
                "source_font_size": 9.0,
                "source_font_bold": role == "title",
                "source_indent": 0.0,
                "source_line_boxes": [bbox],
                "word_boundary_recovered": True,
                "model_role": label,
                "model_coverage": coverage,
            }
        )
    return supplements


def build_hybrid_document(
    pdf: Path,
    production_json: dict,
    *,
    output_assets: Path,
    doclayout_detector,
    page_limit: int | None = None,
    split_limit: int = 620,
) -> dict:
    """Adapt production blocks to the HTML document schema and correct them."""

    import fitz

    pdf = Path(pdf).resolve()
    output_assets = Path(output_assets).resolve()
    output_assets.mkdir(parents=True, exist_ok=True)
    source = fitz.open(pdf)
    page_data_by_number = {
        int(page["page_number"]): page for page in production_json.get("pages", [])
    }
    count = source.page_count if page_limit is None else min(page_limit, source.page_count)
    pages = []
    split_segments = 0
    recovered_blocks = 0
    supplement_blocks = 0
    doclayout_split_blocks = 0

    for page_index in range(count):
        page = source[page_index]
        number = page_index + 1
        model_regions = _doclayout_regions(page, doclayout_detector)
        production_page = page_data_by_number.get(number, {"blocks": []})
        blocks = []
        for block_index, raw in enumerate(production_page.get("blocks", []), 1):
            content_type = str(raw.get("content_type") or "plain")
            source_text = str(raw.get("content") or "").strip()
            bbox = [round(float(value), 3) for value in raw.get("bbox", [0, 0, 0, 0])]
            recovered = recover_word_boundaries(page, source_text, bbox)
            if recovered["matched"]:
                recovered_blocks += 1
            translate = bool(source_text) and content_type not in _NON_TRANSLATABLE_TYPES
            item = {
                "id": f"p{number}-production-{block_index}",
                "bbox": bbox,
                "source_text": recovered["text"] if source_text else "",
                "production_source_text": source_text,
                "translated_text": "",
                "role": _role_from_production(content_type),
                "production_content_type": content_type,
                "translate": translate,
                "preserve_reason": "production_rule" if not translate else "",
                "source_font_size": round(float(raw.get("font_size") or 9.0), 3),
                "source_font_bold": bool(raw.get("font_bold", False)),
                "source_indent": round(float(raw.get("indent") or 0.0), 3),
                "source_line_boxes": recovered["line_boxes"],
                "word_boundary_recovered": recovered["matched"],
            }
            split_items = (
                _split_first_page_title_identity(page, item, model_regions)
                if number == 1
                else [item]
            )
            if len(split_items) > 1:
                doclayout_split_blocks += 1
            for split_item in split_items:
                if "model_role" not in split_item:
                    label, coverage = _model_match(split_item["bbox"], model_regions)
                    _apply_doclayout(split_item, label, coverage)
                if _is_first_page_identity(number, split_item):
                    split_item.update(
                        role="identity",
                        translate=False,
                        preserve_reason="title_page_identity_band",
                    )
                if split_item["translate"] and is_technical_identifier(
                    split_item["source_text"]
                ):
                    split_item.update(
                        role="metadata",
                        translate=False,
                        preserve_reason="technical_identifier_or_document_metadata",
                    )
                if split_item["translate"]:
                    chunks = split_for_api(split_item["source_text"], limit=split_limit)
                    split_item["translation_segments"] = [
                        {
                            "id": f"{split_item['id']}-s{segment_index}",
                            "source_text": chunk,
                            "translated_text": "",
                        }
                        for segment_index, chunk in enumerate(chunks, 1)
                    ]
                    split_segments += len(chunks)
                blocks.append(split_item)

        supplements = _native_supplements(page, blocks, model_regions)
        for local_index, item in enumerate(supplements, 1):
            item["id"] = f"p{number}-{item['id']}-{local_index}"
            supplement_chunks = split_for_api(item["source_text"], limit=split_limit)
            item["translation_segments"] = [
                {
                    "id": f"{item['id']}-s{segment_index}",
                    "source_text": chunk,
                    "translated_text": "",
                }
                for segment_index, chunk in enumerate(supplement_chunks, 1)
            ]
        supplement_blocks += len(supplements)
        split_segments += sum(
            len(item["translation_segments"]) for item in supplements
        )
        blocks.extend(supplements)
        pages.append(
            {
                "number": number,
                "width": float(page.rect.width),
                "height": float(page.rect.height),
                "background_image": str((output_assets / f"page-{number:03d}.png").resolve()),
                "background_text_removed": True,
                "doclayout_regions": model_regions,
                "blocks": blocks,
            }
        )

    source.close()
    _render_clean_backgrounds(pdf, pages)
    return {
        "schema_version": "3.0",
        "source_pdf": str(pdf),
        "page_limit": page_limit,
        "architecture": "production-pdf2json-plus-doclayout-plus-html",
        "split_limit": split_limit,
        "statistics": {
            "production_blocks": sum(len(page.get("blocks", [])) for page in production_json.get("pages", [])[:count]),
            "word_boundary_recovered_blocks": recovered_blocks,
            "doclayout_split_production_blocks": doclayout_split_blocks,
            "doclayout_supplement_blocks": supplement_blocks,
            "translation_segments": split_segments,
        },
        "pages": pages,
    }


def _render_clean_backgrounds(pdf: Path, pages: list[dict]) -> None:
    import fitz

    clean = fitz.open(pdf)
    for page_data in pages:
        page = clean[int(page_data["number"]) - 1]
        for block in page_data["blocks"]:
            if not block.get("translate"):
                continue
            boxes = block.get("source_line_boxes") or [block["bbox"]]
            for box in boxes:
                rect = fitz.Rect(*box)
                rect.x0 -= 0.35
                rect.y0 -= 0.35
                rect.x1 += 0.35
                rect.y1 += 0.35
                page.add_redact_annot(rect, fill=False)
        if list(page.annots() or []):
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                text=fitz.PDF_REDACT_TEXT_REMOVE,
            )
        background = Path(page_data["background_image"])
        background.parent.mkdir(parents=True, exist_ok=True)
        page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False, annots=True).save(background)
    clean.close()


def hybrid_translation_payload(document: dict) -> dict:
    segments = []
    for page in document.get("pages", []):
        for block in page.get("blocks", []):
            for segment in block.get("translation_segments", []):
                segments.append(
                    {
                        "id": segment["id"],
                        "page": page["number"],
                        "role": block.get("role", "body"),
                        "source_text": segment["source_text"],
                        "translated_text": segment.get("translated_text", ""),
                    }
                )
    return {"schema_version": "1.0", "segments": segments}


def attach_hybrid_translations(document: dict, translated_payload: dict) -> None:
    translated = {
        item["id"]: item.get("translated_text", "")
        for item in translated_payload.get("segments", [])
    }
    for page in document.get("pages", []):
        for block in page.get("blocks", []):
            pieces = []
            for segment in block.get("translation_segments", []):
                segment["translated_text"] = translated.get(segment["id"], "")
                pieces.append(segment["translated_text"].strip())
            if pieces:
                block["translated_text"] = "".join(pieces)
