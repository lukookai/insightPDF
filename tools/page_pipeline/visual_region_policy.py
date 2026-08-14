"""VisualRegionPolicy -- fixed-canvas region classification (visual-v01).

On the visual route every page object is classified into one of:

  hard_anchor        display formula / table / figure / image:
                     x/y/w/h locked to source geometry, never displaced
                     by soft-text growth.
  protected_inline   inline formula: atomic object that lives *inside* its
                     owning soft-text region (ownership unique, cardinality
                     correct).  Not an independent layout object.
  soft_text          body / heading / list / abstract / author / affiliation
                     prose: may reflow ONLY inside its source-derived region.
  reserved_bottom    footnote reserved region (bottom band) + footnote text.
  reserved_caption   figure / table caption: bound to its anchor, may only
                     adapt inside its own source region.
  front_matter       title / author / affiliation / abstract blocks rendered
                     from FrontMatterModel (existing structured blocks).

Document-general: classification is driven purely by region payload /
semantic role / placement / ownership evidence -- never by file name,
page number or paper title.
"""

from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------- roles ---
# semantic_role / style_role hints that mark a soft text as a caption
CAPTION_ROLE_HINTS = (
    "figure_caption", "table_caption", "caption",
    "figure_title", "table_title",
)
FOOTNOTE_ROLE_HINTS = ("footnote", "footnote_text")
FRONTMATTER_ROLE_HINTS = ("title", "subtitle", "author", "affiliation",
                          "abstract", "abstract_body", "doc_title",
                          "front_matter")
HEADING_ROLE_HINTS = ("heading", "subheading", "section", "subsection",
                      "subsubsection", "paragraph_title", "heading_1",
                      "heading_2", "heading_3")

# region classification labels (stable API)
HARD_ANCHOR = "hard_anchor"
PROTECTED_INLINE = "protected_inline"
SOFT_TEXT = "soft_text"
RESERVED_BOTTOM = "reserved_bottom"
RESERVED_CAPTION = "reserved_caption"
FRONT_MATTER = "front_matter"

_LABELS = (HARD_ANCHOR, PROTECTED_INLINE, SOFT_TEXT,
           RESERVED_BOTTOM, RESERVED_CAPTION, FRONT_MATTER)


def _role_text(region) -> str:
    payload = region.get("payload") or {}
    return str(payload.get("semantic_role")
               or payload.get("style_role") or "").lower()


def _hint(role: str, hints) -> bool:
    r = role.lower()
    return any(h in r for h in hints)


