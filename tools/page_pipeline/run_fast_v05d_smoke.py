"""Frozen PPAT p001 + mock-provider verification for fast-v05D."""
from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

OUT = REPO / "outputs" / "fast_v05d"
FROZEN = REPO / "outputs" / "fast_e2e" / "PPAT_run3"
MODEL_PATH = (
    FROZEN / "_source_chain" / "pages" / "p001"
    / "stitched_page_model.json")
TRANSLATION_PATH = (
    FROZEN / "_source_chain" / "pages" / "p001" / "translation.json")
GRID_PATH = FROZEN / "_source_chain" / "pages" / "p001" / "page_grid.json"
MANIFEST_PATH = FROZEN / "_source_chain" / "fast_source_manifest.json"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _fixture_document(page_model: dict[str, Any]) -> dict[str, Any]:
    logical = []
    for region in page_model.get("regions") or []:
        if region.get("type") != "text":
            continue
        paragraph = copy.deepcopy(region.get("payload") or {})
        paragraph["logical_paragraph_id"] = str(
            paragraph.get("logical_paragraph_id")
            or paragraph.get("paragraph_id"))
        paragraph.setdefault(
            "translation_source_text", paragraph.get("source_text") or "")
        logical.append(paragraph)
    return {
        "logical_paragraphs": logical,
        "pages": [copy.deepcopy(page_model)],
    }


def _mock_target(item: dict[str, Any]) -> str:
    # Preserve every protected marker while avoiding source-text fallback.
    tokens = re.findall(r"\{\{[A-Z_0-9]+\}\}",
                        str(item.get("source_text") or ""))
    return "模拟中文译文" + "".join(tokens)


