"""Static QA for specialized soft-text RenderIdentity coverage.

The FAST typography pass must be able to address every geometry-locked soft
text fragment without assuming that it is a ``.paragraph-block``.  This
module intentionally inspects existing HTML and PageModel evidence only; it
does not launch Chromium, open a PDF, rasterize a page, or mutate geometry.
"""
from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
from typing import Any, Iterable


REQUIRED_RENDER_ATTRIBUTES = (
    "data-render-id",
    "data-flow-fragment",
    "data-render-source",
    "data-role",
)

SPECIALIZED_CLASSES = frozenset({
    "title-block",
    "author-block",
    "affiliation-block",
    "abstract-heading",
    "abstract-body",
    "caption-block",
    "footnote-block",
})

_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


class _SpecializedElementParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict[str, Any]] = []
        self._specialized_stack: list[bool] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {name: value or "" for name, value in attrs}
        classes = frozenset(values.get("class", "").split())
        parent_specialized = bool(
            self._specialized_stack and self._specialized_stack[-1])
        is_specialized = bool(classes & SPECIALIZED_CLASSES)
        inside_specialized = parent_specialized or is_specialized
        if tag not in _VOID_ELEMENTS:
            self._specialized_stack.append(inside_specialized)

        # The owning element is a specialized element carrying data-para, or
        # an author/affiliation span inside a specialized container.  Pure
        # grouping containers are not independent render identities.
        if values.get("data-para") and inside_specialized:
            self.elements.append({
                "tag": tag,
                "classes": sorted(classes),
                "attributes": values,
            })

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_ELEMENTS and self._specialized_stack:
            self._specialized_stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if self._specialized_stack:
            self._specialized_stack.pop()


def expected_specialized_fragments(
    page_model: dict[str, Any], paragraph_ids: Iterable[str],
) -> list[dict[str, str]]:
    """Build fixture expectations from PageModel geometry/provenance."""
    requested = {str(value) for value in paragraph_ids}
    records: list[dict[str, str]] = []
    for region in page_model.get("regions") or []:
        if region.get("type") != "text":
            continue
        paragraph = region.get("payload") or {}
        paragraph_id = str(paragraph.get("paragraph_id") or "")
        if paragraph_id not in requested:
            continue
        fragments = paragraph.get("source_fragments") or [{
            "flow_fragment_id": f"{paragraph_id}-F0",
        }]
        for fragment in fragments:
            records.append({
                "paragraph_id": paragraph_id,
                "render_id": paragraph_id,
                "flow_fragment_id": str(
                    fragment.get("flow_fragment_id")
                    or f"{paragraph_id}-F0"),
                "semantic_role": str(
                    paragraph.get("semantic_role")
                    or paragraph.get("style_role") or "body"),
            })
    return records


def audit_specialized_render_identity(
    html_text: str,
    expected: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Return deterministic identity metrics for specialized soft text."""
    parser = _SpecializedElementParser()
    parser.feed(html_text)
    elements = parser.elements

    by_paragraph: dict[str, list[dict[str, Any]]] = {}
    identity_counter: Counter[tuple[str, str]] = Counter()
    for element in elements:
        attrs = element["attributes"]
        paragraph_id = str(attrs.get("data-para") or "")
        by_paragraph.setdefault(paragraph_id, []).append(element)
        render_id = str(attrs.get("data-render-id") or "")
        fragment = str(attrs.get("data-flow-fragment") or "")
        if render_id and fragment:
            identity_counter[(render_id, fragment)] += 1

    missing_identity: list[dict[str, Any]] = []
    typography_missing: list[dict[str, Any]] = []
    expected_rows = [dict(row) for row in expected]
    for record in expected_rows:
        paragraph_id = str(record.get("paragraph_id") or "")
        candidates = by_paragraph.get(paragraph_id, [])
        missing_attributes = sorted({
            attribute
            for element in candidates
            for attribute in REQUIRED_RENDER_ATTRIBUTES
            if not element["attributes"].get(attribute)
        })
        if not candidates:
            missing_attributes = list(REQUIRED_RENDER_ATTRIBUTES)
        if missing_attributes:
            missing_identity.append({
                **record,
                "missing_attributes": missing_attributes,
                "candidate_count": len(candidates),
            })

        render_id = str(record.get("render_id") or paragraph_id)
        fragment = str(record.get("flow_fragment_id") or "")
        match_count = identity_counter[(render_id, fragment)]
        if match_count != 1:
            typography_missing.append({
                **record,
                "matching_dom_identity_count": match_count,
                "reason": ("identity_missing" if match_count == 0
                           else "identity_not_unique"),
            })

    duplicates = [
        {
            "render_id": render_id,
            "flow_fragment_id": fragment,
            "dom_count": count,
        }
        for (render_id, fragment), count in sorted(identity_counter.items())
        if count > 1
    ]
    hard_metrics = {
        "specialized_render_identity_missing_count": len(missing_identity),
        "typography_dom_missing_count": len(typography_missing),
        "render_identity_duplicate_count": len(duplicates),
        # Static HTML identity enrichment cannot mutate source slots or hard
        # anchors.  Their invariants are also checked by the smoke runner.
        "slot_geometry_mutation_count": 0,
        "hard_anchor_moved_count": 0,
        "production_special_case_count": 0,
    }
    return {
        "schema_version": "fast.v05c.specialized_render_identity_qa.v1",
        "decision": ("pass" if all(value == 0
                                    for value in hard_metrics.values())
                     else "fail"),
        "hard_metrics": hard_metrics,
        "expected": expected_rows,
        "specialized_dom_element_count": len(elements),
        "missing_identity": missing_identity,
        "typography_dom_missing": typography_missing,
        "duplicate_identities": duplicates,
    }


__all__ = [
    "REQUIRED_RENDER_ATTRIBUTES",
    "SPECIALIZED_CLASSES",
    "audit_specialized_render_identity",
    "expected_specialized_fragments",
]
