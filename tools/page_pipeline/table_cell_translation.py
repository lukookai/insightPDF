# -*- coding: utf-8 -*-
"""Canonical translation closure for existing TableModel LogicalCells."""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any, Callable

from table_cell_translation_qa import table_cell_translation_requirement
from translate_batch import BATCH_SIZE, translate_batch


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _valid_target(source: str, target: str | None) -> bool:
    target = (target or "").strip()
    if not target or target == source.strip():
        return False
    return bool(_CJK_RE.search(target))


class ExistingTranslationCache:
    """Read-only union of existing document translation caches.

    Entries are indexed by source text because historical cache keys include
    the stable prompt/version payload rather than the LogicalCell id.
    """

    def __init__(self, paths: list[str | Path] | None = None):
        self._targets: dict[str, tuple[str, str]] = {}
        for path_value in paths or []:
            path = Path(path_value)
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            entries = data.get("entries") if isinstance(data, dict) else None
            if not isinstance(entries, dict):
                continue
            for entry in entries.values():
                if not isinstance(entry, dict):
                    continue
                source = str(entry.get("source_text") or "").strip()
                target = str(entry.get("translation") or "").strip()
                if source and _valid_target(source, target):
                    self._targets.setdefault(source, (target, str(path)))

    def get(self, source: str) -> tuple[str, str] | None:
        return self._targets.get(source.strip())


def _estimated_width(text: str, font_size: float) -> float:
    units = 0.0
    for char in text:
        if char.isspace():
            units += 0.28
        elif _CJK_RE.match(char):
            units += 1.0
        elif char.isdigit():
            units += 0.52
        elif char.isascii():
            units += 0.55
        else:
            units += 0.8
    return units * font_size


def _cell_local_fit(cell: dict[str, Any], text: str,
                    minimum_font_ratio: float = 0.70) -> None:
    """Fit only this cell's text; never mutate table/row/column geometry."""
    bbox = cell.get("layout_bbox") or cell.get("bbox") or [0, 0, 0, 0]
    width = max(float(bbox[2]) - float(bbox[0]), 0.1)
    source_size = float(cell.get("source_font_size") or 9.0)
    needed = _estimated_width(text, source_size)
    ratio = min(1.0, (width * 0.97) / max(needed, 0.1))
    applied_ratio = max(ratio, minimum_font_ratio)
    cell["render_font_size"] = round(source_size * applied_ratio, 3)
    cell["fit_strategy"] = ("original" if applied_ratio >= 0.999
                            else "cell_local_font_fit")
    cell["minimum_font_ratio"] = minimum_font_ratio
    cell["overflow"] = bool(ratio < minimum_font_ratio)


def _split_target(text: str, weights: list[int]) -> list[str]:
    """Split one cached sentence into ordered, non-overlapping cell targets."""
    if len(weights) <= 1:
        return [text]
    total_weight = max(sum(weights), 1)
    cuts = []
    previous = 0
    cumulative = 0
    for index, weight in enumerate(weights[:-1]):
        cumulative += weight
        ideal = round(len(text) * cumulative / total_weight)
        minimum = previous + 1
        maximum = len(text) - (len(weights) - index - 1)
        cut = max(minimum, min(ideal, maximum))
        cuts.append(cut)
        previous = cut
    parts = []
    start = 0
    for cut in cuts + [len(text)]:
        parts.append(text[start:cut].strip())
        start = cut
    return parts


def _redistribute_continuation_targets(page_model: dict[str, Any],
                                       minimum_font_ratio: float) -> int:
    """Remove cache over-completion across adjacent physical line cells.

    A lowercase-leading cell immediately below the same column is a strong,
    geometry-independent continuation signal.  The group keeps its existing
    cached sentence, but partitions it once across the original LogicalCells.
    """
    redistributed = 0
    for region in page_model.get("regions") or []:
        if region.get("type") != "table" or not region.get("payload"):
            continue
        cells = region["payload"].get("cells") or []
        by_col: dict[int, list[dict[str, Any]]] = {}
        for cell in cells:
            by_col.setdefault(int(cell.get("col", -1)), []).append(cell)
        for column_cells in by_col.values():
            column_cells.sort(key=lambda cell: int(cell.get("row", -1)))
            index = 0
            while index < len(column_cells):
                group = [column_cells[index]]
                cursor = index + 1
                while cursor < len(column_cells):
                    previous, current = group[-1], column_cells[cursor]
                    source = str(current.get("source_text") or "").lstrip()
                    adjacent = (int(current.get("row", -1))
                                == int(previous.get("row", -1)) + 1)
                    lowercase_continuation = bool(source and source[0].islower())
                    if not (adjacent and lowercase_continuation
                            and previous.get("translation_required")
                            and current.get("translation_required")):
                        break
                    group.append(current)
                    cursor += 1
                if len(group) > 1:
                    candidates = [str(cell.get("canonical_target") or "")
                                  for cell in group]
                    sentence = max(candidates, key=len)
                    if sentence:
                        weights = [max(len(str(cell.get("source_text") or "")), 1)
                                   for cell in group]
                        parts = _split_target(sentence, weights)
                        for cell, part in zip(group, parts):
                            cell["canonical_target"] = part
                            cell["translated_text"] = part
                            cell["render_text"] = part
                            cell["translation_route"] += \
                                "+continuation_redistribution"
                            _cell_local_fit(
                                cell, part,
                                minimum_font_ratio=minimum_font_ratio)
                        redistributed += len(group)
                index = max(cursor, index + 1)
    return redistributed


