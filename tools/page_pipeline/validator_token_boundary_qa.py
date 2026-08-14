# -*- coding: utf-8 -*-
"""Phase 4E.1B-C3.2 -- Validator token-boundary QA.

Proves the MathDenseTranslationValidator no longer relies on substring
matching for single-character protected math tokens.  Six cases exercise the
exact blind spot from C3.1 (a single-char value like "R"/"e" being "found" as
a substring of "FORMULA"/"base"), plus identity/order/count correctness.

The module is signature-agnostic: it calls the current validator with the
protected output when supported, and falls back to the 3-arg form for the
pre-fix validator (so it can also emit the OLD red evidence).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(HERE.parent.parent / "实现源码" / "pdf_translator") not in sys.path:
    sys.path.insert(0, str(HERE.parent.parent / "实现源码" / "pdf_translator"))

from math_dense_translation_router import validate_translation  # noqa: E402

# --------------------------------------------------------------------------
# 6 boundary cases.  expected_pass = what a CORRECT validator must return.
# --------------------------------------------------------------------------
CASES = [
    {
        # single-char "R" token deleted, but the literal word "FORMULA" (which
        # contains "R") is still present -> substring matching wrongly passes.
        "case": "delete_math_token_R_with_FORMULA_substring",
        "source_text": "R denotes the rate",
        "mapping": {"{{MATH_TOKEN_001}}": "R"},
        "protected_output": "FORMULA 表示速率",
        "restored": "FORMULA 表示速率",
        "expected_pass": False,
        "note": "FORMULA is literal English containing 'R'; old validator sees 'R' as a substring.",
    },
    {
        # single-char "e" token deleted, but prose still contains "e" (in
        # "base") -> substring matching wrongly passes.
        "case": "delete_math_token_e_with_prose_e",
        "source_text": "e is the base",
        "mapping": {"{{MATH_TOKEN_001}}": "e"},
        "protected_output": "是自然对数 base 的底数",
        "restored": "是自然对数 base 的底数",
        "expected_pass": False,
        "note": "prose word 'base' contains 'e'; old validator sees 'e' as substring.",
    },
    {
        # placeholder fully present -> must PASS.
        "case": "token_present_fully",
        "source_text": "x is the value",
        "mapping": {"{{MATH_TOKEN_001}}": "x"},
        "protected_output": "{{MATH_TOKEN_001}} 是值",
        "restored": "x 是值",
        "expected_pass": True,
        "note": "placeholder present exactly once -> pass.",
    },
    {
        # identity ok but order swapped -> must FAIL.
        "case": "token_order_swapped",
        "source_text": "a and b",
        "mapping": {"{{MATH_TOKEN_001}}": "a", "{{MATH_TOKEN_002}}": "b"},
        "protected_output": "{{MATH_TOKEN_002}} 和 {{MATH_TOKEN_001}} 是值",
        "restored": "b 和 a 是值",
        "expected_pass": False,
        "note": "identity ok but order swapped -> fail.",
    },
    {
        # token repeated once -> must FAIL.
        "case": "token_repeated",
        "source_text": "a",
        "mapping": {"{{MATH_TOKEN_001}}": "a"},
        "protected_output": "{{MATH_TOKEN_001}} {{MATH_TOKEN_001}} 是值",
        "restored": "a a 是值",
        "expected_pass": False,
        "note": "token duplicated -> fail.",
    },
    {
        # token missing once -> must FAIL.
        "case": "token_missing_once",
        "source_text": "a and b",
        "mapping": {"{{MATH_TOKEN_001}}": "a", "{{MATH_TOKEN_002}}": "b"},
        "protected_output": "{{MATH_TOKEN_001}} 和 是值",
        "restored": "a 和 是值",
        "expected_pass": False,
        "note": "second token missing -> fail.",
    },
]


def _validate(source, template, restored, protected_output):
    """Call the current validator, tolerating both the pre-fix (3-arg) and
    post-fix (4-arg) signatures."""
    try:
        return validate_translation(source, template, restored, protected_output)
    except TypeError:
        return validate_translation(source, template, restored)


def assess():
    rows = []
    for c in CASES:
        template = {"mapping": c["mapping"]}
        v = _validate(c["source_text"], template, c["restored"], c["protected_output"])
        actual_pass = bool(v.get("pass"))
        rows.append({
            "case": c["case"],
            "note": c["note"],
            "expected_pass": c["expected_pass"],
            "actual_pass": actual_pass,
            "correct": actual_pass == c["expected_pass"],
            "failed_checks": v.get("failed", []),
        })
    correct = sum(1 for r in rows if r["correct"])
    return {
        "schema_version": "phase4e1b.c32.validator_token_boundary_qa.v1",
        "cases": rows,
        "summary": {
            "case_count": len(rows),
            "correct_count": correct,
            "all_correct": correct == len(rows),
            "false_negative_count": sum(
                1 for r in rows if r["expected_pass"] is False and r["actual_pass"] is True),
        },
        "decision": "pass" if correct == len(rows) else "red",
    }


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "validator_boundary_after.json"
    result = assess()
    Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    for r in result["cases"]:
        print(f'{r["case"]:45s} expected={str(r["expected_pass"]):5s} '
              f'actual={str(r["actual_pass"]):5s} correct={r["correct"]} '
              f'failed={r["failed_checks"]}')
    print("summary:", json.dumps(result["summary"], ensure_ascii=False))
    print("decision:", result["decision"])


if __name__ == "__main__":
    main()
