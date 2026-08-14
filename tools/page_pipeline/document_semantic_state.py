# -*- coding: utf-8 -*-
"""DocumentSemanticState (Phase 4E.1B).

A document-level section state machine.  The reference / appendix /
acknowledgement roles are DOCUMENT state propagated along the reading order,
never a per-paragraph regex guess and never a page-number special case.

States:
    normal_body, references, appendix, acknowledgement

Entry / exit uses SOURCE structural evidence collected per page:
    * heading style_role + heading text lexicon ("References", "Bibliography",
      "参考文献", "Acknowledgments", "Appendix", ...);
    * reference-entry pattern density (author/year/title lines);
    * citation-like leading index density;
    * paragraph style transitions.

Once in ``references``, later pages INHERIT the state until a strong new
top-level section is observed or EOF -- the section heading need not repeat.
"""
from __future__ import annotations

import re

REFERENCE_HEADINGS = {"references", "bibliography", "参考文献", "参考文献列表",
                      "参考", "bibliographie"}
ACK_HEADINGS = {"acknowledgments", "acknowledgements", "acknowledgment",
                "acknowledgement", "致谢"}
APPENDIX_HEADINGS = {"appendix", "appendix a", "appendix b", "附录", "附錄"}

_REF_HEAD_RE = re.compile(r"^\s*(references|bibliography)\s*$", re.I)
_ACK_HEAD_RE = re.compile(r"^\s*acknowledg(?:e)?ments?\s*$", re.I)
_APP_HEAD_RE = re.compile(r"^\s*(appendix(?:\s+[A-Za-z])?|附录[Ａ-ＺA-Z]?)\s*$", re.I)
_APP_LETTERED_RE = re.compile(r"^[A-Z]\s+[A-Z]")  # "A Information Bottleneck..."
_SECTION_HEAD_RE = re.compile(r"^\d+(?:\.\d+)*\s+\S", re.I)


class PageEvidence:
    """Structural evidence observed on one source page."""

    def __init__(self, page_no, headings, paragraphs, style_roles,
                 ref_density, entry_ratio):
        self.page_no = page_no
        self.headings = headings          # list of (text, style_role, y_center)
        self.paragraphs = paragraphs      # list of source texts
        self.style_roles = style_roles    # list of style_role
        self.ref_density = ref_density    # is_reference_item fraction
        self.entry_ratio = entry_ratio    # citation-like leading index fraction


class DocumentSemanticState:
    ROLES = ("normal_body", "references", "appendix", "acknowledgement")

    def __init__(self):
        self.current_section_role = "normal_body"
        self.section_start_page = None
        self.section_start_region = None
        self.confidence = 0.0
        self.evidence = []
        self.inherited_from_previous_page = False
        self.termination_reason = None
        self._history = []

    # ---------------------------------------------------------------- entry --
    def _classify_heading(self, text):
        low = (text or "").strip().lower()
        if _REF_HEAD_RE.match(low):
            return "references"
        if _ACK_HEAD_RE.match(low):
            return "acknowledgement"
        if _APP_HEAD_RE.match(low):
            return "appendix"
        if _APP_LETTERED_RE.match(text.strip()):
            return "appendix"  # "A Information Bottleneck ..."
        return None

    def _strong_ref_density(self, ev: PageEvidence) -> bool:
        # >=60% of paragraphs look like bibliography entries -> strong signal
        return ev.ref_density >= 0.6

    def _strong_new_top_level(self, ev: PageEvidence) -> str | None:
        """A new top-level section that can terminate the current state."""
        for text, role, _y in ev.headings:
            if role in ("heading", "appendix_heading"):
                cls = self._classify_heading(text)
                if cls in ("references", "appendix", "acknowledgement"):
                    return cls
                # numbered top-level section (1. Intro) after references
                if _SECTION_HEAD_RE.match(text.strip()) and len(text) < 60:
                    return "normal_body"
        return None

    # --------------------------------------------------------------- advance --
    def observe_page(self, ev: PageEvidence):
        self.inherited_from_previous_page = False
        prior = self.current_section_role

        # process ALL headings on the page in reading order; a page can carry
        # several top-level section starts ("Acknowledgment" then "References")
        transitions = []
        for text, hrole, _y in ev.headings:
            if hrole not in ("heading", "appendix_heading"):
                continue
            cls = self._classify_heading(text)
            if cls and cls != self.current_section_role:
                transitions.append((text, cls))

        if not transitions and self.current_section_role == "normal_body" \
                and self._strong_ref_density(ev):
            transitions.append(("<density>", "references"))

        for text, cls in transitions:
            if cls != self.current_section_role:
                self.evidence.append({"page": ev.page_no, "type": "heading",
                                      "heading": text, "entered": cls,
                                      "weight": 1.0})
                self.current_section_role = cls
                self.section_start_page = ev.page_no
                self.confidence = 0.9
                self.termination_reason = None

        if not transitions:
            if self.current_section_role != "normal_body":
                # already in references/appendix/acknowledgement; a strong new
                # top-level numbered section on this page terminates it
                leave = self._strong_new_top_level(ev)
                if leave is not None:
                    self.termination_reason = ("new_top_level_%s" % leave
                                               if leave != "normal_body"
                                               else "new_numbered_section")
                    self.current_section_role = "normal_body"
                    self.inherited_from_previous_page = False
                    self.evidence.append({"page": ev.page_no,
                                          "type": "exit_evidence",
                                          "left": prior,
                                          "reason": self.termination_reason})
                else:
                    self.inherited_from_previous_page = True
                    self.evidence.append({"page": ev.page_no,
                                          "type": "inherited",
                                          "state": self.current_section_role})
            else:
                self.evidence.append({"page": ev.page_no, "type": "body",
                                      "state": "normal_body"})

        self._history.append({"page": ev.page_no, "role": self.current_section_role,
                              "inherited": self.inherited_from_previous_page})
        return self.current_section_role

    # -------------------------------------------------------------- snapshot --
    def snapshot(self):
        return {
            "current_section_role": self.current_section_role,
            "section_start_page": self.section_start_page,
            "section_start_region": self.section_start_region,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence[-6:],
            "inherited_from_previous_page": self.inherited_from_previous_page,
            "termination_reason": self.termination_reason,
        }


