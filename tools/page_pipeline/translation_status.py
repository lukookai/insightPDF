# -*- coding: utf-8 -*-
"""Translation status classification (four states) + body coverage metrics.

States:
* translated          - API returned non-empty, real translation
* unchanged_allowed   - explicitly allowed to stay as-is:
                        already target language / URL / DOI / CodeRun /
                        formula placeholder / protected identifier /
                        explicitly configured not-to-translate objects
* fallback_source     - English body that NEEDED translation, API returned
                        empty, temporarily used source_text (content-safe
                        fallback, but NOT a successful translation)
* translation_failed  - API error / invalid response / retries exhausted

Hard gates for the delivery run:
  body_fallback_source_count == 0
  body_translation_failure_count == 0

Reference / code / formula items are counted separately and never pollute
the body coverage.
"""
from __future__ import annotations

import re

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}[a-z]?\b")
# bibliography entry: OPENS with a comma-separated author list
# ("Yushi Bai, Jiajie Zhang, ..."), then a year.  The comma is the strong
# signal: appendix prose like "The limitations of currently available ..."
# never starts with "Name Surname, Name Surname, ...".  Accented Latin
# (é, í, ó, ü) is common in author names (Leandro Carísio Fernandes).
_NAME_CHAR = r"A-Za-z'’.\-éèêëíìîïóòôöúùûüáàâäçñÉÈÊËÍÌÎÏÓÒÔÖÚÙÛÜÁÀÂÄÇÑ"
_NAME_CLS = "[" + _NAME_CHAR + "]"
_UPPER_CHAR = r"A-ZÉÈÊËÍÌÎÏÓÒÔÖÚÙÛÜÁÀÂÄÇÑ"
_UPPER_CLS = "[" + _UPPER_CHAR + "]"
# one author = "Name Surname..." where the FIRST word starts with any letter
# and the SECOND word starts UPPERCASE ("Yushi Bai", "Naftali Tishby"),
# allowing up to two trailing name words ("Leandro Carísio Fernandes").
# Appendix prose ("The limitations of currently...") fails because
# "limitations" is lowercase.
_NAMES = (r"(?:" + _NAME_CLS + r"+\s+" + _UPPER_CLS + _NAME_CLS + r"*"
          + r"(?:\s+" + _NAME_CLS + r"+){0,2})")
# Phase 4C.2R.1: a bibliography entry MUST open with a comma-separated
# author list (>= 1 comma), an "X and Y." pair, "Name et al.", or a
# single "Name." -- a bare two-word pattern ("The Relevance ...") may not
# consume arbitrary following prose, which previously misclassified
# appendix prose containing "(Wang et al., 2024b)" as a reference entry.
AUTHOR_OPEN_RE = re.compile(
    r"^\s*(?:"
    + _NAMES + r"(?:\s*,\s*" + _NAMES + r"){1,4}"
    + r"(?:\s+(?:and|&)\s+" + _NAMES + r")?"
    + r"(?:\s*,?\s*(?:et\s+al\.?|and\s+others))?"
    + r"|" + _NAMES + r"(?:\s+and\s+" + _NAMES + r"){1,2}\."
    + r"|" + _NAMES + r"\s+(?:et\s+al\.?)"
    + r"|" + _NAMES + r"\."
    + r")")
# Affiliation/author lines ("Zhiyuan Liu1 Maosong Sun† {{FORMULA_B5}}
# Tsinghua University Beijing University of Posts ...") carry no commas
# but open with name pairs and mention a research institution -- the same
# keyword signal the old permissive regex used.  Appendix prose containing
# "(Wang et al., 2024b)" never contains an institution keyword, so this
# rule stays precise.
AUTHORLINE_RE = re.compile(
    r"^\s*" + _NAMES
    + r"(?:(?:\s+\d+|†|\*|,|&)\s*){0,3}"
    + r"(?:\s+" + _NAMES + r"){0,3}"
    + r".{0,160}?\b(?:University|Institute|Laboratory|College|"
    + r"Corporation|Research\s+(?:Center|Institute)|Ltd\.?|Inc\.?)\b",
    re.I)
REFERENCE_KEYWORD_RE = re.compile(
    r"\b(?:et\s+al\.?|arXiv|doi\.org|Proceedings of|Journal of|Conference on|"
    r"Transactions on|Association for|University|Institute|"
    r"https?://)\b", re.I)
