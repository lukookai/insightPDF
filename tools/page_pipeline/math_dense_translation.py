# -*- coding: utf-8 -*-
"""MathDenseTranslation (Phase 4E.1B).

Classifies a paragraph into a translation route based on its math density
(formula placeholders + math-span adjacency), never by page number.

Routes:
    body_prose, math_dense_prose, caption, heading, reference, code_protected
"""
from __future__ import annotations

import re

FORMULA_PLACEHOLDER = re.compile(r"\{\{FORMULA_[A-Z0-9]+\}\}")
PLACEHOLDER_ALL = re.compile(r"\{\{[A-Z_0-9]+\}\}")
CJK = re.compile(r"[\u3400-\u9fff]")


def math_density_score(text: str) -> float:
    """0..1: fraction of translatable prose that is math placeholders."""
    if not text:
        return 0.0
    placeholders = FORMULA_PLACEHOLDER.findall(text)
    if not placeholders:
        return 0.0
    placeholder_chars = sum(len(t) for t in placeholders)
    stripped = PLACEHOLDER_ALL.sub(" ", text)
    prose_len = max(len(re.sub(r"\s+", "", stripped)), 1)
    return round(placeholder_chars / max(placeholder_chars + prose_len, 1), 3)


def has_translatable_prose(text: str) -> bool:
    stripped = PLACEHOLDER_ALL.sub(" ", text or "")
    return bool(CJK.search(stripped) or len(re.sub(r"\s+", "", stripped)) >= 4)


def classify_route(payload: dict, semantic_role: str = "normal_body") -> str:
    style_role = (payload.get("style_role") or "body").lower()
    text = payload.get("translation_source_text") or payload.get(
        "source_text") or ""
    if semantic_role in ("references", "appendix", "acknowledgement"):
        return "reference" if semantic_role == "references" else "body_prose"
    if style_role in ("caption", "table_caption", "figure_caption"):
        return "caption"
    if style_role in ("heading", "appendix_heading"):
        return "heading"
    density = math_density_score(text)
    if density >= 0.12:
        return "math_dense_prose"
    return "body_prose"


# ------------------------------------------------------------- QA helpers --
def placeholder_loss(translation: str, source: str) -> list[str]:
    src = set(FORMULA_PLACEHOLDER.findall(source))
    got = set(FORMULA_PLACEHOLDER.findall(translation or ""))
    return sorted(src - got)


def placeholder_mutation(translation: str, source: str) -> list[str]:
    src = FORMULA_PLACEHOLDER.findall(source)
    got = FORMULA_PLACEHOLDER.findall(translation or "")
    # a placeholder whose exact token does not survive (loss or reorder)
    return [t for t in src if got.count(t) != src.count(t)]
