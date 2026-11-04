# Spec Delta: platform-differentiation

## ADDED Requirements

### Requirement: 跨平台重合自动改写闭环

在多平台生成流程中，若两两平台的成稿逐字重合率超过设定阈值（默认 10%），系统 MUST 自动触发定向改写闭环，无须人工手动修稿。
改写时系统 MUST 将检出的具体冲突重复句子作为负向约束（`avoid_sentences` 与针对性反馈）注入冲突后发平台的提示词中，且改写过程 MUST 保留事实数据与平台 DNA 格式。

#### Scenario: 触发自动去重改写且达标收敛
- **WHEN** 多平台初稿生成后，检测到平台 A 与平台 B 重合率高于阈值（例如知乎与公众号重合率 16.7% > 10%）且启用了自动改写
- **THEN** 系统自动提取重复冲突句子，对后发平台发起第 2 轮改写；改写后重合率降低至 ≤10%，系统采纳改写成果交付并清除阻塞警告

#### Scenario: 改写达到上限仍未收敛时的安全降级
- **WHEN** 自动改写达到最大轮数（由 `diff_max_rewrite_rounds` 控制）后两两重合率仍高于阈值
- **THEN** 系统保留历次改写中重合度最低的成稿版本，并在该平台的 `qa.warnings` 中保留 `【跨平台】` 诊断提示，绝不中断整批生成任务

#### Scenario: 自动改写配置禁用
- **WHEN** 配置项 `diff_auto_rewrite_enabled` 设为 false
- **THEN** 系统跳过自动改写环节，直接将重合诊断告警存入 `qa.warnings` 交付