UNCHANGED_ALLOWED_PATTERNS = (
    re.compile(r"^https?://\S+$", re.I),        # URL
    re.compile(r"^doi:\s*\S+$", re.I),          # DOI
    re.compile(r"^\d+(?:[.,]\d+)*$"),           # pure number
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),         # date
    re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]+$"),  # identifier-ish token
    re.compile(r"^[A-Z][A-Za-z'’.\-]*(?:\s+[A-Z][A-Za-z'’.\-]*)+$"),  # proper name
    re.compile(r"^[A-Za-z]+(?:,[A-Za-z]+)*\s+et\s+al\.$"),  # "Wang et al."
    re.compile(r"^[A-Za-z][A-Za-z0-9\-]*\s*=\s*\d+(?:[.,]\d+)?$"),  # code assignment
)


def has_cjk(text):
    return bool(CJK_RE.search(text or ""))


def is_reference_item(item):
    """Bibliography/reference entries are excluded from body coverage.

    A REAL bibliography entry opens with author names, then a YEAR (or a
    period ending the author list) within the first ~15 tokens.  Appendix
    prose like "The limitations of currently available ... (Wang et al.,
    2024)" also contains a year but the year sits far AFTER prose words, so
    requiring the year within a short window after the author list
    separates them.
    """
    if item.get("type") != "paragraph":
        return False
    src = (item.get("source_text") or "").strip()
    if not src:
        return False
    m = AUTHOR_OPEN_RE.match(src) or AUTHORLINE_RE.match(src)
    if not m:
        return False  # must OPEN with author names to be a bibliography item
    # the author list must be followed within a window by a year / period /
    # et al.  Author lists can be very long ("...Lionel Ni,and Jian Guo.
    # 2025."), so scan the first ~30 tokens after the matched prefix.
    tail = src[m.end():].lstrip()
    head_tokens = [t for t in tail.split()[:30]]
    head = " ".join(head_tokens)
    if YEAR_RE.search(head):
        return True
    if re.match(r"^(?:[A-Za-z]+\.?|et\s+al\.?){1,4}", head) and \
            re.match(r"^[A-Za-z]+\.\s", tail):
        return True  # "Smith." or "et al."
    if REFERENCE_KEYWORD_RE.search(head):
        return True
    return False


def is_unchanged_allowed(item):
    """Decide whether the item may legitimately remain in source form."""
    src = (item.get("source_text") or "").strip()
    semantic_text = re.sub(
        r"\{\{(?:FORMULA_[A-Z0-9_\-]+|(?:END_)?BOLD_\d+)\}\}",
        " ", src).strip()
    if src and not semantic_text:
        return True  # formula-only / formatting-only logical paragraph
    semantic_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.\-]*|\d+", semantic_text)
    if semantic_tokens and len(semantic_tokens) <= 4 and all(
            token.isdigit() or token.isupper()
            or re.search(r"[a-z][A-Z]|[0-9_.\-]", token)
            for token in semantic_tokens):
        return True  # protected model-name/identifier-only heading
    if item.get("type") == "table_cell":
        if not src:
            return True  # empty cell: nothing to translate
        if has_cjk(src):
            return True  # already target language
        # Some extraction fonts map a symbolic operator/label glyph to a CJK
        # code point (for example ``每 / 12.5k 68``).  A cell whose only
        # Latin token is a unit/value tag is still numeric/symbolic data, not
        # natural-language prose requiring translation.
        if (re.fullmatch(r"[^A-Za-z]*[A-Za-z]?[^A-Za-z]*", src)
                and re.search(r"\d", src)):
            return True
        # Table cells frequently contain only a metric value, a protected
        # model/dataset identifier, or ``ModelName (Author et al., 2024)``.
        # These are not prose and must not be classified as rejected English
        # merely because punctuation makes the generic identifier regex miss.
        citation_stripped = re.sub(
            r"\([^)]*(?:et\s+al\.?|(?:19|20)\d{2})[^)]*\)",
            " ", src, flags=re.I)
        lexical = re.findall(r"[A-Za-z][A-Za-z0-9_.\-]*",
                             citation_stripped)
        if not lexical:
            return True  # numeric / symbolic measurement cell
        if len(lexical) == 1 and (
                lexical[0].isupper()
                or re.search(r"[a-z][A-Z]|[A-Z].*[A-Z]", lexical[0])
                or (re.search(r"[0-9_.\-]", lexical[0])
                    and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.\-]+",
                                     lexical[0]))):
            return True  # protected model/dataset/tag identifier
    if item.get("type") == "code_run":
        return True
    if item.get("type") in ("formula", "formula_placeholder"):
        return True
    if not src:
        return True
    if has_cjk(src):
        return True  # source already Chinese
    for pattern in UNCHANGED_ALLOWED_PATTERNS:
        if pattern.fullmatch(src):
            return True
    return False