class VisualRegionPolicy:
    """Per-page region classification for the fixed-canvas visual route."""

    def __init__(self, page_model: Dict[str, Any],
                 grid: Dict[str, Any] | None = None,
                 frontmatter: Dict[str, Any] | None = None,
                 bottom_reserved_regions: List[Dict[str, Any]] | None = None):
        self.page_model = page_model
        self.grid = grid or {}
        self.frontmatter = frontmatter or {}
        self.bottom_reserved = bottom_reserved_regions or []
        # front-matter paragraphs are matched by the SAME band logic the
        # renderer uses (y-band + x constraint), so the visual route's
        # classification never diverges from what build_unified_html will
        # actually render as a structured block.
        self._fm_para_ids = set()
        if self.frontmatter:
            try:
                from html_render import _assign_frontmatter_paras
                para_regions = [r for r in page_model.get("regions", [])
                                if r.get("type") == "text"]
                roles = _assign_frontmatter_paras(self.frontmatter,
                                                  para_regions)
                self._fm_para_ids = set(roles.keys())
            except Exception:  # noqa: BLE001
                self._fm_para_ids = set()
        # owner paragraphs of the bottom reserved (footnote) region
        self._footnote_owner_ids = {
            pid for region in self.bottom_reserved
            for pid in region.get("owner_paragraph_ids", [])}
        self._classifications = self._classify_all()

    # ------------------------------------------------------------- public
    def classify_region(self, region: Dict[str, Any]) -> Dict[str, Any]:
        rtype = region.get("type")
        region_id = region.get("region_id", "")
        payload = region.get("payload") or {}
        role = _role_text(region)
        evidence = {"region_type": rtype, "semantic_role": role}

        if rtype == "formula":
            if payload.get("placement") == "inline":
                return {"label": PROTECTED_INLINE,
                        "evidence": {**evidence, "placement": "inline"}}
            return {"label": HARD_ANCHOR,
                    "evidence": {**evidence, "placement": "display",
                                 "reason": "display formula geometry locked"}}
        if rtype in ("table", "figure", "image"):
            return {"label": HARD_ANCHOR,
                    "evidence": {**evidence,
                                 "reason": "table/figure/image geometry locked"}}
        if rtype != "text":
            return {"label": HARD_ANCHOR,
                    "evidence": {**evidence, "reason": "unknown region treated as locked"}}

        # --- text -----------------------------------------------------
        pid = payload.get("paragraph_id", "")
        if pid in self._footnote_owner_ids or _hint(role, FOOTNOTE_ROLE_HINTS):
            return {"label": RESERVED_BOTTOM,
                    "evidence": {**evidence,
                                 "reason": "footnote / bottom reserved owner"}}
        if pid in self._fm_para_ids or _hint(role, FRONTMATTER_ROLE_HINTS):
            return {"label": FRONT_MATTER,
                    "evidence": {**evidence,
                                 "reason": "front-matter structured block"}}
        if _hint(role, CAPTION_ROLE_HINTS):
            return {"label": RESERVED_CAPTION,
                    "evidence": {**evidence,
                                 "reason": "caption bound to hard anchor"}}
        # geometric caption detection (document-general): a short text block
        # sitting immediately below a table/figure anchor with horizontal
        # overlap is that anchor's caption even when the semantic role is
        # generic.
        bbox = region.get("bbox") or []
        if len(bbox) == 4:
            for a in self.page_model.get("regions", []):
                if a.get("type") not in ("table", "figure", "image"):
                    continue
                ab = a.get("bbox") or []
                if len(ab) != 4:
                    continue
                dist = bbox[1] - ab[3]
                if not (0.0 <= dist <= 30.0):
                    continue
                overlap_w = min(bbox[2], ab[2]) - max(bbox[0], ab[0])
                if overlap_w > 0.5 * max(bbox[2] - bbox[0], 1.0) \
                        and (bbox[3] - bbox[1]) < 40.0:
                    return {"label": RESERVED_CAPTION,
                            "evidence": {**evidence,
                                         "reason": "geometric caption below "
                                                   "hard anchor"}}
        return {"label": SOFT_TEXT,
                "evidence": {**evidence,
                             "reason": "prose: reflow inside region only"}}

    def _classify_all(self) -> Dict[str, Dict[str, Any]]:
        out = {}
        for region in self.page_model.get("regions", []):
            rid = region.get("region_id")
            if not rid:
                continue
            out[rid] = self.classify_region(region)
        return out

    def classification(self, region_id: str) -> Dict[str, Any]:
        return self._classifications.get(region_id, {"label": HARD_ANCHOR,
                                                     "evidence": {}})

    def label_of(self, region_id: str) -> str:
        return self.classification(region_id)["label"]

    def regions_of_label(self, label: str) -> List[Dict[str, Any]]:
        return [r for r in self.page_model.get("regions", [])
                if self.label_of(r.get("region_id")) == label]

    def all_classifications(self) -> Dict[str, Dict[str, Any]]:
        """{region_id: {label, evidence}} for the whole page."""
        return dict(self._classifications)

    # ---------------------------------------------------------- summary
    def summary(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {lab: 0 for lab in _LABELS}
        for cls in self._classifications.values():
            counts[cls["label"]] += 1
        hard_anchor_ids = [r.get("region_id")
                           for r in self.page_model.get("regions", [])
                           if self.label_of(r.get("region_id")) == HARD_ANCHOR]
        soft_ids = [r.get("region_id")
                    for r in self.page_model.get("regions", [])
                    if self.label_of(r.get("region_id")) == SOFT_TEXT]
        return {
            "schema_version": "visual_v01.visual_region_policy.v1",
            "counts": counts,
            "hard_anchor_region_ids": hard_anchor_ids,
            "soft_text_region_ids": soft_ids,
            "protected_inline_count": counts[PROTECTED_INLINE],
            "classifications": self.all_classifications(),
        }


def build_visual_region_policy(page_model, grid=None, frontmatter=None,
                               bottom_reserved_regions=None):
    return VisualRegionPolicy(page_model, grid=grid,
                              frontmatter=frontmatter,
                              bottom_reserved_regions=bottom_reserved_regions)


def summarize_page(page_model, grid=None, frontmatter=None,
                   bottom_reserved_regions=None) -> Dict[str, Any]:
    policy = build_visual_region_policy(
        page_model, grid=grid, frontmatter=frontmatter,
        bottom_reserved_regions=bottom_reserved_regions)
    return policy.summary()
