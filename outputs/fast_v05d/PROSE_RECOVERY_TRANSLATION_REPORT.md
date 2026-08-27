# FAST v05D — Move Prose Recovery Translation Out of Renderer

## 结论

**PASS**。冻结 PPAT p001 证明旧 translation.json 完成后仍有
5 个 recovery candidate；修复后这些候选在 renderer 启动前与
6 个普通页面翻译项合并进入同一 translation preparation
列表。测试只使用 mock provider，没有调用真实翻译 API，没有运行 Chromium，
也没有生成或栅格化 PDF。

## 旧行为证据

- recovery_candidate_after_translation_count = 5
- candidates = PAF_B1_00, PAF_B1_01, PAF_B1_02, PAF_B1_03, PAF_B1_04
- renderer 曾为每个 recovery paragraph 调用 math-dense translation router

候选识别仍由 `recover_prose_adopted_formulas` 的原有 PDF 文本几何、语义与
公式归属规则完成；5D 只改变识别发生的生命周期位置，没有改变判定规则。

## 新 translation preparation

- translation_item_count = 11
- provider_batch_count = 1
- provider_item_count = 11
- prose_recovery_item_count = 5
- translation_time = 0.002699s（mock）
- normal_and_recovery_share_provider_batch = true
- independent_recovery_provider_request_count = 0

normal paragraph、caption、front matter 对应的普通 DocumentModel 条目、表格
条目以及 prose recovery candidate 现在由同一个 `translate_document` 批量
入口处理。每页 prepared recovery 作为 `prose_recovery.json` 写入 source
chain，renderer 只读取 target。

## Renderer API guard

- recovery_candidate_after_translation_count = 0
- renderer_translation_api_call_count = 0
- prose_recovery_translation_api_call_count = 0
- required_target_missing_before_render_count = 0

FAST renderer 不再创建 prose recovery translator。表格闭环也注入同一个
provider guard；任何未准备的 required target 都会立即返回
`translation_preparation_incomplete`，不会临时翻译、重试、静默使用源文。

## Cache 与范围

- translation_cache_enabled = false
- translation_cache_lookup_count = 0
- translation_cache_read_count = 0
- translation_cache_write_count = 0
- production_special_case_count = 0

未修改 prose recovery 判定规则、Typography、SourceTextSlot、renderer 几何或
Figure/Table/Formula 策略。没有运行完整 PPAT 或 2504。
