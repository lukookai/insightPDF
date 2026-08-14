from __future__ import annotations

import re
from pathlib import Path


_IDENTITY = re.compile(
    r"\b(university|institute|department|laborator(?:y|ies)|college|school|"
    r"corresponding authors?|equal contribution|affiliation)\b|@",
    re.IGNORECASE,
)
_IDENTITY_NOTE = re.compile(
    r"\b(corresponding authors?|equal contribution|contributed equally)\b",
    re.IGNORECASE,
)
_SECTION = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+)?[A-Z][^.!?]{1,100}$")
_CAPTION = re.compile(r"^(figure|fig\.|table)\s*\d+", re.IGNORECASE)
_PAGE_NUMBER = re.compile(r"^\s*\d{1,6}\s*$")
_MATH_FONT = re.compile(r"(?:CMMI|CMSY|Math|Symbol|MTMI|MTSY)", re.IGNORECASE)
_LIGATURES = str.maketrans(
    {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
)


def _intersection_area(left: list[float], right: list[float]) -> float:
    return max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0, min(left[3], right[3]) - max(left[1], right[1])
    )


def _coverage(box: list[float], region: list[float]) -> float:
    area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
    return _intersection_area(box, region) / area


def _expanded_intersects(left: list[float], right: list[float], gap: float) -> bool:
    return not (
        left[2] + gap < right[0]
        or right[2] + gap < left[0]
        or left[3] + gap < right[1]
        or right[3] + gap < left[1]
    )


def _union(boxes: list[list[float]]) -> list[float]:
    return [
        round(min(box[0] for box in boxes), 3),
        round(min(box[1] for box in boxes), 3),
        round(max(box[2] for box in boxes), 3),
        round(max(box[3] for box in boxes), 3),
    ]


def _image_clusters(image_boxes: list[list[float]], page_area: float) -> list[list[float]]:
    """Join tiled PDF images into figure regions using coordinates only."""

    pending = [list(box) for box in image_boxes]
    components: list[list[list[float]]] = []
    while pending:
        component = [pending.pop()]
        changed = True
        while changed:
            changed = False
            merged = _union(component)
            for index in range(len(pending) - 1, -1, -1):
                if _expanded_intersects(merged, pending[index], 8.0):
                    component.append(pending.pop(index))
                    changed = True
        components.append(component)

    regions = []
    for component in components:
        box = _union(component)
        area = (box[2] - box[0]) * (box[3] - box[1])
        if len(component) >= 2 and area / page_area >= 0.008:
            # Tiled academic figures frequently keep labels just above/below
            # their raster tiles.  Expand enough to protect those labels while
            # leaving the following caption as its own translatable block.
            regions.append(
                [box[0] - 2.0, box[1] - 20.0, box[2] + 2.0, box[3] + 18.0]
            )
        elif len(component) == 1 and area / page_area >= 0.025:
            regions.append(
                [box[0] - 2.0, box[1] - 6.0, box[2] + 2.0, box[3] + 6.0]
            )
    return regions


def _block_text(block: dict) -> tuple[str, list[dict]]:
    spans: list[dict] = []
    lines: list[str] = []
    for line in block.get("lines", []):
        line_spans = line.get("spans", [])
        spans.extend(line_spans)
        text = "".join(span.get("text", "") for span in line_spans).strip()
        if text:
            lines.append(text)
    joined = "\n".join(lines).translate(_LIGATURES).replace("\u00ad", "")
    joined = re.sub(r"(?<=[A-Za-z])-\n(?=[a-z])", "", joined)
    joined = re.sub(r"\s*\n\s*", " ", joined)
    return re.sub(r"\s+", " ", joined).strip(), spans


def _column(box: list[float], page_width: float) -> str:
    width = box[2] - box[0]
    if width >= page_width * 0.62:
        return "full"
    return "left" if (box[0] + box[2]) / 2 < page_width / 2 else "right"


def _math_like(text: str, fonts: list[str]) -> bool:
    if not text or len(text) > 180:
        return False
    math_chars = len(re.findall(r"[=+−×÷∑∫√<>_^{}\\]", text))
    letters = len(re.findall(r"[A-Za-z]", text))
    return bool(_MATH_FONT.search(" ".join(fonts))) and (
        math_chars >= 1 or letters <= 4
    )


def _native_role(
    *,
    text: str,
    box: list[float],
    page_number: int,
    page_width: float,
    page_height: float,
    font_size: float,
    median_font: float,
    fonts: list[str],
    figure_regions: list[list[float]],
) -> tuple[str, bool, str]:
    stripped = text.strip()
    # BabelDOC treats native text inside a figure as text (figure_text), not as
    # part of the raster/graphic object.  Formula detection has higher priority.
    if _PAGE_NUMBER.fullmatch(stripped):
        return "page_number", False, "numeric_page_marker"
    if _math_like(stripped, fonts):
        return "formula", False, "math_font_or_symbols"
    if any(_coverage(box, region) >= 0.55 for region in figure_regions):
        return "figure_text", True, ""
    if box[1] < page_height * 0.055:
        return "page_header", True, ""
    if box[3] > page_height * 0.925:
        return "page_footer", True, ""
    if _IDENTITY_NOTE.search(stripped):
        return "footnote", True, ""
    if "@" in stripped:
        return "identity", False, "email_or_contact"
    if page_number == 1 and 145 <= box[1] <= 220 and len(stripped) < 260:
        return "identity", False, "title_page_identity_band"
    caption = _CAPTION.match(stripped)
    if caption:
        role = "table_caption" if caption.group(1).casefold() == "table" else "figure_caption"
        return role, True, "caption_regex"
    if stripped.casefold() == "references":
        return "references_heading", True, ""
    if page_number == 1 and font_size >= max(13.0, median_font * 1.35):
        return "title", True, "largest_title_page_font"
    if (
        len(stripped) <= 120
        and font_size >= median_font * 1.08
        and _SECTION.match(stripped)
    ):
        return "heading", True, "font_and_heading_shape"
    if len(re.findall(r"[A-Za-z]", stripped)) < 3:
        return "symbol", False, "insufficient_prose"
    return "body", True, "native_text_block"


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


