# -*- coding: utf-8 -*-
"""MathDenseTranslationRouter (Phase 4E.1B-C3).

Routes a paragraph to one of four translation routes by CONTENT STRUCTURE,
builds a ProtectedTranslationTemplate that shields every math object behind
a ``{{MATH_TOKEN_NNN}}``, then validates the restored result byte-for-byte.

    NORMAL_PROSE          -> existing ordinary translation
    MATH_PROTECTED_PROSE  -> protect math objects, translate prose
    MATH_DENSE_PROSE      -> protect math objects, translate prose (dense)
    NON_TRANSLATABLE_MATH -> never enters prose translation

Existing ``{{FORMULA_Bn}}`` / ``{{CODE_*}}`` / ``{{MONO_*}}`` placeholders are
kept VERBATIM (never re-encoded).  No LaTeX is generated, no OCR, no formula
recognition model; formula ink stays on the existing SVG protection chain.
"""
from __future__ import annotations

import re

from math_density import (FORMULA_PH_RE, PLACEHOLDER_RE, MATH_FUNCTION_WORDS,
                          PROSE_HINT_WORDS, build_math_density_profile)
from translation_route import (NORMAL, MATH_PROTECTED, MATH_DENSE,
                               NON_TRANSLATABLE_MATH, classify_route)

# ---- math expression lexing ----------------------------------------------
MATH_SYMBOL_CHARS = (
    "∈∉⊂⊃⊆⊇∪∩×·∘∗⊕⊗⊖⊙⋅∙−±∓÷√∛∝∞∂∇∫∮∑∏≤≥≪≫≠≈≡≃∼∝≺≻⊥∥∧∨¬→←↔⇒⇐⇔↦∈∋⊢⊨∃∀∅⊤⊥"
    "∥∣∤~±×÷−–—′″°¹²³⁴⁵⁶⁷⁸⁹⁰₀₁₂₃₄₅₆₇₈₉"
    "()[]{}⟨⟩|,.;:"
)
GREEK = "αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"

# a math atom: symbol/greek run, number(+unit), equation reference, loss
# name, math function name, standalone uppercase variable, or a lowercase
# function application.  Multi-letter prose words (lowercase OR capitalized)
# are NEVER protected -- proper nouns / model names stay in the prose and
# are kept by the prompt's preserve-proper-noun rule.
_MATH_ATOM = re.compile(
    r"(?:"
    r"[∈∉⊂⊃⊆⊇∪∩×·∘∗⊕⊗⊖⊙⋅∙−±∓÷√∛∝∞∂∇∫∮∑∏≤≥≪≫≠≈≡≃∼∝≺≻⊥∥∧∨¬→←↔⇒⇐⇔↦∈∋⊢⊨∃∀∅⊤⊥∥∣∤~±×÷−–—′″°"
    r"⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉Α-Ωα-ω]"
    r"|\b(?:Eq\.?\s*)?\(\d{1,3}\)"
    r"|\b10\^-?\d+\b|\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b"
    r"|[A-Za-z]_\w+"
    r"|\b(?:sin|cos|tan|log|ln|exp|max|min|argmax|argmin|softmax|concat|"
    r"conv|det|diag|norm|sigmoid|relu|gelu|mean|std|var|lim|sup|inf)\b"
    r"|\b[A-Z]\b"
    r"|[a-z](?=\()"
    r")"
)


def is_standalone_math_atom(match, segment):
    """Decide whether a single lowercase ASCII letter is a standalone math
    function/variable atom.

    A lowercase letter that directly follows another Latin letter is the tail
    of a prose word (a word whose last letter immediately precedes a
    parenthesis), so it must NOT be protected.  A lowercase letter at a
    lexical boundary (space, "(", "-", start of text, ...) is a standalone
    atom (``f(`` in ``f(x)``) and stays protected.

    This is the structural fix for the C3.1 false-positive -- no word
    blacklist, just the "previous char is NOT [A-Za-z]" boundary rule.
    """
    pos = match.start()
    if pos > 0 and segment[pos - 1].isascii() and segment[pos - 1].isalpha():
        return False
    return True


def _protect_math_objects(text):
    """Replace math objects in ``text`` with ``{{MATH_TOKEN_NNN}}``.

    Existing ``{{...}}`` placeholders are preserved verbatim.  Returns
    (protected_text, mapping) where mapping maps token -> original math text.

    A single lowercase ASCII letter matched by ``[a-z](?=\\()`` is only kept as
    a math atom when it is a standalone lexical token (previous char is not a
    Latin letter) -- otherwise it is the tail of a prose word and is left
    untouched.
    """
    mapping = {}
    counter = [0]

    def _emit(match, segment):
        original = match.group(0)
        # never re-encode an existing placeholder
        if PLACEHOLDER_RE.fullmatch(original):
            return original
        # single lowercase ASCII letter -> must be a standalone atom, not the
        # tail of a prose word (a word ending in a letter right before a paren)
        if (len(original) == 1 and original.isascii() and original.islower()
                and not is_standalone_math_atom(match, segment)):
            return original
        counter[0] += 1
        token = "{{MATH_TOKEN_%03d}}" % counter[0]
        mapping[token] = original
        return token

    # First split on placeholders (keep them), then protect math objects in
    # the prose segments.
    out = []
    pos = 0
    for m in PLACEHOLDER_RE.finditer(text):
        prose_seg = text[pos:m.start()]
        if prose_seg:
            out.append(_MATH_ATOM.sub(lambda mo, seg=prose_seg: _emit(mo, seg),
                                      prose_seg))
        out.append(m.group(0))  # keep the existing placeholder verbatim
        pos = m.end()
    tail = text[pos:]
    if tail:
        out.append(_MATH_ATOM.sub(lambda mo, seg=tail: _emit(mo, seg), tail))
    return "".join(out), mapping