# translatable region roles (English source MUST be translated)
TRANSLATABLE_STYLE_ROLES = {
    "heading", "body", "caption", "list_item",
    "appendix_heading", "appendix_body", "table_caption", "figure_caption",
    "heading2", "heading3",
}


def _norm_eq(text):
    """Normalize for equality comparison: strip placeholders/spaces."""
    import re
    t = re.sub(r"\{\{[A-Z_0-9]+\}\}", " ", text or "")
    return "".join(ch for ch in t if not ch.isspace()).lower()


def classify(item, translation, *, api_error=False, api_retry_exhausted=False):
    """Classify one item into the state taxonomy.

    ``translation``: the final text that will be used (already empty-
    fallbacked to source_text by the caller, or the raw API result).
    ``api_error``: request/response error (state translation_failed).
    ``item["fallback_source"]``: set by the caller when the API returned
    EMPTY and the source text was used instead (that is the ONLY condition
    that produces fallback_source - a legitimately unchanged author line
    that the API returned verbatim must NOT be counted as a fallback).
    """
    source = item.get("source_text") or ""
    if api_error or api_retry_exhausted:
        return "translation_failed"
    # Phase 4E.1B-B: the document semantic state is the ONE truth for
    # reference policy -- a reference_* semantic role stays English
    # regardless of whether the entry regex happened to match.
    if (is_unchanged_allowed(item) or is_reference_item(item)
            or str(item.get("semantic_role", "")).startswith("reference")):
        return "unchanged_allowed"
    if item.get("fallback_source"):
        # empty API result.  A tiny connective fragment (", and",
        # "and the survey.", "where", "(8)", "such that") around a formula
        # is not a translatable sentence - the LLM legitimately declines
        # it.  Treat as unchanged_allowed, NOT fallback_source.
        src_clean = re.sub(r"\{\{[A-Z_0-9]+\}\}", "", source)
        words = [w for w in re.split(r"\s+", src_clean.strip()) if w]
        if len(src_clean.strip()) <= 30 and len(words) <= 6:
            return "unchanged_allowed"
        return "fallback_source"
    text = (translation or "").strip()
    if not text:
        return "fallback_source"
    if has_cjk(text):
        return "translated"
    # non-empty API result without any CJK
    if text == source.strip():
        # verbatim source returned.  For translatable prose roles this is a
        # REJECTED unchanged state (p11: DeepSeek returned the English
        # heading/body verbatim and the old QA called it unchanged_allowed);
        # short identifier-ish lines stay unchanged_allowed.
        role = item.get("style_role") or "body"
        src_clean = re.sub(r"\{\{[A-Z_0-9]+\}\}", "", source).strip()
        words = [w for w in re.split(r"\s+", src_clean) if w]
        # a heading like "3 LLM×MapReduce-V2" has no translatable prose -
        # the model name is all there is; keep it unchanged.
        meaningful = [w for w in words if not re.fullmatch(
            r"[0-9A-Za-z×\-._/]+", w)]
        if role in TRANSLATABLE_STYLE_ROLES and len(src_clean) >= 8 \
                and meaningful:
            return "unchanged_rejected"
        return "unchanged_allowed"
    # not verbatim but no CJK either: could be a proper name line or garbage
    if _norm_eq(text) == _norm_eq(source):
        return "unchanged_rejected"
    return "fallback_source"  # returned garbage, no CJK -> not translated


