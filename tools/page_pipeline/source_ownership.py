# -*- coding: utf-8 -*-
"""Exclusive primary ownership for PDF source text spans.

Ownership is resolved before paragraph construction.  Figure membership is
relative to the source span's own geometry; absolute point distances are
retained as supporting evidence but never veto a Figure owner by themselves.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class PrimaryOwner(str, Enum):
    FIGURE = "FIGURE"
    TABLE = "TABLE"
    FORMULA = "FORMULA"
    TEXT = "TEXT"


@dataclass(frozen=True)
class FigureOwnershipPolicy:
    """Central policy shared by all documents and pages."""

    strong_inclusion_ratio: float = 0.95
    ambiguous_inclusion_ratio: float = 0.80
    ambiguous_score_threshold: float = 0.67
    peer_font_size_relative_delta: float = 0.18
    peer_baseline_distance_em: float = 0.55
    peer_spatial_gap_em: float = 8.0
    peer_reading_order_window: int = 12
    edge_escape_soft_limit_ratio: float = 0.25


def _bbox(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return []
    return [float(item) for item in value]


def _area(box: list[float]) -> float:
    if len(box) != 4:
        return 0.0
    return max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0)


def _intersection(first: list[float], second: list[float]) -> list[float]:
    if len(first) != 4 or len(second) != 4:
        return []
    result = [max(first[0], second[0]), max(first[1], second[1]),
              min(first[2], second[2]), min(first[3], second[3])]
    return result if result[2] > result[0] and result[3] > result[1] else []


def text_in_figure_ratio(text_bbox: Any, figure_bbox: Any) -> float:
    """Return intersection(text, figure) area divided by text area.

    This intentionally is not IoU: a small annotation inside a large Figure
    must retain a score of 1.0 rather than being diluted by Figure area.
    """
    text = _bbox(text_bbox)
    figure = _bbox(figure_bbox)
    return _area(_intersection(text, figure)) / max(_area(text), 1e-12)


def _center_inside(container: list[float], item: list[float]) -> bool:
    if len(container) != 4 or len(item) != 4:
        return False
    cx = (item[0] + item[2]) / 2.0
    cy = (item[1] + item[3]) / 2.0
    return container[0] <= cx <= container[2] \
        and container[1] <= cy <= container[3]


def _edge_escape(container: list[float], item: list[float]
                 ) -> tuple[dict[str, float], float, float]:
    sides = {
        "left": max(container[0] - item[0], 0.0),
        "top": max(container[1] - item[1], 0.0),
        "right": max(item[2] - container[2], 0.0),
        "bottom": max(item[3] - container[3], 0.0),
    }
    width = max(item[2] - item[0], 1e-12)
    height = max(item[3] - item[1], 1e-12)
    ratio = max(sides["left"] / width, sides["right"] / width,
                sides["top"] / height, sides["bottom"] / height)
    return sides, max(sides.values()), ratio


def _box_gap(first: list[float], second: list[float]) -> float:
    dx = max(first[0] - second[2], second[0] - first[2], 0.0)
    dy = max(first[1] - second[3], second[1] - first[3], 0.0)
    return (dx * dx + dy * dy) ** 0.5


def _font_key(span: dict[str, Any]) -> str:
    return str(span.get("font") or "").strip().lower()


class SourceOwnershipResolver:
    """Resolve exactly one :class:`PrimaryOwner` for every source span.

    Table and formula claims are accepted from their existing specialized
    models and keep their established precedence.  Only remaining spans are
    evaluated for Figure ownership, so this resolver does not broaden or
    refactor formula/table semantics.
    """

    def __init__(self, policy: FigureOwnershipPolicy | None = None):
        self.policy = policy or FigureOwnershipPolicy()

    @staticmethod
    def _figure_defs(figure_defs: list[dict[str, Any]]
                     ) -> list[dict[str, Any]]:
        output = []
        for index, raw in enumerate(figure_defs or []):
            box = _bbox(raw.get("bbox"))
            if not box or _area(box) <= 0.0:
                continue
            output.append({
                "figure_id": str(raw.get("figure_id")
                                 or raw.get("region_id")
                                 or "FIG%d" % (index + 1)),
                "bbox": box,
                "source_index": index,
            })
        return output

    def _base_figure_evidence(
            self, span: dict[str, Any], reading_order: int,
            figure: dict[str, Any]) -> dict[str, Any]:
        box = _bbox(span.get("bbox"))
        figure_box = figure["bbox"]
        ratio = text_in_figure_ratio(box, figure_box)
        sides, escape_pt, escape_ratio = _edge_escape(figure_box, box)
        return {
            "figure_id": figure["figure_id"],
            "figure_bbox": [round(value, 3) for value in figure_box],
            "text_in_figure_ratio": round(ratio, 6),
            "text_center_inside_figure": _center_inside(figure_box, box),
            "edge_escape_sides_pt": {
                key: round(value, 3) for key, value in sides.items()},
            "edge_escape_distance_pt": round(escape_pt, 3),
            "edge_escape_ratio": round(escape_ratio, 6),
            # Figure hard anchors crop the source page SVG at figure_bbox.
            "source_svg_crop_inclusion": round(ratio, 6),
            "font": span.get("font"),
            "font_size": round(float(span.get("size") or 0.0), 3),
            "source_reading_order": reading_order,
        }

    def _peer_evidence(
            self, span: dict[str, Any], reading_order: int,
            figure_id: str, strong_peers: list[tuple[int, dict[str, Any], str]]
            ) -> dict[str, Any]:
        policy = self.policy
        box = _bbox(span.get("bbox"))
        size = max(float(span.get("size") or 0.0), 1.0)
        candidates = []
        for peer_order, peer, peer_figure_id in strong_peers:
            if peer_figure_id != figure_id:
                continue
            if str(peer.get("id") or "") == str(span.get("id") or ""):
                continue
            peer_box = _bbox(peer.get("bbox"))
            peer_size = max(float(peer.get("size") or 0.0), 1.0)
            size_delta = abs(size - peer_size) / max(size, peer_size, 1.0)
            baseline_distance_em = abs(box[3] - peer_box[3]) / max(
                size, peer_size, 1.0)
            spatial_gap_em = _box_gap(box, peer_box) / max(
                size, peer_size, 1.0)
            reading_delta = abs(reading_order - peer_order)
            font_similar = bool(_font_key(span)
                                and _font_key(span) == _font_key(peer))
            size_similar = size_delta <= policy.peer_font_size_relative_delta
            baseline_similar = (
                baseline_distance_em <= policy.peer_baseline_distance_em)
            spatial_neighbor = spatial_gap_em <= policy.peer_spatial_gap_em
            reading_neighbor = (
                reading_delta <= policy.peer_reading_order_window)
            candidates.append({
                "span_id": str(peer.get("id") or ""),
                "font_similar": font_similar,
                "font_size_relative_delta": round(size_delta, 6),
                "font_size_similar": size_similar,
                "baseline_distance_em": round(baseline_distance_em, 6),
                "baseline_similar": baseline_similar,
                "spatial_gap_em": round(spatial_gap_em, 6),
                "spatial_neighbor": spatial_neighbor,
                "reading_order_delta": reading_delta,
                "reading_order_neighbor": reading_neighbor,
            })
        candidates.sort(key=lambda row: (
            not row["reading_order_neighbor"], row["reading_order_delta"],
            row["spatial_gap_em"]))
        best = candidates[0] if candidates else {}
        return {
            "neighboring_figure_owned_span_count": len(candidates),
            "peer_span_id": best.get("span_id"),
            "font_similarity": bool(best.get("font_similar")),
            "font_size_similarity": bool(best.get("font_size_similar")),
            "font_size_relative_delta": best.get(
                "font_size_relative_delta"),
            "baseline_similarity": bool(best.get("baseline_similar")),
            "baseline_distance_em": best.get("baseline_distance_em"),
            "neighbor_geometry_relation": bool(
                best.get("spatial_neighbor")),
            "neighbor_spatial_gap_em": best.get("spatial_gap_em"),
            "source_reading_order_relation": bool(
                best.get("reading_order_neighbor")),
            "neighbor_reading_order_delta": best.get(
                "reading_order_delta"),
        }

    def _ambiguous_score(self, evidence: dict[str, Any]) -> float:
        policy = self.policy
        ratio = float(evidence["text_in_figure_ratio"])
        band = max(policy.strong_inclusion_ratio
                   - policy.ambiguous_inclusion_ratio, 1e-12)
        inclusion_band_score = max(0.0, min(
            (ratio - policy.ambiguous_inclusion_ratio) / band, 1.0))
        escape_quality = max(0.0, 1.0 - (
            float(evidence["edge_escape_ratio"])
            / max(policy.edge_escape_soft_limit_ratio, 1e-12)))
        score = (
            0.40 * inclusion_band_score
            + 0.15 * float(bool(evidence["text_center_inside_figure"]))
            + 0.05 * ratio
            + 0.10 * escape_quality
            + 0.06 * float(bool(evidence["font_similarity"]))
            + 0.04 * float(bool(evidence["font_size_similarity"]))
            + 0.05 * float(bool(evidence["baseline_similarity"]))
            + 0.08 * float(bool(evidence["neighbor_geometry_relation"]))
            + 0.07 * float(bool(evidence["source_reading_order_relation"]))
        )
        return round(score, 6)

    def resolve(
            self, spans: list[dict[str, Any]],
            figure_defs: list[dict[str, Any]] | None = None,
            table_span_ids: set[str] | list[str] | None = None,
            formula_span_ids: set[str] | list[str] | None = None,
            ) -> dict[str, Any]:
        figures = self._figure_defs(figure_defs or [])
        table_ids = {str(value) for value in (table_span_ids or [])}
        formula_ids = {str(value) for value in (formula_span_ids or [])}
        indexed = [(index, span) for index, span in enumerate(spans)]
        candidates: dict[str, dict[str, Any]] = {}

        for reading_order, span in indexed:
            span_id = str(span.get("id") or "")
            if not span_id or span_id in table_ids or span_id in formula_ids:
                continue
            evidence = [self._base_figure_evidence(
                span, reading_order, figure) for figure in figures]
            if evidence:
                candidates[span_id] = max(
                    evidence,
                    key=lambda row: (row["text_in_figure_ratio"],
                                     row["text_center_inside_figure"]),
                )

        strong_peers = []
        for reading_order, span in indexed:
            evidence = candidates.get(str(span.get("id") or ""))
            if (evidence
                    and evidence["text_center_inside_figure"]
                    and evidence["text_in_figure_ratio"]
                    >= self.policy.strong_inclusion_ratio):
                strong_peers.append((reading_order, span,
                                     evidence["figure_id"]))

        primary_owner_by_span: dict[str, str] = {}
        figure_id_by_span: dict[str, str] = {}
        evidence_by_span: dict[str, dict[str, Any]] = {}
        claims_by_span: dict[str, list[str]] = {}
        for reading_order, span in indexed:
            span_id = str(span.get("id") or "")
            raw_claims = []
            if span_id in table_ids:
                raw_claims.append(PrimaryOwner.TABLE.value)
            if span_id in formula_ids:
                raw_claims.append(PrimaryOwner.FORMULA.value)
            evidence = candidates.get(span_id)
            ratio = float((evidence or {}).get(
                "text_in_figure_ratio") or 0.0)
            if evidence and ratio >= self.policy.ambiguous_inclusion_ratio:
                raw_claims.append(PrimaryOwner.FIGURE.value)
            raw_claims.append(PrimaryOwner.TEXT.value)
            claims_by_span[span_id] = raw_claims

            if span_id in table_ids:
                owner = PrimaryOwner.TABLE
                decision = "existing_table_model_claim"
            elif span_id in formula_ids:
                owner = PrimaryOwner.FORMULA
                decision = "existing_formula_model_claim"
            else:
                peer = self._peer_evidence(
                    span, reading_order,
                    str((evidence or {}).get("figure_id") or ""),
                    strong_peers)
                if evidence:
                    evidence.update(peer)
                if (evidence
                        and evidence["text_center_inside_figure"]
                        and ratio >= self.policy.strong_inclusion_ratio):
                    owner = PrimaryOwner.FIGURE
                    decision = "strong_relative_figure_inclusion"
                    score = 1.0
                elif (evidence
                      and evidence["text_center_inside_figure"]
                      and ratio >= self.policy.ambiguous_inclusion_ratio):
                    score = self._ambiguous_score(evidence)
                    if score >= self.policy.ambiguous_score_threshold:
                        owner = PrimaryOwner.FIGURE
                        decision = "ambiguous_relative_geometry_with_peers"
                    else:
                        owner = PrimaryOwner.TEXT
                        decision = "ambiguous_evidence_below_policy_score"
                else:
                    owner = PrimaryOwner.TEXT
                    decision = "relative_inclusion_prefers_text"
                    score = 0.0
                if evidence:
                    evidence["ambiguous_evidence_score"] = score
            primary_owner_by_span[span_id] = owner.value
            if owner == PrimaryOwner.FIGURE and evidence:
                figure_id_by_span[span_id] = str(evidence["figure_id"])
            evidence_by_span[span_id] = {
                "span_id": span_id,
                "primary_owner": owner.value,
                "decision": decision,
                "primary_owner_count": 1,
                "candidate_claims": raw_claims,
                "figure_ownership_evidence": evidence,
            }

        owner_sets = {
            owner.value: sorted(span_id for span_id, resolved in
                                primary_owner_by_span.items()
                                if resolved == owner.value)
            for owner in PrimaryOwner
        }
        multi_primary = sum(
            int(row.get("primary_owner_count") or 0) != 1
            for row in evidence_by_span.values())
        return {
            "schema_version": "visual_v07.source_ownership.v1",
            "policy": asdict(self.policy),
            "primary_owner_by_span": primary_owner_by_span,
            "owner_sets": owner_sets,
            "figure_id_by_span": figure_id_by_span,
            "claims_by_span": claims_by_span,
            "evidence_by_span": evidence_by_span,
            "metrics": {
                "source_span_count": len(spans),
                "source_span_multi_primary_owner_count": multi_primary,
                "source_span_unowned_count": sum(
                    span_id not in primary_owner_by_span
                    for span_id in [str(span.get("id") or "")
                                    for span in spans]),
            },
        }
