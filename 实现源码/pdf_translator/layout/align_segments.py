from __future__ import annotations

import difflib
import re


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[.$%/-][A-Za-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    normalized = (
        text.replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("–", "-")
        .replace("—", "-")
        .lower()
    )
    return TOKEN_PATTERN.findall(normalized)


def _union(boxes: list[list[float]]) -> list[float]:
    return [
        round(min(box[0] for box in boxes), 3),
        round(min(box[1] for box in boxes), 3),
        round(max(box[2] for box in boxes), 3),
        round(max(box[3] for box in boxes), 3),
    ]


def _page_words(page: dict) -> list[dict]:
    words = []
    for block in page.get("blocks", []):
        for line in block.get("lines", []):
            words.extend(line.get("words", []))
    return words


def align_segment(page: dict, segment: dict) -> dict:
    supplied_bbox = segment.get("bbox")
    if supplied_bbox and len(supplied_bbox) == 4:
        return {
            "id": segment["id"],
            "page": segment["page"],
            "order": segment["order"],
            "region_type": segment.get("region_type", "text"),
            "source_text": segment["source_text"],
            "translated_text": segment.get("translated_text", ""),
            "bbox": [round(float(value), 3) for value in supplied_bbox],
            "alignment": {
                "method": "layout-region-direct",
                "source_tokens": len(tokenize(segment.get("source_text", ""))),
                "matched_tokens": len(tokenize(segment.get("source_text", ""))),
                "coverage": 1.0,
                "matched_words": 0,
            },
        }
    words = _page_words(page)
    page_tokens: list[str] = []
    token_word_indices: list[int] = []
    for word_index, word in enumerate(words):
        tokens = tokenize(word["text"])
        page_tokens.extend(tokens)
        token_word_indices.extend([word_index] * len(tokens))

    source_tokens = tokenize(segment.get("source_text", ""))
    matcher = difflib.SequenceMatcher(
        None,
        source_tokens,
        page_tokens,
        autojunk=False,
    )
    matched_page_tokens: list[int] = []
    matched_source_count = 0
    for match in matcher.get_matching_blocks():
        if match.size == 0:
            continue
        source_slice = source_tokens[match.a : match.a + match.size]
        meaningful = match.size >= 2 or any(len(token) >= 5 for token in source_slice)
        if not meaningful:
            continue
        matched_source_count += match.size
        matched_page_tokens.extend(range(match.b, match.b + match.size))

    matched_word_indices = sorted(
        {
            token_word_indices[index]
            for index in matched_page_tokens
            if 0 <= index < len(token_word_indices)
        }
    )
    boxes = [words[index]["bbox"] for index in matched_word_indices]
    coverage = matched_source_count / max(1, len(source_tokens))

    return {
        "id": segment["id"],
        "page": segment["page"],
        "order": segment["order"],
        "source_text": segment["source_text"],
        "translated_text": segment.get("translated_text", ""),
        "bbox": _union(boxes) if boxes else None,
        "alignment": {
            "method": "token-sequence-match",
            "source_tokens": len(source_tokens),
            "matched_tokens": matched_source_count,
            "coverage": round(coverage, 4),
            "matched_words": len(matched_word_indices),
        },
    }


def attach_translations(layout: dict, translation_data: dict) -> dict:
    by_page: dict[int, list[dict]] = {}
    for segment in translation_data.get("segments", []):
        by_page.setdefault(int(segment["page"]), []).append(segment)
    for page in layout.get("pages", []):
        segments = sorted(by_page.get(page["number"], []), key=lambda item: item["order"])
        page["translations"] = [align_segment(page, segment) for segment in segments]
    layout["translation_source"] = translation_data.get("source_pdf")
    layout["translation_language"] = translation_data.get("target_language", "zh-CN")
    return layout