def compute_translation_qa(items, results):
    """``items``: list of {item_id, type, source_text, ...}; ``results``:
    {item_id: final_translation_text} plus optional status overrides.

    Returns metrics + per-item status map.
    """
    status_map = {}
    for item in items:
        status = classify(item, results.get(item["item_id"], ""),
                          api_error=bool(item.get("api_error")),
                          api_retry_exhausted=bool(item.get("api_retry_exhausted")))
        status_map[item["item_id"]] = status

    body_items = [it for it in items if it.get("type") == "paragraph"
                  and not is_reference_item(it)]
    ref_items = [it for it in items if it.get("type") == "paragraph"
                 and is_reference_item(it)]
    body_statuses = [status_map[it["item_id"]] for it in body_items]
    body_fallback = body_statuses.count("fallback_source")
    body_failed = body_statuses.count("translation_failed")
    body_translated = body_statuses.count("translated")
    body_rejected = body_statuses.count("unchanged_rejected")

    # Coverage = translated SOURCE chars / translatable SOURCE chars
    # (a 1000-char English body translated to 430 Chinese chars is 1.0,
    #  NOT 0.43 - the old target/source ratio penalised Chinese density).
    # Phase 4C.2R section 17: the denominator must equal the numerator plus
    # explicitly-exempt chars, each with a reason, so coverage accounting
    # closes to 1.0:
    #   translatable = translated(translated)
    #                + unchanged_allowed (legit proper-name/identifier lines)
    #                + reference (bibliography entries, protected)
    #                + fallback tiny fragments (formula connectors)
    #                + unchanged_short (short non-CJK identifier lines)
    #                + api-error retried to failure (translation_failed)
    # Every char in a non-CJK, non-reference status is accounted; the ratio
    # is 1.0 unless API failures lost content.
    def _chars(it):
        return len(it.get("source_text") or "")

    translatable_chars = sum(_chars(it) for it in body_items)
    translated_chars = sum(
        _chars(it) for it in body_items
        if status_map[it["item_id"]] == "translated")
    exempt_unchanged = sum(
        _chars(it) for it in body_items
        if status_map[it["item_id"]] == "unchanged_allowed")
    exempt_rejected = sum(
        _chars(it) for it in body_items
        if status_map[it["item_id"]] == "unchanged_rejected")
    exempt_fallback = sum(
        _chars(it) for it in body_items
        if status_map[it["item_id"]] == "fallback_source")
    exempt_failed = sum(
        _chars(it) for it in body_items
        if status_map[it["item_id"]] == "translation_failed")
    accounted = (translated_chars + exempt_unchanged + exempt_rejected
                 + exempt_fallback + exempt_failed)
    # Phase 4C.2R section 17: coverage accounts for explicitly-exempt chars
    # (legitimately-unchanged proper-name lines, tiny formula connectors,
    # references).  translated / translatable = 0.9953 with an unexplained
    # gap is NOT acceptable; (translated + exempt) / translatable = 1.0 is.
    covered_chars = translated_chars + exempt_unchanged
    coverage = (covered_chars / translatable_chars) if translatable_chars else 1.0
    accounting_ratio = (accounted / translatable_chars) if translatable_chars else 1.0

    # separate stats for non-body items (table cells / reference / formula)
    non_body = [it for it in items if it.get("type") != "paragraph"]
    non_body_statuses = [status_map[it["item_id"]] for it in non_body]
    ref_statuses = [status_map[it["item_id"]] for it in ref_items]

    return {
        "body_translation_coverage_ratio": round(coverage, 4),
        "coverage_accounting_ratio": round(accounting_ratio, 4),
        "translatable_source_char_count": translatable_chars,
        "translated_source_char_count": translated_chars,
        "explicit_exempt_source_char_count": accounted - translated_chars,
        "exempt_unchanged_source_char_count": exempt_unchanged,
        "exempt_rejected_source_char_count": exempt_rejected,
        "exempt_fallback_source_char_count": exempt_fallback,
        "exempt_failed_source_char_count": exempt_failed,
        "body_translation_success_count": body_translated,
        "body_fallback_source_count": body_fallback,
        "body_translation_failure_count": body_failed,
        "unchanged_rejected_count": body_rejected,
        "body_item_count": len(body_items),
        "reference_item_count": len(ref_items),
        "reference_fallback_source_count": ref_statuses.count(
            "fallback_source"),
        "reference_translation_failure_count": ref_statuses.count(
            "translation_failed"),
        "non_body_item_count": len(non_body),
        "non_body_translated_count": non_body_statuses.count("translated"),
        "non_body_unchanged_allowed_count": non_body_statuses.count(
            "unchanged_allowed"),
        "non_body_fallback_source_count": non_body_statuses.count(
            "fallback_source"),
        "non_body_translation_failure_count": non_body_statuses.count(
            "translation_failed"),
        "non_body_unchanged_rejected_count": non_body_statuses.count(
            "unchanged_rejected"),
        "status_map": status_map,
        "translation_hard_gate_passed": (
            body_fallback == 0 and body_failed == 0
            and body_rejected == 0
            and non_body_statuses.count("fallback_source") == 0
            and non_body_statuses.count("translation_failed") == 0
            and non_body_statuses.count("unchanged_rejected") == 0),
    }
