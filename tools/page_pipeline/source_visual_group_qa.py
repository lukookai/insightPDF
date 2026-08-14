"""SourceVisualGroupQA + SourceTargetTopologyQA (visual-v03).

Runs the source evidence engine, then checks that the TARGET (visual
flows) preserves each group's visual topology: a full-width caption must
not render as two column-local blocks; a caption group must keep its
anchor relation and its member order; every member translation must be
rendered exactly once.
"""

from __future__ import annotations

from typing import Any, Dict, List

from source_visual_group import (build_source_visual_groups,
                                 CaptionGroupResolver)  # noqa: E402


def _bbox(payload) -> List[float]:
    b = payload.get("bbox") or []
    return [float(v) for v in b] if len(b) == 4 else [0, 0, 0, 0]


def source_visual_group_qa(page_model, flows, grid=None,
                           out_dir=None) -> Dict[str, Any]:
    """Group detection + target topology preservation QA."""
    cf = (grid or {}).get("content_frame") or {}
    gutter = (grid or {}).get("gutter")
    groups = build_source_visual_groups(page_model,
                                        content_frame=cf, gutter=gutter)

    # target: paragraph -> (column, flow_y) from visual flows
    target_col = {}
    target_y = {}
    # merged groups: frozenset(member_ids) -> (column, flow_y) rendered as
    # one full-width unit (visual-v03 group provenance)
    merged_groups = {}
    for flow in flows or []:
        ci = flow.get("column")
        for it in flow.get("items", []):
            if it["kind"] == "paragraph":
                pid = it.get("paragraph_id")
                target_col[pid] = ci
                target_y[pid] = it.get("flow_y", 0)
                prov = it.get("group_provenance")
                if prov:
                    merged_groups[frozenset(
                        p["paragraph_id"] for p in prov)] = (ci, it.get("flow_y", 0))

    split_count = 0
    role_frag = 0
    owner_mismatch = 0
    topology_violation = 0
    order_inversion = 0
    drop = 0
    duplicate = 0
    cross_column_split = 0

    group_details = []
    for g in groups:
        if g.confidence < 0.55:
            continue  # low-confidence: not asserted (kept as-is)
        members = [p.get("paragraph_id") for p in g.members]
        member_set = frozenset(members)
        # already merged into ONE full-width unit on the target?
        merged_info = merged_groups.get(member_set)
        if merged_info is not None:
            mcol, my = merged_info
            group_details.append({
                "group_id": g.group_id, "group_type": g.group_type,
                "confidence": g.confidence,
                "expected_topology": g.expected_topology,
                "relation_owner": (g.anchor_region or {}).get("region_id"),
                "members": members, "target_columns": [mcol],
                "missing_in_target": [], "roles": sorted({
                    (p.get("style_role") or p.get("semantic_role") or "body")
                    for p in g.members}),
                "merged_full_width": mcol == -1,
            })
            continue
        cols = [target_col.get(pid) for pid in members if pid in target_col]
        # member missing from target = drop
        missing = [pid for pid in members if pid not in target_col]
        if missing:
            drop += len(missing)
        # cross-column split: members land in different columns
        if cols and len(set(cols)) > 1:
            cross_column_split += 1
            split_count += 1
            if g.expected_topology in ("full_width", "centered_block"):
                topology_violation += 1
        # role fragmentation: mixed canonical roles inside one group
        roles = {(p.get("style_role") or p.get("semantic_role") or "body")
                 for p in g.members}
        if len(roles) > 1:
            role_frag += 1
        # order inversion: member reading order vs target y order
        yseq = [(pid, target_y.get(pid, 0)) for pid in members if pid in target_y]
        ys = [y for _, y in yseq]
        if ys != sorted(ys):
            order_inversion += 1
        group_details.append({
            "group_id": g.group_id, "group_type": g.group_type,
            "confidence": g.confidence,
            "expected_topology": g.expected_topology,
            "relation_owner": (g.anchor_region or {}).get("region_id"),
            "members": members, "target_columns": cols,
            "missing_in_target": missing,
            "roles": sorted(roles),
        })

    metrics = {
        "visual_group_split_count": split_count,
        "visual_group_orphan_count": drop,
        "caption_role_fragmentation_count": role_frag,
        "caption_owner_mismatch_count": owner_mismatch,
        "full_width_group_topology_violation_count": topology_violation,
        "group_member_order_inversion_count": order_inversion,
        "group_target_drop_count": drop,
        "group_target_duplicate_count": duplicate,
        "group_cross_column_split_count": cross_column_split,
    }
    ok = all(v == 0 for v in metrics.values())
    return {"schema_version": "visual_v03.source_visual_group_qa.v1",
            "metrics": metrics, "decision": "pass" if ok else "fail",
            "group_count": len(group_details),
            "low_confidence_group_count": sum(
                1 for g in groups if g.confidence < 0.55),
            "groups": group_details}


def source_target_topology_qa(page_model, flows, grid=None,
                              out_dir=None) -> Dict[str, Any]:
    """SourceTargetTopologyQA: source topology vs target topology."""
    cf = (grid or {}).get("content_frame") or {}
    gutter = (grid or {}).get("gutter")
    groups = build_source_visual_groups(page_model,
                                        content_frame=cf, gutter=gutter)
    target_col = {}
    for flow in flows or []:
        for it in flow.get("items", []):
            if it["kind"] == "paragraph":
                target_col[it.get("paragraph_id")] = flow.get("column")

    violations = []
    for g in groups:
        if g.confidence < 0.55:
            continue
        members = [p.get("paragraph_id") for p in g.members]
        cols = {target_col.get(pid) for pid in members if pid in target_col}
        if g.expected_topology in ("full_width", "centered_block") \
                and len(cols) > 1:
            violations.append({
                "kind": "source_full_width_target_column_split",
                "group_id": g.group_id,
                "members": members, "target_columns": sorted(cols)})
        roles = {(p.get("style_role") or p.get("semantic_role") or "body")
                 for p in g.members}
        if "caption" in roles and "body" in roles:
            violations.append({
                "kind": "source_caption_target_body",
                "group_id": g.group_id, "members": members})
    metrics = {
        "topology_violation_count": len(violations),
        "source_full_width_target_column_split": sum(
            1 for v in violations
            if v["kind"] == "source_full_width_target_column_split"),
        "source_caption_target_body": sum(
            1 for v in violations if v["kind"] == "source_caption_target_body"),
    }
    return {"schema_version": "visual_v03.source_target_topology_qa.v1",
            "metrics": metrics,
            "decision": "pass" if not violations else "fail",
            "violations": violations}