# ------------------------------------------------------------ integration --
_ENTRY_INDEX_RE = re.compile(r"^\s*(\[\d+\]|\d+\.)\s+")
_REF_HEAD_LEX = {"references", "bibliography", "参考文献", "参考文献列表"}


def _entry_like(src):
    if _ENTRY_INDEX_RE.match(src):
        return True
    from translation_status import is_reference_item
    return bool(is_reference_item({"type": "paragraph", "source_text": src or ""}))


def _assign_semantic_role(payload, page_state, ref_y, inherited, state):
    style_role = (payload.get("style_role") or "body").lower()
    src = (payload.get("source_text") or "").strip()
    bb = payload.get("bbox") or [0, 0, 0, 0]
    y = (float(bb[1]) + float(bb[3])) / 2

    if page_state == "references":
        if style_role in ("heading", "appendix_heading"):
            low = src.lower()
            if any(k in low for k in _REF_HEAD_LEX):
                return "reference_heading"
        if inherited or ref_y is None or y >= ref_y:
            if style_role in ("caption", "table_caption", "figure_caption"):
                return "caption"
            return "reference_item" if _entry_like(src) else "reference_continuation"
        # above the References heading on the entry page -> prior section
        if style_role in ("heading", "appendix_heading"):
            return "heading"
        return "acknowledgement" if _ACK_HEAD_RE.match(src) else "body"
    if page_state == "acknowledgement":
        return "acknowledgement"
    if page_state == "appendix":
        return "appendix"
    # normal body
    if style_role in ("heading", "appendix_heading"):
        return "heading"
    if style_role in ("caption", "table_caption", "figure_caption"):
        return "caption"
    return "body"


def apply_document_semantics(document):
    """Write ``semantic_role`` into every logical paragraph (document truth).

    Runs the section state machine over the document's pages in reading
    order, then assigns per-paragraph semantic_role.  Writes BOTH the
    per-page region payloads and the document["logical_paragraphs"] entries
    (keyed by logical_paragraph_id).  Returns the state machine.
    """
    state = DocumentSemanticState()
    role_by_lpid = {}
    for page in document.get("pages", []):
        pno = page.get("page")
        ev = PageEvidence(pno, [], [], [], 0.0, 0.0)
        # build evidence from this page's regions
        ev.headings = []
        ev.paragraphs = []
        ev.style_roles = []
        for r in page.get("regions", []):
            if r.get("type") != "text":
                continue
            p = r.get("payload") or {}
            src = (p.get("source_text") or "").strip()
            if not src:
                continue
            srole = (p.get("style_role") or "body").lower()
            bb = p.get("bbox") or [0, 0, 0, 0]
            y = (float(bb[1]) + float(bb[3])) / 2
            ev.paragraphs.append(src)
            ev.style_roles.append(srole)
            if srole in ("heading", "appendix_heading"):
                ev.headings.append((src, srole, y))
            if _entry_like(src):
                ev.ref_density += 1.0
            ev.ref_density /= max(len(ev.paragraphs), 1)
        role = state.observe_page(ev)
        ref_y = None
        if role == "references":
            for text, hrole, y in ev.headings:
                if state._classify_heading(text) == "references":
                    ref_y = y
                    break
        inherited = state.inherited_from_previous_page
        for r in page.get("regions", []):
            if r.get("type") != "text":
                continue
            p = r.get("payload") or {}
            srole = _assign_semantic_role(p, role, ref_y, inherited, state)
            p["semantic_role"] = srole
            lpid = p.get("logical_paragraph_id") or p.get("paragraph_id")
            if lpid:
                role_by_lpid[lpid] = srole
    for lp in document.get("logical_paragraphs", []):
        lpid = lp.get("logical_paragraph_id") or lp.get("paragraph_id")
        if lpid and lpid in role_by_lpid:
            lp["semantic_role"] = role_by_lpid[lpid]
    return state
