# -*- coding: utf-8 -*-
"""TranslationRoute (Phase 4E.1B-C3).

Classifies a paragraph into one of four translation routes from its
MathDensityProfile, by CONTENT STRUCTURE only (no page / paragraph id).

    NORMAL_PROSE            - ordinary prose, no math objects to protect
    MATH_PROTECTED_PROSE    - prose that embeds formulas / variables / model
                              names; protect math objects, translate prose
    MATH_DENSE_PROSE        - many math tokens but still natural-language
                              prose that MUST be translated
    NON_TRANSLATABLE_MATH   - nearly pure math / equation / equation-number;
                              never enters prose translation
"""
from __future__ import annotations

import re

from math_density import build_math_density_profile, is_pure_math

NORMAL = "NORMAL"
MATH_PROTECTED = "MATH_PROTECTED"
MATH_DENSE = "MATH_DENSE"
NON_TRANSLATABLE_MATH = "NON_TRANSLATABLE_MATH"

# semantic roles whose content is already target-language / not-to-translate
EXEMPT_ROLES = {
    "references", "reference", "acknowledgement", "appendix_reference",
}

# math density threshold: above this the prose is math-dense
MATH_DENSE_RATIO = 0.30
MATH_PROTECTED_MIN_RATIO = 0.0


def _has_math(profile):
    return (profile["formula_placeholder_count"] > 0
            or profile["math_symbol_count"] > 0
            or profile["relation_operator_count"] > 0
            or profile["latin_identifier_count"] > 0
            or profile["greek_count"] > 0
            or profile["protected_run_count"] > 0)


def classify_route(profile):
    """Classify a MathDensityProfile into one of the four routes."""
    role = (profile.get("semantic_role") or "").lower()
    prose = profile["prose_char_count"]
    math_ratio = profile["math_ratio"]
    has_math = _has_math(profile)

    # pure math / equation / equation-number with no prose -> not translated
    if prose == 0 and has_math:
        return NON_TRANSLATABLE_MATH
    # no math objects at all -> ordinary prose
    if not has_math:
        return NORMAL
    # math objects present but prose dominates -> protect + translate prose
    if math_ratio < MATH_DENSE_RATIO:
        return MATH_PROTECTED
    # math-heavy but still has prose -> math-dense prose (translate prose)
    return MATH_DENSE


def classify_paragraph(paragraph, page_context=None):
    """Build the profile and classify the paragraph."""
    profile = build_math_density_profile(paragraph, page_context)
    profile["route_candidate"] = classify_route(profile)
    return profile, profile["route_candidate"]
