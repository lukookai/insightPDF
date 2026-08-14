# -*- coding: utf-8 -*-
"""MathDenseTranslationQA (Phase 4E.1B-C3) -- QA-FIRST red-evidence tool.

Builds a MathDensityProfile + route for every paragraph, then flags the
math-dense translation failures the old baseline silently degraded into
``unchanged_rejected`` (math-heavy content returned verbatim).

Metrics (QA-FIRST, against the OLD baseline):

    math_dense_paragraph_count          math-containing, non-reference paras
    math_dense_unchanged_rejected_count of those, returned verbatim
    math_dense_placeholder_loss_count   lost {{FORMULA_Bn}}
    math_dense_math_token_mutation_count  altered protected math token
    math_dense_prose_residual_count     untranslated English prose words
    math_dense_false_positive_count     math-dense prose with no prose at all

Document-general: fixture ids live only in the harness.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from math_density import (FORMULA_PH_RE, PLACEHOLDER_RE,
                          build_math_density_profile)
from translation_route import classify_route

FORMULA_TOKEN = re.compile(r"\{\{FORMULA_[A-Z0-9]+\}\}")
PROTECTED_TOKEN = re.compile(r"\{\{(?:CODE|MONO)_[A-Z0-9_]+\}\}")
CJK = re.compile(r"[\u4e00-\u9fff]")


def _norm_eq(a, b):
    return re.sub(r"\s+", "", (a or "")) == re.sub(r"\s+", "", (b or ""))


def _has_math(profile):
    return (profile["formula_placeholder_count"] > 0
            or profile["math_symbol_count"] > 0
            or profile["relation_operator_count"] > 0
            or profile["latin_identifier_count"] > 0
            or profile["greek_count"] > 0
            or profile["protected_run_count"] > 0)


def _placeholder_loss(source, translation):
    return sorted(set(FORMULA_TOKEN.findall(source))
                  - set(FORMULA_TOKEN.findall(translation or "")))


def _math_token_mutation(source, translation):
    src = PROTECTED_TOKEN.findall(source)
    got = PROTECTED_TOKEN.findall(translation or "")
    return sorted(set(t for t in src if got.count(t) != src.count(t)))


def _residual_prose_words(translation):
    from math_density import PROSE_HINT_WORDS
    tgt = PLACEHOLDER_RE.sub(" ", translation or "")
    if CJK.search(tgt):
        return 0
    words = re.findall(r"[A-Za-z]+", tgt)
    return sum(1 for w in words if w.lower() in PROSE_HINT_WORDS)


def _iter_paragraphs(page_model):
    for r in page_model.get("regions", []):
        if r.get("type") == "text":
            yield r.get("payload") or {}


_REFERENCE_LIKE_RE = re.compile(
    r"(^|\s)\[\d{1,3}\]|arXiv|doi\.org|Proceedings of|Journal of|"
    r"Conference on|Transactions on|Association for|\bet al\.|Springer|"
    r"\bpages?\s+\d|vol\.?\s*\d", re.I)


def _is_reference_like(para):
    """Broad reference detection: citation prefix / bibliography keywords.

    The reference classifier is a SEPARATE concern from math-dense prose;
    this only keeps bibliography entries from polluting the math-dense
    metrics (recorded, not fixed, per C3 scope).
    """
    src = (para.get("source_text") or "").strip()
    if not src:
        return False
    if _REFERENCE_LIKE_RE.search(src):
        return True
    from translation_status import is_reference_item
    return is_reference_item({"type": "paragraph", "source_text": src,
                              "semantic_role": para.get("semantic_role")})


def math_dense_translation_qa(page_models, translations, status_map=None,
                              out_dir=None, out_path=None):
    status_map = status_map or {}
    if isinstance(page_models, dict):
        page_models = [page_models[p] for p in sorted(page_models)]

    rows = []
    route_counts = {"NORMAL": 0, "MATH_PROTECTED": 0, "MATH_DENSE": 0,
                    "NON_TRANSLATABLE_MATH": 0}
    for page_model in page_models:
        page = int(page_model.get("page") or 0)
        page_trans = translations.get(page, {})
        formula_regions = [r for r in page_model.get("regions", [])
                           if r.get("type") == "formula"]
        page_context = {"formula_region_count": len(formula_regions)}
        for para in _iter_paragraphs(page_model):
            pid = para.get("paragraph_id")
            # skip reference/bibliography items (separate classification)
            if _is_reference_like(para):
                continue
            profile = build_math_density_profile(para, page_context)
            route = classify_route(profile)
            profile["route_candidate"] = route
            route_counts[route] = route_counts.get(route, 0) + 1

            src = (para.get("translation_source_text")
                   or para.get("source_text") or "")
            tr = page_trans.get(pid, "") or ""
            unchanged = bool(tr) and _norm_eq(tr, src)
            loss = _placeholder_loss(src, tr)
            mutation = _math_token_mutation(src, tr)
            residual = _residual_prose_words(tr)

            rows.append({
                "page": page, "paragraph_id": pid, "route": route,
                "semantic_role": profile["semantic_role"],
                "prose_char_count": profile["prose_char_count"],
                "math_ratio": profile["math_ratio"],
                "formula_placeholder_count": profile["formula_placeholder_count"],
                "unchanged": unchanged,
                "placeholder_loss": loss,
                "math_token_mutation": mutation,
                "prose_residual_count": residual,
                "old_status": status_map.get(pid, ""),
                "has_math": _has_math(profile),
            })

    # math-dense = has math content (non-reference)
    math_rows = [r for r in rows if r["has_math"]]
    # unchanged_rejected = the OLD baseline's verbatim-source failures, not
    # merely "translation == source" (formula-only paragraphs are
    # legitimately unchanged_allowed)
    unchanged_rejected = [r for r in math_rows
                          if r["old_status"] == "unchanged_rejected"]
    loss_rows = [r for r in math_rows if r["placeholder_loss"]]
    mutation_rows = [r for r in math_rows if r["math_token_mutation"]]
    residual_total = sum(r["prose_residual_count"] for r in math_rows)
    # false positive: math-dense route but zero prose (pure math mislabelled
    # dense), and math rows whose prose is already translated (no failure)
    false_positive = [r for r in rows if r["route"] == "MATH_DENSE"
                      and r["prose_char_count"] == 0]

    counts = {
        "math_dense_paragraph_count": len(math_rows),
        "math_dense_unchanged_rejected_count": len(unchanged_rejected),
        "math_dense_placeholder_loss_count": sum(
            len(r["placeholder_loss"]) for r in loss_rows),
        "math_dense_math_token_mutation_count": sum(
            len(r["math_token_mutation"]) for r in mutation_rows),
        "math_dense_prose_residual_count": residual_total,
        "math_dense_false_positive_count": len(false_positive),
    }

    result = {
        "schema_version": "phase4e1b.c3.math_dense_translation_qa.v1",
        "counts": counts,
        "route_distribution": route_counts,
        "rows": rows,
        "math_dense_rows": math_rows,
        "unchanged_rejected_rows": unchanged_rejected,
        "placeholder_loss_rows": loss_rows,
        "math_token_mutation_rows": mutation_rows,
        "decision": ("fail" if (len(unchanged_rejected) or loss_rows
                                or mutation_rows) else "pass"),
    }
    result.update(counts)
    if out_path:
        _dump(out_path, result)
    elif out_dir:
        _dump(Path(out_dir) / "math_dense_translation_qa.json", result)
    return result


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")
