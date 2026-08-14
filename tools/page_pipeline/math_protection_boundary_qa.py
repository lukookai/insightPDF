# -*- coding: utf-8 -*-
"""Phase 4E.1B-C3.2 -- Math protection boundary QA (QA-FIRST).

Regression test for the MathDenseTranslationRouter protection layer.  It
proves two things on a fixed fixture corpus, with NO production changes:

  * NEGATIVE fixtures (prose word tail before a parenthesis) must NOT have
    their trailing letter split into a ``{{MATH_TOKEN_NNN}}``:
        article(i.e., ...)  ->  the "e" of "article" is prose, not math
        method(s)           ->  the "d" of "method" is prose, not math

  * POSITIVE fixtures (standalone function / variable) MUST stay protected:
        f(x)     -> f        sin(x) -> sin        L(x) -> L
        f_i(x)   -> f_i      g_theta(x) -> g_theta

The only structural rule being tested is the ``[a-z](?=\\()`` false-positive
from C3.1 (which split "article(" into "articl" + "e(").  No page / DLP-id /
filename special case anywhere.
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

from math_dense_translation_router import build_protected_template  # noqa: E402

# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
# (fixture_text, expected_protected_values).  A value of None means "the
# mapping must be EMPTY" (no math object at all -- the prose word tail must
# not be split out).
NEGATIVE_FIXTURES = [
    "article(i.e., ...)",
    "example(e.g., ...)",
    "method(s)",
    "model(s)",
    "result(s)",
    "section(s)",
    "reference(s)",
    "image(s)",
    "paper(s)",
    "function(s)",
    "condition(s)",
]

# (fixture_text, [values that MUST be protected])
POSITIVE_FIXTURES = [
    ("f(x)", ["f"]),
    ("g(x)", ["g"]),
    ("h(t)", ["h"]),
    ("p(y)", ["p"]),
    ("q(z)", ["q"]),
    ("sin(x)", ["sin"]),
    ("cos(x)", ["cos"]),
    ("log(x)", ["log"]),
    ("exp(x)", ["exp"]),
    ("softmax(x)", ["softmax"]),
    ("L(x)", ["L"]),
    ("H(X)", ["H", "X"]),
    ("I(X;Y)", ["I", "X", "Y"]),
    ("f_i(x)", ["f_i"]),
    ("g_θ(x)", ["g_θ"]),
]


def _word_tail_letters(text):
    """Return the list of lowercase-letter chars that sit immediately before
    a '(' AND are the tail of a longer alphabetic word (i.e. preceded by a
    letter).  These are the exact chars the old ``[a-z](?=\\()`` rule wrongly
    protected (e.g. the "e" of "article(")."""
    out = []
    for i, ch in enumerate(text):
        if i + 1 < len(text) and text[i + 1] == "(" and ch.islower() and ch.isascii():
            if i > 0 and text[i - 1].isalpha():
                out.append((i, ch))
    return out


def assess():
    """Run the protection-boundary QA against the CURRENT production code.

    Returns a dict with per-fixture results plus a summary.  Called BEFORE the
    fix it proves the old false-positive (old_red); called AFTER the fix it
    proves the boundary is repaired.
    """
    neg_rows = []
    fp_total = 0
    for text in NEGATIVE_FIXTURES:
        tpl = build_protected_template(text)
        values = list(tpl["mapping"].values())
        tails = [ch for _, ch in _word_tail_letters(text)]
        # a false positive = any word-tail letter ended up protected
        fp = sorted(set(tails) & set(values))
        if fp:
            fp_total += 1
        neg_rows.append({
            "fixture": text,
            "protected_text": tpl["protected_text"],
            "mapping_values": values,
            "word_tail_letters": tails,
            "false_positive_tokens": fp,
            "false_positive": bool(fp),
        })

    pos_rows = []
    tp_detected = 0
    tp_expected = 0
    lost = []
    for text, expected in POSITIVE_FIXTURES:
        tpl = build_protected_template(text)
        values = list(tpl["mapping"].values())
        missing = [v for v in expected if v not in values]
        tp_expected += len(expected)
        tp_detected += len(expected) - len(missing)
        if missing:
            lost.append({"fixture": text, "missing": missing})
        pos_rows.append({
            "fixture": text,
            "expected": expected,
            "detected": [v for v in expected if v in values],
            "missing": missing,
            "recall_ok": not missing,
        })

    summary = {
        "negative_fixture_count": len(NEGATIVE_FIXTURES),
        "false_positive_count": fp_total,
        "positive_fixture_count": len(POSITIVE_FIXTURES),
        "true_math_expected": tp_expected,
        "true_math_detected": tp_detected,
        "lost_true_math_tokens": tp_expected - tp_detected,
    }
    return {
        "schema_version": "phase4e1b.c32.math_protection_boundary_qa.v1",
        "negative_fixtures": neg_rows,
        "positive_fixtures": pos_rows,
        "summary": summary,
        "decision": "pass" if (fp_total == 0 and tp_expected == tp_detected)
                    else "red",
    }


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "math_protection_boundary_after.json"
    result = assess()
    Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    s = result["summary"]
    print(json.dumps(s, ensure_ascii=False))
    print("decision:", result["decision"])


if __name__ == "__main__":
    main()
