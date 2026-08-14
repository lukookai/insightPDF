from __future__ import annotations

import dataclasses
import json
import types
from functools import lru_cache
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints


@lru_cache(maxsize=None)
def _hints(model: type) -> dict[str, Any]:
    return get_type_hints(model)


def _convert(value: Any, target: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(target)
    if origin is list:
        item_type = get_args(target)[0]
        return [_convert(item, item_type) for item in value]
    if origin in {types.UnionType, getattr(__import__("typing"), "Union", object())}:
        candidates = [item for item in get_args(target) if item is not type(None)]
        return _convert(value, candidates[0]) if candidates else value
    if isinstance(target, type) and dataclasses.is_dataclass(target):
        hints = _hints(target)
        values = {
            field.name: _convert(value[field.name], hints.get(field.name, Any))
            for field in dataclasses.fields(target)
            if field.name in value
        }
        return target(**values)
    return value


def load_document_il(path: Path):
    """Load BabelDOC's snake_case debug JSON back into its dataclass IL."""

    from babeldoc.format.pdf.document_il.il_version_1 import Document

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return _convert(payload, Document)
