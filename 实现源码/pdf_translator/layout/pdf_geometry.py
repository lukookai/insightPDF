from __future__ import annotations

import json
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from pdf_translator.ocr.unlimited_ocr import UnlimitedOCRBackend


_INVALID_XML_CHARACTERS = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f]"
)


def _float(element: ET.Element, name: str) -> float:
    return round(float(element.attrib[name]), 3)


def _bbox(element: ET.Element) -> list[float]:
    return [
        _float(element, "xMin"),
        _float(element, "yMin"),
        _float(element, "xMax"),
        _float(element, "yMax"),
    ]


def extract_pdf_geometry(pdf: Path) -> dict:
    """Extract exact page/line/word coordinates using Poppler pdftotext."""
    pdf = pdf.resolve()
    command = ["pdftotext", "-bbox-layout", str(pdf), "-"]
    result = subprocess.run(command, check=True, capture_output=True)
    # Some academic PDFs contain embedded glyphs that Poppler emits as raw
    # control characters. XML 1.0 forbids those bytes, so sanitize them while
    # preserving every printable glyph and all coordinates.
    xml_text = result.stdout.decode("utf-8", errors="replace")
    root = ET.fromstring(_INVALID_XML_CHARACTERS.sub("", xml_text))
    namespace = root.tag.split("}", 1)[0].lstrip("{") if "}" in root.tag else ""
    q = (lambda name: f"{{{namespace}}}{name}") if namespace else (lambda name: name)
    document = root.find(f".//{q('doc')}")
    if document is None:
        raise RuntimeError("pdftotext 输出中没有 doc 节点")

    pages = []
    for page_number, page in enumerate(document.findall(q("page")), 1):
        page_data = {
            "number": page_number,
            "width": round(float(page.attrib["width"]), 3),
            "height": round(float(page.attrib["height"]), 3),
            "blocks": [],
        }
        for block_number, block in enumerate(page.findall(f".//{q('block')}"), 1):
            block_data = {
                "id": f"p{page_number}-g{block_number}",
                "bbox": _bbox(block),
                "lines": [],
            }
            block_words = []
            for line in block.findall(f".//{q('line')}"):
                words = []
                for word in line.findall(q("word")):
                    text = "".join(word.itertext()).strip()
                    if not text:
                        continue
                    word_data = {"text": text, "bbox": _bbox(word)}
                    words.append(word_data)
                    block_words.append(text)
                if words:
                    block_data["lines"].append(
                        {
                            "bbox": _bbox(line),
                            "text": " ".join(item["text"] for item in words),
                            "words": words,
                        }
                    )
            block_data["text"] = " ".join(block_words)
            if block_data["text"]:
                page_data["blocks"].append(block_data)
        pages.append(page_data)
    geometry = {
        "schema_version": "1.0",
        "source_pdf": str(pdf),
        "geometry_source": "poppler-pdftotext-bbox-layout",
        "pages": pages,
    }
    return augment_native_images(geometry, pdf)


def extract_native_image_regions(pdf: Path) -> dict[int, list[dict]]:
    """Extract PDF-native raster image boxes using Poppler, without OCR."""
    with tempfile.TemporaryDirectory(prefix="pdf-native-images-") as temporary:
        prefix = Path(temporary) / "document"
        subprocess.run(
            [
                "pdftohtml",
                "-xml",
                "-hidden",
                "-zoom",
                "1",
                "-nodrm",
                str(pdf),
                str(prefix),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        xml_path = prefix.with_suffix(".xml")
        xml_text = xml_path.read_text(encoding="utf-8", errors="replace")
        root = ET.fromstring(_INVALID_XML_CHARACTERS.sub("", xml_text))
        result: dict[int, list[dict]] = {}
        for page in root.findall("page"):
            page_number = int(page.attrib["number"])
            seen = set()
            images = []
            for index, image in enumerate(page.findall("image"), 1):
                left = float(image.attrib["left"])
                top = float(image.attrib["top"])
                width = float(image.attrib["width"])
                height = float(image.attrib["height"])
                bbox = (
                    round(left, 3),
                    round(top, 3),
                    round(left + width, 3),
                    round(top + height, 3),
                )
                if width < 2 or height < 2 or bbox in seen:
                    continue
                seen.add(bbox)
                images.append(
                    {
                        "id": f"p{page_number}-native-image-{index}",
                        "bbox": list(bbox),
                    }
                )
            result[page_number] = images
        return result


def augment_native_images(geometry: dict, pdf: Path) -> dict:
    regions = extract_native_image_regions(pdf)
    for page in geometry.get("pages", []):
        page["native_images"] = regions.get(int(page["number"]), [])
    geometry["native_image_source"] = "poppler-pdftohtml-xml"
    return geometry


def merge_unlimited_ocr(geometry: dict, pdf: Path, ocr_dir: Path) -> dict:
    """Attach per-page Unlimited-OCR Markdown and semantic blocks."""
    ocr_pages = {
        page.page: page
        for page in UnlimitedOCRBackend.load_results(pdf.resolve(), ocr_dir.resolve())
    }
    for page in geometry.get("pages", []):
        ocr_page = ocr_pages.get(page["number"])
        page_image = None
        for candidate in (
            ocr_dir / "pages" / f"page-{page['number']:02d}.png",
            ocr_dir / "pages" / f"page-{page['number']}.png",
            ocr_dir / "pages" / f"page_{page['number']:04d}.png",
        ):
            if candidate.is_file():
                page_image = str(candidate.resolve())
                break
        detections = []
        if ocr_page:
            for detection in ocr_page.detections:
                mapped = dict(detection)
                mapped.pop("raw", None)
                mapped["pdf_bboxes"] = [
                    [
                        round(box[0] / 999 * page["width"], 3),
                        round(box[1] / 999 * page["height"], 3),
                        round(box[2] / 999 * page["width"], 3),
                        round(box[3] / 999 * page["height"], 3),
                    ]
                    for box in detection["normalized_bboxes"]
                ]
                detections.append(mapped)
        page["ocr"] = (
            {
                "engine": "baidu/Unlimited-OCR",
                "source_file": ocr_page.source_file,
                "page_image": page_image,
                "raw_output": ocr_page.raw_output,
                "markdown": ocr_page.markdown,
                "semantic_blocks": ocr_page.blocks,
                "detections": detections,
            }
            if ocr_page
            else None
        )
    geometry["ocr_engine"] = "baidu/Unlimited-OCR"
    geometry["ocr_pages_found"] = len(ocr_pages)
    return geometry


def save_layout(layout: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
