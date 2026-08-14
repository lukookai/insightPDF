# -*- coding: utf-8 -*-
"""Raster-backed QA for per-formula SVG assets.

An SVG file existing on disk is not evidence that it contains visible ink.
This module parses the declared geometry and independently rasterizes the
asset with MuPDF before reporting its ink envelope.  It deliberately has no
knowledge of a document name or page number so it can be used by production
and by stand-alone audits.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree

import pymupdf
from PIL import Image


INK_THRESHOLD = 235
DEFAULT_SCALE = 4.0


def _float(value, default=0.0):
    try:
        return float(re.sub(r"[^0-9eE+.-]", "", str(value)))
    except (TypeError, ValueError):
        return float(default)


def _ink_metrics(image: Image.Image, threshold=INK_THRESHOLD) -> dict:
    rgb = image.convert("RGB")
    pixels = rgb.load()
    xs, ys = [], []
    for y in range(rgb.height):
        for x in range(rgb.width):
            if min(pixels[x, y]) < threshold:
                xs.append(x)
                ys.append(y)
    if not xs:
        return {
            "ink_pixel_count": 0,
            "ink_bbox_px": None,
            "ink_area_px": 0,
            "ink_touches_edge": False,
            "ink_edge_sides": [],
        }
    bbox = [min(xs), min(ys), max(xs) + 1, max(ys) + 1]
    sides = []
    tolerance = 1
    if bbox[0] <= tolerance:
        sides.append("left")
    if bbox[1] <= tolerance:
        sides.append("top")
    if bbox[2] >= rgb.width - tolerance:
        sides.append("right")
    if bbox[3] >= rgb.height - tolerance:
        sides.append("bottom")
    return {
        "ink_pixel_count": len(xs),
        "ink_bbox_px": bbox,
        "ink_area_px": (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]),
        "ink_touches_edge": bool(sides),
        "ink_edge_sides": sides,
    }


def _parse_svg(svg_bytes: bytes) -> dict:
    try:
        root = ElementTree.fromstring(svg_bytes)
    except ElementTree.ParseError as exc:
        return {
            "parse_ok": False,
            "parse_error": str(exc),
            "width": 0.0,
            "height": 0.0,
            "viewbox": None,
            "path_count": 0,
            "text_node_count": 0,
            "use_count": 0,
            "image_count": 0,
        }
    viewbox_raw = root.attrib.get("viewBox") or root.attrib.get("viewbox")
    viewbox = None
    if viewbox_raw:
        vals = [_float(x) for x in re.split(r"[\s,]+", viewbox_raw.strip())]
        if len(vals) == 4:
            viewbox = vals
    counts = {"path": 0, "text": 0, "use": 0, "image": 0}
    transform_count = clip_count = white_count = transparent_count = 0
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag in counts:
            counts[tag] += 1
        attrs = " ".join("%s=%s" % (key, value)
                         for key, value in node.attrib.items()).lower()
        transform_count += int("transform" in node.attrib)
        clip_count += int(tag == "clippath" or "clip-path" in attrs)
        white_count += int(any(token in attrs for token in (
            "fill=white", "fill=#fff", "fill=#ffffff",
            "stroke=white", "stroke=#fff", "stroke=#ffffff")))
        transparent_count += int(any(token in attrs for token in (
            "opacity=0", "fill-opacity=0", "stroke-opacity=0")))
    return {
        "parse_ok": True,
        "parse_error": None,
        "width": _float(root.attrib.get("width")),
        "height": _float(root.attrib.get("height")),
        "viewbox": viewbox,
        "path_count": counts["path"],
        "text_node_count": counts["text"],
        "use_count": counts["use"],
        "image_count": counts["image"],
        "transform_node_count": transform_count,
        "clip_path_count": clip_count,
        "white_style_node_count": white_count,
        "transparent_style_node_count": transparent_count,
    }


def analyze_formula_svg(svg_path, *, expected_bbox=None,
                        scale=DEFAULT_SCALE) -> dict:
    """Return structural and actual-ink evidence for one SVG asset."""
    path = Path(svg_path)
    record = {
        "svg_path": str(path.resolve()) if path.exists() else str(path),
        "svg_exists": path.exists(),
        "svg_generated": path.exists(),
        "svg_sha256": None,
        "svg_byte_count": 0,
        "expected_bbox": ([float(v) for v in expected_bbox]
                          if expected_bbox else None),
        "raster_scale": float(scale),
    }
    if not path.exists():
        record.update({
            "parse_ok": False,
            "parse_error": "missing",
            "width": 0.0,
            "height": 0.0,
            "viewbox": None,
            "path_count": 0,
            "text_node_count": 0,
            "use_count": 0,
            "image_count": 0,
            "transform_node_count": 0,
            "clip_path_count": 0,
            "white_style_node_count": 0,
            "transparent_style_node_count": 0,
            "raster_width": 0,
            "raster_height": 0,
            "rasterized_ink_pixel_count": 0,
            "nonwhite_ink_bbox_px": None,
            "zero_size": True,
            "zero_ink": True,
            "raster_error": None,
            "zero_ink_cause": "asset_missing",
        })
        return record
    svg_bytes = path.read_bytes()
    record["svg_sha256"] = hashlib.sha256(svg_bytes).hexdigest()
    record["svg_byte_count"] = len(svg_bytes)
    record.update(_parse_svg(svg_bytes))
    raster_error = None
    raster = None
    try:
        doc = pymupdf.open(stream=svg_bytes, filetype="svg")
        if len(doc):
            pix = doc[0].get_pixmap(
                matrix=pymupdf.Matrix(float(scale), float(scale)),
                alpha=False)
            raster = Image.frombytes("RGB", (pix.width, pix.height),
                                     pix.samples)
        doc.close()
    except Exception as exc:  # noqa: BLE001 - evidence must retain failure
        raster_error = "%s: %s" % (type(exc).__name__, exc)
    metrics = (_ink_metrics(raster) if raster is not None else {
        "ink_pixel_count": 0,
        "ink_bbox_px": None,
        "ink_area_px": 0,
        "ink_touches_edge": False,
        "ink_edge_sides": [],
    })
    ink_bbox_units = None
    viewbox = record.get("viewbox")
    ink_bbox_px = metrics["ink_bbox_px"]
    if (viewbox and ink_bbox_px and raster is not None
            and raster.width > 0 and raster.height > 0):
        ink_bbox_units = [
            round(viewbox[0] + ink_bbox_px[0] / raster.width * viewbox[2], 4),
            round(viewbox[1] + ink_bbox_px[1] / raster.height * viewbox[3], 4),
            round(viewbox[0] + ink_bbox_px[2] / raster.width * viewbox[2], 4),
            round(viewbox[1] + ink_bbox_px[3] / raster.height * viewbox[3], 4),
        ]
    if metrics["ink_pixel_count"] > 0:
        zero_ink_cause = None
    elif raster_error:
        zero_ink_cause = "rasterization_or_path_conversion_failed"
    elif sum(record.get(name, 0) for name in (
            "path_count", "text_node_count", "use_count", "image_count")) == 0:
        zero_ink_cause = "empty_svg_no_drawable_nodes"
    elif record.get("transparent_style_node_count", 0):
        zero_ink_cause = "transparent_drawable_nodes"
    elif record.get("white_style_node_count", 0):
        zero_ink_cause = "white_drawable_nodes"
    else:
        zero_ink_cause = "drawable_nodes_outside_viewbox_or_glyph_missing"
    record.update({
        "raster_width": raster.width if raster is not None else 0,
        "raster_height": raster.height if raster is not None else 0,
        "rasterized_ink_pixel_count": metrics["ink_pixel_count"],
        "nonwhite_ink_bbox_px": metrics["ink_bbox_px"],
        "nonwhite_ink_bbox_svg_units": ink_bbox_units,
        "nonwhite_ink_area_px": metrics["ink_area_px"],
        "ink_touches_edge": metrics["ink_touches_edge"],
        "ink_edge_sides": metrics["ink_edge_sides"],
        "zero_size": (record.get("width", 0) <= 0
                      or record.get("height", 0) <= 0),
        "zero_ink": metrics["ink_pixel_count"] == 0,
        "raster_error": raster_error,
        "zero_ink_cause": zero_ink_cause,
    })
    return record


def formula_svg_qa(records: Iterable[dict], out_path=None) -> dict:
    """Aggregate SVG evidence already produced by ``analyze_formula_svg``.

    ``records`` may contain direct SVG records or formula trace records with
    a ``svg.segments`` list.
    """
    flat = []
    for item in records or []:
        if "svg_path" in item:
            flat.append(item)
        else:
            flat.extend((item.get("svg") or {}).get("segments") or [])
    missing = [r for r in flat if not r.get("svg_exists")]
    # Missing, zero-size and zero-ink are mutually attributable stages.  A
    # missing asset has no measurable dimensions/ink; it must not also be
    # counted as an existing zero-size or blank SVG.
    zero_size = [r for r in flat if r.get("svg_exists")
                 and r.get("zero_size")]
    zero_ink = [r for r in flat if r.get("svg_exists") and r.get("zero_ink")]
    crop = [r for r in flat if r.get("viewbox_crop")]
    result = {
        "schema_version": "phase4e1b.c2.formula_svg_qa.v1",
        "svg_segment_count": len(flat),
        "svg_missing_count": len(missing),
        "svg_zero_size_count": len(zero_size),
        "svg_zero_ink_count": len(zero_ink),
        "svg_viewbox_crop_count": len(crop),
        "hard": {
            "svg_missing_count": len(missing),
            "svg_zero_size_count": len(zero_size),
            "svg_zero_ink_count": len(zero_ink),
            "svg_viewbox_crop_count": len(crop),
        },
        "details": {
            "missing": missing[:32],
            "zero_size": zero_size[:32],
            "zero_ink": zero_ink[:32],
            "viewbox_crop": crop[:32],
        },
        "decision": ("pass" if not (missing or zero_size or zero_ink or crop)
                     else "fail"),
    }
    if out_path:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return result
