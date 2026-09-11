# 验收清单（checklist）：LLM 运行时加固 + 全链路可观测

- 文档类型：checklist（事前三件套之三）
- 版本：v1.0 · 日期：2026-09-11
- 配套：`26-LLM运行时加固与可观测-task.md`、`27-LLM运行时加固与可观测-spec.md`
- 判定方式：每条独立判定，全部 `[x]` 方可交付

---

## A. LLM 调用加固

- [ ] A1 单次请求有超时保护，超时抛 `LLMTimeout`
- [ ] A2 429 + `tpm/rpm limit` 被分类为 `RateLimited`（可重试）
- [ ] A3 429 + `insufficient_quota` 被分类为 `QuotaExhausted`（不重试）
- [ ] A4 `model route not found` 被分类为 `QuotaExhausted`
- [ ] A5 连接失败被分类为 `LLMNetworkError`（可重试）
- [ ] A6 `RateLimited` 会重试，退避序列符合 `min(2^n, 8) + jitter`
- [ ] A7 重试次数上限 3，超限后抛出明确异常
- [ ] A8 `QuotaExhausted` **不重试**，立即切换下一个模型
- [ ] A9 全部模型耗尽时抛出可读错误（含已尝试模型列表）
- [ ] A10 mock 模式下加固逻辑完全不介入（离线能力不受影响）

## B. 模型降级链

- [ ] B1 默认链为 `deepseek-v4-flash → kimi-k3 → sensenova-u1-fast`
- [ ] B2 链可通过 `LLM_FALLBACK_MODELS` 环境变量覆盖
- [ ] B3 主模型额度耗尽时自动切换备用模型并成功返回
- [ ] B4 切换模型的事件被记录（usage 中可见 `model` 变化）
- [ ] B5 显式指定 `model=` 时不走降级链（尊重调用方）

## C. trace 上下文贯穿

- [ ] C1 Java 在生成入口生成 `traceId`
- [ ] C2 Java 请求 Python 时带 `X-Trace-Id` header
- [ ] C3 Python 中间件读取并存入 `contextvars`
- [ ] C4 `understand` / `learn` / `draft` / `qa` 等场景均能取到 trace_id
- [ ] C5 **线程池内的 LLM 调用也能取到 trace_id**（并发场景不丢）
- [ ] C6 Java 响应体含 `trace_id`
- [ ] C7 trace_id 在一次请求内**全局一致**（跨 Java 与 Python）
- [ ] C8 无 header 时 Python 自行生成 trace_id（不报错）

## D. 本地用量落库

- [ ] D1 每次真实 LLM 调用追加一行 JSONL
- [ ] D2 记录含 `ts/trace_id/scene/platform/model/attempt/ok/error_type/duration_ms`
- [ ] D3 记录含 `prompt_chars/completion_chars`，有 token 时记录 token
- [ ] D4 **JSONL 中不含 prompt/response 正文**（脱敏红线）
- [ ] D5 `GET /usage/summary` 返回聚合（total/failures/retries/avg/by_model/by_scene/recent）
- [ ] D6 `GET /api/usage` 由 Java 正确转发
- [ ] D7 usage 文件不进 git
- [ ] D8 文件损坏/半行不影响聚合（容错跳过）
- [ ] D9 mock 调用不污染用量（或标记为 mock）

## E. Langfuse 集成

- [ ] E1 `LANGFUSE_ENABLED=false` 时**零开销**（不初始化客户端、不发请求）
- [ ] E2 启用后 Python 侧能上报 `llm.chat` span/generation
- [ ] E3 Java 侧能上报 `trace-create`（含编排阶段）
- [ ] E4 上报使用同一 `traceId`，Langfuse 中可见完整链路
- [ ] E5 上报失败**不影响业务**（仅告警，响应正常）
- [ ] E6 一个生成请求在 Langfuse 中可见：understand + 各平台 brief/draft/qa
- [ ] E7 上报内容不含 API Key（脱敏校验）
- [ ] E8 实现前已按 langfuse skill 要求查阅官方文档确认 SDK 形态

## F. 限流串行化降级

- [ ] F1 默认多平台并行
- [ ] F2 本轮检测到 `RateLimited` 后转为串行
- [ ] F3 串行模式下调用了最小间隔（`LLM_MIN_INTERVAL_MS`，默认 1500ms）
- [ ] F4 串行能完成 5 平台生成（真实环境实测通过）
- [ ] F5 最小间隔可配置

## G. 前端

- [ ] G1 生成结果区显示 trace 摘要（模型 / 耗时 / 重试次数 / trace 短 id）
- [ ] G2 摘要**不含 prompt 正文**
- [ ] G3 mock 模式下摘要显示"mock"而非虚假的模型名
- [ ] G4 失败时前端展示可读原因（额度耗尽 / 超时 / 网络）

## H. 测试

- [ ] H1 Python 单元测试全绿（含新增错误分类/退避/降级/JSONL/trace 用例）
- [ ] H2 Java 单元测试全绿（含 header 透传与 usage 转发）
- [ ] H3 **真实模式冒烟测试**存在且可独立运行（`-m real`）
- [ ] H4 无 Key 时真实测试**自动 skip**（不误报失败）
- [ ] H5 端到端：真实链路素材 → 5 平台生成成功，且 usage 有记录
- [ ] H6 端到端：模拟 429 → 重试后成功，`attempt>=2` 可查
- [ ] H7 mock 模式全链路无回归（既有 61 单测 / 96 E2E 断言仍通过）

## I. 文档与交付

- [ ] I1 `05-SRS` 新增 NFR-10（可观测性）与 FR-70~72
- [ ] I2 `08-ADR` 新增 ADR-015（观测本地优先 + 可选 Langfuse）
- [ ] I3 `docs/29-LLM运行时加固与可观测-交付说明.md` 产出
- [ ] I4 交付说明含**真实链路实测记录**（模型/耗时/重试/质量观察）
- [ ] I5 代码已提交，工作区干净（**确认 .env 未入库**）

---

## 验收结论

| 项 | 结果 | 备注 |
|---|---|---|
| A~I 全部勾选 | ⬜ 待执行 | |
| 真实链路验证 | ⬜ 待执行 | 需真实 LLM，额度可能受限 |
| mock 回归 | ⬜ 待执行 | |
| Langfuse 可见性 | ⬜ 待执行 | |
| 未通过项 | — | 若有，须逐条说明原因；额度受限导致未完成项须显式标注 |