def build_protected_template(source_text):
    """Build a ProtectedTranslationTemplate from a source paragraph.

    Returns dict: {protected_text, mapping, math_token_count,
                   formula_placeholder_count}.
    """
    protected, mapping = _protect_math_objects(source_text)
    return {
        "protected_text": protected,
        "mapping": mapping,
        "math_token_count": len(mapping),
        "formula_placeholder_count": len(FORMULA_PH_RE.findall(source_text)),
    }


def restore_template(protected_translation, mapping):
    """Restore ``{{MATH_TOKEN_NNN}}`` -> original math in the translation."""
    out = protected_translation
    for token, original in mapping.items():
        out = out.replace(token, original)
    return out


# --------------------------------------------------------------------------
# Validator
# --------------------------------------------------------------------------
class MathDenseTranslationValidator:
    """Byte-for-byte validation of a math-dense translation result.

    ``protected_output`` is the model's pre-restore output (still carrying the
    ``{{MATH_TOKEN_NNN}}`` placeholders).  When provided, math-token identity is
    checked on those UNRESTORED placeholder tokens (exact count/identity/order)
    instead of substring-searching the restored raw values -- which is what let
    a single-char token like "R" falsely "match" the "FORMULA" substring in the
    old implementation.
    """

    def __init__(self, source_text, template, restored, protected_output=None):
        self.source = source_text
        self.template = template
        self.restored = restored
        self.protected_output = protected_output

    def validate(self):
        src = self.source
        tpl = self.template
        rst = self.restored or ""
        prot = self.protected_output
        checks = {}

        # (1) formula placeholder identity / count / order
        src_ph = FORMULA_PH_RE.findall(src)
        got_ph = FORMULA_PH_RE.findall(rst)
        checks["placeholder_count_equal"] = len(src_ph) == len(got_ph)
        checks["placeholder_identity_equal"] = set(src_ph) == set(got_ph)
        checks["placeholder_order_valid"] = src_ph == got_ph

        # (2) MATH_TOKEN placeholder identity / count / order -- checked on the
        #     PROTECTED output where each token is an unambiguous
        #     ``{{MATH_TOKEN_NNN}}`` string, not the raw restored value.
        mapping = tpl.get("mapping") or {}
        token_re = re.compile(r"\{\{MATH_TOKEN_\d+\}\}")
        expected_tokens = list(mapping.keys())
        if prot is not None:
            got_tokens = token_re.findall(prot)
            checks["math_token_count_equal"] = (
                len(got_tokens) == len(expected_tokens))
            checks["math_token_identity_equal"] = (
                set(got_tokens) == set(expected_tokens))
            checks["math_token_order_valid"] = got_tokens == expected_tokens
            checks["no_new_math_token"] = set(got_tokens) <= set(expected_tokens)
            missing_math = [tok for tok in expected_tokens
                            if tok not in set(got_tokens)]
        else:
            # fallback (no protected output): restored text must carry no
            # leftover placeholder
            leftover = token_re.findall(rst)
            checks["math_token_count_equal"] = not leftover
            checks["math_token_identity_equal"] = not leftover
            checks["math_token_order_valid"] = not leftover
            checks["no_new_math_token"] = True
            missing_math = []

        # no leftover math token in the restored text
        checks["no_leftover_math_token"] = not token_re.findall(rst)

        # (3) no math rewrite: the model must have preserved every math
        #     placeholder exactly; deterministic restore then guarantees the
        #     values land at the correct positions.  Token-boundary aware --
        #     never a substring search of the restored raw values.
        checks["no_math_rewrite"] = (
            checks["math_token_count_equal"]
            and checks["math_token_identity_equal"]
            and checks["math_token_order_valid"])

        # no new / deleted formula (placeholders)
        src_all = re.findall(r"\{\{[A-Z_0-9]+\}\}", src)
        got_all = re.findall(r"\{\{[A-Z_0-9]+\}\}", rst)
        checks["no_new_formula"] = set(got_all) <= set(src_all)
        checks["no_deleted_formula"] = set(src_all) <= set(got_all)

        # prose translation present: CJK in the restored result
        checks["prose_translation_present"] = bool(
            re.search(r"[\u4e00-\u9fff]", rst))

        all_pass = all(checks.values())
        failed = [k for k, v in checks.items() if not v]
        return {"pass": all_pass, "checks": checks, "failed": failed,
                "missing_math_objects": missing_math}