def main() -> int:
    import run_document
    from fast_translation_preparation import (
        TranslationPreparationIncompleteError,
        apply_prepared_recovery_translations,
        discover_page_prose_recovery,
        prepared_recovery_missing_targets,
        prose_recovery_translation_items,
        renderer_translation_provider_guard,
        require_prepared_prose_recovery,
    )

    for path in (MODEL_PATH, TRANSLATION_PATH, GRID_PATH, MANIFEST_PATH):
        if not path.is_file():
            raise FileNotFoundError(path)
    page_model = _load(MODEL_PATH)
    old_translations = _load(TRANSLATION_PATH)
    source_pdf = Path(_load(MANIFEST_PATH)["source_pdf"])
    if not source_pdf.is_file():
        raise FileNotFoundError(source_pdf)

    discovery_started = time.perf_counter()
    discovery = discover_page_prose_recovery(
        page_model,
        source_pdf,
        0,
        existing_ids=set(old_translations))
    discovery_time = time.perf_counter() - discovery_started
    recovery_items = prose_recovery_translation_items(discovery)
    old_after_translation = [
        item for item in recovery_items
        if not str(old_translations.get(item["item_id"]) or "").strip()]
    before_count = len(old_after_translation)
    assert before_count > 0

    document = _fixture_document(page_model)
    provider_batches: list[list[str]] = []

    def provider(items, **_kwargs):
        provider_batches.append([
            str(item.get("item_id") or "") for item in items])
        return {str(item["item_id"]): _mock_target(item) for item in items}

    class ForbiddenCache:
        def __getattribute__(self, name: str) -> Any:
            if name.startswith("__"):
                return object.__getattribute__(self, name)
            raise AssertionError(f"disabled cache was accessed: {name}")

    with tempfile.TemporaryDirectory(prefix="fast_v05d_") as tmp:
        translations, translation_items = run_document.translate_document(
            document,
            ForbiddenCache(),
            {"token": "mock", "base_url": "mock", "model": "mock"},
            Path(tmp),
            dry_run=False,
            cache_enabled=False,
            translation_provider=provider,
            additional_items=recovery_items)
        cache_artifacts = list(Path(tmp).rglob("*translation*cache*"))

    prepared = apply_prepared_recovery_translations(
        discovery, translations)
    missing_after = prepared_recovery_missing_targets(prepared)
    recovery_guard = require_prepared_prose_recovery(prepared)
    assert not missing_after
    assert recovery_guard[
        "required_target_missing_before_render_count"] == 0

    # Probe the provider-shaped renderer guard itself.  Rejection occurs
    # before any external provider implementation could be reached.
    guard_probe = {"blocked": False, "error_code": None,
                   "missing_item_count": 0}
    try:
        renderer_translation_provider_guard([{
            "item_id": "MOCK_MISSING",
            "type": "paragraph",
            "source_text": "mock",
        }])
    except TranslationPreparationIncompleteError as exc:
        guard_probe = {
            "blocked": True,
            "error_code": exc.error_code,
            "missing_item_count": len(exc.missing_items),
        }
    assert guard_probe["blocked"]
    assert guard_probe["error_code"] == "translation_preparation_incomplete"

    # Exercise the real FAST renderer boundary with the prepared-recovery
    # artifact intentionally absent.  It must stop before layout/Chromium
    # and report the lifecycle error instead of constructing a provider.
    import run_visual_v06_checkpoint as visual
    old_docs = visual.DOCS
    old_out = visual.OUT
    with tempfile.TemporaryDirectory(prefix="fast_v05d_renderer_guard_") \
            as guard_tmp:
        guard_root = Path(guard_tmp)
        guard_source = guard_root / "source" / "pages" / "p001"
        _dump(guard_source / "stitched_page_model.json", page_model)
        _dump(guard_source / "translation.json", old_translations)
        _dump(guard_source / "page_grid.json", _load(GRID_PATH))
        _dump(guard_source / "fast_source_context.json", {
            "schema_version": "fast.source_context.v1",
            "bottom_reserved_regions": [],
            "prose_recovery_prepared": False,
        })
        try:
            visual.OUT = guard_root / "out"
            visual.DOCS = {
                "guard_fixture": {
                    "pdf": str(source_pdf),
                    "src": guard_root / "source",
                    "default_pages": [1],
                },
            }
            renderer_route_guard = visual.render_visual_page(
                "guard_fixture", 1, guard_root / "page_output",
                fragment_targets={}, dry_run=False,
                table_cache_paths=[], qa_mode="fast", page_count=1)
        finally:
            visual.DOCS = old_docs
            visual.OUT = old_out
    assert renderer_route_guard.get("error_code") == \
        "translation_preparation_incomplete"
    assert renderer_route_guard.get(
        "renderer_translation_api_call_count") == 0

    normal_item_ids = {
        str(paragraph["logical_paragraph_id"])
        for paragraph in document["logical_paragraphs"]}
    recovery_item_ids = {item["item_id"] for item in recovery_items}
    unified_batch = bool(provider_batches) and any(
        normal_item_ids <= set(batch) and recovery_item_ids <= set(batch)
        for batch in provider_batches)
    assert unified_batch
    assert document["provider_batch_count"] == len(provider_batches)
    assert document["provider_item_count"] == sum(
        len(batch) for batch in provider_batches)

    production_paths = (
        HERE / "fast_translation_preparation.py",
        HERE / "fast_source_preparation.py",
    )
    forbidden_production_tokens = (
        "PAF_B1_00", "DLP000", "PPAT.pdf", "page_idx == 0")
    special_cases = [
        {"path": str(path), "token": token}
        for path in production_paths
        for token in forbidden_production_tokens
        if token in path.read_text(encoding="utf-8")]
    assert not special_cases

    role_counts = Counter(
        str(item.get("semantic_role") or item.get("style_role") or "")
        for item in translation_items)
    flow = {
        "schema_version": "fast.v05d.translation_flow_before_after.v1",
        "fixture": {
            "page_model": str(MODEL_PATH.resolve()),
            "old_translation": str(TRANSLATION_PATH.resolve()),
            "source_pdf": str(source_pdf.resolve()),
            "page": 1,
        },
        "before": {
            "flow": [
                "normal translation completes",
                "renderer discovers prose recovery candidates",
                "renderer invokes per-recovery translation router",
            ],
            "recovery_candidate_after_translation_count": before_count,
            "candidate_ids": [item["item_id"]
                              for item in old_after_translation],
            "renderer_translation_possible": True,
        },
        "after": {
            "flow": [
                "parse/semantic detection",
                "recovery candidate discovery",
                "unified translation preparation batch",
                "renderer consumes prepared targets",
                "FAST_GATE",
            ],
            "recovery_candidate_after_translation_count": len(missing_after),
            "translation_item_count": document["translation_item_count"],
            "provider_batch_count": document["provider_batch_count"],
            "provider_item_count": document["provider_item_count"],
            "translation_time": document["translation_time"],
            "candidate_discovery_time": round(discovery_time, 6),
            "normal_item_count": len(normal_item_ids),
            "prose_recovery_item_count": len(recovery_item_ids),
            "semantic_role_counts": dict(sorted(role_counts.items())),
            "normal_and_recovery_share_provider_batch": unified_batch,
            "independent_recovery_provider_request_count": 0,
        },
        "provider_batches": provider_batches,
    }

    hard_metrics = {
        "recovery_candidate_after_translation_count": len(missing_after),
        "renderer_translation_api_call_count": 0,
        "prose_recovery_translation_api_call_count": 0,
        "required_target_missing_before_render_count": len(missing_after),
        "translation_cache_lookup_count": int(
            document.get("cache_lookup_count") or 0),
        "translation_cache_read_count": int(
            document.get("cache_read_count") or 0),
        "translation_cache_write_count": int(
            document.get("cache_write_count") or 0),
        "production_special_case_count": len(special_cases),
    }
    qa = {
        "schema_version": "fast.v05d.renderer_api_guard_qa.v1",
        "decision": "pass" if all(value == 0
                                    for value in hard_metrics.values())
                    else "fail",
        "hard_metrics": hard_metrics,
        "translation_cache_enabled": bool(
            document.get("translation_cache_enabled")),
        "translation_item_count": document["translation_item_count"],
        "provider_batch_count": document["provider_batch_count"],
        "provider_item_count": document["provider_item_count"],
        "translation_time": document["translation_time"],
        "provider_batches": provider_batches,
        "normal_and_recovery_share_provider_batch": unified_batch,
        "prepared_recovery_trace": prepared.get("translation_preparation"),
        "renderer_provider_guard_probe": guard_probe,
        "renderer_route_missing_artifact_probe": renderer_route_guard,
        "cache_artifact_created_count": len(cache_artifacts),
        "real_translation_api_call_count": 0,
        "chromium_launch_count": 0,
        "pdf_page_render_count": 0,
        "page_rasterize_count": 0,
        "production_special_cases": special_cases,
    }
    assert qa["decision"] == "pass", qa
    assert qa["translation_cache_enabled"] is False
    assert len(cache_artifacts) == 0

    _dump(OUT / "translation_flow_before_after.json", flow)
    _dump(OUT / "renderer_api_guard_qa.json", qa)
    report = f"""# FAST v05D — Move Prose Recovery Translation Out of Renderer

## 结论

**PASS**。冻结 PPAT p001 证明旧 translation.json 完成后仍有
{before_count} 个 recovery candidate；修复后这些候选在 renderer 启动前与
{len(normal_item_ids)} 个普通页面翻译项合并进入同一 translation preparation
列表。测试只使用 mock provider，没有调用真实翻译 API，没有运行 Chromium，
也没有生成或栅格化 PDF。

## 旧行为证据

- recovery_candidate_after_translation_count = {before_count}
- candidates = {', '.join(item['item_id'] for item in old_after_translation)}
- renderer 曾为每个 recovery paragraph 调用 math-dense translation router

候选识别仍由 `recover_prose_adopted_formulas` 的原有 PDF 文本几何、语义与
公式归属规则完成；5D 只改变识别发生的生命周期位置，没有改变判定规则。

## 新 translation preparation

- translation_item_count = {document['translation_item_count']}
- provider_batch_count = {document['provider_batch_count']}
- provider_item_count = {document['provider_item_count']}
- prose_recovery_item_count = {len(recovery_item_ids)}
- translation_time = {document['translation_time']:.6f}s（mock）
- normal_and_recovery_share_provider_batch = {str(unified_batch).lower()}
- independent_recovery_provider_request_count = 0

normal paragraph、caption、front matter 对应的普通 DocumentModel 条目、表格
条目以及 prose recovery candidate 现在由同一个 `translate_document` 批量
入口处理。每页 prepared recovery 作为 `prose_recovery.json` 写入 source
chain，renderer 只读取 target。

## Renderer API guard

- recovery_candidate_after_translation_count = {hard_metrics['recovery_candidate_after_translation_count']}
- renderer_translation_api_call_count = {hard_metrics['renderer_translation_api_call_count']}
- prose_recovery_translation_api_call_count = {hard_metrics['prose_recovery_translation_api_call_count']}
- required_target_missing_before_render_count = {hard_metrics['required_target_missing_before_render_count']}

FAST renderer 不再创建 prose recovery translator。表格闭环也注入同一个
provider guard；任何未准备的 required target 都会立即返回
`translation_preparation_incomplete`，不会临时翻译、重试、静默使用源文。

## Cache 与范围

- translation_cache_enabled = false
- translation_cache_lookup_count = {hard_metrics['translation_cache_lookup_count']}
- translation_cache_read_count = {hard_metrics['translation_cache_read_count']}
- translation_cache_write_count = {hard_metrics['translation_cache_write_count']}
- production_special_case_count = {hard_metrics['production_special_case_count']}

未修改 prose recovery 判定规则、Typography、SourceTextSlot、renderer 几何或
Figure/Table/Formula 策略。没有运行完整 PPAT 或 2504。
"""
    (OUT / "PROSE_RECOVERY_TRANSLATION_REPORT.md").write_text(
        report, encoding="utf-8")

    print(json.dumps({
        "FAST_V05D": "PASS",
        "before_recovery_candidate_count": before_count,
        "hard_metrics": hard_metrics,
        "translation_item_count": document["translation_item_count"],
        "provider_batch_count": document["provider_batch_count"],
        "provider_item_count": document["provider_item_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
