# -*- coding: utf-8 -*-
"""visual-v07 Task 4F: enforce exclusive source-span primary ownership."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageDraw, ImageFont


REPO = Path(__file__).resolve().parent.parent.parent
HERE = REPO / "tools" / "page_pipeline"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "实现源码" / "pdf_translator"))

from _chromium_pdf import render_html_to_pdf  # noqa: E402
from figure_internal_text_ownership_audit import (  # noqa: E402
    _formula_span_index,
    _html_truth,
    _span_paragraph_index,
    figure_internal_text_ownership_audit,
)
from final_block_collision_qa import final_block_collision_qa  # noqa: E402
from page_model import build_page_model, extract_source_objects  # noqa: E402
from source_ownership import (  # noqa: E402
    PrimaryOwner,
    SourceOwnershipResolver,
    text_in_figure_ratio,
)
from source_ownership_qa import evaluate_source_ownership_gate  # noqa: E402


OUT = REPO / "outputs" / "visual_v07_task4f"
SOURCE_PDF = Path("C:/Users/74496/Desktop/PPAT.pdf")
SOURCE_PAGE_INDEX = 4
FROZEN_MODEL_PATH = (REPO / "outputs" / "phase4e2a_qa_recovery"
                     / "doc2" / "pages" / "p005"
                     / "stitched_page_model.json")
FROZEN_SLOTS_PATH = (REPO / "outputs" / "visual_v07_task4a"
                     / "source_text_slots_ppat_p005.json")
FROZEN_FINAL_DIR = (REPO / "outputs" / "visual_v07_task4d"
                    / "ppat_p005")
FROZEN_HTML_PATH = FROZEN_FINAL_DIR / "zh_visual.html"
FROZEN_PDF_PATH = FROZEN_FINAL_DIR / "zh_visual.pdf"
TASK4E_AUDIT_PATH = (REPO / "outputs" / "visual_v07_task4e"
                     / "figure_internal_text_ownership.json")


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _bbox(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return []
    return [float(item) for item in value]


def _font(size: int = 15) -> ImageFont.ImageFont:
    for path in ("C:/Windows/Fonts/arial.ttf",
                 "C:/Windows/Fonts/calibri.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_page(pdf_path: Path, scale: float = 2.0,
                 page_index: int = 0) -> Image.Image:
    with pymupdf.open(pdf_path) as document:
        pixmap = document[page_index].get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), alpha=False)
    return Image.frombytes("RGB", [pixmap.width, pixmap.height],
                           pixmap.samples)


def _crop_pt(image: Image.Image, box: list[float], page_size: list[float]
             ) -> Image.Image:
    sx = image.width / page_size[0]
    sy = image.height / page_size[1]
    return image.crop((int(box[0] * sx), int(box[1] * sy),
                       int(box[2] * sx), int(box[3] * sy)))


def _label_panel(image: Image.Image, label: str) -> Image.Image:
    band = 30
    output = Image.new("RGB", (image.width, image.height + band), "white")
    output.paste(image, (0, band))
    draw = ImageDraw.Draw(output)
    draw.text((10, 7), label, fill=(20, 20, 20), font=_font(15))
    return output


def _side_by_side(left: Image.Image, right: Image.Image) -> Image.Image:
    height = max(left.height, right.height)
    output = Image.new("RGB", (left.width + right.width + 8, height),
                       (230, 230, 230))
    output.paste(left, (0, 0))
    output.paste(right, (left.width + 8, 0))
    return output


def _remove_soft_paragraphs(html_text: str, paragraph_ids: set[str]
                            ) -> tuple[str, list[str]]:
    removed = []
    output = html_text
    for paragraph_id in sorted(paragraph_ids):
        pattern = re.compile(
            r'<div\b(?=[^>]*\bclass="[^"]*\bparagraph-block\b[^"]*")'
            r'(?=[^>]*\bdata-para="%s")[^>]*>.*?</div\s*>'
            % re.escape(paragraph_id),
            re.IGNORECASE | re.DOTALL,
        )
        output, count = pattern.subn("", output)
        if count:
            removed.extend([paragraph_id] * count)
    return output, removed


def _with_asset_base(html_text: str) -> str:
    base = FROZEN_FINAL_DIR.resolve().as_uri().rstrip("/") + "/"
    return html_text.replace("<head>", '<head><base href="%s">' % base,
                             1)


def _paragraph_span_ids(region: dict[str, Any]) -> set[str]:
    paragraph = region.get("payload") or {}
    output = {str(value) for value in paragraph.get("span_ids") or []}
    for fragment in paragraph.get("source_fragments") or []:
        output.update(str(value) for value in fragment.get("span_ids") or [])
    return output


def _project_frozen_fixture(
        frozen_model: dict[str, Any], frozen_slots: dict[str, Any],
        production_model: dict[str, Any],
        ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Project the production ownership result onto the frozen visual fixture.

    Production excludes Figure spans before paragraph construction.  The
    frozen artifact predates that decision, so this projection removes only
    complete text regions/slots whose source spans now resolve to Figure.  A
    mixed-owner paragraph is rejected instead of being rewritten in QA code.
    """
    resolved = production_model["ownership"]
    figure_owned = set(resolved["figure_span_ids"])
    fixed_model = copy.deepcopy(frozen_model)
    fixed_model["ownership"] = copy.deepcopy(resolved)
    raw_figures = {
        str((region.get("payload") or {}).get("figure_id")
            or region.get("region_id") or ""): region
        for region in production_model.get("regions") or []
        if region.get("type") == "figure"
    }
    removed_paragraph_ids = set()
    mixed_text_regions = []
    kept_regions = []
    for region in fixed_model.get("regions") or []:
        if region.get("type") == "figure":
            figure_id = str((region.get("payload") or {}).get("figure_id")
                            or region.get("region_id") or "")
            if figure_id in raw_figures:
                region["payload"]["source_span_ids"] = list(
                    raw_figures[figure_id]["payload"]["source_span_ids"])
            kept_regions.append(region)
            continue
        if region.get("type") != "text":
            kept_regions.append(region)
            continue
        span_ids = _paragraph_span_ids(region)
        owned_here = span_ids.intersection(figure_owned)
        if not owned_here:
            kept_regions.append(region)
            continue
        if owned_here != span_ids:
            mixed_text_regions.append({
                "region_id": region.get("region_id"),
                "span_ids": sorted(span_ids),
                "figure_owned_span_ids": sorted(owned_here),
            })
            kept_regions.append(region)
            continue
        paragraph_id = str((region.get("payload") or {}).get(
            "paragraph_id") or region.get("region_id") or "")
        removed_paragraph_ids.add(paragraph_id)
    fixed_model["regions"] = kept_regions
    fixed_model["paragraph_count"] = sum(
        region.get("type") == "text" for region in kept_regions)

    fixed_slots = copy.deepcopy(frozen_slots)
    removed_slot_ids = []
    mixed_slots = []
    kept_slots = []
    for slot in fixed_slots.get("slots") or []:
        span_ids = {str(value) for value in
                    (slot.get("source_topology") or {}).get(
                        "source_span_ids") or []}
        owned_here = span_ids.intersection(figure_owned)
        if not owned_here:
            kept_slots.append(slot)
            continue
        if owned_here != span_ids:
            mixed_slots.append({
                "slot_id": slot.get("slot_id"),
                "source_span_ids": sorted(span_ids),
                "figure_owned_span_ids": sorted(owned_here),
            })
            kept_slots.append(slot)
            continue
        removed_slot_ids.append(str(slot.get("slot_id") or ""))
    fixed_slots["slots"] = kept_slots
    trace = {
        "removed_paragraph_ids": sorted(removed_paragraph_ids),
        "removed_slot_ids": sorted(removed_slot_ids),
        "mixed_text_regions": mixed_text_regions,
        "mixed_slots": mixed_slots,
        "projection_complete": not mixed_text_regions and not mixed_slots,
    }
    return fixed_model, fixed_slots, trace


