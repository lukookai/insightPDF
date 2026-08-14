# -*- coding: utf-8 -*-
"""MathDensityProfile (Phase 4E.1B-C3).

Computes a document-general math-density profile for a logical paragraph so
the MathDenseTranslationRouter can choose a translation route by CONTENT
STRUCTURE -- never by page number or paragraph id.

The profile integrates five evidence sources:
  * formula ownership (``{{FORMULA_Bn}}`` placeholders),
  * protected runs (``{{CODE_*}}`` / ``{{MONO_*}}`` / ``{{BOLD_*}}``),
  * source-span font evidence (math fonts, super/subscript size),
  * math-symbol / Greek / relation-operator / identifier lexing,
  * adjacent display-formula / equation-reference context.

It deliberately does NOT rely on a single regex: prose vs math is decided by
the union of those signals.
"""
from __future__ import annotations

import re
import unicodedata

# ---- token / placeholder patterns ---------------------------------------
PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_0-9]+\}\}")
FORMULA_PH_RE = re.compile(r"\{\{FORMULA_[A-Z0-9]+\}\}")
PROTECTED_PH_RE = re.compile(r"\{\{(?:CODE|MONO|END_CODE|END_MONO)_[A-Z0-9_]+\}\}")
STYLE_PH_RE = re.compile(r"\{\{(?:BOLD|ITALIC|END_BOLD|END_ITALIC)_[A-Z0-9_]+\}\}")

# ---- math evidence -------------------------------------------------------
MATH_FONT_HINTS = (
    "cmmi", "cmsy", "cmr", "cmbx", "cmex", "msam", "msbm", "eufm",
    "mtsy", "stix", "xits", "math", "latinmodern",
)

# math symbol characters (mathematical operators / delimiters / arrows)
MATH_SYMBOL_CHARS = set(
    "∈∉⊂⊃⊆⊇∪∩×·∘∗⊕⊗⊖⊙⋅∙−±∓÷√∛∝∞∂∇∫∮∑∏≤≥≪≫≠≈≡≃∼∝≺≻⊥∥∧∨¬→←↔⇒⇐⇔↦∈∋⊢⊨"
    "∃∀∅∈∊⊤⊥⋀⋁⋂⋃⋃⨯⨁⨂"
    "∥∣∤~±×÷−–—′″°¹²³⁴⁵⁶⁷⁸⁹⁰₀₁₂₃₄₅₆₇₈₉"
)
RELATION_OP_CHARS = set("=<>≤≥≈≠≡≃∼∈∉⊂⊆⊃⊇≪≫∝±∓")
GREEK_SMALL = set("αβγδεζηθικλμνξοπρστυφχψω")
GREEK_CAP = set("ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ")
SUBSUP_DIGITS = set("⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉")

# prose stop-words that indicate real natural-language prose (not math)
PROSE_HINT_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "to", "in", "on",
    "and", "or", "for", "with", "by", "from", "where", "that", "this",
    "these", "those", "each", "every", "which", "who", "we", "they",
    "our", "their", "denotes", "represents", "is", "be", "being", "used",
    "using", "given", "such", "between", "into", "at", "as", "not", "no",
    "over", "under", "after", "before", "then", "also", "can", "may",
    "will", "would", "should", "has", "have", "had", "avoid", "avoids",
    "respectively", "denote", "follows", "following", "because", "since",
}
MATH_FUNCTION_WORDS = {
    "sin", "cos", "tan", "log", "ln", "exp", "max", "min", "arg", "argmax",
    "argmin", "softmax", "concat", "conv", "det", "tr", "diag", "norm",
    "sigmoid", "relu", "gelu", "sum", "prod", "mean", "std", "var", "lim",
    "sup", "inf", "grad", "div", "curl", "range", "rank", "dim", "mod",
}
LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]*")
LATIN_IDENT_RE = re.compile(r"^[A-Za-z]$")  # single-letter variable
NUMBER_RE = re.compile(r"(?:\d+(?:\.\d+)?(?:e[+-]?\d+)?|10\^\{?-\d+\}?)")


def _is_math_font(font):
    low = (font or "").lower()
    return any(h in low for h in MATH_FONT_HINTS)


def _strip_all(text):
    return PLACEHOLDER_RE.sub(" ", text or "")


def _strip_protected_spans(text):
    """Remove the CONTENT of {{MONO_N}}...{{END_MONO_N}} / {{CODE_N}} spans so
    protected code/email/model identifiers never count as translatable prose."""
    out = text or ""
    # remove balanced MONO/CODE ... END_* spans (content is protected)
    out = re.sub(r"\{\{(?:MONO|CODE)_[A-Z0-9_]+\}\}.*?\{\{END_(?:MONO|CODE)_[A-Z0-9_]+\}\}",
                 " ", out, flags=re.S)
    return out