def close_table_cell_translations(
        page_model: dict[str, Any], *,
        translations: dict[str, str] | None = None,
        cache_paths: list[str | Path] | None = None,
        token: str = "", base_url: str | None = None,
        model_name: str | None = None, dry_run: bool = False,
        provider: Callable[..., dict[str, str]] = translate_batch,
        minimum_font_ratio: float = 0.70) -> tuple[dict[str, Any], dict[str, int]]:
    """Return a copy with canonical targets attached to every LogicalCell."""
    model_copy = copy.deepcopy(page_model)
    translations = translations or {}
    cache = ExistingTranslationCache(cache_paths)
    missing: list[dict[str, str]] = []
    required_count = 0
    translated_count = 0
    cache_hits = 0

    for region in model_copy.get("regions") or []:
        if region.get("type") != "table" or not region.get("payload"):
            continue
        for cell in region["payload"].get("cells") or []:
            source = str(cell.get("source_text") or "").strip()
            requirement = table_cell_translation_requirement(source)
            required = bool(requirement["required"])
            cell_id = str(cell.get("cell_id") or
                          "R%dC%d" % (cell.get("row", -1),
                                      cell.get("col", -1)))
            cell["semantic_role"] = ("table_header" if cell.get("is_header")
                                     else "table_cell")
            cell["translation_required"] = required
            if not required:
                cell["translation_route"] = requirement["route"]
                cell["canonical_target"] = source
                cell["render_text"] = source
                cell["render_source"] = "preserved_source"
                if not source:
                    cell["translation_status"] = "empty"
                else:
                    cell["translation_status"] = "unchanged"
                _cell_local_fit(cell, source,
                                minimum_font_ratio=minimum_font_ratio)
                continue

            required_count += 1
            candidates = [
                (cell.get("canonical_target"), "existing_cell_target"),
                (cell.get("translated_text"), "existing_cell_target"),
                (translations.get(cell_id), "page_translation_cache"),
            ]
            cached = cache.get(source)
            if cached:
                candidates.append((cached[0], "existing_translation_cache"))
            target = ""
            route = ""
            for candidate, candidate_route in candidates:
                if _valid_target(source, str(candidate or "")):
                    target = str(candidate).strip()
                    route = candidate_route
                    break
            if target:
                if route in ("page_translation_cache",
                             "existing_translation_cache"):
                    cache_hits += 1
                cell["canonical_target"] = target
                cell["translated_text"] = target
                cell["translation_status"] = "translated"
                cell["translation_route"] = route
                cell["render_text"] = target
                cell["render_source"] = "canonical_target"
                _cell_local_fit(cell, target,
                                minimum_font_ratio=minimum_font_ratio)
                translated_count += 1
            else:
                cell["translation_route"] = "translation_provider"
                missing.append({"item_id": cell_id, "type": "table_cell",
                                "source_text": source})

    api_calls = 0
    provided: dict[str, str] = {}
    if missing and not dry_run:
        kwargs: dict[str, Any] = {"token": token, "dry_run": False}
        if base_url:
            kwargs["base_url"] = base_url
        if model_name:
            kwargs["model"] = model_name
        provided = provider(missing, **kwargs)
        api_calls = int(math.ceil(len(missing) / float(BATCH_SIZE)))

    by_id = {item["item_id"]: item for item in missing}
    for region in model_copy.get("regions") or []:
        if region.get("type") != "table" or not region.get("payload"):
            continue
        for cell in region["payload"].get("cells") or []:
            cell_id = str(cell.get("cell_id") or
                          "R%dC%d" % (cell.get("row", -1),
                                      cell.get("col", -1)))
            if cell_id not in by_id:
                continue
            source = by_id[cell_id]["source_text"]
            target = str(provided.get(cell_id) or "").strip()
            if _valid_target(source, target):
                cell["canonical_target"] = target
                cell["translated_text"] = target
                cell["translation_status"] = "translated"
                cell["translation_route"] = "translation_provider"
                cell["render_text"] = target
                cell["render_source"] = "canonical_target"
                translated_count += 1
                _cell_local_fit(cell, target,
                                minimum_font_ratio=minimum_font_ratio)
            else:
                cell["canonical_target"] = ""
                cell["translated_text"] = None
                cell["translation_status"] = "translation_failed"
                cell["render_text"] = source
                cell["render_source"] = "source_fallback"
                cell["overflow"] = False

    redistributed = _redistribute_continuation_targets(
        model_copy, minimum_font_ratio=minimum_font_ratio)

    return model_copy, {
        "required_table_cell_count": required_count,
        "translated_cell_count": translated_count,
        "cache_hit_cell_count": cache_hits,
        "table_translation_api_calls": api_calls,
        "table_translation_provider_cell_count": len(missing),
        "continuation_redistributed_cell_count": redistributed,
    }


__all__ = ["ExistingTranslationCache", "close_table_cell_translations"]
