# 验收清单（checklist）：长任务预算与部分失败保留

- 文档类型：checklist（事前三件套之三）
- 版本：v1.1 · 日期：2026-09-16（v1.0 为事前列，v1.1 为交付时回填）
- 配套：`43-长任务预算与部分失败保留-task.md`、`44-长任务预算与部分失败保留-spec.md`、`46-…-交付说明.md`
- 判定方式：每条独立判定；未通过项在 §验收结论 逐条说明

---

## A. Python 逐平台容错

- [x] A1 单平台失败**不影响**其他平台（其余照常返回）— `test_partial_failure_keeps_successful`
- [x] A2 失败信息记入 `failures`（含 platform + error）
- [x] A3 `error` 含异常类型（形如 `LLMError: ...`）— 断言 `"RuntimeError" in failures[0]["error"]`
- [x] A4 失败**有日志**（不静默）— `logger.exception("platform %s failed", code)`
- [x] A5 全部平台失败时**不抛异常**，返回空 drafts + 完整 failures — `test_all_failures_do_not_raise`
- [x] A6 成功时为 `failures=[]` — `test_single_success_has_no_failures`
- [x] A7 容错**只在单平台外层**，未改 `_generate_one` 内部逻辑

## B. 响应契约

- [x] B1 `GenerateFailure` / `GenerateResponse.failures` 已定义
- [x] B2 新增字段有默认值（`default_factory=list`，老调用方兼容）
- [x] B3 Java 侧透传 `failures` 与 `ok_count` / `fail_count`

## C. Java 超时预算

- [x] C1 超时改为**可配置**（`polyface.llm.timeout-sec`，默认 240s）
- [x] C2 可用环境变量覆盖（`POLYFACE_LLM_TIMEOUT_SEC`）
- [x] C3 启动或调用时**日志可见**实际超时值 — 「短任务 60s / 生成 240s」（见 `docs/46` §3.3）
- [x] C4 未设置无限超时（有明确上限）

## D. Java 部分落库

- [x] D1 成功平台的稿子**正常落库** — `PartialFailureTest`
- [x] D2 失败平台**不阻塞**落库
- [x] D3 全失败时返回 **200 + fail_count>0**（不是 502）
- [x] D4 超时时**不谎报成功**（明确提示未完成）— 分支与文案已有，浏览器实测 E6 验证渲染；**真实超时未触发**（见 §残余）
- [x] D5 落库数量与 `ok_count` 一致 — `scripts/e2e_partial.py` 第 2 项

## E. 前端

- [x] E1 默认只勾选**单平台** — 实测 1/5
- [x] E2 勾选 >1 时提示预计耗时 — 「2 个平台，每个约 1~4 分钟」
- [x] E3 生成后显示「成功 X / 失败 Y」
- [x] E4 失败平台与原因**可见**（不笼统报错）
- [x] E5 提供「🔁 重试失败平台」（只重跑失败平台）— 实测只带 `douyin`
- [x] E6 超时场景有可读提示（可能超时 + 已完成部分已保存）
- [x] **E1b（新增）默认勾选的平台有高亮样式** — 实测暴露的缺陷，已修（见 `docs/46` §5）

## F. 测试与回归

- [x] F1 Python 新增测试覆盖 4 个场景且全绿
- [x] F2 Java 新增测试覆盖 4 个场景且全绿
- [x] F3 既有 Python 测试全绿 — **68 passed**（64 既有 + 4 新增）
- [x] F4 既有 Java 测试全绿 — **43 passed**（39 既有 + 4 新增）
- [x] F5 端到端 `scripts/e2e_partial.py` 通过 — **9/9**
- [x] F6 浏览器实测：默认单平台 / 计数 / 重试按钮 — **15/15**（`scripts/e2e_partial_browser.py`，Edge/CDP）

## G. 文档勘误与交付

- [x] G1 `docs/29` 耗时预估标注为「外推预估」并给实测区间（101s ~ 250s）
- [x] G2 `docs/34` 补充「已通过部分失败保留缓解」+ 与 180s 超时的冲突说明
- [x] G3 README 明确单平台耗时预期（1~4 分钟）+ 超时可配 + 部分成功保留
- [x] G4 `docs/46-长任务预算与部分失败保留-交付说明.md` 产出
- [x] G5 交付说明含**实测证据**（E2E 9/9 + 浏览器 15/15 + 测试计数 + 日志行）
- [x] G6 未验证部分显式标注（§6 残余项 4 条）
- [x] G7 已提交，工作区干净
- [x] G8 未越界（未做后台队列 / 未顺手改隐私表述 / 未动自动成片）

---

## 验收结论

| 项 | 结果 | 备注 |
|---|---|---|
| A~G 全部勾选 | ✅ 通过 | 见上 |
| 部分成功保留 | ✅ 通过 | Python/Java 单测 + 浏览器 E4/E5 实测 |
| 超时可配 | ✅ 通过 | 双客户端 60s/240s，日志可见 |
| 浏览器实测 | ✅ 通过 | 15/15，Edge/CDP |
| **未验证项** | ⚠️ 2 条 | ① 真实 LLM 链路（本轮全 mock）；② 真实超时下的「已落库部分」未跑 —— 均为额度所限，已在 `docs/46` §6 记录 |