def validate_translation(source_text, template, restored, protected_output=None):
    v = MathDenseTranslationValidator(source_text, template, restored,
                                      protected_output)
    return v.validate()


# --------------------------------------------------------------------------
# Router entry
# --------------------------------------------------------------------------
def route_paragraph(paragraph, page_context=None):
    """Classify a paragraph into its route and build the profile."""
    profile = build_math_density_profile(paragraph, page_context)
    route = classify_route(profile)
    profile["route_candidate"] = route
    return profile, route


def should_translate(route):
    return route in (NORMAL, MATH_PROTECTED, MATH_DENSE)


def should_protect(route):
    return route in (MATH_PROTECTED, MATH_DENSE)


def is_non_translatable(route):
    return route == NON_TRANSLATABLE_MATH


# --------------------------------------------------------------------------
# Prompts (math-dense + strict fallback)
# --------------------------------------------------------------------------
MATH_DENSE_PROMPT = (
    "You translate technical paper text from English into Simplified Chinese.\n"
    "Translate only the natural-language prose into Chinese.\n"
    "Do not translate, modify, reorder, delete, merge, or recreate any "
    "placeholder or mathematical token.\n"
    "All tokens of the forms {{FORMULA_*}}, {{MATH_TOKEN_*}}, {{CODE_*}} "
    "must be preserved exactly once and byte-for-byte at their original "
    "relative position.\n"
    "Do not solve equations.\n"
    "Do not rewrite equations in LaTeX.\n"
    "Do not explain mathematical notation.\n"
    "Preserve technical abbreviations, proper nouns, model names, dataset "
    "names and numbers as-is.\n"
    "Return only the translated paragraph, plain text, no Markdown."
)

MATH_STRICT_PROMPT = (
    "Strict math-preservation translation from English into Simplified "
    "Chinese.\n"
    "Rules (all mandatory):\n"
    "1. Translate ONLY the natural-language prose; never touch any "
    "{{FORMULA_*}}, {{MATH_TOKEN_*}} or {{CODE_*}} placeholder.\n"
    "2. Every placeholder token must appear EXACTLY once, byte-for-byte, "
    "in the same order as the source.\n"
    "3. Do NOT add, drop, reorder, merge or rename any placeholder.\n"
    "4. Do NOT solve, rewrite, or explain any equation or notation.\n"
    "5. Numbers, units, Greek letters, variables, function names, loss "
    "names and model names stay verbatim.\n"
    "Return ONLY the translated paragraph, plain text."
)


def _route_to_label(route):
    return {
        NORMAL: "normal_prose",
        MATH_PROTECTED: "math_protected_prose",
        MATH_DENSE: "math_dense_prose",
        NON_TRANSLATABLE_MATH: "non_translatable_math",
    }[route]


def plan_translation(paragraph, page_context=None):
    """Produce a translation plan for a paragraph (no API call).

    Returns {route, route_label, profile, template, action}.
    """
    profile, route = route_paragraph(paragraph, page_context)
    template = None
    action = "translate_plain"
    if route == NON_TRANSLATABLE_MATH:
        action = "skip_math"
    elif route in (MATH_PROTECTED, MATH_DENSE):
        template = build_protected_template(
            paragraph.get("translation_source_text")
            or paragraph.get("source_text") or "")
        action = "protect_and_translate"
    return {
        "route": route,
        "route_label": _route_to_label(route),
        "profile": profile,
        "template": template,
        "action": action,
    }


def translate_math_dense(source_text, translator_fn, *, max_passes=2):
    """Translate a math-dense paragraph with protection + validation + retry.

    ``translator_fn``: callable(prompt, protected_text) -> translated text.

    Returns a full per-paragraph evidence record:
      {source, template, translated_template, restored_result,
       validation, retry_trace, final_status}.
    """
    template = build_protected_template(source_text)
    retry_trace = []
    final_status = "translated"

    for attempt in range(1, max_passes + 1):
        prompt = MATH_DENSE_PROMPT if attempt == 1 else MATH_STRICT_PROMPT
        translated_template = translator_fn(prompt, template["protected_text"])
        restored = restore_template(translated_template, template["mapping"])
        validation = validate_translation(source_text, template, restored,
                                          translated_template)
        retry_trace.append({
            "pass": attempt,
            "prompt": "math_dense" if attempt == 1 else "math_strict",
            "model_output": translated_template,
            "validation": validation,
        })
        if validation["pass"]:
            final_status = "translated"
            break
        final_status = "rejected" if attempt == max_passes else "retrying"

    return {
        "source": source_text,
        "protected_template": template["protected_text"],
        "mapping": template["mapping"],
        "translated_template": (retry_trace[-1]["model_output"]
                                if retry_trace else ""),
        "restored_result": (restore_template(
            retry_trace[-1]["model_output"], template["mapping"])
            if retry_trace else ""),
        "validation": (retry_trace[-1]["validation"] if retry_trace else {}),
        "retry_trace": retry_trace,
        "final_status": final_status,
        "restored": (restore_template(
            retry_trace[-1]["model_output"], template["mapping"])
            if retry_trace and retry_trace[-1]["validation"]["pass"]
            else ""),
    }
