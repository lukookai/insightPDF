# -*- coding: utf-8 -*-
"""HTTPS-only translation bridge for a runtime with a working SSL module."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import translate_batch as tb


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-url", default=tb.DEFAULT_BASE_URL)
    parser.add_argument("--model", default=tb.DEFAULT_MODEL)
    args = parser.parse_args()
    token = os.environ.get("DEEPSEEK_API_KEY", "")
    if not token:
        raise RuntimeError("missing DEEPSEEK_API_KEY")
    items = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = tb.translate_batch(items, token=token, base_url=args.base_url,
                                model=args.model, dry_run=False)
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
