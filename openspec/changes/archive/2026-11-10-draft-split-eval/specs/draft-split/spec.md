# Spec Delta

## Purpose

在不改变下游校验与装配的前提下，降低骨架生成的尾延迟。

## ADDED Requirements

### Requirement: 产出形状不变

拆步模式 MUST 产出与单次模式**同形状**的骨架对象；下游 MUST NOT 感知模式差异。

#### Scenario: 两种模式都进同一个闸门
- **WHEN** `llm_draft_split=true`
- **THEN** 闸门、装配、质检走的是同一段代码

### Requirement: 单段只带自己的事实

每段的提示词 MUST 只包含该段声明的事实原文，MUST NOT 附带其它段的待选事实。

#### Scenario: 段声明 fact 2
- **WHEN** 某段 `facts=[2]`
- **THEN** 提示词里出现事实 2 的原文，不出现事实 1/3 的原文（整份清单仍作为 material_facts 存在）

### Requirement: 段序稳定

并行完成后 MUST 按提纲顺序拼装；完成先后 MUST NOT 影响段落顺序。

#### Scenario: 后段先返回
- **WHEN** 第 3 段比第 1 段先返回
- **THEN** 结果仍是第 1、2、3 段的顺序

### Requirement: 默认关闭且可回退

`llm_draft_split` MUST 默认 `false`。关闭时行为 MUST 与拆步前逐字节一致。

#### Scenario: 关闭开关
- **WHEN** 未设置该环境变量
- **THEN** 只发生一次 `scene="draft"` 调用

### Requirement: 采纳要有前置数据

开启前 MUST 有评测集数据；判定标准见 `docs/79` §3。

#### Scenario: 指标不达标
- **WHEN** 通过率或原子支持率低于基线，或 p95 降幅不足 40%
- **THEN** MUST NOT 默认开启，并在结论里写明原因

## Outcome（2026-11-10）

实验**否决**：两轮子集评测（10 格）通过率 0% → 10%，原子支持率 73% → 89.1%，
耗时均高于基线。开关 `llm_draft_split` 保持默认 `false`，代码保留作为"这条路怎么失败"的记录。
详见 `docs/79` §5~§6。
