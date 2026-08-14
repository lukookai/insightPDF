from __future__ import annotations

import json
import re
from pathlib import Path


TRANSLATABLE_LABELS = {"title", "text", "image_caption", "page_footnote"}


_IDENTITY_KEYWORDS = re.compile(
    r"\b(university|institute|department|laborator(?:y|ies)|college|school of|"
    r"research center|research centre|microsoft research|corresponding authors?|"
    r"contributed equally|equal contribution|affiliation)\b",
    re.IGNORECASE,
)


def _looks_like_identity(text: str) -> bool:
    if "@" in text or _IDENTITY_KEYWORDS.search(text):
        return True
    plain = re.sub(r"\\\([^)]*\\\)|\\[A-Za-z]+|[{}_^†‡*0-9]", " ", text)
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", plain)
    # Author lists are short title-case fragments without sentence punctuation.
    title_case = sum(word[:1].isupper() for word in words)
    return (
        1 <= len(words) <= 45
        and not re.search(r"[.!?]", plain)
        and title_case / len(words) >= 0.55
    )


def _union(boxes: list[list[float]]) -> list[float]:
    return [
        round(min(box[0] for box in boxes), 3),
        round(min(box[1] for box in boxes), 3),
        round(max(box[2] for box in boxes), 3),
        round(max(box[3] for box in boxes), 3),
    ]


def _should_translate(page_number: int, label: str, text: str, bbox: list[float]) -> bool:
    if label not in TRANSLATABLE_LABELS or not text.strip():
        return False
    if re.fullmatch(r"(?:https?://|doi:)\S+", text.strip(), flags=re.IGNORECASE):
        return False
    # Preserve author names, affiliations, emails and contribution statements
    # on a paper's title page. Content features are more robust than a fixed Y
    # threshold because conference and journal title pages differ greatly.
    if page_number == 1 and label in {"text", "page_footnote"}:
        explicit_identity = "@" in text or bool(_IDENTITY_KEYWORDS.search(text))
        if explicit_identity or (bbox[1] < 400 and _looks_like_identity(text)):
            return False
    # A region consisting almost entirely of formula syntax is not prose.
    letters = len(re.findall(r"[A-Za-z]", text))
    if letters < 3 and label == "text":
        return False
    return True


def _covered_ratio(inner: list[float], outer: list[float]) -> float:
    x1 = max(inner[0], outer[0])
    y1 = max(inner[1], outer[1])
    x2 = min(inner[2], outer[2])
    y2 = min(inner[3], outer[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area = max(1.0, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return intersection / area


def _duplicate_overlap(left: list[float], right: list[float]) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(1.0, (left[2] - left[0]) * (left[3] - left[1]))
    right_area = max(1.0, (right[2] - right[0]) * (right[3] - right[1]))
    return intersection / min(left_area, right_area)


def _box_area(box: list[float]) -> float:
    return max(1.0, (box[2] - box[0]) * (box[3] - box[1]))


def build_region_segments(document: dict) -> dict:
    """Create translation segments from OCR semantic regions.

    Each segment inherits its final PDF bbox, so two-column regions never get
    merged by a later page-wide text search.
    """
    segments: list[dict] = []
    excluded_nested_regions = 0
    excluded_duplicate_regions = 0
    for page in document.get("pages", []):
        page_number = int(page["number"])
        order = 0
        detections = (page.get("ocr") or {}).get("detections", [])
        preserved_containers = [
            box
            for detection in detections
            if detection.get("label") in {"image", "table", "equation"}
            for box in detection.get("pdf_bboxes", [])
        ]
        page_area = max(
            1.0,
            float(page.get("width", 612.0)) * float(page.get("height", 792.0)),
        )
        preserved_containers.extend(
            image["bbox"]
            for image in page.get("native_images", [])
            if (
                (image["bbox"][2] - image["bbox"][0])
                * (image["bbox"][3] - image["bbox"][1])
                / page_area
                < 0.75
            )
        )
        for detection_index, detection in enumerate(detections, 1):
            boxes = detection.get("pdf_bboxes") or []
            if not boxes:
                continue
            label = detection.get("label", "text")
            text = detection.get("text", "").strip()
            bbox = _union(boxes)
            if label in {"text", "title"} and any(
                _covered_ratio(bbox, container) >= 0.72
                for container in preserved_containers
            ):
                excluded_nested_regions += 1
                continue
            if not _should_translate(page_number, label, text, bbox):
                continue
            candidate = {
                "id": f"p{page_number}-r{detection_index}",
                "page": page_number,
                "order": order + 1,
                "region_type": label,
                "source_text": text,
                "translated_text": "",
                "bbox": bbox,
            }
            normalized_text = re.sub(r"\s+", " ", text).casefold()
            duplicate_index = next(
                (
                    index
                    for index in range(len(segments) - 1, -1, -1)
                    if segments[index]["page"] == page_number
                    and re.sub(
                        r"\s+", " ", segments[index]["source_text"]
                    ).casefold()
                    == normalized_text
                    and _duplicate_overlap(segments[index]["bbox"], bbox) >= 0.85
                ),
                None,
            )
            if duplicate_index is not None:
                excluded_duplicate_regions += 1
                if _box_area(bbox) > _box_area(segments[duplicate_index]["bbox"]):
                    candidate["order"] = segments[duplicate_index]["order"]
                    segments[duplicate_index] = candidate
                continue
            order += 1
            segments.append(candidate)
    return {
        "schema_version": "1.1",
        "source_pdf": document.get("source_pdf"),
        "source_language": "en",
        "target_language": "zh-CN",
        "segmentation": "unlimited-ocr-semantic-regions",
        "excluded_nested_regions": excluded_nested_regions,
        "excluded_duplicate_regions": excluded_duplicate_regions,
        "segments": segments,
    }


def load_and_build(document_path: Path, output: Path) -> None:
    document = json.loads(document_path.read_text(encoding="utf-8"))
    payload = build_region_segments(document)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
