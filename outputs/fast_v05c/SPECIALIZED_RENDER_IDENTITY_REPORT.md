# FAST v05C — Specialized RenderIdentity + Cache Disabled

## 结论

**PASS**。本任务仅运行冻结 HTML / PageModel 静态 QA 和 mock provider
smoke；没有打开 PDF、没有启动 Chromium、没有栅格化页面、没有调用真实翻译
API，也没有重新运行 PPAT 或 2504。

## QA-FIRST 证据

冻结的 PPAT p001 HTML 中，`DLP00002-F0` 位于 `author-block` 的专用
span，`DLP00004-F0` 位于 `caption-block`。旧 Typography selector 只查找
`.paragraph-block[data-flow-fragment]`，因此两个真实存在的 DOM 元素都无法
定位：

- specialized_render_identity_missing_count = 2
- typography_dom_missing_count = 2

修复后，所有专用软文本输出统一携带 `data-render-id`、
`data-flow-fragment`、`data-render-source` 和 `data-role`；Typography 使用
RenderIdentity 精确查找，不依赖元素是否为 paragraph-block。

## 修复后硬指标

- specialized_render_identity_missing_count = 0
- typography_dom_missing_count = 0
- render_identity_duplicate_count = 0
- slot_geometry_mutation_count = 0
- hard_anchor_moved_count = 0
- production_special_case_count = 0
- typography_writeback_mismatch_count = 0

标题、作者、单位、摘要标题、摘要正文、图注和脚注保留各自专用 DOM 结构；
没有被强制改成 paragraph-block。Fit/Fill 的 font-size、line-height、
text-indent（适用时）以及文本内容 top offset 都从同一个 flow state 回写到
专用元素。

## 翻译缓存

FAST E2E、FAST source preparation、公共 translate_document 以及人工 CLI
的默认缓存开关均为 OFF。关闭时不构造 shared/canonical cache，不搜索 Task
4M cache，不建立 output-dir cache，也不读写 `_fast_shared_translation_cache`。
缓存实现保留，未来只有显式 `--translation-cache on` 才会恢复。

- translation_cache_enabled = false
- cache_lookup_count = 0
- cache_read_count = 0
- cache_write_count = 0
- cache_hit = 0
- mock provider item count = 1
- real translation API call count = 0

## 范围声明

未修改 SourceTextSlot 几何、Typography Fit/Fill 策略、Figure/Table/Formula、
prose recovery 或最终 PDF 视觉内容。本任务未生成 PDF，完整端到端留给后续
人工运行。
