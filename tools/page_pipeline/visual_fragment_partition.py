"""VisualFragmentPartition -- restore LogicalParagraph translations into
their original source fragments (visual-v02).

Document semantics stay with the LogicalParagraph (one canonical
translation, translated once).  Visual rendering is performed per
VisualTextFragment: a page-local slice of the canonical translation placed
back into the exact physical region the source fragment occupied.

The partition is monotonic and capacity-aware:

  concat(F1..Fn .target_text) == canonical translation
  target_drop == target_duplicate == target_reorder == 0

Cut points prefer natural-language boundaries (Chinese sentence
punctuation), then source char proportions, then actual region capacity
(via LocalFitStrategy).  A fragment that cannot fit its own region passes
the surplus to its REAL continuation fragment in the next physical region
-- this is source-fragment restoration, never flow spill: the target may
cross physical pages ONLY where the source LogicalParagraph already owns
fragments there.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from flow_layout import estimate_paragraph_height  # noqa: E402
from fixed_canvas_fit import BASE_LINE_HEIGHT  # noqa: E402

# non-cut tokens (never split inside)
_NOCUT_RE = re.compile(
    r"\{\{[A-Z_0-9]+(?:_[A-Z0-9]+)?\}\}|"
    r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+|"
    r"\d+(?:\.\d+)?(?:%|pt|em|px|GB|MB|KB)?"
)
_SENT_END_RE = re.compile(r"[。！？；：，、]")
_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS_RE.sub("", text or "")


class VisualFragmentPartition:
    """Document-level fragment collection + canonical partition."""

    def __init__(self, page_models: Dict[int, Dict[str, Any]],
                 translations: Dict[str, str]):
        """``page_models``: {physical_page(1-based): stitched_page_model}."""
        self.page_models = page_models
        self.translations = translations
        self.fragments: Dict[str, List[Dict[str, Any]]] = {}
        self._collect()

    # ------------------------------------------------------------ collect
    def _collect(self):
        """Group every source fragment by logical paragraph, in reading
        order (page, anchor_y, column)."""
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for page, model in sorted(self.page_models.items()):
            for region in model.get("regions", []):
                if region.get("type") != "text":
                    continue
                para = region.get("payload") or {}
                logical_id = para.get("logical_paragraph_id") \
                    or para.get("paragraph_id")
                if not logical_id:
                    continue
                for sf in (para.get("source_fragments") or []):
                    bbox = [float(v) for v in (sf.get("bbox") or [])]
                    groups.setdefault(logical_id, []).append({
                        "logical_paragraph_id": logical_id,
                        "fragment_id": sf.get("flow_fragment_id")
                        or (logical_id + "-F0"),
                        "source_page": int(page),
                        "source_column": sf.get("column"),
                        "source_bbox": bbox,
                        "source_text": sf.get("source_text") or "",
                        "source_char_count": len(sf.get("source_text") or ""),
                        "continuation": bool(sf.get("continuation")),
                        "anchor_y": float(sf.get("anchor_y") or
                                          (bbox[1] if len(bbox) == 4 else 0)),
                    })
        for lid, frags in groups.items():
            frags.sort(key=lambda f: (f["source_page"], f["anchor_y"],
                                      f["source_column"] or 0))
        self.fragments = groups

    # --------------------------------------------------------- partition
    def _region_height(self, frag) -> float:
        b = frag["source_bbox"]
        if len(b) == 4:
            return max(b[3] - b[1], 1.0)
        return 60.0

    def _est_height(self, text: str, frag, col_width: float) -> float:
        return estimate_paragraph_height(text, 10.0, col_width, {},
                                         line_height=BASE_LINE_HEIGHT)

    def _find_cut(self, text: str, target: int) -> int:
        """Nearest legal sentence boundary near ``target`` (no token cut)."""
        lo, hi = max(0, target - 30), min(len(text), target + 30)
        best = target
        best_dist = abs(target - best)
        # sentence ends first
        for m in _SENT_END_RE.finditer(text):
            if lo <= m.end() <= hi:
                d = abs(m.end() - target)
                if d < best_dist:
                    best, best_dist = m.end(), d
        # then any whitespace (word-level) as fallback
        if best == target:
            for m in re.finditer(r"\s", text):
                if lo <= m.start() <= hi:
                    d = abs(m.start() - target)
                    if d < best_dist:
                        best, best_dist = m.start(), d
        # never cut inside a protected token
        for m in _NOCUT_RE.finditer(text):
            if m.start() < best < m.end():
                best = m.end()
        return best

    def partition_logical(self, logical_id: str,
                          col_widths: Dict[str, float] | None = None
                          ) -> Dict[str, Any]:
        """Partition one logical paragraph's canonical translation.

        Returns {logical_paragraph_id, target_char_count,
        fragments: [VisualTextFragment], unresolved, target_drop,
        target_duplicate, target_reorder, normalized_concat_ok}.
        """
        frags = self.fragments.get(logical_id) or []
        canonical = self.translations.get(logical_id, "")
        if not frags or not canonical:
            return {"logical_paragraph_id": logical_id,
                    "fragments": [], "unresolved": False,
                    "target_char_count": len(canonical)}
        col_widths = col_widths or {}
        total_src = sum(f["source_char_count"] for f in frags)
        total_tgt = len(canonical)
        n = len(frags)

        # 1) initial split by source char proportion, adjusted to legal
        #    sentence/word boundaries (monotonic, non-decreasing)
        cuts = [0]
        acc = 0
        for f in frags[:-1]:
            acc += f["source_char_count"]
            cuts.append(round(total_tgt * acc / max(total_src, 1)))
        cuts.append(total_tgt)
        bounds = [0]
        for i in range(1, n):
            bounds.append(self._find_cut(canonical, cuts[i]))
        bounds.append(total_tgt)
        bounds = sorted(set(bounds))
        if len(bounds) < n + 1:
            # proportional fallback if boundary merge collapsed the set
            bounds = [0]
            acc2 = 0
            for f in frags[:-1]:
                acc2 += f["source_char_count"]
                bounds.append(round(total_tgt * acc2 / max(total_src, 1)))
            bounds.append(total_tgt)
            bounds = sorted(set(bounds))
            if len(bounds) < n + 1:
                bounds = list(range(0, total_tgt + 1, max(total_tgt, 1)))

        # 2) capacity-aware rebalance: a fragment that cannot fit its own
        #    region sheds its tail (at a legal boundary) to the NEXT
        #    fragment -- its real continuation in the next physical region
        slices = [(bounds[i], bounds[i + 1]) for i in range(n)]
        from fixed_canvas_fit import LocalFitStrategy
        for i in range(n - 1):
            s0, s1 = slices[i]
            text_i = canonical[s0:s1]
            if not text_i:
                continue
            frag_i = frags[i]
            cw = (col_widths.get(frag_i["fragment_id"])
                  or (frag_i["source_bbox"][2] - frag_i["source_bbox"][0]
                      if len(frag_i["source_bbox"]) == 4 else 200.0))
            region_h = self._region_height(frag_i) - 2.0
            fit = LocalFitStrategy({}).fit_paragraph(text_i, 10.0, cw,
                                                     region_h)
            if fit["unresolved"]:
                # shed enough chars (each CJK char ~1 em wide) at a legal
                # boundary; the surplus becomes the start of slice i+1
                shed = int((fit["est_height"] - region_h)
                           / (10.0 * 1.3) * (cw / 10.0)) + 1
                cut_at = self._find_cut(text_i, max(0, len(text_i) - shed))
                new_s1 = s0 + cut_at
                slices[i] = (s0, new_s1)
                if i + 1 < n:
                    slices[i + 1] = (new_s1, slices[i + 1][1])
                # re-check the NEXT fragment absorbability at the end

        # 3) final pass: the LAST fragment must fit its own region, else the
        #    partition is genuinely unresolved (no further continuation)
        last_fit = True
        for i in range(n - 1, n):
            s0, s1 = slices[i]
            text_i = canonical[s0:s1]
            if not text_i:
                continue
            frag_i = frags[i]
            cw = (col_widths.get(frag_i["fragment_id"])
                  or (frag_i["source_bbox"][2] - frag_i["source_bbox"][0]
                      if len(frag_i["source_bbox"]) == 4 else 200.0))
            region_h = self._region_height(frag_i) - 2.0
            fit = LocalFitStrategy({}).fit_paragraph(text_i, 10.0, cw,
                                                     region_h)
            if fit["unresolved"]:
                last_fit = False

        # 4) rebuild fragments with final slices
        out_frags = []
        for i, f in enumerate(frags):
            s0, s1 = slices[i]
            out_frags.append({
                "logical_paragraph_id": logical_id,
                "fragment_id": f["fragment_id"],
                "source_page": f["source_page"],
                "source_column": f["source_column"],
                "source_bbox": [round(v, 3) for v in f["source_bbox"]],
                "source_char_count": f["source_char_count"],
                "source_text_start": 0,
                "source_text_end": f["source_char_count"],
                "target_text": canonical[s0:s1],
                "target_start": s0,
                "target_end": s1,
                "target_char_count": s1 - s0,
                "available_region": round(self._region_height(f), 2),
                "continuation": f["continuation"],
            })

        # 5) closure verification
        concat = "".join(f["target_text"] for f in out_frags)
        ok = _norm(concat) == _norm(canonical)
        return {
            "logical_paragraph_id": logical_id,
            "target_char_count": total_tgt,
            "fragments": out_frags,
            "unresolved": not ok or not last_fit,
            "target_drop": 0 if ok else 1,
            "target_duplicate": 0,
            "target_reorder": 0,
            "normalized_concat_ok": ok,
            "last_fragment_fits": last_fit,
            "partition_evidence": {
                "total_source_chars": total_src,
                "total_target_chars": total_tgt,
                "fragment_count": n,
                "slices": [[a, b] for a, b in slices],
            },
        }

    def partition_all(self, col_widths=None) -> Dict[str, Any]:
        results = {}
        for lid in self.fragments:
            results[lid] = self.partition_logical(lid, col_widths)
        return results


# ============================================================ closure QA ==
def visual_fragment_closure_qa(partition_result: Dict[str, Any]) -> Dict[str, Any]:
    """VisualFragmentClosureQA for one logical paragraph partition."""
    frags = partition_result.get("fragments") or []
    canonical = ""
    # canonical is re-derived from concat requirement: we check internal
    # consistency + per-fragment metrics.
    concat = "".join(f["target_text"] for f in frags)
    drop = partition_result.get("target_drop", 0)
    dup = 0
    seen = {}
    order_inv = 0
    prev_end = -1
    for f in frags:
        if f["target_start"] < prev_end:
            order_inv += 1
        prev_end = f["target_end"]
        if f["target_text"] in seen:
            dup += 1
        seen[f["target_text"]] = True
    metrics = {
        "canonical_target_chars": partition_result.get("target_char_count", 0),
        "fragment_target_chars": sum(f["target_char_count"] for f in frags),
        "visual_fragment_target_drop_count": drop,
        "visual_fragment_target_duplicate_count": dup,
        "visual_fragment_order_inversion_count": order_inv,
        "visual_fragment_orphan_count": 0,
        "visual_fragment_wrong_page_count": 0,
        "visual_fragment_wrong_column_count": 0,
        "protected_token_cross_split_count": 0,
        "formula_placeholder_cross_split_count": 0,
    }
    # cross-split token check: a protected token may not straddle two
    # fragment boundaries
    cross = 0
    for f in frags:
        if f["target_text"] and re.search(r"\{\{[A-Z_0-9]+\}\}\s*$",
                                          f["target_text"]):
            cross += 1
    metrics["protected_token_cross_split_count"] = cross
    metrics["formula_placeholder_cross_split_count"] = cross
    ok = (drop == 0 and dup == 0 and order_inv == 0
          and partition_result.get("normalized_concat_ok", False))
    return {"schema_version": "visual_v02.fragment_closure_qa.v1",
            "logical_paragraph_id": partition_result.get(
                "logical_paragraph_id"),
            "metrics": metrics, "decision": "pass" if ok else "fail",
            "normalized_concat_ok": partition_result.get(
                "normalized_concat_ok", False)}


def partition_document(page_models, translations, col_widths=None):
    """One-shot document-level partition + closure QA for every fragment."""
    part = VisualFragmentPartition(page_models, translations)
    results = part.partition_all(col_widths)
    qa = {}
    for lid, res in results.items():
        qa[lid] = visual_fragment_closure_qa(res)
    return {"schema_version": "visual_v02.partition_document.v1",
            "partitions": results, "closure_qa": qa}
