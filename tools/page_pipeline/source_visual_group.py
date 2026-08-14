"""SourceVisualGroup -- source visual grouping (visual-v03).

Detects, from SOURCE geometry/typography/anchor evidence only (no OCR, no
string hardcoding), visual units that belong together on the source page:

    figure_caption / table_caption / full_width_heading / author_group /
    affiliation_group / front_matter_group / full_width_note

A SourceVisualGroup may contain several LogicalParagraphs / spans that the
document paragraph segmentation split apart (e.g. a full-width caption
"Figure 6: Screenshot of ..." parsed as DLP A (left column) + DLP B (right
column)).  The group restores the source visual topology for rendering
without changing document semantics or re-translating.

Grouping evidence (all source-side, relative geometry):
  A. geometry: shared baseline, horizontal continuity, y-band, reading order
  B. typography: font family/size/style similarity
  C. anchor relation: caption adjacent to figure/table (above/below),
     horizontal-center alignment, width relation
  D. source raster: optional visual-line occupancy / centered relation

Confidence is evidence-weighted; low-confidence pairs stay separate.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

_FONT_FAMILY_KEYS = ("font", "font_name")
_TOL_BASELINE_PT = 2.0
_TOL_GAP_EM = 0.6           # horizontal gap <= 0.6 * font_size
_TOL_ANCHOR_DIST_PT = 30.0  # caption adjacent to its anchor
_TOL_FONT_SIZE_REL = 0.08   # relative font-size tolerance


def _span_font(para) -> tuple:
    for line in (para.get("lines") or []):
        for s in line.get("spans", []):
            return (s.get("font") or "", s.get("size") or 0.0)
    return ("", 0.0)


def _para_font_profile(para) -> Dict[str, Any]:
    f, size = _span_font(para)
    return {"family": (f or "").lower(), "size": float(size or 0)}


def _same_typography(a, b) -> bool:
    pa, pb = _para_font_profile(a), _para_font_profile(b)
    if not pa["family"] or not pb["family"]:
        return True
    same_family = pa["family"] == pb["family"]
    size_ok = abs(pa["size"] - pb["size"]) <= _TOL_FONT_SIZE_REL \
        * max(pa["size"], pb["size"], 1.0)
    return same_family and size_ok


def _bbox(payload) -> List[float]:
    b = payload.get("bbox") or []
    return [float(v) for v in b] if len(b) == 4 else [0, 0, 0, 0]


class SourceVisualGroup:
    """One source visual unit (may span several paragraphs/columns)."""

    def __init__(self, group_id, group_type, page, members,
                 anchor_region=None, expected_topology=None):
        self.group_id = group_id
        self.group_type = group_type
        self.page = page
        self.members = members            # [para payload, ...]
        self.anchor_region = anchor_region  # {region_id, bbox, type} or None
        self.expected_topology = expected_topology
        # derive union bbox + dominant role + confidence
        boxes = [_bbox(p) for p in members]
        self.source_union_bbox = [
            min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]
        roles = {}
        for p in members:
            r = p.get("style_role") or p.get("semantic_role") or "body"
            roles[r] = roles.get(r, 0) + 1
        self.dominant_role = max(roles, key=roles.get)
        self.member_paragraph_ids = [
            p.get("paragraph_id") for p in members]
        self.grouping_evidence = {}
        self.confidence = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group_id": self.group_id,
            "group_type": self.group_type,
            "page": self.page,
            "source_union_bbox": [round(v, 2) for v in self.source_union_bbox],
            "member_paragraph_ids": self.member_paragraph_ids,
            "dominant_role": self.dominant_role,
            "expected_topology": self.expected_topology,
            "relation_owner_id": (self.anchor_region or {}).get("region_id"),
            "grouping_evidence": self.grouping_evidence,
            "confidence": round(self.confidence, 3),
        }


class CaptionGroupResolver:
    """Find figure/table caption groups from source evidence."""

    def __init__(self, page_model, content_frame=None, gutter=None):
        self.page_model = page_model
        self.cf = content_frame or {}
        self.gutter = gutter
        self.cf_w = max(float((self.cf.get("x1") or 0)
                              - (self.cf.get("x0") or 0)), 1.0)

    # ------------------------------------------------------------ public
    def resolve(self) -> List[SourceVisualGroup]:
        anchors = self._anchors()
        paragraphs = [r["payload"] for r in self.page_model.get("regions", [])
                      if r.get("type") == "text"]
        groups: List[SourceVisualGroup] = []
        for ai, anchor in enumerate(anchors):
            near = self._nearby_caption_paras(anchor, paragraphs)
            if not near:
                continue
            # cluster members on the SAME visual line (shared baseline +
            # horizontal continuity + same typography)
            clusters = self._cluster_same_line(near)
            for ci, cluster in enumerate(clusters):
                gid = "G%03d-%s-%d-%d" % (self.page_model.get("page", 0),
                                          anchor["type"], ai, ci)
                g = SourceVisualGroup(
                    gid, "%s_caption" % anchor["type"], 
                    self.page_model.get("page", 0), cluster,
                    anchor_region=anchor,
                    expected_topology=self._topology_for(cluster, anchor))
                g.grouping_evidence = self._evidence(cluster, anchor)
                g.confidence = self._confidence(cluster, anchor,
                                                g.grouping_evidence)
                groups.append(g)
        return groups

    # ------------------------------------------------------------ helpers
    def _anchors(self) -> List[Dict[str, Any]]:
        out = []
        for r in self.page_model.get("regions", []):
            if r.get("type") in ("figure", "table"):
                out.append({"region_id": r.get("region_id"),
                            "type": r.get("type"),
                            "bbox": [float(v) for v in r.get("bbox", [])]})
        return out

    def _nearby_caption_paras(self, anchor, paragraphs):
        ab = anchor["bbox"]
        near = []
        for p in paragraphs:
            pb = _bbox(p)
            # adjacency: paragraph just below or above the anchor
            below = 0.0 <= (pb[1] - ab[3]) <= _TOL_ANCHOR_DIST_PT
            above = 0.0 <= (ab[1] - pb[3]) <= _TOL_ANCHOR_DIST_PT
            if not (below or above):
                continue
            # horizontal overlap with the anchor (>= 40% of paragraph width)
            ox = min(pb[2], ab[2]) - max(pb[0], ab[0])
            if ox > 0.4 * max(pb[2] - pb[0], 1.0):
                near.append(p)
        return near

    def _cluster_same_line(self, paras):
        """Cluster paragraphs sharing a visual line (baseline + continuity
        + typography)."""
        ps = sorted(paras, key=lambda p: _bbox(p)[0])
        clusters = []
        for p in ps:
            pb = _bbox(p)
            placed = False
            for c in clusters:
                cb = _bbox(c[-1])
                same_baseline = abs(pb[1] - cb[1]) <= _TOL_BASELINE_PT
                gap_ok = (pb[0] - cb[2]) <= _TOL_GAP_EM * max(
                    _span_font(p)[1] or 10.0, 10.0)
                if same_baseline and gap_ok and _same_typography(c[-1], p):
                    c.append(p)
                    placed = True
                    break
            if not placed:
                clusters.append([p])
        return clusters

    def _topology_for(self, cluster, anchor):
        ub = [min(_bbox(p)[0] for p in cluster),
              min(_bbox(p)[1] for p in cluster),
              max(_bbox(p)[2] for p in cluster),
              max(_bbox(p)[3] for p in cluster)]
        w = ub[2] - ub[0]
        # caption groups that straddle the gutter are FULL-WIDTH even when
        # the union is shorter than the frame (they span both columns on
        # one visual line, e.g. "Figure 6: Screenshot of ...")
        if self.gutter:
            gx0, gx1 = self.gutter["x0"], self.gutter["x1"]
            if ub[0] < gx0 and ub[2] > gx1:
                return "full_width"
            cx = (ub[0] + ub[2]) / 2.0
            if (gx0 - 8.0 <= cx <= gx1 + 8.0):
                return "centered_block"
        if self.cf_w and w > 0.75 * self.cf_w:
            return "full_width"
        return "anchored_to_%s" % anchor["type"]

    def _evidence(self, cluster, anchor):
        boxes = [_bbox(p) for p in cluster]
        ub = [min(b[0] for b in boxes), min(b[1] for b in boxes),
              max(b[2] for b in boxes), max(b[3] for b in boxes)]
        same_baseline = max(b[1] for b in boxes) - min(b[1] for b in boxes) \
            <= _TOL_BASELINE_PT
        gaps = []
        ps = sorted(cluster, key=lambda p: _bbox(p)[0])
        for i in range(1, len(ps)):
            gaps.append(_bbox(ps[i])[0] - _bbox(ps[i - 1])[2])
        typo_ok = all(_same_typography(ps[0], p) for p in ps[1:])
        ab = anchor["bbox"]
        below = 0.0 <= (ub[1] - ab[3]) <= _TOL_ANCHOR_DIST_PT
        above = 0.0 <= (ab[1] - ub[3]) <= _TOL_ANCHOR_DIST_PT
        adj = below or above
        overlap = (min(ub[2], ab[2]) - max(ub[0], ab[0])) / max(ub[2] - ub[0], 1.0)
        return {
            "same_baseline": same_baseline,
            "horizontal_gaps_pt": [round(g, 2) for g in gaps],
            "typography_similar": typo_ok,
            "anchor_adjacent": adj,
            "anchor_overlap_ratio": round(overlap, 2),
            "member_count": len(cluster),
        }

    def _confidence(self, cluster, anchor, ev):
        score = 0.0
        if ev["same_baseline"]:
            score += 0.35
        if ev["typography_similar"]:
            score += 0.2
        if ev["anchor_adjacent"]:
            score += 0.25
        if ev["anchor_overlap_ratio"] >= 0.4:
            score += 0.2
        if len(cluster) >= 2:
            score += 0.05  # multi-member group bonus
        return min(score, 1.0)


def build_source_visual_groups(page_model, content_frame=None,
                               gutter=None) -> List[SourceVisualGroup]:
    resolver = CaptionGroupResolver(page_model,
                                    content_frame=content_frame,
                                    gutter=gutter)
    return resolver.resolve()


def groups_to_dict(groups) -> List[Dict[str, Any]]:
    return [g.to_dict() for g in groups]
