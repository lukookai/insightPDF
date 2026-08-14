# Versioning

当前共同基线：

    base

从 `base` 开始有两条独立开发路线。

## Visual Alignment

版本：

    visual-v01
    visual-v02
    ...
    visual-v99

目标：

- source visual geometry first
- formula / figure / table hard anchors
- fixed-canvas / source-layout preservation
- 中文优先在原区域适配
- 不以全局 reflow 为默认策略

分支：

    visual

tag：

    visual-vXX

## Flow Layout

版本：

    flow-v01
    flow-v02
    ...
    flow-v99

目标：

- continuous document flow
- paragraph reflow
- obstacle-aware placement
- page capacity
- cross-column / cross-region continuation

分支：

    flow

tag：

    flow-vXX

## Rules

禁止以后使用新的：

    A
    B
    C
    C1
    C2
    C3

作为开发版本编号。

阶段内部 QA 名称可以保留历史名字，
但新的正式 release / version 只能使用：

    base
    visual-v01 ~ visual-v99
    flow-v01 ~ flow-v99

`visual` 与 `flow` 不允许直接互相 merge。

如需要共享修复：

    base / common fix
或明确 cherry-pick commit

禁止把一个分支的实验性排版策略直接 merge 到另一分支。
