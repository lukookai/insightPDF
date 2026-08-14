"""Env shims shared by the direct-layout tools."""

from __future__ import annotations

import os
from pathlib import Path


def ensure_babeldoc_importable(cache_home: Path) -> None:
    """Point HOME / caches at ``cache_home`` so the ONNX model resolves.

    Mirrors ``tools.table_geometry_debug.path_babeldoc._ensure_babeldoc_importable``.
    """
    cache_home = Path(cache_home).expanduser().resolve()
    cache_home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(cache_home)
    os.environ["XDG_CACHE_HOME"] = str(cache_home / ".cache")
    os.environ["HF_HOME"] = str(cache_home / ".hf-home")
