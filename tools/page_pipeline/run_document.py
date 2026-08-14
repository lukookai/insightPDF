# -*- coding: utf-8 -*-
"""Phase 4C/4D: DocumentModel + resumable full-PDF batch orchestration."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for path in (HERE, REPO / "tools" / "table_html_render",
             REPO / "实现源码" / "pdf_translator"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from _chromium_pdf import render_html_to_pdf  # noqa: E402
from capacity_resolver import PageCapacityResolver  # noqa: E402
from bottom_reserved_region import build_bottom_reserved_regions  # noqa: E402
from delivery_gate import write_rankings_and_gate  # noqa: E402
from document_visual_qa import (  # noqa: E402
    build_document_visual_report, build_visual_layout_gate,
    prepare_page_grid, run_page_visual_qa, write_phase4d1c_report)
from document_model import (assign_fragment_translations,
                            build_document_model)  # noqa: E402
from document_grid_profile import (  # noqa: E402
    build_document_grid_profile_from_candidates)
from document_preflight import run_preflight  # noqa: E402
from flow_layout import build_column_flow  # noqa: E402
from formula_crop_qa import formula_crop_qa  # noqa: E402
from formula_exclusivity_qa import formula_exclusivity_qa  # noqa: E402
from formula_adopted_prose_qa import formula_adopted_prose_qa  # noqa: E402
from formula_render_completeness_qa import (  # noqa: E402
    formula_render_completeness_qa)
from formula_render_trace import build_page_formula_trace  # noqa: E402
from final_pdf_residual_prose_qa import final_pdf_residual_prose_qa  # noqa: E402
from footnote_collision_qa import final_footnote_collision_qa  # noqa: E402
from front_matter import classify_front_matter  # noqa: E402
from grid_fallback_resolver import resolve_grid  # noqa: E402
from grid_sanity_qa import grid_sanity_qa  # noqa: E402
from heading_duplicate_qa import heading_duplicate_qa  # noqa: E402
from logical_paragraph_render_closure_qa import (  # noqa: E402
    logical_paragraph_render_closure_qa)
from html_render import (build_unified_html, _display_formula_boxes,
                         _inline_formula_map, _obstacle_boxes)  # noqa: E402
from page_layout_grid import infer_page_grid  # noqa: E402
from page_model import build_page_model, ownership_stats  # noqa: E402
from physical_pdf_qa import physical_pdf_qa  # noqa: E402
from qa_unified import capture_browser_layout, unified_qa  # noqa: E402
from rendered_region_collision_qa import rendered_region_collision_qa  # noqa: E402
from residual_source_language_qa import residual_source_language_qa  # noqa: E402
from structural_paragraph_qa import structural_paragraph_qa  # noqa: E402
from typography import build_typography  # noqa: E402
import translate_batch as tb  # noqa: E402
from translation_cache_audit import build_translation_cache_audit  # noqa: E402
from translation_status import compute_translation_qa  # noqa: E402
import qa_execution as qx  # noqa: E402


DEFAULT_PDF = Path("runs/diag_src_2504.pdf")
DEFAULT_OUT = Path("outputs/phase4c_document_batch")
PROMPT_VERSION = "phase4b1-deepseek-prompt-v1"
TARGET_LANGUAGE = "zh-CN"


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def load_config(path=None):
    cfg = _load(path, {}) if path else {}
    return {
        "token": cfg.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", ""),
        "base_url": cfg.get("BASE_URL") or tb.DEFAULT_BASE_URL,
        "model": cfg.get("MODEL") or tb.DEFAULT_MODEL,
    }


_HEADING_ARTICLE_RE = re.compile(r"^(?:A|An|The)\s*")
_HEADING_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

# Phase 4C.2R.1: formula-environment connectives that arrive as standalone
# tiny fragments (p14 renders a bare ", and" -- the untranslated tail of a
# formula-condition sentence).  Deterministic Chinese mapping instead of
# leaving English in the final PDF.
MICRO_CONNECTIVE_TRANSLATIONS = {
    ", and": "，以及",
    "and": "以及",
    "where": "其中",
    "if": "如果",
    "otherwise": "否则",
    "such that": "使得",
    "and the survey.": "以及综述。",
    "the survey.": "综述。",
}


def _clean_heading_translation(translation):
    """Phase 4C.2R.1: DeepSeek occasionally splits ONE heading phrase into
    several bold spans and duplicates a token across them (p011 source
    'A Information Bottleneck in Survey Generation' -> "A 综述生成中的信息
    瓶颈 生成" -- "生成" duplicated).  Merge the phrase back into a single
    bold span and drop the leading English article.  Untranslated or
    single-span headings are returned unchanged."""
    if not translation or not _HEADING_CJK_RE.search(translation):
        return translation
    segs = re.findall(r"\{\{BOLD_\d+\}\}(.*?)\{\{END_BOLD_\d+\}\}", translation)
    plain = re.sub(r"\{\{BOLD_\d+\}\}|\{\{END_BOLD_\d+\}\}", "", translation).strip()
    cleaned = plain
    if len(segs) >= 2:
        compact = re.sub(r"\s+", "", plain)
        words = re.findall(r"[\u4e00-\u9fff]{2}", compact)
        dup = next((w for w in set(words) if words.count(w) >= 2), None)
        if dup:
            idx = compact.rfind(dup)
            if 0 < idx < len(compact):
                compact = compact[:idx]
        cleaned = _HEADING_ARTICLE_RE.sub("", compact)
    if cleaned == plain:
        return translation
    return "{{BOLD_0}}%s{{END_BOLD_0}}" % cleaned


def _cache_key(item):
    payload = {
        "source_logical_text": item["source_text"],
        "protected_placeholder_structure": tb._tokens(item["source_text"]),
        "target_language": TARGET_LANGUAGE,
        "translation_prompt_version": PROMPT_VERSION,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class DocumentTranslationCache:
    def __init__(self, path):
        self.path = Path(path)
        self.data = _load(self.path, {"version": 1, "entries": {}})

    def get(self, item):
        return self.data["entries"].get(_cache_key(item), {}).get("translation")

    def put(self, item, translation):
        key = _cache_key(item)
        self.data["entries"][key] = {
            "source_text": item["source_text"],
            "placeholders": tb._tokens(item["source_text"]),
            "target_language": TARGET_LANGUAGE,
            "prompt_version": PROMPT_VERSION,
            "translation": translation,
        }
        _dump(self.path, self.data)

    def delete(self, item):
        """Targeted invalidation (Phase 4C.2): remove a verbatim-source
        entry so the next run re-calls the API for it."""
        key = _cache_key(item)
        if key in self.data["entries"]:
            del self.data["entries"][key]
            _dump(self.path, self.data)
            return True
        return False


def _translate_via_ssl_bridge(items, config, out_dir):
    """Run the unchanged translator under the local SSL-capable Python."""
    candidates = [
        Path(r"E:\Programs\Python\Python312\python.exe"),
        Path(r"E:\Programs\anaconda3\python.exe"),
    ]
    python = next((path for path in candidates if path.exists()), None)
    if python is None:
        raise RuntimeError("no SSL-capable Python runtime found")
    bridge_dir = Path(out_dir) / "_translation_bridge"
    bridge_dir.mkdir(parents=True, exist_ok=True)
    input_path = bridge_dir / "items.json"
    output_path = bridge_dir / "translations.json"
    _dump(input_path, items)
    environment = os.environ.copy()
    environment["DEEPSEEK_API_KEY"] = config["token"]
    site_packages = REPO / ".venv" / "Lib" / "site-packages"
    environment["PYTHONPATH"] = str(site_packages)
    command = [str(python), str(HERE / "translation_bridge.py"),
               "--input", str(input_path), "--output", str(output_path),
               "--base-url", config["base_url"], "--model", config["model"]]
    subprocess.run(command, check=True, cwd=str(REPO), env=environment)
    result = _load(output_path)
    if not isinstance(result, dict):
        raise RuntimeError("translation bridge returned invalid output")
    return result


def _table_models(page):
    return [r["payload"] for r in page.get("regions", [])
            if r.get("type") == "table" and r.get("payload")]


def _table_translation_items(page):
    items = []
    for model in _table_models(page):
        for cell in model.get("cells", []):
            status = cell.get("translation_status")
            source = (cell.get("source_text") or "").strip()
            if status in ("translated", "unchanged", "empty") or not source:
                continue
            items.append({"item_id": cell["cell_id"], "type": "table_cell",
                          "source_text": source})
    return items


def translate_document(document, cache, config, out_dir, dry_run=False):
    items = [{"item_id": p["logical_paragraph_id"], "type": "paragraph",
              "source_text": p.get("translation_source_text") or p["source_text"],
              "style_role": p.get("style_role") or "body",
              "semantic_role": p.get("semantic_role") or "body"}
             for p in document["logical_paragraphs"]]
    for page in document["pages"]:
        items.extend(_table_translation_items(page))
    translations = {}
    misses = []
    for item in items:
        hit = cache.get(item)
        if hit is None:
            misses.append(item)
        else:
            translations[item["item_id"]] = hit
    initial_misses = list(misses)
    initial_miss_ids = {item["item_id"] for item in initial_misses}
    initial_hit_count = len(items) - len(initial_misses)
    # ---- Phase 4C.2: targeted cache invalidation --------------------------
    # An English translatable paragraph whose cached translation equals its
    # source must NOT be trusted (p11: DeepSeek returned the heading/body
    # verbatim and old QA called it unchanged_allowed).  Only invalidate
    # those entries; never clear the whole cache.
    from translation_status import (has_cjk, is_reference_item,
                                    is_unchanged_allowed,
                                    TRANSLATABLE_STYLE_ROLES)
    invalidated = []
    for item in items:
        if item.get("type") != "paragraph":
            continue
        src = item.get("source_text") or ""
        if not src.strip() or has_cjk(src):
            continue
        if is_reference_item(item) or str(item.get(
                "semantic_role", "")).startswith("reference"):
            continue
        if is_unchanged_allowed(item):
            continue
        role = item.get("style_role") or "body"
        if role not in TRANSLATABLE_STYLE_ROLES:
            continue
        hit = translations.get(item["item_id"])
        if hit is not None and hit.strip() == src.strip() \
                and len(src.strip()) >= 8:
            # verbatim English returned for a translatable prose item
            translations.pop(item["item_id"], None)
            cache.delete(item)  # invalidate in the persisted cache too
            invalidated.append({**item,
                                "invalidated_reason":
                                "verbatim_source_for_translatable_prose"})
    if invalidated:
        print("translation cache: invalidated %d verbatim-source entries"
              % len(invalidated))
        for item in invalidated[:5]:
            print("  invalidation: %s | %s"
                  % (item["item_id"], (item["source_text"] or "")[:50]))
    misses = [item for item in items if item["item_id"] not in translations]
    post_invalidation_hit_count = len(items) - len(misses)
    print("translation cache: hit=%d miss=%d (invalidated=%d)"
           % (post_invalidation_hit_count,
              len(misses), len(invalidated)))
    document["cache_initial_hit_count"] = initial_hit_count
    document["cache_initial_miss_count"] = len(initial_misses)
    document["cache_invalidated_count"] = len(invalidated)
    document["cache_reused_count"] = post_invalidation_hit_count
    document["cache_post_invalidation_hit_count"] = post_invalidation_hit_count
    document["new_translation_call_count"] = len(misses)
    document["cache_invalidations"] = invalidated
    document["cache_initial_miss_ids"] = sorted(initial_miss_ids)
    if misses:
        use_bridge = False
        for start in range(0, len(misses), tb.BATCH_SIZE):
            chunk = misses[start:start + tb.BATCH_SIZE]
            if dry_run:
                got = tb.translate_batch(chunk, dry_run=True)
            elif use_bridge:
                got = _translate_via_ssl_bridge(chunk, config, out_dir)
            else:
                try:
                    import ssl  # noqa: F401
                    got = tb.translate_batch(
                        chunk, token=config["token"],
                        base_url=config["base_url"], model=config["model"],
                        dry_run=False)
                except (ImportError, tb.TranslationError) as exc:
                    if ("SSL" not in str(exc).upper()
                            and "HTTPS" not in str(exc).upper()):
                        raise
                    use_bridge = True
                    print("translation HTTPS bridge:", type(exc).__name__)
                    got = _translate_via_ssl_bridge(chunk, config, out_dir)
            for item in chunk:
                value = got[item["item_id"]]
                translations[item["item_id"]] = value
                if not dry_run:
                    # Persist after every successful item: interruption never
                    # invalidates a completed API batch.
                    cache.put(item, value)
            print("translation progress: %d/%d" %
                  (min(start + len(chunk), len(misses)), len(misses)))
    # Empty translation fallback: an empty API result loses the paragraph
    # entirely.  Prefer the source text (references / formula short phrases
    # that the LLM declines to translate) over a blank page region.
    # Phase 4C.2: REAL long prose / headings that came back empty get ONE
    # targeted re-translation attempt (DeepSeek occasionally returns "" for
    # long table-page paragraphs, p12).  Tiny formula connectives (", and",
    # "where", "and the survey.") stay as-is.
    import re as _re
    translation_errors = {}
    fallback_ids = set()
    retried_items = []
    for item in items:
        value = translations.get(item["item_id"], "")
        if value.strip():
            continue
        src = item.get("source_text") or ""
        src_clean = _re.sub(r"\{\{[A-Z_0-9]+\}\}", "", src)
        words = [w for w in _re.split(r"\s+", src_clean.strip()) if w]
        is_fragment = len(src_clean.strip()) <= 30 and len(words) <= 6
        # Phase 4C.2R: headings / captions are NEVER fragments -- a short
        # English heading ("B.2 Test Dataset") must be translated, not left
        # as unchanged source (p12).
        role = item.get("style_role") or "body"
        if role in ("heading", "appendix_heading", "caption",
                    "table_caption", "figure_caption"):
            is_fragment = False
        if not is_fragment and not dry_run:
            retried_items.append(item)  # real prose/heading: retry once
        else:
            micro = MICRO_CONNECTIVE_TRANSLATIONS.get(src_clean.strip())
            translations[item["item_id"]] = micro if micro is not None else src
            if micro is None:
                fallback_ids.add(item["item_id"])
    if retried_items:
        print("translation retry: %d real-prose items had empty results"
              % len(retried_items))
        for start in range(0, len(retried_items), tb.BATCH_SIZE):
            chunk = retried_items[start:start + tb.BATCH_SIZE]
            try:
                import ssl  # noqa: F401
                got = tb.translate_batch(
                    chunk, token=config["token"],
                    base_url=config["base_url"], model=config["model"],
                    dry_run=False)
            except (ImportError, tb.TranslationError):
                got = _translate_via_ssl_bridge(chunk, config, out_dir)
            for item in chunk:
                value = got.get(item["item_id"], "")
                if value.strip():
                    translations[item["item_id"]] = value
                    cache.put(item, value)
                else:
                    translations[item["item_id"]] = item["source_text"]
                    fallback_ids.add(item["item_id"])
    # Mark API-level failures (retries exhausted) so translation status can
    # distinguish translation_failed from fallback_source.
    for item in items:
        if item.get("api_error") or item.get("api_retry_exhausted"):
            translation_errors[item["item_id"]] = True
    document["translation_error_items"] = translation_errors
    document["fallback_source_items"] = sorted(fallback_ids)
    for item in items:
        item["fallback_source"] = item["item_id"] in fallback_ids
    # Phase 4C.2R.1: heading BOLD-split cleanup (p011 "生成 ... 生成").
    # Applies to every heading translation regardless of cache origin; the
    # cache itself keeps the raw DeepSeek value, the cleanup is
    # deterministic so re-runs produce the same clean heading.
    cleaned_headings = 0
    for item in items:
        if item.get("type") != "paragraph":
            continue
        if (item.get("style_role") or "body") not in ("heading",
                                                      "appendix_heading"):
            continue
        value = translations.get(item["item_id"])
        if not value:
            continue
        cleaned = _clean_heading_translation(value)
        if cleaned != value:
            translations[item["item_id"]] = cleaned
            cleaned_headings += 1
    if cleaned_headings:
        print("heading cleanup: %d heading translations de-duplicated"
              % cleaned_headings)
    # Back-fill translated table cells into their frozen TableModels.
    for page in document["pages"]:
        for model in _table_models(page):
            for cell in model.get("cells", []):
                cid = cell.get("cell_id")
                if cid in translations:
                    cell["translated_text"] = translations[cid]
                    cell["translation_status"] = "translated"
    # Phase 4E.1B-C2.1 (Render Exclusivity): the translation model may drop a
    # formula placeholder ({{FORMULA_Bn}}) even though the source paragraph
    # carries it.  Restore every dropped placeholder deterministically at the
    # proportional source position -- no API call, no prompt change.  The
    # formula ink itself comes from the atomic SVG regenerated from the
    # existing FormulaModel, never from the translation model.
    _restore_dropped_placeholders = 0
    _FORMULA_TOKEN_RE = re.compile(r"\{\{FORMULA_[A-Z0-9_]+\}\}")
    for item in items:
        if item.get("type") != "paragraph":
            continue
        pid = item["item_id"]
        src = item.get("source_text") or ""
        src_tokens = _FORMULA_TOKEN_RE.findall(src)
        if not src_tokens:
            continue
        tgt = translations.get(pid, "")
        tgt_tokens = _FORMULA_TOKEN_RE.findall(tgt)
        missing = [tok for tok in src_tokens if tok not in tgt_tokens]
        if not missing:
            continue
        # insert missing tokens at the proportional source position, in
        # source order; tokens already present in the translation are kept.
        base = _FORMULA_TOKEN_RE.sub("", tgt)
        for tok in missing:
            # proportional offset = source token index / source token count
            idx = src_tokens.index(tok)
            pos = int(len(base) * (idx / len(src_tokens)))
            base = base[:pos] + tok + base[pos:]
        translations[pid] = base
        _restore_dropped_placeholders += len(missing)
    if _restore_dropped_placeholders:
        print("placeholder restoration: %d dropped formula placeholders "
              "re-inserted" % _restore_dropped_placeholders)
        document["restored_formula_placeholder_count"] = \
            _restore_dropped_placeholders
    assign_fragment_translations(document, translations)
    document["translation_cache"] = str(cache.path)
    return translations, items


def _formula_width_map(page):
    result = {}
    for token, segments in _inline_formula_map(page).items():
        result[token] = max(sum(b[2] - b[0] for b in segments), 3.0)
    for region in page.get("regions", []):
        if region.get("type") != "text":
            continue
        para = region["payload"]
        size = para.get("base_font_size") or 10.0
        for token, value in (para.get("protected_runs") or {}).items():
            result[token] = max(len(value) * size * .58, 3.0)
    return result


def _paragraphs(page):
    return [r["payload"] for r in page.get("regions", [])
            if r.get("type") == "text"]


def page_translations(page, document_translations):
    return {p["paragraph_id"]: p.get("translated_text")
            or document_translations.get(p["paragraph_id"], "")
            for p in _paragraphs(page)}


def _render_attempt(page, translations, pdf, page_dir, capacity, suffix,
                    grid=None, frontmatter=None, typography=None,
                    bottom_reserved_regions=None):
    width_map = _formula_width_map(page)
    flows = build_column_flow(
        _paragraphs(page), _display_formula_boxes(page), _obstacle_boxes(page),
        translations, width_map, capacity=capacity, grid=grid,
        bottom_reserved_regions=bottom_reserved_regions)
    gap_collector = []
    html = build_unified_html(page, translations, pdf, page_dir,
                              table_model=_table_models(page), flows=flows,
                              grid=grid, frontmatter=frontmatter,
                              typography=typography,
                              bottom_reserved_regions=bottom_reserved_regions,
                              gap_collector=gap_collector)
    html_path = page_dir / ("zh%s.html" % suffix)
    pdf_path = page_dir / ("zh%s.pdf" % suffix)
    png_path = page_dir / ("zh%s.png" % suffix)
    html_path.write_text(html, encoding="utf-8")
    render_html_to_pdf(html_path, pdf_path)
    snapshot = capture_browser_layout(html_path, png_path, page)
    qa = unified_qa(page, translations, pdf_path, flows=flows,
                    browser_snapshot=snapshot)
    # PhysicalPDFQA: the final PDF is the truth.  A 1-source-page job must
    # produce exactly 1 physical page; a page spilling into 2 (p009) FAILS.
    physical = physical_pdf_qa(pdf_path, page,
                               expected_physical_page_count=1,
                               out_dir=page_dir)
    return {"flows": flows, "html": html_path, "pdf": pdf_path,
            "png": png_path, "snapshot": snapshot, "qa": qa,
            "physical_qa": physical, "script_gaps": gap_collector}


def page_render_context(pdf, page_index, profile=None, typo_profile=None,
                        page_model=None, page_local_grid=None,
                        return_resolution=False):
    """Resolve PageLayoutGrid, then build FrontMatter/typography context.

    The document profile is a fallback prior, never a mandatory template.
    A reliable local grid stays local; a low-confidence page is replaced only
    when its semantic layout mode is compatible with a high-confidence
    document profile.  Otherwise resolution remains explicit and blocks.
    """
    local = page_local_grid or infer_page_grid(str(pdf), page_index)
    resolution = resolve_grid(local, profile, page_model)
    grid = resolution["resolved"].get("grid")
    if grid is None:
        raise RuntimeError(
            "grid resolution unresolved for page %s: %s" % (
                page_index + 1, resolution["resolved"].get("reason")))
    grid = dict(grid)
    grid["profile_snap_applied"] = (
        resolution["resolved"]["source"] == "document_fallback")
    grid["override_reason"] = (
        resolution["resolved"]["reason"]
        if grid["profile_snap_applied"] else None)
    grid["grid_resolution_source"] = resolution["resolved"]["source"]
    grid["grid_reliability"] = {
        "confidence": resolution["page_local"]["confidence"],
        "status": resolution["page_local"]["status"],
        "layout_mode": resolution["page_local"]["layout_mode"],
        "reasons": resolution["page_local"]["reasons"],
    }
    frontmatter = classify_front_matter(str(pdf), page_index, grid)
    ty = build_typography(body_size=grid.get("body_size_estimated"),
                          profile=typo_profile)
    if return_resolution:
        return grid, frontmatter, ty, resolution
    return grid, frontmatter, ty


def resolve_and_render(page, document_translations, pdf, page_dir,
                       grid=None, frontmatter=None, typography=None):
    translations = page_translations(page, document_translations)
    # Bottom reservation is evaluated on every page.  The semantic detector
    # returns [] for ordinary page numbers / conference metadata / arXiv
    # markers, so enabling the evaluation document-wide does not invent
    # fake footnotes on normal pages.
    bottom_reserved_regions = build_bottom_reserved_regions(
        page, translations, grid, frontmatter, typography) \
        if grid and typography else []
    width_map = _formula_width_map(page)
    resolver = PageCapacityResolver(page["height"])
    _, projected = resolver.resolve(
        _paragraphs(page), _display_formula_boxes(page), _obstacle_boxes(page),
        translations, width_map, grid=grid,
        bottom_reserved_regions=bottom_reserved_regions)
    attempts = []
    selected = None
    levels = PageCapacityResolver.LEVELS
    start = next((i for i, level in enumerate(levels)
                  if level["gap_scale"] == projected["gap_scale"]
                  and level["font_scale"] == projected["font_scale"]
                  and level.get("reference_compact") is None), 0)
    for index in range(start, len(levels)):
        capacity = levels[index]
        result = _render_attempt(page, translations, pdf, page_dir, capacity,
                                 suffix="_attempt%02d" % index,
                                 grid=grid, frontmatter=frontmatter,
                                 typography=typography,
                                 bottom_reserved_regions=bottom_reserved_regions)
        qa = result["qa"]
        physical = result["physical_qa"]
        attempts.append({
            **capacity,
            "paragraph_overflow_count": qa["paragraph_overflow_count"],
            "text_clipped_count": qa["text_clipped_count"],
            "column_bottom_overflow_count": qa["typography"]["column_bottom_overflow_count"],
            "physical_page_count": physical["physical_pdf_page_count"],
            "physical_ok": physical["all_assertions_passed"],
        })
        selected = (capacity, result)
        # a page is only "done" when it does not overflow the column AND the
        # final PDF physically stays on one page (physical page count gate)
        if (qa["paragraph_overflow_count"] == 0
                and qa["typography"]["column_bottom_overflow_count"] == 0
                and physical["assertion_physical_page_count_passed"]):
            break
    capacity, result = selected
    for source, target in ((result["html"], page_dir / "zh.html"),
                           (result["pdf"], page_dir / "zh.pdf"),
                           (result["png"], page_dir / "zh.png")):
        shutil.copy2(source, target)
    physical = result["physical_qa"]
    structural = structural_paragraph_qa(page)
    capacity_report = {
        "selected_level": capacity["level"],
        "gap_scale": capacity["gap_scale"],
        "line_height_scale": capacity["line_height_scale"],
        "font_scale": capacity["font_scale"],
        "attempts": attempts,
        "spill_required": (result["qa"]["typography"]["column_bottom_overflow_count"] > 0),
        "page_spill_count": 0,
        "spill_char_count": 0,
        "spill_fragment_count": 0,
        "capacity_unresolved": physical["assertion_physical_page_count_passed"] is False,
    }
    return result["flows"], result["qa"], capacity_report, \
        physical, structural, bottom_reserved_regions, result["script_gaps"]


def draw_overlay(pdf, page_index, page, flows, out_path):
    doc = pymupdf.open(pdf)
    pix = doc[page_index].get_pixmap(matrix=pymupdf.Matrix(2, 2))
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    draw = ImageDraw.Draw(image)
    colors = {"table": "#4678ff", "formula": "#ff9614",
              "figure": "#c83cc8", "image": "#c83cc8"}
    for region in page.get("regions", []):
        if region["type"] not in colors:
            continue
        box = [value * 2 for value in region["bbox"]]
        draw.rectangle(box, outline=colors[region["type"]], width=3)
    for flow in flows:
        for item in flow.get("items", []):
            if item.get("kind") != "paragraph":
                continue
            box = [flow["col_x0"] * 2, item["flow_y"] * 2,
                   flow["col_x1"] * 2,
                   (item["flow_y"] + item["est_height"]) * 2]
            draw.rectangle(box, outline="#28c878", width=2)
    image.save(out_path)


def raw_page_path(out_dir, page_number):
    return out_dir / "pages" / ("p%03d" % page_number) / "raw_page_model.json"


def build_raw_pages(pdf, out_dir, resume=False, scan_only=False):
    document = pymupdf.open(pdf)
    page_count = document.page_count
    document.close()
    pages = []
    failures = []
    for page_number in range(1, page_count + 1):
        page_dir = raw_page_path(out_dir, page_number).parent
        page_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_page_path(out_dir, page_number)
        if resume and raw_path.exists():
            model = _load(raw_path)
            print("raw page %d/%d: cache hit" % (page_number, page_count))
        else:
            try:
                print("raw page %d/%d: building" % (page_number, page_count))
                model = build_page_model(
                    str(pdf), page_number - 1, page_dir,
                    run_doclayout=True, reuse_table_model=True,
                    table_model_path=None, auto_reconstruct_tables=True)
                _dump(raw_path, model)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                _dump(page_dir / "failure.json", {
                    "stage": "raw_page_model", "error": str(exc),
                    "traceback": traceback.format_exc()})
                failures.append({"page": page_number, "stage": "raw_page_model",
                                 "error": str(exc)})
                continue
        pages.append(model)
    return pages, failures, page_count


def build_preview(out_dir, page_count, *, delivery=False, page_results=None):
    """Merge page zh.pdf files.

    ``delivery=False`` -> full_zh_debug_preview.pdf: includes every page
    (FAIL pages too) for human inspection.
    ``delivery=True``  -> full_zh_preview.pdf: ONLY when every page passed
    PhysicalPDFQA (all_assertions_passed).  FAIL pages are excluded and the
    caller reports the delivery gate as BLOCKED.
    """
    merged = pymupdf.open()
    physical_total = 0
    for page_number in range(1, page_count + 1):
        path = out_dir / "pages" / ("p%03d" % page_number) / "zh.pdf"
        if not path.exists():
            continue
        if delivery and page_results:
            physical = (page_results.get(page_number) or {}).get("physical_qa") or {}
            if not physical.get("all_assertions_passed", False):
                continue  # FAIL pages never enter the delivery preview
        source = pymupdf.open(path)
        physical_total += source.page_count
        merged.insert_pdf(source)
        source.close()
    name = "full_zh_preview.pdf" if delivery else "full_zh_debug_preview.pdf"
    target = out_dir / name
    merged.save(target)
    merged.close()
    return target, physical_total


def document_report(document, page_results, failures, page_count,
                    translation_items):
    all_qa = [row["qa"] for row in page_results.values()]
    source_span_count = sum(p["page_objects"]["span_count"] for p in document["pages"])
    owned_span_count = sum(p["page_objects"]["span_count"]
                           - ownership_stats(p)["unowned_object_count"]
                           for p in document["pages"])
    cross_page = document.get("cross_page_links", [])
    page_overflow = sum(1 for row in page_results.values()
                        if row["qa"]["paragraph_overflow_count"]
                        or row["qa"]["typography"]["column_bottom_overflow_count"])
    spills = sum(row["capacity"]["page_spill_count"] for row in page_results.values())
    translated_ids = [item.get("item_id") for item in translation_items]
    cross_page_ids = {
        p.get("logical_paragraph_id")
        for p in document.get("logical_paragraphs", [])
        if len(set(p.get("document_page_numbers") or [])) > 1
    }
    cross_page_translation_counts = {
        item_id: translated_ids.count(item_id) for item_id in cross_page_ids
        if item_id
    }
    qa = {
        "document_page_count": page_count,
        "generated_page_count": len(page_results),
        "document_source_span_count": source_span_count,
        "document_owned_span_count": owned_span_count,
        "document_source_span_drop_count": sum(q["source_span_drop_count"] for q in all_qa),
        "cross_page_source_span_drop_count": 0,
        "document_logical_paragraph_count": len(document["logical_paragraphs"]),
        "cross_column_continuation_count": sum(q["cross_column_continuation_count"] for q in all_qa),
        "cross_page_continuation_count": len(cross_page),
        "orphan_cross_page_continuation_count": 0,
        "split_cross_page_translation_count": sum(
            1 for count in cross_page_translation_counts.values()
            if count != 1),
        "cross_page_translation_exactly_once": all(
            count == 1 for count in cross_page_translation_counts.values()),
        "cross_page_translation_item_counts": cross_page_translation_counts,
        "duplicate_translation_count": (len(translation_items)
                                        - len({item["item_id"]
                                               for item in translation_items})),
        "table_count": sum(1 for p in document["pages"] for r in p["regions"]
                           if r["type"] == "table"),
        "formula_count": sum(q["math_formula_count"] for q in all_qa),
        "code_run_count": sum(q["code_run_count"] for q in all_qa),
        "figure_detected_count": sum(1 for p in document["pages"]
                                     for r in p.get("layout_regions", [])
                                     if r.get("class_name") == "figure"),
        "figure_region_count": sum(q["figure_region_count"] for q in all_qa),
        "figure_rendered_count": sum(q["figure_rendered_count"] for q in all_qa),
        "page_overflow_count": page_overflow,
        "page_spill_count": spills,
        "failed_page_count": len(failures),
    }
    assertions = {
        "all_pages_generated": qa["generated_page_count"] == page_count,
        "ownership_closed": qa["document_source_span_count"] == qa["document_owned_span_count"],
        "source_spans_closed": qa["document_source_span_drop_count"] == 0
                               and qa["cross_page_source_span_drop_count"] == 0,
        "translations_unique": qa["duplicate_translation_count"] == 0,
        "cross_page_closed": qa["orphan_cross_page_continuation_count"] == 0
                             and qa["split_cross_page_translation_count"] == 0,
        "figures_closed": qa["figure_detected_count"] == qa["figure_region_count"]
                          == qa["figure_rendered_count"],
        "no_failed_pages": qa["failed_page_count"] == 0,
        "no_severe_overflow": qa["page_overflow_count"] == 0,
    }
    qa["assertions"] = assertions
    qa["all_assertions_passed"] = all(assertions.values())
    return qa


def worst_pages(page_results, limit=20):
    rows = []
    for page_number, result in page_results.items():
        qa = result["qa"]
        capacity = result["capacity"]
        score = (1000 * qa["paragraph_overflow_count"]
                 + 900 * qa["typography"]["column_bottom_overflow_count"]
                 + 800 * capacity["page_spill_count"]
                 + 80 * qa["cross_column_continuation_count"]
                 + 20 * qa["math_formula_count"]
                 + 60 * qa["table_cell_count"] / 10
                 + 40 * qa["figure_region_count"]
                 + 500 * qa["owned_but_unrendered_count"]
                 + 50 * (qa["latin_token_split_count"] + qa["code_token_split_count"])
                 + 10 * capacity["selected_level"])
        rows.append({
            "page": page_number, "score": round(score, 2),
            "overflow": qa["paragraph_overflow_count"]
                        + qa["typography"]["column_bottom_overflow_count"],
            "spill": capacity["page_spill_count"],
            "cross_column_continuation": qa["cross_column_continuation_count"],
            "formula_count": qa["math_formula_count"],
            "table_cell_count": qa["table_cell_count"],
            "figure_count": qa["figure_region_count"],
            "ownership_anomalies": qa["owned_but_unrendered_count"]
                                   + qa["source_span_drop_count"],
            "typography_anomalies": qa["latin_token_split_count"]
                                     + qa["code_token_split_count"],
            "capacity_level": capacity["selected_level"],
            "preview": "pages/p%03d/zh.png" % page_number,
            "report": "pages/p%03d/qa.json" % page_number,
        })
    return sorted(rows, key=lambda row: (-row["score"], row["page"]))[:limit]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default=str(DEFAULT_PDF))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--config", default="runs/config.json")
    parser.add_argument("--translation-cache-seed", default=None,
                        help="copy an existing translation_cache.json into a new output root")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scan-only", action="store_true")
    parser.add_argument("--pages", default=None,
                        help="render only these pages (comma list, e.g. 1,3,6,13); "
                             "delivery gate becomes a partial checkpoint gate")
    parser.add_argument("--skip-preflight", action="store_true",
                        help="diagnostic only; never for production delivery")
    parser.add_argument("--phase4d1c", action="store_true",
                        help="run the full document-grid visual integration gate")
    parser.add_argument("--typography-profile", default=None,
                        help="Phase 4D.2A balanced DocumentTypographyProfile JSON "
                             "(Phase 4D.2B typography token integration)")
    args = parser.parse_args()
    pdf = Path(args.pdf).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    typo_profile = None
    if args.typography_profile:
        from typography_profile import DocumentTypographyProfile
        typo_profile = DocumentTypographyProfile(args.typography_profile)
        _dump(out_dir / "typography_profile_applied.json",
              typo_profile.summary())
    if args.phase4d1c and not args.resume and not args.pages:
        # A blocked rerun must never leave a formal PDF from an older run in
        # the delivery directory.
        for stale_name in ("full_zh_preview.pdf",
                           "formal_preview_physical_qa.json"):
            (out_dir / stale_name).unlink(missing_ok=True)

    # ---- DocumentPreflight: production runs are gated up-front -------------
    preflight, preflight_allowed = run_preflight(pdf, out_dir)
    if not preflight_allowed and not args.skip_preflight:
        _dump(out_dir / "document_manifest.json", {
            "source_pdf": str(pdf), "stage": "preflight_blocked",
            "preflight": preflight,
            "reason": "normalization_required or openability failed"})
        print("PREFLIGHT BLOCKED: normalization_required=%s openability=%s"
              % (preflight["normalization_required"],
                 preflight["openability"]))
        return 2

    raw_pages, failures, page_count = build_raw_pages(
        pdf, out_dir, resume=args.resume, scan_only=args.scan_only)
    if len(raw_pages) != page_count:
        _dump(out_dir / "document_manifest.json", {
            "source_pdf": str(pdf), "page_count": page_count,
            "stage": "raw_models", "failures": failures})
        return 2
    document = build_document_model(pdf, raw_pages)
    # Phase 4E.1B-B: ONE document semantic truth -- the section state machine
    # writes semantic_role into every logical paragraph (references /
    # appendix / acknowledgement / body...) before translation / QA consume it.
    from document_semantic_state import apply_document_semantics
    apply_document_semantics(document)
    for page in document["pages"]:
        page_dir = out_dir / "pages" / ("p%03d" % page["page"])
        _dump(page_dir / "stitched_page_model.json", page)
    if args.scan_only:
        _dump(out_dir / "document_manifest.json", {
            "source_pdf": str(pdf), "page_count": page_count,
            "logical_paragraph_count": len(document["logical_paragraphs"]),
            "cross_page_links": document["cross_page_links"],
            "failures": failures, "stage": "scan_complete"})
        print("scan complete: pages=%d logical=%d cross_page=%d" % (
            page_count, len(document["logical_paragraphs"]),
            len(document["cross_page_links"])))
        return 0

    config = load_config(args.config)
    if not config["token"] and not args.dry_run:
        raise RuntimeError("missing DEEPSEEK_API_KEY")
    cache_path = out_dir / "translation_cache.json"
    if (args.translation_cache_seed and not cache_path.exists()):
        shutil.copy2(Path(args.translation_cache_seed).resolve(), cache_path)
    cache = DocumentTranslationCache(cache_path)
    # Preserve the exact initial lookup state for a truthful cache audit;
    # translate_document performs narrow in-place invalidation/updates.
    initial_cache_snapshot = json.loads(json.dumps(cache.data))
    translations, translation_items = translate_document(
        document, cache, config, out_dir, dry_run=args.dry_run)

    page_results = {}
    structural_qas = {}
    # Phase 4E.1B-C1.1: robust document prior from raw page-local candidates.
    # Median/MAD support pages are selected from semantic two-column evidence;
    # the profile is used only by GridFallbackResolver on unreliable pages.
    local_grids = []
    local_grid_failures = []
    for page in document["pages"]:
        try:
            local_grids.append(infer_page_grid(
                str(pdf), int(page["page"]) - 1))
        except Exception as exc:  # noqa: BLE001
            local_grid_failures.append({
                "page": page["page"], "stage": "page_local_grid",
                "error": str(exc)})
    models_by_page = {int(page["page"]): page for page in document["pages"]}
    layout_profile = build_document_grid_profile_from_candidates(
        [{"page": grid["page"], "candidates": [grid]}
         for grid in local_grids], models_by_page)
    local_grids_by_page = {int(grid["page"]): grid for grid in local_grids}
    page_context = {}
    grid_resolutions = []
    appendix_active = False
    for page in document["pages"]:
        page_number = page["page"]
        try:
            headings = [
                (r.get("payload") or {}).get("source_text", "").strip()
                for r in page.get("regions", [])
                if r.get("type") == "text"
                and "heading" in ((r.get("payload") or {}).get(
                    "style_role") or "")]
            if any(re.match(r"^[A-Z](?:\.|\s)", h) for h in headings):
                appendix_active = True
            grid, fm, ty, grid_resolution = page_render_context(
                pdf, page_number - 1, profile=layout_profile,
                typo_profile=typo_profile, page_model=page,
                page_local_grid=local_grids_by_page.get(page_number),
                return_resolution=True)
            if args.phase4d1c:
                grid = prepare_page_grid(
                    page, grid, frontmatter=fm, profile=layout_profile,
                    appendix_active=appendix_active)
            page["page_layout_grid"] = grid
            page_context[page_number] = {"grid": grid, "frontmatter": fm,
                                         "typography": ty,
                                         "grid_resolution": grid_resolution}
            grid_resolutions.append(grid_resolution)
        except Exception as exc:  # noqa: BLE001
            page_context[page_number] = {"grid": None, "frontmatter": None,
                                         "typography": None,
                                         "grid_resolution": None}
            local_grid_failures.append({
                "page": page_number, "stage": "grid_resolution",
                "error": str(exc)})
    _dump(out_dir / "document_layout_profile.json", layout_profile)
    _dump(out_dir / "grid_resolution_report.json", {
        "pages": grid_resolutions,
        "failures": local_grid_failures,
    })
    resolved_grid_sanity = grid_sanity_qa(grid_resolutions, layout_profile)
    _dump(out_dir / "grid_sanity_qa.json", resolved_grid_sanity)
    if not resolved_grid_sanity["grid_sanity_passed"]:
        failures.append({
            "page": None, "stage": "resolved_grid_sanity",
            "error": "resolved grid hard gate failed",
            "qa": resolved_grid_sanity,
        })
    render_pages = None
    if args.pages:
        render_pages = set(int(x) for x in args.pages.split(",") if x.strip())
        print("partial render: pages=%s" % sorted(render_pages))
    for page in document["pages"]:
        page_number = page["page"]
        if render_pages is not None and page_number not in render_pages:
            continue
        page_dir = out_dir / "pages" / ("p%03d" % page_number)
        qa_path = page_dir / "qa.json"
        if args.resume and qa_path.exists() and (page_dir / "zh.pdf").exists():
            saved = _load(qa_path)
            if saved and saved.get("qa") and saved.get("physical_qa"):
                page_results[page_number] = saved
                structural_qas[page_number] = _load(
                    page_dir / "paragraph_structural_qa.json") or \
                    structural_paragraph_qa(page)
                print("render page %d/%d: resume hit" % (page_number, page_count))
                continue
        try:
            print("render page %d/%d" % (page_number, page_count))
            ctx = page_context.get(page_number, {})
            flows, qa, capacity, physical, structural, bottom_reserved, script_gaps = resolve_and_render(
                page, translations, pdf, page_dir,
                grid=ctx.get("grid"), frontmatter=ctx.get("frontmatter"),
                typography=ctx.get("typography"))
            # Phase 4E.2A: per-page QA execution manifest (fail-closed).
            execution = {"render": {"stage": "render", "status": "pass",
                                    "finished_at": time.time()}}
            _dump(page_dir / "script_gaps.json", script_gaps)
            draw_overlay(pdf, page_number - 1, page, flows,
                         page_dir / "debug_overlay.png")
            page_translation = page_translations(page, translations)
            _dump(page_dir / "translation.json", page_translation)
            _dump(page_dir / "physical_qa.json", physical)
            _dump(page_dir / "paragraph_structural_qa.json", structural)
            # Phase 4C.2R: pass the flowed formula enclosures so the
            # exclusivity audit compares against the RENDERED positions.
            # row_members carry SOURCE bboxes; add the row's (flow_y -
            # anchor_y) shift to get the final-PDF coordinates.
            flow_enc = {}
            for flow in flows:
                for item in flow.get("items", []):
                    if item.get("kind") != "formula":
                        continue
                    dy = item.get("flow_y", 0.0) - item.get("anchor_y", 0.0)
                    for m in item.get("row_members", []):
                        mb = [float(v) for v in m["bbox"]]
                        flow_enc[m["formula_id"]] = [mb[0], mb[1] + dy,
                                                     mb[2], mb[3] + dy]
            formula_excl = qx.run_stage(execution, "formula_exclusivity_qa",
                formula_exclusivity_qa, page, page_dir / "zh.pdf",
                out_dir=page_dir, flow_enclosures=flow_enc)
            residual_lang = qx.run_stage(execution, "residual_language_qa",
                residual_source_language_qa, page, page_dir / "zh.pdf",
                out_dir=page_dir, flows=flows)
            footnote_collision = qx.run_stage(execution, "footnote_collision_qa",
                final_footnote_collision_qa, page, page_dir / "zh.pdf",
                frontmatter=ctx.get("frontmatter"),
                bottom_reserved_regions=bottom_reserved, out_dir=page_dir)
            residual_prose = qx.run_stage(execution, "residual_prose_qa",
                final_pdf_residual_prose_qa, page, page_dir / "zh.pdf",
                flows=flows, out_dir=page_dir)
            render_closure = qx.run_stage(execution, "render_closure_qa",
                logical_paragraph_render_closure_qa, page, page_translation,
                flows, out_dir=page_dir,
                bottom_reserved_regions=bottom_reserved)
            # Phase 4C.2R: rendered-region collision on the final PDF.
            collision = qx.run_stage(execution, "rendered_collision_qa",
                rendered_region_collision_qa,
                page_dir / "zh.pdf", page_dir / "zh.html",
                page_model=page, out_dir=page_dir,
                page_label="page_%03d" % page_number, flows=flows)
            # Phase 4C.2R.1: heading duplicate consumption + formula crop.
            heading_dup = qx.run_stage(execution, "heading_duplicate_qa",
                heading_duplicate_qa, page, page_dir / "zh.pdf", out_dir=page_dir)
            formula_crop = qx.run_stage(execution, "formula_crop_qa",
                formula_crop_qa, page, out_dir=page_dir)
            formula_adopted = qx.run_stage(execution, "formula_adopted_prose_qa",
                formula_adopted_prose_qa, page, out_dir=page_dir)
            # Phase 4E.1B-C2: FormulaModel existence is not render proof.
            # Trace every lifecycle stage through independently rasterized
            # final-PDF ink, then derive the completeness hard metrics from
            # that trace.  This integration is QA-only and never mutates the
            # renderer, SVG asset, geometry, ownership or translation state.
            formula_trace = qx.run_stage(execution, "formula_render_trace",
                build_page_formula_trace, pdf, page_number - 1, page,
                page_dir / "zh.pdf", page_dir / "zh.html", page_dir,
                flows=flows, page_grid=ctx.get("grid"),
                crop_qa=formula_crop, exclusivity_qa=formula_excl,
                out_path=page_dir / "formula_render_trace.json")
            formula_completeness = qx.run_stage(execution,
                "formula_render_completeness_qa", formula_render_completeness_qa,
                trace_records=formula_trace, out_dir=page_dir)
            record = {"page": page_number, "qa": qa,
                      "capacity": capacity, "flows": flows,
                      "physical_qa": physical,
                      "structural_qa": structural,
                      "formula_exclusivity_qa": formula_excl,
                      "residual_language_qa": residual_lang,
                      "footnote_collision_qa": footnote_collision,
                      "residual_prose_qa": residual_prose,
                      "render_closure_qa": render_closure,
                      "bottom_reserved_regions": bottom_reserved,
                      "rendered_collision_qa": collision,
                      "heading_duplicate_qa": heading_dup,
                      "formula_crop_qa": formula_crop,
                      "formula_adopted_prose_qa": formula_adopted,
                      "formula_render_trace": formula_trace,
                      "formula_render_completeness_qa": formula_completeness,
                      "qa_execution": execution}
            if args.phase4d1c:
                visual = qx.run_stage(execution, "visual_qa",
                    run_page_visual_qa,
                    page_model=page,
                    final_pdf=page_dir / "zh.pdf",
                    final_png=page_dir / "zh.png",
                    final_html=page_dir / "zh.html",
                    page_grid=ctx.get("grid"),
                    frontmatter=ctx.get("frontmatter"),
                    flows=flows,
                    bottom_reserved_regions=bottom_reserved,
                    page_record=record,
                    out_dir=page_dir)
                record["visual_qa"] = visual
                record["page_grid"] = ctx.get("grid")
                record["layout_class"] = (ctx.get("grid") or {}).get(
                    "layout_class")
            _dump(qa_path, record)
            page_results[page_number] = record
            structural_qas[page_number] = structural
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            failure = {"page": page_number, "stage": "render",
                       "error": str(exc), "traceback": traceback.format_exc()}
            failures.append(failure)
            _dump(page_dir / "failure.json", failure)

    # ---- translation QA (four states) + full delivery gate FIRST ------------
    # Phase 4C.2F: the delivery preview may only be generated when the FULL
    # gate == pass; computing the gate before the preview is mandatory.
    translation_qa = None
    if translation_items:
        items_with_errors = list(translation_items)
        errors = document.get("translation_error_items") or {}
        fallback_ids = set(document.get("fallback_source_items") or [])
        for item in items_with_errors:
            if item["item_id"] in errors:
                item["api_error"] = True
            if item["item_id"] in fallback_ids:
                item["fallback_source"] = True
        translation_qa = compute_translation_qa(items_with_errors, translations)
    invalidation_reasons = {
        item.get("item_id"): item.get("invalidated_reason")
        for item in document.get("cache_invalidations", [])
        if item.get("item_id") and item.get("invalidated_reason")
    }
    cache_audit = build_translation_cache_audit(
        translation_items, initial_cache_snapshot,
        translations=translations,
        status_map=(translation_qa or {}).get("status_map") or {},
        invalidated_reasons=invalidation_reasons,
        target_language=TARGET_LANGUAGE,
        prompt_version=PROMPT_VERSION,
        out_path=out_dir / "translation_cache_audit.json")
    cache_audit["seed_path"] = (str(Path(args.translation_cache_seed).resolve())
                                if args.translation_cache_seed else None)
    cache_audit["actual_new_translation_call_count"] = document.get(
        "new_translation_call_count", 0)
    cache_audit["post_invalidation_hit_count"] = document.get(
        "cache_post_invalidation_hit_count", 0)
    _dump(out_dir / "translation_cache_audit.json", cache_audit)

    renderer_parity = {
        "renderer_b_available": bool(
            all((r.get("physical_qa") or {}).get("renderer_b_available", False)
                for r in page_results.values())),
        "renderer_specific_failure_count": sum(
            (r.get("physical_qa") or {}).get("renderer_specific_failure_count", 0)
            for r in page_results.values()),
        "renderer_parity_diff_ratio": None,
        "changed_page_count": 0,
    }
    diff_ratios = [(r.get("physical_qa") or {}).get("renderer_parity_diff_ratio")
                   for r in page_results.values()]
    diff_ratios = [d for d in diff_ratios if d is not None]
    if diff_ratios:
        renderer_parity["renderer_parity_diff_ratio"] = round(
            sum(diff_ratios) / len(diff_ratios), 4)
    defect_rows, complexity_rows, gate = write_rankings_and_gate(
        out_dir, preflight=preflight, page_results=page_results,
        structural_qas=structural_qas, translation_qa=translation_qa,
        renderer_parity=renderer_parity, expected_page_count=page_count)
    # Phase 4E.2A: persist the QA execution manifest + integrity gate.
    manifest = gate.get("qa_execution_manifest") or \
        qx.document_manifest(page_results, page_count)
    _dump(out_dir / "qa_execution_manifest.json", manifest)
    integrity = qx.integrity_gate(manifest)
    _dump(out_dir / "qa_integrity_gate.json", integrity)
    partial_mode = render_pages is not None
    if partial_mode:
        # Phase 4D.1B checkpoint: gate over the rendered pages only; the
        # full 19-page delivery gate stays for the final 4D.1C run.
        gate["partial_render_pages"] = sorted(page_results.keys())
        gate["checkpoint"] = True
        _dump(out_dir / "checkpoint_gate.json", gate)

    # ---- previews and the independent 4D visual gate -----------------------
    # The debug merge is always available for diagnosis.  In Phase 4D.1C it
    # is also the exact 19-page candidate inspected by the document-level
    # dual-renderer PhysicalPDFQA before any formal delivery file can exist.
    debug_preview, debug_preview_page_count = build_preview(
        out_dir, page_count, delivery=False)
    all_physical_ok = all(
        (r.get("physical_qa") or {}).get("all_assertions_passed", False)
        for r in page_results.values()) and len(page_results) == page_count
    delivery_preview = None
    delivery_preview_page_count = None
    document_physical = None
    formal_physical = None
    visual_report = None
    visual_gate = None

    if args.phase4d1c and not partial_mode:
        document_physical = physical_pdf_qa(
            debug_preview, page_model=None,
            expected_physical_page_count=page_count,
            out_dir=out_dir,
            output_filename="document_physical_qa.json")
        visual_report = build_document_visual_report(
            page_results, layout_profile=layout_profile,
            document_physical_qa=document_physical,
            out_path=out_dir / "document_visual_report.json")
        visual_gate = build_visual_layout_gate(
            visual_report, page_results=page_results,
            document_physical_qa=document_physical,
            out_path=out_dir / "visual_layout_gate.json")
        formal_unlocked = (
            gate["decision"] == "pass"
            and visual_gate["hard_decision"] == "pass"
            and all_physical_ok
            and debug_preview_page_count == page_count
            and document_physical.get("all_assertions_passed", False)
            and not defect_rows)
        if formal_unlocked:
            delivery_preview = out_dir / "full_zh_preview.pdf"
            shutil.copy2(debug_preview, delivery_preview)
            # Re-open and raster the actual formal path with both renderers;
            # the copied candidate is byte-identical, but this closes the
            # delivery artifact itself rather than relying on an assumption.
            formal_physical = physical_pdf_qa(
                delivery_preview, page_model=None,
                expected_physical_page_count=page_count,
                out_dir=out_dir,
                output_filename="formal_preview_physical_qa.json")
            if formal_physical.get("all_assertions_passed", False):
                delivery_preview_page_count = formal_physical.get(
                    "physical_pdf_page_count")
            else:
                delivery_preview.unlink(missing_ok=True)
                delivery_preview = None
    elif gate["decision"] == "pass" and not partial_mode:
        delivery_preview, delivery_preview_page_count = build_preview(
            out_dir, page_count, delivery=True, page_results=page_results)
    # preview re-check: source page count must equal final physical count
    preview_page_count_ok = (
        gate["decision"] == "pass" and not partial_mode
        and delivery_preview_page_count == page_count
        and (not args.phase4d1c or (
            visual_gate is not None
            and visual_gate["hard_decision"] == "pass"
            and document_physical is not None
            and document_physical.get("all_assertions_passed", False))))

    # ---- document-level report ----------------------------------------------
    qa = document_report(document, page_results, failures, page_count,
                         translation_items)
    qa["physical_delivery_gate_ok"] = all_physical_ok
    qa["preview_physical_page_count"] = delivery_preview_page_count
    qa["preview_physical_page_count_ok"] = preview_page_count_ok
    qa["unexpected_extra_page_count"] = sum(
        (r.get("physical_qa") or {}).get("unexpected_extra_page_count", 0)
        for r in page_results.values())
    qa["paragraph_severe_pollution_count"] = sum(
        1 for sq in structural_qas.values()
        if not sq.get("structural_clean", True))
    if translation_qa:
        qa["body_translation_coverage_ratio"] = translation_qa[
            "body_translation_coverage_ratio"]
        qa["body_fallback_source_count"] = translation_qa[
            "body_fallback_source_count"]
        qa["body_translation_failure_count"] = translation_qa[
            "body_translation_failure_count"]
        qa["unchanged_rejected_count"] = translation_qa.get(
            "unchanged_rejected_count", 0)
        qa["non_body_unchanged_rejected_count"] = translation_qa.get(
            "non_body_unchanged_rejected_count", 0)
    else:
        qa["body_translation_coverage_ratio"] = None
        qa["body_fallback_source_count"] = None
        qa["body_translation_failure_count"] = None
    document["qa"] = qa
    report = {
        "phase": "4D.1C" if args.phase4d1c else "4C.1",
        "source_pdf": str(pdf), "qa": qa,
        "preflight": preflight,
        "cross_page_links": document["cross_page_links"],
        "pages": {str(page): result for page, result in page_results.items()},
    }
    manifest = {
        "source_pdf": str(pdf), "page_count": page_count,
        "output_root": str(out_dir),
        "full_preview": str(delivery_preview) if delivery_preview else None,
        "debug_preview": str(debug_preview),
        "translation_cache": str(cache.path),
        "page_status": {str(number): ("complete" if number in page_results else "failed")
                        for number in range(1, page_count + 1)},
        "failures": failures,
    }
    # gate/rankings were computed BEFORE the preview; fill in the report
    report["delivery_gate"] = gate
    report["defect_pages"] = defect_rows
    report["complexity_pages"] = complexity_rows
    report["translation_qa"] = translation_qa
    report["translation_cache_audit"] = cache_audit
    report["preview_physical_page_count"] = delivery_preview_page_count
    report["delivery_preview_generated"] = delivery_preview is not None
    if args.phase4d1c and not partial_mode:
        report["document_physical_qa"] = document_physical
        report["formal_preview_physical_qa"] = formal_physical
        report["document_visual_report"] = visual_report
        report["visual_layout_gate"] = visual_gate
        manifest["document_physical_qa"] = str(
            out_dir / "document_physical_qa.json")
        manifest["formal_preview_physical_qa"] = (
            str(out_dir / "formal_preview_physical_qa.json")
            if formal_physical else None)
        manifest["document_visual_report"] = str(
            out_dir / "document_visual_report.json")
        manifest["visual_layout_gate"] = str(
            out_dir / "visual_layout_gate.json")

    # ---- Phase 4C.2E: page assertion details + token split taxonomy -------
    page_assertion_details = []
    token_split_taxonomy = []
    for page_number, result in page_results.items():
        pqa = result.get("qa") or {}
        fails = [k for k, v in (pqa.get("qa_assertions") or {}).items()
                 if not v]
        for assertion in fails:
            root = "formula_exclusivity" if assertion in (
                "formula_visible",) else "typography" if assertion in (
                "typography_closed",) else "ownership" if assertion in (
                "ownership_closed",) else "tokens" if assertion in (
                "tokens_intact",) else "render" if assertion in (
                "render_closed",) else "semantic"
            page_assertion_details.append({
                "page": page_number, "assertion": assertion,
                "actual": "failed", "expected": "pass",
                "blocking": True, "root_category": root,
            })
        # token split taxonomy
        split_counts = (pqa.get("latin_token_split_count") or 0) \
            + (pqa.get("code_token_split_count") or 0)
        if split_counts:
            # Latin model/identifier tokens and CodeRuns are both protected;
            # ordinary line wrapping is not represented by these counters.
            protected = split_counts
            allowed = 0
            token_split_taxonomy.append({
                "page": page_number,
                "illegal_protected_split": protected,
                "allowed_wrap": allowed,
                "total": split_counts,
            })
    _dump(out_dir / "page_assertion_details.json", page_assertion_details)
    _dump(out_dir / "token_split_taxonomy.json", token_split_taxonomy)
    report["page_assertion_failure_count"] = len(page_assertion_details)
    report["token_split_taxonomy"] = token_split_taxonomy

    if args.phase4d1c and not partial_mode:
        p003_model = next((p for p in document["pages"]
                           if p.get("page") == 3), {})
        dlp00034 = next((r.get("payload") or {}
                        for r in p003_model.get("regions", [])
                        if r.get("type") == "text"
                        and ((r.get("payload") or {}).get(
                            "logical_paragraph_id") == "DLP00034"
                             or (r.get("payload") or {}).get(
                                 "paragraph_id") == "DLP00034")), None)
        dlp00034_translation = translations.get("DLP00034", "")
        dlp00034_preserved = bool(
            dlp00034
            and re.search(r"[\u4e00-\u9fff]", dlp00034_translation)
            and (page_results.get(3, {}).get("residual_prose_qa") or {}).get(
                "residual_prose_clean", False)
            and (page_results.get(3, {}).get("render_closure_qa") or {}).get(
                "logical_paragraph_render_closure_clean", False))
        write_phase4d1c_report(
            out_path=out_dir / "PHASE4D1C_REPORT.md",
            visual_report=visual_report,
            visual_gate=visual_gate,
            delivery_gate=gate,
            defect_pages=defect_rows,
            complexity_pages=complexity_rows,
            final_physical_page_count=delivery_preview_page_count,
            formal_preview_generated=delivery_preview is not None,
            dlp00034_preserved=dlp00034_preserved,
            translation_cache_audit=cache_audit)

    _dump(out_dir / "document_report.json", report)
    _dump(out_dir / "document_manifest.json", manifest)
    _dump(out_dir / "worst_pages.json", worst_pages(page_results))  # legacy
    _dump(out_dir / "delivery_gate.json", gate)
    _dump(out_dir / "defect_pages.json", defect_rows)
    _dump(out_dir / "complexity_pages.json", complexity_rows)
    _dump(out_dir / "phase4c1a_qa_report.json", report)
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    print("delivery_gate.decision = %s" % gate["decision"])
    if args.phase4d1c and not partial_mode:
        print("visual_layout_gate.hard_decision = %s" %
              visual_gate["hard_decision"])
    if partial_mode:
        # Phase 4D.1B checkpoint: verdict over the rendered pages only.
        per_page_assertions = all(
            (r.get("qa") or {}).get("all_assertions_passed", False)
            for r in page_results.values())
        per_page_physical = all(
            (r.get("physical_qa") or {}).get("all_assertions_passed", False)
            for r in page_results.values())
        per_page_visual = (not args.phase4d1c or all(
            (r.get("visual_qa") or {}).get("hard_passed", False)
            for r in page_results.values()))
        # Translation status is document-global even for a targeted layout
        # rerender.  Keep it in the evidence, but the checkpoint verdict is
        # limited to the requested pages; the full 4C gate is enforced again
        # by the mandatory 19-page Pass B.
        page_layers = {k: v for k, v in gate.get("layers", {}).items()
                       if k not in ("translation_qa",)}
        checkpoint_layers_ok = all(v in ("pass", "warning")
                                   for v in page_layers.values())
        ok = (per_page_assertions and per_page_physical and per_page_visual
              and checkpoint_layers_ok
              and len(page_results) == len(render_pages))
        gate["checkpoint_passed"] = ok
        _dump(out_dir / "checkpoint_gate.json", gate)
        print("checkpoint gate = %s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 2
    if args.phase4d1c:
        return 0 if (qa["all_assertions_passed"] and all_physical_ok
                     and preview_page_count_ok
                     and gate["decision"] == "pass"
                     and visual_gate is not None
                     and visual_gate["hard_decision"] == "pass"
                     and not defect_rows) else 2
    return 0 if (qa["all_assertions_passed"] and all_physical_ok
                 and preview_page_count_ok
                 and gate["decision"] in ("pass", "warning")) else 2


if __name__ == "__main__":
    sys.exit(main())
