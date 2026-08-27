"""Frozen-HTML and mock-provider smoke for fast-v05C.

No PDF is opened, no page is rasterized, Chromium is not launched, and the
real translation provider is never called.
"""
from __future__ import annotations

import copy
import inspect
import json
import re
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

OUT = REPO / "outputs" / "fast_v05c"
FROZEN = REPO / "outputs" / "fast_e2e" / "PPAT_run3"
FROZEN_MODEL = (
    FROZEN / "_source_chain" / "pages" / "p001"
    / "stitched_page_model.json")
FROZEN_GRID = (
    FROZEN / "_source_chain" / "pages" / "p001" / "page_grid.json")
FROZEN_HTML = (
    FROZEN / "pages" / "p001" / "_typography_fast_path"
    / "source_slot_baseline.html")
FIXTURE_IDS = ("DLP00002", "DLP00004")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class _IdentifiedElementParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict[str, str]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {name: value or "" for name, value in attrs}
        if values.get("data-render-id"):
            self.elements.append(values)


def _render_after_fixture(
    page_model: dict[str, Any], grid: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Exercise the production specialized renderer on frozen p001 data."""
    from html_render import _render_frontmatter_blocks
    from typography import build_typography

    model_before = copy.deepcopy(page_model)
    regions = [row for row in page_model.get("regions") or []
               if row.get("type") == "text"]
    para_map = {
        row["payload"]["paragraph_id"]: row for row in regions
        if row["payload"].get("paragraph_id") in FIXTURE_IDS}
    translations = {
        pid: str((para_map[pid]["payload"].get("translated_text")
                  or para_map[pid]["payload"].get("source_text") or ""))
        for pid in FIXTURE_IDS}

    flow_items_by_pid: dict[str, list[dict[str, Any]]] = {}
    applied_expected: dict[str, dict[str, float]] = {}
    for index, pid in enumerate(FIXTURE_IDS):
        para = para_map[pid]["payload"]
        fragment = copy.deepcopy((para.get("source_fragments") or [])[0])
        source_font = float(fragment.get("base_font_size") or 10.0)
        source_line = round(source_font * 1.28, 6)
        font_scale = 0.94 + index * 0.01
        line_scale = 0.92 + index * 0.01
        fill_font_scale = 1.01
        fill_line_scale = 1.02
        fragment.update({
            "kind": "paragraph",
            "paragraph_id": pid,
            "geometry_locked": True,
            "source_slot_bbox": copy.deepcopy(fragment["bbox"]),
            "source_slot_font_size": source_font,
            "source_slot_line_height": source_line,
            "local_typography_fit_level": "L2",
            "local_typography_font_scale": font_scale,
            "local_typography_line_height_scale": line_scale,
            "local_typography_fill_level": "F1",
            "local_typography_fill_font_scale": fill_font_scale,
            "local_typography_fill_line_height_scale": fill_line_scale,
            "local_typography_fill_applied": True,
            "source_slot_content_top_offset": 0.25,
            "render_source": "canonical_target",
            "render_source_reason": "normal_translation",
        })
        if pid == "DLP00004":
            fragment["source_paragraph_segments"] = [{
                "text": translations[pid],
                "source_style": {"first_line_indent_em": 0.5},
            }]
        flow_items_by_pid[pid] = [fragment]
        applied_expected[pid] = {
            "font_size": round(source_font * font_scale
                               * fill_font_scale, 3),
            "line_height": round(source_line * line_scale
                                 * fill_line_scale, 3),
            "text_indent_em": 0.5 if pid == "DLP00004" else 0.0,
        }

    figure_bbox = next(
        copy.deepcopy(row["bbox"])
        for row in page_model.get("regions") or []
        if row.get("type") == "figure")
    author_bbox = para_map["DLP00002"]["payload"]["bbox"]
    frontmatter = {
        "authors": {
            "bbox": {
                "x0": author_bbox[0], "y0": author_bbox[1],
                "x1": author_bbox[2], "y1": author_bbox[3],
            },
        },
        "figure": {"bbox": figure_bbox},
    }
    roles = {"DLP00002": "authors", "DLP00004": "caption"}
    ty = build_typography()
    with tempfile.TemporaryDirectory(prefix="fast_v05c_identity_") as tmp:
        parts = _render_frontmatter_blocks(
            para_map, roles, translations, grid, frontmatter, ty,
            {}, "", float(page_model["width"]), float(page_model["height"]),
            Path(tmp), None,
            flow_items_by_pid=flow_items_by_pid)
    html = "<html><body>%s</body></html>" % "".join(parts)

    # The renderer received live model/flow objects; assert that it changed
    # neither source slot bboxes nor any hard anchor.
    slot_bboxes_after = {
        pid: copy.deepcopy(flow_items_by_pid[pid][0]["source_slot_bbox"])
        for pid in FIXTURE_IDS}
    slot_bboxes_before = {
        pid: copy.deepcopy((para_map[pid]["payload"].get(
            "source_fragments") or [])[0]["bbox"])
        for pid in FIXTURE_IDS}
    hard_before = [
        copy.deepcopy(row.get("bbox"))
        for row in model_before.get("regions") or []
        if row.get("type") in {"figure", "table", "formula"}]
    hard_after = [
        copy.deepcopy(row.get("bbox"))
        for row in page_model.get("regions") or []
        if row.get("type") in {"figure", "table", "formula"}]
    return html, {
        "slot_geometry_mutation_count": int(
            slot_bboxes_before != slot_bboxes_after),
        "hard_anchor_moved_count": int(hard_before != hard_after),
        "applied_typography_expected": applied_expected,
    }


def _typography_writeback_evidence(
    html: str, expected: dict[str, dict[str, float]],
) -> dict[str, Any]:
    parser = _IdentifiedElementParser()
    parser.feed(html)
    by_id = {row["data-render-id"]: row for row in parser.elements}
    records = []
    mismatch = 0
    for render_id, target in expected.items():
        attrs = by_id[render_id]
        style = attrs.get("style", "")
        def value(name: str, unit: str) -> float:
            match = re.search(
                rf"(?:^|;)\s*{re.escape(name)}:([-+0-9.]+){unit}", style)
            if not match:
                raise AssertionError(f"{name} missing from {render_id}")
            return float(match.group(1))
        actual = {
            "font_size": value("font-size", "pt"),
            "line_height": value("line-height", "pt"),
            "text_indent_em": value("text-indent", "em"),
        }
        row_mismatch = any(
            abs(actual[key] - target[key]) > 0.002 for key in target)
        mismatch += int(row_mismatch)
        records.append({
            "render_id": render_id,
            "specialized_role": attrs.get("data-role"),
            "flow_fragment_id": attrs.get("data-flow-fragment"),
            "expected": target,
            "actual": actual,
            "typography_writeback_match": not row_mismatch,
        })
    return {"typography_writeback_mismatch_count": mismatch,
            "records": records}


def _cache_disabled_smoke() -> dict[str, Any]:
    import run_document
    import run_fast_e2e
    from fast_e2e_pipeline import run_fast_e2e as e2e_pipeline
    from fast_source_preparation import prepare_fast_source_chain

    class ForbiddenCache:
        def __getattribute__(self, name: str) -> Any:
            if name.startswith("__"):
                return object.__getattribute__(self, name)
            raise AssertionError(f"disabled cache was accessed: {name}")

    document = {
        "logical_paragraphs": [{
            "logical_paragraph_id": "MOCK_LP1",
            "source_text": "Translate this sentence.",
            "translation_source_text": "Translate this sentence.",
            "style_role": "body",
            "semantic_role": "body",
            "source_fragments": [{
                "flow_fragment_id": "MOCK_LP1-F0",
                "source_text": "Translate this sentence.",
            }],
        }],
        "pages": [{
            "page": 1,
            "regions": [{
                "type": "text",
                "payload": {
                    "paragraph_id": "MOCK_LP1",
                    "logical_paragraph_id": "MOCK_LP1",
                    "source_text": "Translate this sentence.",
                    "source_fragments": [{
                        "flow_fragment_id": "MOCK_LP1-F0",
                        "source_text": "Translate this sentence.",
                    }],
                },
            }],
        }],
    }
    provider_items: list[str] = []

    def provider(items, **_kwargs):
        provider_items.extend(str(row["item_id"]) for row in items)
        return {str(row["item_id"]): "这是模拟译文。" for row in items}

    with tempfile.TemporaryDirectory(prefix="fast_v05c_cache_") as tmp:
        translations, _ = run_document.translate_document(
            document,
            ForbiddenCache(),
            {"token": "mock", "base_url": "mock", "model": "mock"},
            Path(tmp),
            dry_run=False,
            cache_enabled=False,
            translation_provider=provider)
        cache_files = list(Path(tmp).rglob("*translation*cache*"))

    cli_args = run_fast_e2e.build_parser().parse_args([
        "--input", "mock.pdf", "--output-dir", "mock-output"])
    cli_opt_in = run_fast_e2e.build_parser().parse_args([
        "--input", "mock.pdf", "--output-dir", "mock-output",
        "--translation-cache", "on"])
    defaults = {
        "translate_document": inspect.signature(
            run_document.translate_document).parameters[
                "cache_enabled"].default,
        "prepare_fast_source_chain": inspect.signature(
            prepare_fast_source_chain).parameters[
                "translation_cache_enabled"].default,
        "run_fast_e2e": inspect.signature(e2e_pipeline).parameters[
            "translation_cache_enabled"].default,
        "cli": cli_args.translation_cache,
    }
    metrics = {
        "translation_cache_enabled": bool(
            document.get("translation_cache_enabled")),
        "cache_lookup_count": int(document.get("cache_lookup_count") or 0),
        "cache_read_count": int(document.get("cache_read_count") or 0),
        "cache_write_count": int(document.get("cache_write_count") or 0),
        "cache_hit": int(document.get("cache_hit") or 0),
        "provider_item_count": len(provider_items),
        "cache_file_created_count": len(cache_files),
    }
    expected_zero = (
        "cache_lookup_count", "cache_read_count", "cache_write_count",
        "cache_hit", "cache_file_created_count")
    default_off = (
        defaults["translate_document"] is False
        and defaults["prepare_fast_source_chain"] is False
        and defaults["run_fast_e2e"] is False
        and defaults["cli"] == "off")
    explicit_opt_in_available = cli_opt_in.translation_cache == "on"
    decision = (
        not metrics["translation_cache_enabled"]
        and all(metrics[key] == 0 for key in expected_zero)
        and metrics["provider_item_count"] == 1
        and provider_items == ["MOCK_LP1"]
        and translations.get("MOCK_LP1") == "这是模拟译文。"
        and default_off
        and explicit_opt_in_available)
    return {
        "schema_version": "fast.v05c.translation_cache_disabled_qa.v1",
        "decision": "pass" if decision else "fail",
        **metrics,
        "provider_item_ids": provider_items,
        "provider_received_required_item": provider_items == ["MOCK_LP1"],
        "real_translation_api_call_count": 0,
        "default_switches": defaults,
        "all_normal_entry_defaults_off": default_off,
        "cache_implementation_deleted": False,
        "explicit_opt_in_available": explicit_opt_in_available,
    }


def main() -> int:
    from specialized_render_identity_qa import (
        audit_specialized_render_identity,
        expected_specialized_fragments,
    )

    missing = [path for path in (FROZEN_MODEL, FROZEN_GRID, FROZEN_HTML)
               if not path.is_file()]
    if missing:
        raise FileNotFoundError("frozen PPAT p001 evidence missing: %s"
                                % ", ".join(map(str, missing)))

    page_model = _load(FROZEN_MODEL)
    grid = _load(FROZEN_GRID)
    frozen_html = FROZEN_HTML.read_text(encoding="utf-8")
    expected = expected_specialized_fragments(page_model, FIXTURE_IDS)
    before = audit_specialized_render_identity(frozen_html, expected)
    assert before["hard_metrics"][
        "specialized_render_identity_missing_count"] > 0
    assert before["hard_metrics"]["typography_dom_missing_count"] > 0
    assert {row["flow_fragment_id"]
            for row in before["typography_dom_missing"]} == {
                "DLP00002-F0", "DLP00004-F0"}

    after_html, invariants = _render_after_fixture(page_model, grid)
    after = audit_specialized_render_identity(after_html, expected)
    after["hard_metrics"]["slot_geometry_mutation_count"] = invariants[
        "slot_geometry_mutation_count"]
    after["hard_metrics"]["hard_anchor_moved_count"] = invariants[
        "hard_anchor_moved_count"]
    writeback = _typography_writeback_evidence(
        after_html, invariants["applied_typography_expected"])
    after["typography_writeback"] = writeback
    after["decision"] = "pass" if (
        all(value == 0 for value in after["hard_metrics"].values())
        and writeback["typography_writeback_mismatch_count"] == 0
    ) else "fail"
    assert after["decision"] == "pass", after

    cache_qa = _cache_disabled_smoke()
    assert cache_qa["decision"] == "pass", cache_qa

    production_paths = (
        REPO / "tools" / "page_pipeline" / "render_identity.py",
        REPO / "tools" / "page_pipeline" / "html_render.py",
        REPO / "tools" / "page_pipeline" / "typography_fast_path.py",
    )
    forbidden = ("DLP00002", "DLP00004", "PPAT_run3")
    special_cases = [
        {"path": str(path), "token": token}
        for path in production_paths
        for token in forbidden
        if token in path.read_text(encoding="utf-8")]
    assert not special_cases
    after["hard_metrics"]["production_special_case_count"] = len(
        special_cases)

    before_after = {
        "schema_version": "fast.v05c.render_identity_before_after.v1",
        "frozen_fixture": {
            "html": str(FROZEN_HTML.resolve()),
            "page_model": str(FROZEN_MODEL.resolve()),
            "paragraph_ids": list(FIXTURE_IDS),
            "flow_fragment_ids": [row["flow_fragment_id"]
                                  for row in expected],
        },
        "before": before,
        "after": after,
        "typography_selector_before": (
            ".paragraph-block[data-flow-fragment]"),
        "typography_selector_after": (
            "[data-render-id][data-flow-fragment] (exact identity pair)"),
        "production_special_cases": special_cases,
        "chromium_launch_count": 0,
        "pdf_open_count": 0,
        "page_rasterize_count": 0,
        "real_translation_api_call_count": 0,
    }
    _dump(OUT / "render_identity_before_after.json", before_after)
    _dump(OUT / "render_identity_qa.json", after)
    _dump(OUT / "translation_cache_disabled_qa.json", cache_qa)

    report = f"""# FAST v05C — Specialized RenderIdentity + Cache Disabled

## 结论

**PASS**。本任务仅运行冻结 HTML / PageModel 静态 QA 和 mock provider
smoke；没有打开 PDF、没有启动 Chromium、没有栅格化页面、没有调用真实翻译
API，也没有重新运行 PPAT 或 2504。

## QA-FIRST 证据

冻结的 PPAT p001 HTML 中，`DLP00002-F0` 位于 `author-block` 的专用
span，`DLP00004-F0` 位于 `caption-block`。旧 Typography selector 只查找
`.paragraph-block[data-flow-fragment]`，因此两个真实存在的 DOM 元素都无法
定位：

- specialized_render_identity_missing_count = {before['hard_metrics']['specialized_render_identity_missing_count']}
- typography_dom_missing_count = {before['hard_metrics']['typography_dom_missing_count']}

修复后，所有专用软文本输出统一携带 `data-render-id`、
`data-flow-fragment`、`data-render-source` 和 `data-role`；Typography 使用
RenderIdentity 精确查找，不依赖元素是否为 paragraph-block。

## 修复后硬指标

- specialized_render_identity_missing_count = {after['hard_metrics']['specialized_render_identity_missing_count']}
- typography_dom_missing_count = {after['hard_metrics']['typography_dom_missing_count']}
- render_identity_duplicate_count = {after['hard_metrics']['render_identity_duplicate_count']}
- slot_geometry_mutation_count = {after['hard_metrics']['slot_geometry_mutation_count']}
- hard_anchor_moved_count = {after['hard_metrics']['hard_anchor_moved_count']}
- production_special_case_count = {after['hard_metrics']['production_special_case_count']}
- typography_writeback_mismatch_count = {writeback['typography_writeback_mismatch_count']}

标题、作者、单位、摘要标题、摘要正文、图注和脚注保留各自专用 DOM 结构；
没有被强制改成 paragraph-block。Fit/Fill 的 font-size、line-height、
text-indent（适用时）以及文本内容 top offset 都从同一个 flow state 回写到
专用元素。

## 翻译缓存

FAST E2E、FAST source preparation、公共 translate_document 以及人工 CLI
的默认缓存开关均为 OFF。关闭时不构造 shared/canonical cache，不搜索 Task
4M cache，不建立 output-dir cache，也不读写 `_fast_shared_translation_cache`。
缓存实现保留，未来只有显式 `--translation-cache on` 才会恢复。

- translation_cache_enabled = {str(cache_qa['translation_cache_enabled']).lower()}
- cache_lookup_count = {cache_qa['cache_lookup_count']}
- cache_read_count = {cache_qa['cache_read_count']}
- cache_write_count = {cache_qa['cache_write_count']}
- cache_hit = {cache_qa['cache_hit']}
- mock provider item count = {cache_qa['provider_item_count']}
- real translation API call count = {cache_qa['real_translation_api_call_count']}

## 范围声明

未修改 SourceTextSlot 几何、Typography Fit/Fill 策略、Figure/Table/Formula、
prose recovery 或最终 PDF 视觉内容。本任务未生成 PDF，完整端到端留给后续
人工运行。
"""
    (OUT / "SPECIALIZED_RENDER_IDENTITY_REPORT.md").write_text(
        report, encoding="utf-8")
    print(json.dumps({
        "FAST_V05C": "PASS",
        "before": before["hard_metrics"],
        "after": after["hard_metrics"],
        "cache": {key: cache_qa[key] for key in (
            "translation_cache_enabled", "cache_lookup_count",
            "cache_read_count", "cache_write_count", "cache_hit",
            "provider_item_count")},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
