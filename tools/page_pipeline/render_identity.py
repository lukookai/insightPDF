"""Shared DOM identity and typography state for renderable soft text."""
from __future__ import annotations

from html import escape
from typing import Any


def soft_text_render_identity(
    paragraph_id: str,
    flow_item: dict[str, Any] | None,
    *,
    semantic_role: str,
    identity_suffix: str = "",
) -> dict[str, str]:
    """Resolve one stable identity without depending on DOM block class."""
    flow = flow_item or {}
    recovered = flow.get("_para") or {}
    base_render_id = str(recovered.get("paragraph_id") or paragraph_id)
    base_fragment = str(
        flow.get("flow_fragment_id") or f"{paragraph_id}-F0")
    suffix = f":{identity_suffix}" if identity_suffix else ""
    return {
        "render_id": base_render_id + suffix,
        "flow_fragment_id": base_fragment + suffix,
        "render_source": str(
            flow.get("render_source") or "canonical_target"),
        "render_source_reason": str(
            flow.get("render_source_reason") or "normal_translation"),
        "role": str(semantic_role or "body"),
    }


def render_identity_attributes(identity: dict[str, str]) -> str:
    """Serialize the required RenderIdentity attributes for HTML."""
    return (
        'data-render-id="%s" data-flow-fragment="%s" '
        'data-render-source="%s" data-render-source-reason="%s" '
        'data-role="%s"'
        % tuple(escape(str(identity[key]), quote=True) for key in (
            "render_id",
            "flow_fragment_id",
            "render_source",
            "render_source_reason",
            "role",
        ))
    )


def specialized_typography_state(
    flow_item: dict[str, Any] | None,
    *,
    default_font_size: float,
    default_line_height: float,
) -> dict[str, Any]:
    """Resolve Fit/Fill output for a non-paragraph specialized element.

    Geometry is deliberately absent from this state.  The same frozen flow
    fields used by ordinary paragraphs provide font size, line height,
    content offset and source-relative first-line indentation.
    """
    flow = flow_item or {}
    font_size = float(default_font_size)
    line_height = float(default_line_height)
    if flow.get("geometry_locked"):
        font_size = float(flow.get("source_slot_font_size") or font_size)
        line_height = float(
            flow.get("source_slot_line_height") or line_height)

    font_size *= (
        float(flow.get("local_typography_font_scale") or 1.0)
        * float(flow.get("local_typography_fill_font_scale") or 1.0))
    line_height *= (
        float(flow.get("local_typography_line_height_scale") or 1.0)
        * float(flow.get("local_typography_fill_line_height_scale") or 1.0))

    indent_em = 0.0
    segments = list(flow.get("source_paragraph_segments") or [])
    if len(segments) == 1 and segments[0].get("source_style"):
        indent_em = float(
            segments[0]["source_style"].get("first_line_indent_em") or 0.0)

    return {
        "font_size": font_size,
        "line_height": line_height,
        "text_indent_em": indent_em,
        "content_top_offset": float(
            flow.get("source_slot_content_top_offset") or 0.0),
        "local_fit_level": str(
            flow.get("local_typography_fit_level") or "L0"),
        "local_fill_level": str(
            flow.get("local_typography_fill_level") or "F0"),
    }


def specialized_typography_attributes(state: dict[str, Any]) -> str:
    return (
        'data-local-fit-level="%s" data-local-fill-level="%s" '
        'data-slot-content-top-offset="%.3f"'
        % (
            escape(str(state["local_fit_level"]), quote=True),
            escape(str(state["local_fill_level"]), quote=True),
            float(state["content_top_offset"]),
        )
    )


def specialized_typography_style(state: dict[str, Any]) -> str:
    return (
        "font-size:%.3fpt;line-height:%.3fpt;text-indent:%.3fem;"
        % (
            float(state["font_size"]),
            float(state["line_height"]),
            float(state["text_indent_em"]),
        )
    )


__all__ = [
    "render_identity_attributes",
    "soft_text_render_identity",
    "specialized_typography_attributes",
    "specialized_typography_state",
    "specialized_typography_style",
]