# --------------------------------------------------------------------------
def build_math_density_profile(paragraph, page_context=None):
    """Build a MathDensityProfile for one logical paragraph.

    ``paragraph``: paragraph payload (source_text, translation_source_text,
    protected_runs, style_role, semantic_role, lines[].spans[]).
    ``page_context``: optional dict with ``formula_region_count``,
    ``formula_span_map``, ``has_equation_reference`` hints.
    """
    page_context = page_context or {}
    pid = paragraph.get("paragraph_id") or paragraph.get("logical_paragraph_id")
    src = (paragraph.get("translation_source_text")
           or paragraph.get("source_text") or "")
    role = paragraph.get("semantic_role") or paragraph.get("style_role") or "body"

    formula_phs = FORMULA_PH_RE.findall(src)
    protected_phs = PROTECTED_PH_RE.findall(src)
    protected_runs = paragraph.get("protected_runs") or {}

    # ---- span / font evidence --------------------------------------------
    span_fonts = []
    span_sizes = []
    for line in paragraph.get("lines") or []:
        for s in line.get("spans") or []:
            if s.get("font"):
                span_fonts.append(s.get("font"))
            if s.get("size"):
                span_sizes.append(float(s["size"]))
    math_font_spans = sum(1 for f in span_fonts if _is_math_font(f))
    main_size = sorted(span_sizes)[len(span_sizes) // 2] if span_sizes else 0.0
    superscript_subscript_count = sum(
        1 for sz in span_sizes
        if main_size > 0 and sz > 0 and sz < main_size * 0.85)

    # ---- lexing the placeholder-stripped text -----------------------------
    raw = _strip_all(_strip_protected_spans(src))
    raw_len = len(re.sub(r"\s+", "", raw))
    math_char_count = 0
    prose_char_count = 0
    math_symbol_count = 0
    relation_operator_count = 0
    greek_count = 0
    latin_identifier_count = 0
    numeric_token_count = 0
    prose_word_count = 0
    math_function_count = 0

    for ch in raw:
        if ch in MATH_SYMBOL_CHARS:
            math_symbol_count += 1
            math_char_count += 1
            if ch in RELATION_OP_CHARS:
                relation_operator_count += 1
        elif ch in GREEK_SMALL or ch in GREEK_CAP:
            greek_count += 1
            math_char_count += 1
        elif ch in SUBSUP_DIGITS:
            math_char_count += 1
        elif ch.isdigit():
            math_char_count += 1

    # latin tokens: classify word vs identifier vs math function
    for tok in LATIN_WORD_RE.findall(raw):
        low = tok.lower()
        if LATIN_IDENT_RE.match(tok):
            # single letter = variable identifier (math) unless a prose
            # article/pronoun ("a", "I")
            if low in ("a", "i"):
                prose_word_count += 1
                prose_char_count += len(tok)
            else:
                latin_identifier_count += 1
                math_char_count += len(tok)
        elif low in MATH_FUNCTION_WORDS:
            math_function_count += 1
            math_char_count += len(tok)
        elif low in PROSE_HINT_WORDS:
            prose_word_count += 1
            prose_char_count += len(tok)
        elif tok.islower():
            # all-lowercase multi-letter word (not a function/hint) = prose
            prose_word_count += 1
            prose_char_count += len(tok)
        else:
            # capitalized / mixed-case / all-caps / alnum word: a variable,
            # model name, dataset, or proper noun -> protected, NOT prose
            latin_identifier_count += 1
            math_char_count += len(tok)

    numeric_token_count = len(NUMBER_RE.findall(raw))

    # ---- aggregate --------------------------------------------------------
    source_char_count = max(len(src), 1)
    placeholder_char_count = sum(len(t) for t in formula_phs + protected_phs)
    math_char_count += placeholder_char_count
    math_ratio = round(math_char_count / source_char_count, 4)
    prose_ratio = round(prose_char_count / source_char_count, 4)

    has_display_formula_neighbor = bool(
        page_context.get("formula_region_count", 0) > 0)
    has_equation_reference = bool(
        re.search(r"\(\d{1,3}\)|Eq\.?\s*\(?\d|eq\.?\s*\(?\d", raw, re.I)
        or page_context.get("has_equation_reference", False))

    evidence = {
        "math_font_span_count": math_font_spans,
        "superscript_subscript_count": superscript_subscript_count,
        "math_function_word_count": math_function_count,
        "prose_hint_word_count": prose_word_count,
        "formula_placeholder_count": len(formula_phs),
        "protected_placeholder_count": len(protected_phs),
    }

    profile = {
        "paragraph_id": pid,
        "semantic_role": role,
        "source_char_count": source_char_count,
        "prose_char_count": prose_char_count,
        "math_char_count": math_char_count,
        "formula_placeholder_count": len(formula_phs),
        "inline_formula_count": len(set(formula_phs)),
        "math_symbol_count": math_symbol_count,
        "relation_operator_count": relation_operator_count,
        "greek_count": greek_count,
        "superscript_subscript_count": superscript_subscript_count,
        "latin_identifier_count": latin_identifier_count,
        "numeric_token_count": numeric_token_count,
        "prose_ratio": prose_ratio,
        "math_ratio": math_ratio,
        "protected_run_count": len(protected_runs),
        "has_display_formula_neighbor": has_display_formula_neighbor,
        "has_equation_reference": has_equation_reference,
        "route_candidate": None,
        "evidence": evidence,
    }
    return profile


# --------------------------------------------------------------------------
def prose_only_char_count(src):
    """Chars of the raw prose after removing all protected/math placeholders."""
    return len(re.sub(r"\s+", "", PLACEHOLDER_RE.sub(" ", src or "")))


def is_pure_math(profile):
    """True when there is no natural-language prose to translate."""
    return (profile["prose_char_count"] == 0
            and (profile["formula_placeholder_count"] > 0
                 or profile["math_symbol_count"] > 0
                 or profile["relation_operator_count"] > 0
                 or profile["latin_identifier_count"] > 0
                 or profile["greek_count"] > 0))
