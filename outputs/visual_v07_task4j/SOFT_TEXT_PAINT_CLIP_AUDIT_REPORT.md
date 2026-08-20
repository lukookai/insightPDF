# Soft Text Paint / Clip Audit Report

Decision: **BLOCKED**

Root cause: **C.DOM_HEIGHT_STALE**

## 前一个 block 是否真的超出 slot？

是。`PAF_B1_02` 的 SourceTextSlot 为
`[51.3, 465.24, 288.7, 504.042]`，DOM text range 底部为
`513.844pt`，slot 底部为
`504.042pt`。声明高度比实际内容旧
`9.802pt`；最终 PDF 墨迹越过 slot
`7.708pt`。

## 是否存在 overflow:hidden / clip？

problem block 的 `overflow=visible`、
`clip-path=none`。slot 边界不是有效裁剪边界；最近的
有效裁剪来自页面级 BODY/HTML，`previous_clip_bottom` 为
`793.699pt`。因此 `clip_height` 为
`0.000pt`，没有发生父容器裁剪。

## 后一个 block 是否覆盖它？

否。后一个 block 是 `PAF_B1_04`，SourceTextSlot 为
`[51.3, 568.85, 288.7, 698.19]`。它在 DOM 中后绘制，两个 block
均为 `z-index:auto`，但前一个墨迹底部为
`511.750pt`，后一个墨迹顶部为
`569.750pt`，两者没有相交。

## 实际遮挡多少 pt？

`painted_overlap_height=0.000pt`，
`clip_height=0.000pt`。实际遮挡为 **0.000pt**。

## Task 4I 的 sup/sub 是否改变实际行盒高度？

没有。对应 Task 4H block 与 Task 4I block 的 scroll height 差为
`0.000pt`，text range bottom
差为 `0.000pt`；运行时把所有
`<sup>/<sub>` 暂时中和到 baseline 后，最大 scroll-height 差为
`0.000pt`。
sup/sub 确实改变局部墨迹 ascent/descent（最大 ascent
`1.195pt`、descent
`1.043pt`），但由于 `line-height:0`，
没有改变 block 行盒或 measured scroll height。布局仍沿用相同的冻结
SourceTextSlot 高度。

## 根因分类是什么？

**C.DOM_HEIGHT_STALE**。当前冻结 artifact 能确认 stale height，不能
确认实际 container clip 或 next-block paint occlusion。严格 QA-FIRST
要求的 `soft_text_clip_count > 0 OR soft_text_paint_occlusion_count > 0`
没有满足，因此诊断状态为 BLOCKED，并按任务要求不做修复。

## production special case 是否为 0？

是，`production_special_case_count=0`。候选由 SourceTextSlot 与 DOM
高度差、sup/sub 结构和同列后续 block 的相对几何自动选择，没有按
文本、页码、文件名或 render id 分支。