def _apply_model_role(block: dict, label: str | None, coverage: float) -> None:
    block["model_role"] = label
    block["model_coverage"] = coverage
    if not label:
        return
    if label == "isolate_formula" and coverage >= 0.5:
        block.update(
            role=label,
            translate=False,
            preserve_reason=f"doclayout_{label}",
        )
    elif label in {"figure", "table"} and coverage >= 0.5:
        # Match BabelDOC layout priority: a figure/table protects its graphic
        # objects, while native PDF text over it remains translatable.
        if block.get("native_role") not in {"formula", "identity", "page_number"}:
            block.update(
                role="figure_text" if label == "figure" else "table_text",
                translate=True,
                preserve_reason="",
            )
    elif label in {"figure_caption", "table_caption", "formula_caption"}:
        block.update(role=label, translate=True, preserve_reason="")
    elif label == "title" and block["translate"]:
        role = "heading" if block.get("native_role") == "heading" else "title"
        block.update(role=role, preserve_reason="")
    elif label == "abandon" and coverage >= 0.65:
        # BabelDOC includes `abandon` in text layouts.  Keep the native policy
        # rather than using the model label as a blanket translation veto.
        block["role"] = block.get("native_role", block["role"])
    elif (
        label == "plain text"
        and block.get("native_role")
        not in {"identity", "formula", "symbol", "page_number"}
        and block["role"] not in {"references_entry"}
    ):
        block.update(role="body", translate=True, preserve_reason="")


def extract_native_rule_document(
    pdf: Path,
    *,
    output_assets: Path,
    page_limit: int = 2,
    doclayout_detector=None,
) -> dict:
    """Extract native text/font/coordinates and apply deterministic roles."""

    import fitz
    import numpy as np

    pdf = Path(pdf).resolve()
    output_assets.mkdir(parents=True, exist_ok=True)
    source = fitz.open(pdf)
    pages = []
    for page_index in range(min(page_limit, source.page_count)):
        page = source[page_index]
        width, height = float(page.rect.width), float(page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        background = output_assets / f"page-{page_index + 1:03d}.png"

        raw = page.get_text("dict", sort=True)
        image_boxes = [
            [round(float(value), 3) for value in block["bbox"]]
            for block in raw.get("blocks", [])
            if block.get("type") == 1
        ]
        figure_regions = _image_clusters(image_boxes, width * height)
        text_entries = []
        font_sizes = []
        for block in raw.get("blocks", []):
            if block.get("type") != 0:
                continue
            text, spans = _block_text(block)
            if not text or not spans:
                continue
            sizes = [float(span.get("size", 0.0)) for span in spans if span.get("text")]
            size = max(sizes, default=9.0)
            font_sizes.extend(sizes)
            text_entries.append((block, text, spans, size))
        median_font = sorted(font_sizes)[len(font_sizes) // 2] if font_sizes else 9.0

        model_regions = []
        if doclayout_detector is not None:
            samples = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )[:, :, :3]
            # The model receives the 144-DPI page. Convert detections back to PDF points.
            for region in doclayout_detector.predict(samples):
                region["bbox"] = [round(value / 2.0, 3) for value in region["bbox"]]
                model_regions.append(region)

        blocks = []
        for block_index, (raw_block, text, spans, size) in enumerate(text_entries, 1):
            box = [round(float(value), 3) for value in raw_block["bbox"]]
            fonts = sorted({span.get("font", "") for span in spans})
            role, translate, reason = _native_role(
                text=text,
                box=box,
                page_number=page_index + 1,
                page_width=width,
                page_height=height,
                font_size=size,
                median_font=median_font,
                fonts=fonts,
                figure_regions=figure_regions,
            )
            item = {
                "id": f"p{page_index + 1}-b{block_index}",
                "page": page_index + 1,
                "bbox": box,
                "source_text": text,
                "translated_text": "",
                "role": role,
                "native_role": role,
                "translate": translate,
                "preserve_reason": reason if not translate else "",
                "column": _column(box, width),
                "source_font_size": round(size, 3),
                "source_fonts": fonts,
            }
            if doclayout_detector is not None:
                label, coverage = _model_match(box, model_regions)
                _apply_model_role(item, label, coverage)
            blocks.append(item)

        # Build a clean visual base exactly for the HTML overlay: remove only
        # translatable native text, retaining underlying images and vector
        # graphics.  Transparent redaction avoids the white boxes that would
        # otherwise damage colored labels inside figures and tables.
        for item in blocks:
            if item["translate"]:
                page.add_redact_annot(fitz.Rect(*item["bbox"]), fill=False)
        if any(item["translate"] for item in blocks):
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                text=fitz.PDF_REDACT_TEXT_REMOVE,
            )
        page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(background)

        pages.append(
            {
                "number": page_index + 1,
                "width": width,
                "height": height,
                "background_image": str(background.resolve()),
                "background_text_removed": True,
                "native_image_boxes": image_boxes,
                "native_figure_regions": figure_regions,
                "doclayout_regions": model_regions,
                "blocks": blocks,
            }
        )
    source.close()
    return {
        "schema_version": "2.0",
        "source_pdf": str(pdf),
        "page_limit": page_limit,
        "layout_mode": "doclayout-yolo" if doclayout_detector else "native-rules",
        "pages": pages,
    }
