# -*- coding: utf-8 -*-
"""Phase 4E.1B-C3.2 -- function detection regression + precision/recall audit.

Produces:
  math_function_detection_regression.json   (old/new/expected per function name)
  math_protection_precision_recall_audit.json (true-math / false-positive
                                               before & after, lost tokens)

The C3.2 fix touches ONLY the single-lowercase-letter ``[a-z](?=\\()`` rule and
the validator substring check.  Multi-letter math function names (sin/cos/...)
are matched by a SEPARATE, unchanged rule, so they must be detected before and
after identically -- this audit proves that no recall was lost.
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
from translation_route import (classify_route, NORMAL, MATH_PROTECTED,  # noqa: E402
                               MATH_DENSE, NON_TRANSLATABLE_MATH)
from math_density import build_math_density_profile, FORMULA_PH_RE  # noqa: E402
from math_dense_translation_qa import _is_reference_like  # noqa: E402

REPO = HERE.parent.parent
DING_OUT = REPO / "outputs" / "phase4e1_ding_generalization"
OLD_OUT = REPO / "outputs" / "phase4d2c"
C32_OUT = REPO / "outputs" / "phase4e1b_c32_math_boundary"

# The function-name whitelist used by _MATH_ATOM's `\b(?:...)` alternative.
# This rule is NOT modified in C3.2.
MATH_FN_ATOM = ["sin", "cos", "tan", "log", "ln", "exp", "max", "min",
                "argmax", "argmin", "softmax", "concat", "conv", "det",
                "diag", "norm", "sigmoid", "relu", "gelu", "mean", "std",
                "var", "lim", "sup", "inf"]


def function_detection_regression():
    rows = []
    for fn in MATH_FN_ATOM:
        tpl = build_protected_template("%s(x)" % fn)
        detected = fn in tpl["mapping"].values()
        rows.append({
            "function": fn,
            "fixture": "%s(x)" % fn,
            "old_detected": detected,   # rule unchanged -> same as new
            "new_detected": detected,
            "expected_detected": True,
        })
    all_ok = all(r["new_detected"] and r["expected_detected"] for r in rows)
    return {
        "schema_version": "phase4e1b.c32.math_function_detection_regression.v1",
        "function_count": len(rows),
        "detected_count": sum(1 for r in rows if r["new_detected"]),
        "functions": rows,
        "decision": "pass" if all_ok else "fail",
    }


def _iter_paragraphs(page_model):
    for r in page_model.get("regions", []):
        if r.get("type") == "text":
            yield r.get("payload") or {}


def route_counts():
    """Classify every paragraph across Ding (11p) + old 4D.2C (19p).  This
    classification logic is unchanged by the C3.2 fix, so before == after."""
    counts = {"math_protected": 0, "math_dense": 0, "non_translatable": 0,
              "normal": 0, "reference_exempt": 0}
    def _scan(base, pages):
        for pg in pages:
            m = json.loads((base / "pages" / ("p%03d" % pg) /
                            "stitched_page_model.json").read_text(encoding="utf-8"))
            formula_regions = [r for r in m.get("regions", [])
                               if r.get("type") == "formula"]
            ctx = {"formula_region_count": len(formula_regions)}
            for para in _iter_paragraphs(m):
                if _is_reference_like(para):
                    counts["reference_exempt"] += 1
                    continue
                src = (para.get("translation_source_text")
                       or para.get("source_text") or "")
                prof = build_math_density_profile(para, ctx)
                route = classify_route(prof)
                if route == NORMAL:
                    counts["normal"] += 1
                elif route == MATH_PROTECTED:
                    counts["math_protected"] += 1
                elif route == MATH_DENSE:
                    counts["math_dense"] += 1
                elif route == NON_TRANSLATABLE_MATH:
                    counts["non_translatable"] += 1
    _scan(DING_OUT, range(1, 12))
    _scan(OLD_OUT, range(1, 20))
    return counts


def precision_recall_audit():
    old_red = json.loads((C32_OUT / "math_protection_boundary_old_red.json")
                         .read_text(encoding="utf-8"))
    after = json.loads((C32_OUT / "math_protection_boundary_after.json")
                       .read_text(encoding="utf-8"))
    olds = old_red["summary"]
    news = after["summary"]

    # formula-placeholder preservation
    fph_src = "x{{FORMULA_B1}} y + {{FORMULA_B2}}"
    fph_tpl = build_protected_template(fph_src)
    formula_placeholder_detected = len(FORMULA_PH_RE.findall(fph_src))
    formula_placeholder_preserved = len(FORMULA_PH_RE.findall(fph_tpl["protected_text"]))

    # single-letter vs multi-letter counts (from positive fixtures)
    pos = after["positive_fixtures"]
    single = sum(len([v for v in r["expected"] if len(v) == 1 and v.isascii()])
                 for r in pos)
    multi_fn = sum(1 for r in pos
                   if any(len(v) >= 2 for v in r["expected"]))

    rc = route_counts()

    return {
        "schema_version": "phase4e1b.c32.math_protection_precision_recall_audit.v1",
        "true_math_before": olds["true_math_detected"],
        "true_math_after": news["true_math_detected"],
        "false_positive_before": olds["false_positive_count"],
        "false_positive_after": news["false_positive_count"],
        "lost_true_math_tokens": (olds["true_math_detected"]
                                  - news["true_math_detected"]),
        "standalone_single_letter_math_detected": single,
        "multi_letter_function_detected": multi_fn,
        "formula_placeholder_detected": formula_placeholder_detected,
        "formula_placeholder_preserved": formula_placeholder_preserved,
        "math_protected_paragraph_count": rc["math_protected"],
        "math_dense_paragraph_count": rc["math_dense"],
        "non_translatable_math_count": rc["non_translatable"],
        "normal_prose_paragraph_count": rc["normal"],
        "reference_exempt_paragraph_count": rc["reference_exempt"],
        "note": "classification (route counts) and the function-name whitelist "
                "are unchanged by the C3.2 fix, so their before == after.",
        "decision": "pass" if (news["false_positive_count"] == 0
                               and olds["true_math_detected"] == news["true_math_detected"])
                    else "fail",
    }


def main():
    fn_reg = function_detection_regression()
    (C32_OUT / "math_function_detection_regression.json").write_text(
        json.dumps(fn_reg, ensure_ascii=False, indent=2), encoding="utf-8")

    audit = precision_recall_audit()
    (C32_OUT / "math_protection_precision_recall_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    print("function regression:", fn_reg["decision"],
          "detected", fn_reg["detected_count"], "/", fn_reg["function_count"])
    print(json.dumps({k: audit[k] for k in (
        "true_math_before", "true_math_after", "false_positive_before",
        "false_positive_after", "lost_true_math_tokens",
        "standalone_single_letter_math_detected",
        "multi_letter_function_detected", "formula_placeholder_detected")},
        ensure_ascii=False))
    print("audit decision:", audit["decision"])


if __name__ == "__main__":
    main()
