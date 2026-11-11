# Spec Delta

## Purpose

让模型判断"这个停顿该剪还是该留"。规则只看声学，分不清"留白"与"卡壳"；
语义判断交给模型，但**时间与边界仍归程序**。

## ADDED Requirements

### Requirement: 模型只投票，不给时间

模型 MUST 返回 `{id, action: cut|keep, why}` ✓，MUST NOT 返回任何时间戳或时间区间 ✗。
剪点边界 MUST 取自程序给出的候选停顿。

#### Scenario: 模型试图给时间
- **WHEN** 模型返回里带有 `start`/`end` 字段
- **THEN** MUST 忽略该字段，只用 `id` 定位候选

### Requirement: 两个方向都要生效

判定 `keep` 且规则原本会剪 → MUST 去掉这一刀；
判定 `cut` 且规则原本不剪 → MUST 加上这一刀。
**只做单向等于只解决一半问题**。

#### Scenario: 停顿是故意留白
- **WHEN** 一个 2.4s 停顿，规则会剪，模型判 keep
- **THEN** 最终剪点 MUST NOT 包含该停顿

#### Scenario: 停顿是卡壳
- **WHEN** 一个 0.9s 停顿，规则不剪，模型判 cut
- **THEN** 最终剪点 MUST 包含该停顿（仍按 `keep_margin` 两侧回退）

### Requirement: 默认关闭，开启要明确

`pause_review` MUST 默认 `false` ✓；开启后会把**转写文本**发给用户配置的 LLM，
界面 MUST 在勾选框旁写明这一点 ✗ 不可悄悄开。

#### Scenario: 用户没勾
- **WHEN** 用户发起粗剪但没勾"模型复核停顿"
- **THEN** 全程 MUST 零次模型调用

### Requirement: 降级要看得出来

模型不可用、超时、返回坏 JSON → MUST 退回纯规则，且结果 MUST 标记 `llm_skipped=true`。
MUST NOT 静默当作"模型说没问题"。

#### Scenario: 没配 Key
- **WHEN** `pause_review` 打开但模型调用失败
- **THEN** 产出照常，报告 MUST 显示"模型复核未执行"，checkpoint MUST 保留 `llm_skipped=true`

### Requirement: 候选有上限且可预测

候选 MUST 只含 ≥ `pause_review_min_sec` 的停顿，且数量 MUST 不超过 `pause_review_max`
（超出取最长的 N 条后按时间重排）。一次复核 MUST 只发**一次**模型调用。

#### Scenario: 一小时长视频
- **WHEN** 候选有 300 条
- **THEN** 只取最长的 80 条；调用次数仍为 1