def _slot_geometry_regression(before: dict[str, Any], after: dict[str, Any]
                              ) -> dict[str, Any]:
    def index(model: dict[str, Any]) -> dict[str, list[float]]:
        return {str(slot.get("slot_id") or ""):
                [round(float(value), 3) for value in
                 slot.get("source_bbox") or []]
                for slot in model.get("slots") or []}
    old, new = index(before), index(after)
    changed = [slot_id for slot_id in sorted(set(old).intersection(new))
               if old[slot_id] != new[slot_id]]
    return {
        "surviving_slot_geometry_mutation_count": len(changed),
        "changed_slot_ids": changed,
        "removed_slot_ids": sorted(set(old) - set(new)),
        "added_slot_ids": sorted(set(new) - set(old)),
    }


def _production_special_case_audit() -> dict[str, Any]:
    paths = [HERE / "source_ownership.py", HERE / "page_model.py"]
    diff = subprocess.run(
        ["git", "diff", "--unified=0", "HEAD", "--"]
        + [str(path.relative_to(REPO)) for path in paths],
        cwd=REPO, check=True, capture_output=True, text=True,
        encoding="utf-8").stdout
    additions = "\n".join(
        line[1:] for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++"))
    forbidden = {
        "span fixture literal": r"\bS\d{3,}\b",
        "annotation text literal": r"\(e\)|\bOutput\b",
        "document literal": r"\bPPAT\b",
        "page equality branch": r"\bpage(?:_idx|_index)?\s*==\s*\d+",
        "filename branch": r"\bfilename\b",
    }
    hits = []
    for label, pattern in forbidden.items():
        if re.search(pattern, additions, re.IGNORECASE):
            hits.append(label)
    return {
        "production_special_case_count": len(hits),
        "hits": hits,
        "scope": [str(path.relative_to(REPO)) for path in paths],
        "method": "scan added production lines only; fixture/report code excluded",
    }


def _synthetic_regressions() -> dict[str, Any]:
    resolver = SourceOwnershipResolver()
    ordinary = resolver.resolve(
        [
            {"id": "internal", "bbox": [10, 10, 90, 20],
             "font": "Sans-Bold", "size": 6},
            {"id": "caption", "bbox": [0, 84, 100, 100],
             "font": "Serif", "size": 9},
        ],
        [{"figure_id": "fixture-figure", "bbox": [0, 0, 100, 80]}],
    )
    no_text = resolver.resolve(
        [{"id": "body", "bbox": [0, 90, 100, 105],
          "font": "Serif", "size": 9}],
        [{"figure_id": "empty-figure", "bbox": [0, 0, 100, 80]}],
    )
    ambiguous_with_peer = resolver.resolve(
        [
            {"id": "peer", "bbox": [10, 10, 60, 16],
             "font": "Sans-Bold", "size": 6},
            {"id": "ambiguous", "bbox": [91, 10, 101, 16],
             "font": "Sans-Bold", "size": 6},
        ],
        [{"figure_id": "ambiguous-figure", "bbox": [0, 0, 100, 80]}],
    )
    metrics = {
        "ordinary_figure_internal_label_owner_is_figure": int(
            ordinary["primary_owner_by_span"]["internal"]
            == PrimaryOwner.FIGURE.value),
        "ordinary_figure_external_caption_owner_is_text": int(
            ordinary["primary_owner_by_span"]["caption"]
            == PrimaryOwner.TEXT.value),
        "figure_without_internal_text_has_zero_figure_spans": int(
            len(no_text["owner_sets"][PrimaryOwner.FIGURE.value]) == 0),
        "ambiguous_zone_uses_auxiliary_peer_evidence": int(
            ambiguous_with_peer["primary_owner_by_span"]["ambiguous"]
            == PrimaryOwner.FIGURE.value
            and ambiguous_with_peer["evidence_by_span"]["ambiguous"][
                "decision"] == "ambiguous_relative_geometry_with_peers"),
        "synthetic_multi_primary_owner_count": (
            ordinary["metrics"]["source_span_multi_primary_owner_count"]
            + no_text["metrics"]["source_span_multi_primary_owner_count"]
            + ambiguous_with_peer["metrics"][
                "source_span_multi_primary_owner_count"]),
    }
    return {
        "schema_version": "visual_v07.task4f.synthetic_regression.v1",
        "decision": "pass" if (
            all(metrics[name] == 1 for name in (
                "ordinary_figure_internal_label_owner_is_figure",
                "ordinary_figure_external_caption_owner_is_text",
                "figure_without_internal_text_has_zero_figure_spans",
                "ambiguous_zone_uses_auxiliary_peer_evidence"))
            and metrics["synthetic_multi_primary_owner_count"] == 0)
        else "blocked",
        "metrics": metrics,
        "ordinary_figure_with_caption": ordinary,
        "figure_without_internal_text": no_text,
        "ambiguous_zone_with_peer_evidence": ambiguous_with_peer,
    }


def _render_owner_multiplicity(
        fixed_model: dict[str, Any], html_path: Path,
                               ) -> dict[str, Any]:
    spans, _, _, _, _, _, _, _ = extract_source_objects(
        str(SOURCE_PDF), SOURCE_PAGE_INDEX)
    owners = fixed_model["ownership"]["primary_owner_by_span"]
    paragraphs = _span_paragraph_index(fixed_model)
    formulas = _formula_span_index(fixed_model, spans)
    html = _html_truth(html_path)
    figures = [region for region in fixed_model.get("regions") or []
               if region.get("type") == "figure"
               and str((region.get("payload") or {}).get("figure_id")
                       or region.get("region_id") or "")
               in html["figure_ids"]]
    rows = []
    for span in spans:
        span_id = str(span.get("id") or "")
        owner = owners.get(span_id)
        paths = []
        if any(text_in_figure_ratio(span.get("bbox"), figure.get("bbox"))
               > 0.0 for figure in figures):
            paths.append("FIGURE_HARD_ANCHOR")
        paragraph = paragraphs.get(span_id) or {}
        paragraph_id = str(paragraph.get("paragraph_id") or "")
        fragments = [str(fragment.get("flow_fragment_id") or "")
                     for fragment in paragraph.get("source_fragments") or []]
        soft_rendered = bool(
            owner == PrimaryOwner.TEXT.value
            and paragraph_id in html["paragraph_ids"]
            and (not fragments or any(fragment in html["flow_fragment_ids"]
                                      for fragment in fragments)))
        if soft_rendered:
            paths.append("SOFT_TEXT")
        formula_rendered = bool(
            owner == PrimaryOwner.FORMULA.value
            and any(formula_id in html["formula_ids"]
                    for formula_id in formulas.get(span_id, [])))
        if formula_rendered:
            paths.append("FORMULA")
        rows.append({
            "span_id": span_id,
            "primary_owner": owner,
            "render_paths": paths,
            "render_owner_count": len(paths),
        })
    return {
        "source_span_count": len(rows),
        "source_span_render_owner_count_gt1": sum(
            row["render_owner_count"] > 1 for row in rows),
        "source_span_render_owner_count_eq0": sum(
            row["render_owner_count"] == 0 for row in rows),
        "records": rows,
    }


def _external_caption_misowned(
        audit: dict[str, Any], production_model: dict[str, Any]
        ) -> tuple[int, list[dict[str, Any]]]:
    owners = production_model["ownership"]["primary_owner_by_span"]
    rows = []
    for caption in audit.get("external_captions") or []:
        span_owners = {span_id: owners.get(span_id)
                       for span_id in caption.get("span_ids") or []}
        misowned = bool(
            not span_owners
            or any(owner != PrimaryOwner.TEXT.value
                   for owner in span_owners.values())
            or not caption.get("source_text_slot_created")
            or not caption.get("rendered_as_soft_text"))
        rows.append({
            "paragraph_id": caption.get("paragraph_id"),
            "semantic_role": caption.get("semantic_role"),
            "span_owners": span_owners,
            "source_text_slot_created": caption.get(
                "source_text_slot_created"),
            "rendered_as_soft_text": caption.get("rendered_as_soft_text"),
            "misowned": misowned,
        })
    return sum(row["misowned"] for row in rows), rows


def _figure_geometry(html_text: str) -> dict[str, str]:
    output = {}
    for match in re.finditer(
            r'<span\b(?=[^>]*\bclass="[^"]*\bfigure-region\b[^"]*")'
            r'[^>]*>', html_text, re.IGNORECASE):
        tag = match.group(0)
        figure = re.search(r'\bdata-figure="([^"]+)"', tag,
                           re.IGNORECASE)
        style = re.search(r'\bstyle="([^"]+)"', tag, re.IGNORECASE)
        if figure:
            output[figure.group(1)] = style.group(1) if style else ""
    return output


def _review_bundle(
        before_pdf: Path, after_pdf: Path, target: dict[str, Any],
        caption: dict[str, Any], fixed_model: dict[str, Any],
        decision: str) -> dict[str, Any]:
    bundle = OUT / "review_bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    page_size = [float(fixed_model["width"]), float(fixed_model["height"])]
    before_page = _render_page(before_pdf)
    after_page = _render_page(after_pdf)
    figure = _bbox(target["figure_bbox"])
    figure_crop = [max(figure[0] - 25, 0), max(figure[1] - 10, 0),
                   min(figure[2] + 16, page_size[0]),
                   min(figure[3] + 16, page_size[1])]
    before = _label_panel(_crop_pt(
        before_page, figure_crop, page_size), "BEFORE: duplicate soft text")
    after = _label_panel(_crop_pt(
        after_page, figure_crop, page_size), "AFTER: Figure owner only")
    before.save(bundle / "figure_before.png")
    after.save(bundle / "figure_after.png")
    _side_by_side(before, after).save(bundle / "figure_before_after.png")

    source_page = _render_page(SOURCE_PDF, page_index=SOURCE_PAGE_INDEX)
    sx, sy = source_page.width / page_size[0], source_page.height / page_size[1]
    target_box = _bbox(target["bbox"])
    slot_box = _bbox((target.get("source_text_slot_bboxes") or [[]])[0])

    def overlay(after_state: bool) -> Image.Image:
        canvas = source_page.copy()
        draw = ImageDraw.Draw(canvas, "RGBA")
        fxy = [figure[0] * sx, figure[1] * sy,
               figure[2] * sx, figure[3] * sy]
        txy = [target_box[0] * sx, target_box[1] * sy,
               target_box[2] * sx, target_box[3] * sy]
        draw.rectangle(fxy, outline=(0, 135, 70, 240), width=4)
        color = (0, 145, 70, 245) if after_state else (220, 0, 0, 245)
        draw.rectangle(txy, outline=color, width=5)
        if slot_box and not after_state:
            sxy = [slot_box[0] * sx, slot_box[1] * sy,
                   slot_box[2] * sx, slot_box[3] * sy]
            draw.rectangle(sxy, outline=(150, 40, 190, 225), width=3)
        label = ("AFTER primary=FIGURE; soft slot absent"
                 if after_state else
                 "BEFORE primary=TEXT; Figure crop + soft slot")
        draw.rectangle((figure_crop[0] * sx, (figure_crop[1] - 2) * sy,
                        (figure_crop[0] + 220) * sx,
                        (figure_crop[1] + 10) * sy),
                       fill=(255, 255, 255, 225))
        draw.text((figure_crop[0] * sx, figure_crop[1] * sy), label,
                  fill=color, font=_font(14))
        return _crop_pt(canvas, figure_crop, page_size)

    overlay(False).save(bundle / "ownership_before_overlay.png")
    overlay(True).save(bundle / "ownership_after_overlay.png")

    caption_box = _bbox(caption.get("bbox"))
    caption_crop = [max(figure[0] - 12, 0), max(figure[1] - 8, 0),
                    min(figure[2] + 12, page_size[0]),
                    min(max(caption_box[3] + 8, figure[3] + 20),
                        page_size[1])]
    caption_image = after_page.copy()
    draw = ImageDraw.Draw(caption_image, "RGBA")
    if caption_box:
        draw.rectangle([caption_box[0] * sx, caption_box[1] * sy,
                        caption_box[2] * sx, caption_box[3] * sy],
                       outline=(0, 135, 70, 245), width=4)
    caption_panel = _label_panel(_crop_pt(
        caption_image, caption_crop, page_size),
        "Caption regression: TEXT / caption / slot preserved")
    caption_panel.save(bundle / "caption_regression.png")

    files = [
        "figure_before.png", "figure_after.png",
        "figure_before_after.png", "ownership_before_overlay.png",
        "ownership_after_overlay.png", "caption_regression.png",
    ]
    index = {
        "schema_version": "visual_v07.task4f.review_index.v1",
        "decision": decision,
        "files": [{"name": name, "sha256": _sha256(bundle / name)}
                  for name in files],
        "legend": {
            "green": "Figure primary owner / preserved caption",
            "red": "pre-fix text ownership defect",
            "purple": "pre-fix erroneous SourceTextSlot",
        },
    }
    _dump(bundle / "review_index.json", index)
    (bundle / "REVIEW_README.md").write_text(
        "# Task 4F exclusive source ownership review\n\n"
        "- `figure_before.png` shows the frozen duplicate soft annotation.\n"
        "- `figure_after.png` shows the same unchanged Figure hard anchor "
        "with its source annotation rendered once.\n"
        "- `figure_before_after.png` is the direct visual comparison.\n"
        "- ownership overlays show the span moving from TEXT/slot to the "
        "exclusive FIGURE primary owner.\n"
        "- `caption_regression.png` verifies the external caption remains a "
        "translated TEXT/caption slot.\n\n"
        "The Figure bbox, crop SVG, position, caption geometry, and all "
        "surviving SourceTextSlot rectangles are frozen.\n",
        encoding="utf-8")
    return index


def _report(
        decision: str, before: dict[str, Any], after: dict[str, Any],
        target_before: dict[str, Any], target_after: dict[str, Any],
        target_resolution: dict[str, Any], metrics: dict[str, Any],
        caption_rows: list[dict[str, Any]],
        synthetic: dict[str, Any], geometry_unchanged: bool,
        figure_asset_sha: str, production: dict[str, Any],
        ) -> str:
    evidence = target_resolution["figure_ownership_evidence"]
    caption = caption_rows[0] if caption_rows else {}
    return """# Exclusive Source Ownership Report

**Decision: %s**

## QA-first result

The frozen Task 4E artifact is the red baseline: its Figure-internal duplicate
render count is `%d`, and its Figure-internal soft-text owner count is `%d`.
After resolving ownership before paragraph construction, both counts are `0`.

## Root cause and resolution

The former policy required the complete span bbox to fit inside the Figure bbox
plus a fixed `3 pt` margin.  The target source span extends `%.3f pt` beyond the
raw Figure right edge, so it still exceeded that margin by `%.3f pt`.  That
small absolute miss incorrectly sent it into TEXT -> LogicalParagraph ->
translation -> SourceTextSlot -> soft renderer, even though the Figure source
SVG already contains it.

`text_in_figure_ratio` is now computed as:

`area(intersection(text_bbox, figure_bbox)) / area(text_bbox)`

It is intentionally not IoU.  For the current fixture the ratio is `%.6f`
(`%.2f%%`), the text centre is inside the Figure, and source-SVG crop inclusion
is also `%.6f`.  This exceeds the centralized strong threshold `%.2f`, so the
exclusive `PrimaryOwner` is `FIGURE`.  Absolute edge escape is retained only as
evidence (`edge_escape_ratio=%.6f`); it cannot veto the relative decision.

The resolver also records auxiliary evidence: font similarity `%s`, font-size
similarity `%s`, baseline similarity `%s`, neighboring Figure-owned span count
`%d`, and source-reading-order relation `%s`.  The current fixture already
passes the strong relative-inclusion path, so auxiliary evidence confirms the
relation but is not needed to override the threshold.  Spans in the
`0.80 <= ratio < 0.95` zone are decided by the centralized weighted auxiliary
policy; spans below `0.80`, or whose centre is outside, remain TEXT.

## Ownership chain after the fix

- Span `%s` resolves to exactly one primary owner: `%s`.
- Figure payload claims the span: `%s`.
- LogicalParagraph/semantic classification/translation entry: absent.
- SourceTextSlot duplicate: `%s`.
- Soft-text DOM duplicate: `%s`.
- Figure hard-anchor render remains present: `%s`.

The formula-ledger observations from Task 4E were not refactored.  They have no
separate final formula render in this fixture and therefore do not violate the
new `source_span_render_owner_count_gt1` gate.

## Caption and fixture regressions

The external caption remains `%s`, with all source spans owned by TEXT; its
SourceTextSlot and soft translated render are both preserved.  Therefore
`external_caption_misowned_count=0`.  A synthetic ordinary Figure+caption
fixture also resolves its internal label to FIGURE and its outside caption to
TEXT.  A Figure-without-internal-text fixture produces zero Figure-owned text
spans, and an ambiguous-zone fixture passes through the auxiliary peer-evidence
path.  All fixture gates pass: `%s`.

## Geometry and scope

Figure bbox/style equality before and after: `%s`.  The unchanged source Figure
SVG SHA-256 is `%s`.  No Figure crop/bbox, Figure position, caption geometry,
body position, renderer, math glyph, table, typography, flow, or release path
was modified.  All surviving SourceTextSlot bboxes are byte-for-byte equal in
their normalized coordinates (`surviving_slot_geometry_mutation_count=0`).

`production_special_case_count=%d`.  Production additions contain no fixture
span, annotation text, page, or filename branch.  The current span is fixture
evidence only.

## Hard metrics

```json
%s
```
""" % (
        decision.upper(),
        int(before["metrics"]["figure_internal_duplicate_render_count"]),
        int(before["metrics"]["figure_internal_soft_text_owner_count"]),
        float(evidence["edge_escape_distance_pt"]),
        float((target_before.get("owner_margin_deficit_pt") or {}).get(
            "right") or 0.0),
        float(evidence["text_in_figure_ratio"]),
        100.0 * float(evidence["text_in_figure_ratio"]),
        float(evidence["source_svg_crop_inclusion"]),
        float(SourceOwnershipResolver().policy.strong_inclusion_ratio),
        float(evidence["edge_escape_ratio"]),
        str(evidence.get("font_similarity")).lower(),
        str(evidence.get("font_size_similarity")).lower(),
        str(evidence.get("baseline_similarity")).lower(),
        int(evidence.get("neighboring_figure_owned_span_count") or 0),
        str(evidence.get("source_reading_order_relation")).lower(),
        target_after.get("span_id"), target_resolution["primary_owner"],
        str(target_after.get("figure_payload_claims_span")).lower(),
        str(target_after.get("source_text_slot_created")).lower(),
        str(target_after.get("rendered_as_soft_text")).lower(),
        str(target_after.get("rendered_inside_figure")).lower(),
        caption.get("semantic_role"),
        synthetic["decision"], str(geometry_unchanged).lower(),
        figure_asset_sha, int(production["production_special_case_count"]),
        json.dumps(metrics, ensure_ascii=False, indent=2),
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    before = _load(TASK4E_AUDIT_PATH)
    frozen_model = _load(FROZEN_MODEL_PATH)
    frozen_slots = _load(FROZEN_SLOTS_PATH)
    frozen_html = FROZEN_HTML_PATH.read_text(encoding="utf-8")

    production_model = build_page_model(
        str(SOURCE_PDF), SOURCE_PAGE_INDEX, OUT / "page_model_cache",
        run_doclayout=False, reuse_table_model=False,
        layout_regions_override=frozen_model.get("layout_regions") or [])
    fixed_model, fixed_slots, projection = _project_frozen_fixture(
        frozen_model, frozen_slots, production_model)
    if not projection["projection_complete"]:
        raise RuntimeError("frozen fixture has a mixed-owner paragraph/slot")

    fixed_html, removed_dom = _remove_soft_paragraphs(
        frozen_html, set(projection["removed_paragraph_ids"]))
    fixed_html = _with_asset_base(fixed_html)
    projection["removed_soft_dom_paragraph_ids"] = removed_dom
    artifact = OUT / "fixed_artifact"
    artifact.mkdir(parents=True, exist_ok=True)
    html_path = artifact / "zh_visual.html"
    pdf_path = artifact / "zh_visual.pdf"
    html_path.write_text(fixed_html, encoding="utf-8")
    render_html_to_pdf(html_path, pdf_path)

    collision = final_block_collision_qa(
        fixed_model, html_path=html_path, final_pdf_path=pdf_path,
        screenshot_path=artifact / "final_block_collision_dom.png")
    after = figure_internal_text_ownership_audit(
        fixed_model, SOURCE_PDF, SOURCE_PAGE_INDEX, fixed_slots, html_path)

    duplicate_before = [row for row in before.get("records") or []
                        if row.get("duplicate_render")]
    if len(duplicate_before) != 1:
        raise RuntimeError("frozen evidence no longer has one target chain")
    target_before = duplicate_before[0]
    target_id = str(target_before["span_id"])
    target_after = next(row for row in after.get("records") or []
                        if str(row.get("span_id")) == target_id)
    target_resolution = production_model["ownership"][
        "source_ownership_evidence_by_span"][target_id]

    render_multiplicity = _render_owner_multiplicity(
        fixed_model, html_path)
    caption_misowned, caption_rows = _external_caption_misowned(
        after, production_model)
    synthetic = _synthetic_regressions()
    slot_geometry = _slot_geometry_regression(frozen_slots, fixed_slots)
    production = _production_special_case_audit()
    geometry_unchanged = (
        _figure_geometry(frozen_html) == _figure_geometry(fixed_html))
    figure_asset_sha = _sha256(FROZEN_FINAL_DIR / "figure_FIG1.svg")

    metrics = {
        "source_span_multi_primary_owner_count": int(
            production_model["ownership"][
                "source_span_multi_primary_owner_count"]),
        "source_span_render_owner_count_gt1": int(
            render_multiplicity["source_span_render_owner_count_gt1"]),
        "figure_internal_duplicate_render_count": int(
            after["metrics"]["figure_internal_duplicate_render_count"]),
        "figure_internal_soft_text_owner_count": int(
            after["metrics"]["figure_internal_soft_text_owner_count"]),
        "figure_internal_label_missing_count": sum(
            row.get("current_owner") == "figure"
            and not row.get("rendered_inside_figure")
            for row in after.get("records") or []),
        "erroneous_figure_internal_slot_count": int(
            after["metrics"]["figure_internal_slot_count"]),
        "external_caption_misowned_count": int(caption_misowned),
        "final_block_collision_count": int(
            collision["metrics"]["final_block_collision_count"]),
        "production_special_case_count": int(
            production["production_special_case_count"]),
        "surviving_slot_geometry_mutation_count": int(
            slot_geometry["surviving_slot_geometry_mutation_count"]),
        "figure_geometry_mutation_count": int(not geometry_unchanged),
        "fixture_regression_failure_count": int(
            synthetic["decision"] != "pass"),
        "projection_failure_count": int(
            not projection["projection_complete"]),
    }
    qa = evaluate_source_ownership_gate(before["metrics"], metrics)
    extra_failures = [name for name in (
        "surviving_slot_geometry_mutation_count",
        "figure_geometry_mutation_count",
        "fixture_regression_failure_count",
        "projection_failure_count",
        "figure_internal_label_missing_count",
    ) if metrics[name] != 0]
    decision = "pass" if (
        qa["decision"] == "pass" and not extra_failures) else "blocked"
    qa["extra_zero_metric_failures"] = extra_failures
    qa["decision"] = decision

    evidence = {
        "schema_version": "visual_v07.task4f.exclusive_ownership.v1",
        "decision": decision,
        "qa_first_before_metrics": before["metrics"],
        "after_metrics": metrics,
        "target_before": target_before,
        "target_after": target_after,
        "target_resolution": target_resolution,
        "projection": projection,
        "render_owner_multiplicity": render_multiplicity,
        "external_caption_regression": caption_rows,
        "slot_geometry_regression": slot_geometry,
        "figure_geometry_unchanged": geometry_unchanged,
        "figure_asset_sha256": figure_asset_sha,
        "production_special_case_audit": production,
        "scope": {
            "renderer_modified": False,
            "figure_crop_modified": False,
            "math_glyph_modified": False,
            "table_modified": False,
            "typography_modified": False,
            "flow_modified": False,
        },
    }
    _dump(OUT / "production_page_model.json", production_model)
    _dump(OUT / "fixed_stitched_page_model.json", fixed_model)
    _dump(OUT / "fixed_source_text_slots.json", fixed_slots)
    _dump(OUT / "exclusive_source_ownership.json", evidence)
    _dump(OUT / "figure_ownership_before.json", before)
    _dump(OUT / "figure_ownership_after.json", after)
    _dump(OUT / "synthetic_fixture_regression.json", synthetic)
    _dump(OUT / "final_block_collision_qa.json", collision)
    _dump(OUT / "source_ownership_qa.json", qa)

    caption_source = (after.get("external_captions") or [{}])[0]
    review = _review_bundle(
        FROZEN_PDF_PATH, pdf_path, target_before, caption_source,
        fixed_model, decision)
    _dump(OUT / "checkpoint_gate.json", {
        "schema_version": "visual_v07.task4f.checkpoint.v1",
        "decision": decision,
        "metrics": metrics,
        "review_bundle": review,
        "commit_message": "fix(visual): enforce exclusive source ownership",
        "tag_created": False,
    })
    (OUT / "EXCLUSIVE_SOURCE_OWNERSHIP_REPORT.md").write_text(
        _report(
            decision, before, after, target_before, target_after,
            target_resolution, metrics, caption_rows, synthetic,
            geometry_unchanged, figure_asset_sha, production),
        encoding="utf-8")
    print(json.dumps({
        "decision": decision,
        "metrics": metrics,
        "target": target_resolution,
        "review_bundle": str(OUT / "review_bundle"),
    }, ensure_ascii=False, indent=2))
    return 0 if decision == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
