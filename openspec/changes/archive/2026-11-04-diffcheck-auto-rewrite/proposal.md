# Proposal: diffcheck-auto-rewrite

## Why

此前实施的 `platform-differentiation` 建立了跨平台重合检测器（`diffcheck.evaluate`），成功使小红书与公众号的重合率从 30% 压降至 7.69%，小红书与知乎重合率压降至 8.33%。
然而目前检测器属于**事后评估**机制：当偶发重合率超标（如知乎与公众号重合率达 16.67%）时，系统仅将问题输出为 `【跨平台】` 告警存入 `qa.warnings`，仍需创作者人工手动修改重复句子。
为了实现真正的一键自动化交付，需要在整批生成流中建立**跨平台重合自愈改写闭环**：当检测到成稿两两重合超标时，自动提取重合冲突句作为强负约束反馈，对后生成的冲突平台发起定向微调重写，直至达标交付。

## What Changes

- **自动改写触发机制**：在 `generate()` 完成整批初稿并执行 `diffcheck.evaluate()` 后，若存在两两平台重合率 > `diff_max_overlap`（默认 10%），自动识别重合度最高的后发平台发起二次改写。
- **定向冲突句反馈注入**：改写调用中自动提取具体的冲突重合句子（`pairwise_overlap_sentences`），精准注入改写提示词中的 `already_used_sentences` 与针对性改写反馈 `feedback`。
- **改写轮数与开闭可配**：通过 `DIFF_AUTO_REWRITE_ENABLED`（默认 `true`）与 `DIFF_MAX_REWRITE_ROUNDS`（默认 `1`）环境变量支持灵活开闭与轮次配置。
- **优雅收敛与降级**：若改写后重合率降低，采纳改写成果；若改写后仍超标或改写耗尽，保留最佳稿件并在 `qa.warnings` 中如实保留标记，绝不阻断生成流程（Fail-safe 机制）。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `platform-differentiation`: 增加自动去重改写闭环要求（当跨平台重合超标时系统自动尝试自愈重写，避免人工干预）。

## Impact

- **Affected Code**:
  - `python-service/app/config.py`：新增 `diff_auto_rewrite_enabled` 和 `diff_max_rewrite_rounds` 配置项。
  - `python-service/app/pipeline/generate.py`：在整批生成后接入基于冲突句的自动改写收敛逻辑。
  - `python-service/app/pipeline/diffcheck.py`：提供改写靶向目标判定辅助函数。
- **APIs & State**: 保持完全向后兼容，API 请求/响应格式不变，改写过程通过 RunLog 发送 `平台:xxx:去重改写` 事件。
