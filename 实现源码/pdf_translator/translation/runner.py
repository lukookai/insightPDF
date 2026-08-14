from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .lawai import DEFAULT_BASE_URL, LawAIClient, LawAIError


def is_technical_identifier(text: str) -> bool:
    text = text.strip()
    if text in {
        "[Task]",
        "[Evaluation Criteria]",
        "[Topic]",
        "[Section]",
        "[Output Format]",
        "[Claim Definition]",
        "[Guidelines]",
    }:
        return True
    # Legal/academic enumeration markers carry layout structure rather than
    # translatable prose. Sending them alone to the API commonly echoes the
    # source, which is correct and must not be treated as a translation error.
    if re.fullmatch(
        r"(?:\([a-zivxlcdm0-9]+\)|[a-zivxlcdm][.)])",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    compact_metadata = re.sub(r"\s+", "", text)
    if re.fullmatch(
        r"arxiv:\d{4}\.\d+(?:v\d+)?\[[^\]]+\]"
        r"(?:\d{1,2}[A-Za-z]{3}\d{4})?",
        compact_metadata,
        flags=re.IGNORECASE,
    ):
        return True
    if re.fullmatch(
        r"(?:\[[A-Za-z][A-Za-z0-9_-]{0,48}\]|\{[A-Za-z][A-Za-z0-9_-]{0,48}\})",
        text,
    ):
        return True
    if re.fullmatch(
        r"(?:https?://\S+|doi:\s*\S+)", text, flags=re.IGNORECASE
    ):
        return True
    xml_formula = re.fullmatch(
        r"<(?P<tag>[A-Z][A-Z0-9_]*)>\s*(?P<body>[^<>]+?)\s*</(?P=tag)>",
        text,
    )
    if xml_formula and re.fullmatch(
        r"[A-Z0-9_+*/=().,;:\-\s]+", xml_formula.group("body")
    ):
        return True
    body = re.sub(r"^\d+(?:\.\d+)*\s+", "", text)
    if not (
        len(text) <= 40
        and body
        and not re.search(r"\s", body)
        and re.fullmatch(r"[A-Za-z0-9×+_.:/-]+", body)
    ):
        return False
    # A normal title-cased word (e.g. "Inference") is human-readable text,
    # not an identifier.  Preserve tokens with digits, identifier punctuation,
    # all-caps spelling, or an internal capital such as RoadScene / VisDrone.
    return bool(
        re.search(r"[0-9×+_.:/-]", body)
        or (len(body) >= 2 and body.isupper())
        or re.search(r"[A-Z]", body[1:])
    )


def rule_translation(text: str) -> str | None:
    stripped = text.strip()
    bracket_labels = {
        "[Task]": "[任务]",
        "[Evaluation Criteria]": "[评估标准]",
        "[Topic]": "[主题]",
        "[Section]": "[章节]",
        "[Output Format]": "[输出格式]",
        "[Claim Definition]": "[主张定义]",
        "[Guidelines]": "[指南]",
    }
    if stripped in bracket_labels:
        return bracket_labels[stripped]
    fixed = {
        "as follows:": "如下：",
        "as follows": "如下",
        ", and": "，且",
        "and": "且",
        "source:": "来源：",
        "claim:": "陈述：",
        "rationale:": "评分理由：",
        "final score:": "最终得分：",
        "example:": "示例：",
        "list of claims:": "陈述列表：",
    }
    if stripped.lower() in fixed:
        return fixed[stripped.lower()]
    academic_heading = re.fullmatch(
        r"(?:(?P<number>\d+(?:\.\d+)*)\s+)?"
        r"(?P<heading>Abstract|Introduction|Initialization|Experiment|Dataset|"
        r"Baselines|Conclusion|Conclusions|Limitations|References)",
        stripped,
        re.IGNORECASE,
    )
    if academic_heading:
        heading = {
            "abstract": "摘要",
            "introduction": "引言",
            "initialization": "初始化",
            "experiment": "实验",
            "dataset": "数据集",
            "baselines": "基线方法",
            "conclusion": "结论",
            "conclusions": "结论",
            "limitations": "局限性",
            "references": "参考文献",
        }[academic_heading.group("heading").lower()]
        number = academic_heading.group("number")
        return f"{number} {heading}" if number else heading
    technical_heading = re.fullmatch(
        r"([A-Z][A-Z0-9+_.-]{0,15})\s+"
        r"(block|module|network|encoder|decoder)",
        stripped,
        re.IGNORECASE,
    )
    if technical_heading:
        noun = {
            "block": "模块",
            "module": "模块",
            "network": "网络",
            "encoder": "编码器",
            "decoder": "解码器",
        }[technical_heading.group(2).lower()]
        return f"{technical_heading.group(1)}{noun}"
    match = re.fullmatch(
        r"Only\s+reply\s+with\s+['\"]Yes['\"]\s+or\s+['\"]No['\"]\s*:?",
        stripped,
        re.IGNORECASE,
    )
    if match:
        return "仅回答“是”或“否”："
    match = re.fullmatch(
        r"\(Example:\s*(<SCORE>.*?</SCORE>);\s*"
        r"scores can include two decimal places?\)",
        stripped,
        re.IGNORECASE,
    )
    if match:
        return f"（示例：{match.group(1)}；分数可保留两位小数）"
    if re.fullmatch(
        r"Output\s+ONLY\s+the\s+serial\s+numbers\s+to\s+remove\.\s*"
        r"No\s+additional\s+text\.?",
        stripped,
        re.IGNORECASE,
    ):
        return "仅输出要删除的序号，不要添加其他文本。"
    match = re.fullmatch(
        r"\((\d+)\)\s+is simplified as follows:?", stripped, re.IGNORECASE
    )
    if match:
        return f"（{match.group(1)}）式化简如下："
    match = re.fullmatch(
        r"Eq\.\s*\((\d+)\)\s+can be deduced as follows?:?",
        stripped,
        re.IGNORECASE,
    )
    if match:
        return f"式（{match.group(1)}）可推导如下："
    return None


def valid_translation(source: str, translated: str) -> bool:
    source = source.strip()
    translated = translated.strip()
    if not translated:
        return False
    ruled = rule_translation(source)
    if ruled is not None:
        return translated == ruled
    if translated == source:
        return is_technical_identifier(source)
    if len(source) >= 20:
        if not re.search(r"[\u3400-\u9fff]", translated):
            return False
        if len(translated) < max(2, int(len(source) * 0.04)):
            return False
    return True


def translation_variants(text: str) -> list[tuple[str, str]]:
    """Return deterministic fallbacks for API-unfriendly leading labels."""
    variants = [("", text)]
    match = re.match(
        r"^(?P<prefix>\s*[-•]?\s*[A-Z][A-Z0-9+_.:/-]{0,12}\.\s+)(?P<body>.+)$",
        text,
        flags=re.DOTALL,
    )
    if match and len(match.group("body")) >= 20:
        variants.append((match.group("prefix"), match.group("body")))
    numbered = re.match(
        r"^(?P<prefix>\s*(?:(?:Eq\.)?\s*\(\d+\)|\d+[.)])\s+)(?P<body>.+)$",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if numbered and len(numbered.group("body")) >= 10:
        variants.append((numbered.group("prefix"), numbered.group("body")))
    appendix = re.match(
        r"^(?P<prefix>\s*(?:Appendix\s+)?[A-Z](?:\.\d+)*[.)]?\s+)"
        r"(?P<body>[A-Za-z].+)$",
        text,
        flags=re.DOTALL,
    )
    if appendix and len(appendix.group("body")) >= 10:
        variants.append((appendix.group("prefix"), appendix.group("body")))
    normalized = re.sub(r"\s+", " ", text).strip()
    if normalized != text:
        variants.append(("", normalized))
    return variants


def translate_chunk_resilient(
    client: LawAIClient,
    text: str,
    *,
    retries: int = 3,
    depth: int = 0,
) -> tuple[str, int]:
    """Translate one chunk, recursively reducing it when output is truncated."""
    ruled = rule_translation(text)
    if ruled is not None:
        return ruled, 0
    if is_technical_identifier(text):
        return text, 0
    calls = 0
    variants = translation_variants(text)
    attempts = variants + [variants[-1]] * max(0, retries - len(variants))
    last_error: Exception | None = None
    for attempt, (prefix, request_text) in enumerate(attempts[:retries]):
        try:
            calls += 1
            translated = prefix + client.translate_text(request_text)
            if valid_translation(text, translated):
                return translated, calls
            last_error = LawAIError(
                f"接口返回疑似截断或未翻译内容：{translated[:80]!r}"
            )
        except Exception as exc:
            last_error = exc
        if attempt + 1 < retries:
            time.sleep(2**attempt)

    # Some translation backends occasionally classify a short academic
    # sentence as a title or identifier and echo it unchanged. A neutral data
    # label disambiguates the task without adding semantic context; remove the
    # translated label before accepting the result.
    try:
        calls += 1
        wrapped = client.translate_text(f"Academic text: {text}")
        unwrapped = re.sub(
            r"^\s*(?:学术文本|学术语篇|学术性文本)\s*[:：]\s*",
            "",
            wrapped,
            count=1,
        )
        if valid_translation(text, unwrapped):
            return unwrapped, calls
        last_error = LawAIError(
            f"接口包装重试仍为疑似截断或未翻译内容：{unwrapped[:80]!r}"
        )
    except Exception as exc:
        last_error = exc

    if len(text) >= 80 and depth < 4:
        reduced_limit = max(60, min(260, len(text) // 2))
        pieces = split_for_api(text, limit=reduced_limit)
        if len(pieces) > 1:
            translated_pieces = []
            for piece in pieces:
                translated_piece, piece_calls = translate_chunk_resilient(
                    client,
                    piece,
                    retries=retries,
                    depth=depth + 1,
                )
                calls += piece_calls
                translated_pieces.append(translated_piece)
            combined = "".join(translated_pieces)
            if valid_translation(text, combined):
                return combined, calls
    assert last_error is not None
    raise last_error


def split_for_api(text: str, limit: int = 700) -> list[str]:
    if len(text) <= limit:
        return [text]
    sentences = re.split(r"(?<=[.;:?!])\s+|(?<=;)\s*", text)
    units: list[str] = []
    for sentence in sentences:
        sentence = sentence.strip()
        while len(sentence) > limit:
            cut = sentence.rfind(", ", 0, limit)
            if cut < limit // 2:
                cut = sentence.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            else:
                cut += 1
            units.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            units.append(sentence)
    chunks: list[str] = []
    current = ""
    for sentence in units:
        if current and len(current) + len(sentence) + 1 > limit:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def _save(path: Path, payload: dict, segments: list[dict]) -> None:
    output = {key: value for key, value in payload.items() if key != "segments"}
    output["segments"] = segments
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")


def translate_segments_file(
    source: Path,
    output: Path,
    *,
    token: str,
    base_url: str = DEFAULT_BASE_URL,
    retries: int = 3,
    concurrency: int = 1,
) -> dict:
    payload = json.loads(source.read_text(encoding="utf-8"))
    segments = payload.get("segments", [])
    cached: dict[tuple[str, str], str] = {}
    cached_by_source: dict[str, str] = {}
    if output.exists():
        old = json.loads(output.read_text(encoding="utf-8"))
        cached = {
            (item.get("id", ""), item.get("source_text", "")): item.get(
                "translated_text", ""
            )
            for item in old.get("segments", [])
            if valid_translation(
                item.get("source_text", ""), item.get("translated_text", "")
            )
        }
        cached_by_source = {
            item.get("source_text", ""): item.get("translated_text", "")
            for item in old.get("segments", [])
            if item.get("source_text", "").strip()
            and valid_translation(
                item.get("source_text", ""), item.get("translated_text", "")
            )
        }

    pending = []
    for item in segments:
        item["translated_text"] = cached.get(
            (item.get("id", ""), item.get("source_text", "")),
            cached_by_source.get(
                item.get("source_text", ""), item.get("translated_text", "")
            ),
        )
        if not valid_translation(item.get("source_text", ""), item["translated_text"]):
            item["translated_text"] = ""
            pending.append(item)
    if pending and not token.strip():
        raise LawAIError(
            "存在未翻译区域，但环境变量 DEEPSEEK_API_KEY 为空"
        )

    client = LawAIClient(base_url, token) if pending else None
    version = client.verify() if client else {"version": "cached"}
    total = len(segments)
    requests_made = 0
    cache_hits = 0
    for item in segments:
        if item["translated_text"].strip():
            cache_hits += 1

    if concurrency <= 1:
        for index, item in enumerate(segments, 1):
            if item["translated_text"].strip():
                continue
            translated_parts = []
            chunks = split_for_api(item["source_text"])
            for chunk_index, chunk in enumerate(chunks, 1):
                assert client is not None
                translated_chunk, calls = translate_chunk_resilient(
                    client, chunk, retries=retries
                )
                translated_parts.append(translated_chunk)
                requests_made += calls
                print(
                    f"[{index}/{total}] {item['id']} 分片 {chunk_index}/{len(chunks)}",
                    flush=True,
                )
            item["translated_text"] = "".join(translated_parts)
            _save(output, payload, segments)
    else:
        indexed_pending = [
            (index, item)
            for index, item in enumerate(segments, 1)
            if not item["translated_text"].strip()
        ]

        def translate_item(index: int, item: dict):
            local_client = LawAIClient(base_url, token)
            translated_parts = []
            calls_made = 0
            chunks = split_for_api(item["source_text"])
            for chunk in chunks:
                translated_chunk, calls = translate_chunk_resilient(
                    local_client, chunk, retries=retries
                )
                translated_parts.append(translated_chunk)
                calls_made += calls
            return index, item, "".join(translated_parts), calls_made, len(chunks)

        failures = []
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
            futures = {
                executor.submit(translate_item, index, item): (index, item)
                for index, item in indexed_pending
            }
            for completed, future in enumerate(as_completed(futures), 1):
                try:
                    index, item, translated, calls, chunks = future.result()
                except Exception as exc:
                    index, item = futures[future]
                    failures.append((item["id"], str(exc)))
                    print(
                        f"[并发失败；总序号 {index}/{total}] {item['id']}：{exc}",
                        flush=True,
                    )
                    continue
                item["translated_text"] = translated
                requests_made += calls
                _save(output, payload, segments)
                print(
                    f"[{completed}/{len(indexed_pending)} 待处理；总序号 {index}/{total}] "
                    f"{item['id']}，内部片段 {chunks}",
                    flush=True,
                )
        if failures:
            details = "；".join(f"{item_id}: {error}" for item_id, error in failures)
            raise LawAIError(f"{len(failures)} 个翻译分段失败，成功结果已保存：{details}")
    _save(output, payload, segments)
    if cache_hits:
        print(f"复用翻译缓存：{cache_hits}/{total} 个区域", flush=True)
    return {
        "segments": total,
        "pending_at_start": len(pending),
        "requests_made": requests_made,
        "cache_hits": cache_hits,
        "concurrency": max(1, concurrency),
        "service_version": version.get("version", "unknown"),
    }
