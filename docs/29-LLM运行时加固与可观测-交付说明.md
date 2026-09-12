# FR-70/71/72 交付说明（LLM 运行时加固 + 全链路可观测）

日期：2026-09-12 · 状态：✅ 完成
需求：FR-70（调用加固）· FR-71（trace 贯穿）· FR-72（用量可见）· 新增 NFR-10
决策：新增 ADR-015（观测本地优先 + 可选 Langfuse）

---

## 1. 验收结果

| 层 | 项 | 结果 |
|---|---|---|
| 单元 | Java 28 项（新增 5） | ✅ 全绿 |
| 单元 | Python 38 项 | ✅ 全绿 |
| **端到端** | `scripts/e2e_fr70.py` 真实链路 | ✅ **10/10** |
| **端到端** | Langfuse trace 嵌套结构 | ✅ 实测通过 |
| 真实依赖 | 真实 LLM + 真实 Langfuse（非 mock） | ✅ 已用真实依赖验证 |

## 2. 交付内容

### 2.1 LLM 调用加固（FR-70）

| 能力 | 实现 |
|---|---|
| 错误分类 | `RateLimited` / `QuotaExhausted` / `LLMTimeout` / `LLMNetworkError` / `LLMParseError`，按实测报文特征判定 |
| 重试退避 | `min(2^n, 8) + jitter`（实测 1.1s / 2.3s / 4.4s），上限 3 次 |
| **模型降级链** | 额度耗尽**不重试**直接换模型；链 = `LLM_MODEL` + `LLM_FALLBACK_MODELS` |
| 超时 | 单次 60s（可配） |
| 串行化 | 实测上游额度极紧（连续两次即 429）→ `llm_parallel` **默认 false**，串行 + 1500ms 间隔 |

### 2.2 trace 贯穿（FR-71）

```
浏览器 → Java TraceFilter（生成 32 位 W3C id，写响应头）
       → PythonClient 带 X-Trace-Id header
       → Python 中间件 → contextvars
       → 线程池用 run_in_context 显式传播（否则子线程丢 trace）
       → llm.chat() 作为 child span 挂在对应阶段下
```

### 2.3 用量与观测（FR-72）

- 本地 JSONL（`{data-dir}/llm-usage.jsonl`）：**只存长度/token/耗时/重试，不存正文**
- `GET /usage/summary`（Python）→ `GET /api/usage`（Java 转发）
- Langfuse：Python 用官方 SDK v4（drop-in `langfuse.openai`，自动捕获 model/token）；Java 手写 ingestion API（**零新依赖**）
- 可达性探测：Langfuse 未启动时静默降级，**业务零影响**

### 2.4 前端

生成结果区新增 trace 摘要：`📊 模型 deepseek-v4-flash → deepseek-v4-pro · 调用 5 次 · 重试 2 次 · trace 00848684`（**不含 prompt 正文**）

## 3. 端到端实测证据

```
===== FR-70/71/72 端到端验收 =====
PASS 1 建素材成功
PASS 2 生成成功                        真实 LLM，耗时 101s
PASS 2 响应含 trace_id                 008486848e424a33ba0173ba7034cd7a
PASS 2 响应头含 X-Trace-Id             同上（体/头一致）
PASS 3 /api/usage 可用                 total=40
PASS 3 按场景分组                      {understand, brief, draft, qa, ...}
PASS 3 不含 prompt 正文（脱敏）
===== 10/10 通过 =====

Langfuse trace（嵌套验证）：
trace name  : generate:xhs
observations: 11
  SPAN       generate:xhs          ← root
   ├─ SPAN       understand
   ├─ SPAN       brief
   ├─ SPAN       draft
   └─ SPAN       qa
        └─ GENERATION deepseek-v4-flash / deepseek-v4-pro
```

**观测到的真实降级**：trace 中某阶段 `model=deepseek-v4-pro`，说明主模型限流时自动切了备模型——这正是可观测性的直接价值。

## 4. 与计划的偏差（诚实记录）

| 项 | 计划 | 实际 |
|---|---|---|
| Langfuse 接入方式 | 计划用 v2 ingestion API | 实测服务是 **v4**（OTLP 端点 200），改用官方 v4 SDK |
| 降级链默认值 | `deepseek-v4-flash → kimi-k3 → sensenova-u1-fast` | 实测该链**选错**：kimi-k3 常限流、sensenova-u1-* 报 not found；改为 `deepseek-v4-pro, glm-5.2, sensenova-6.8-flash-lite` |
| 并发生成 | 原并行 | 实测额度极紧，**改默认串行** |
| trace_id 长度 | 16 位 | v4 要求 W3C 32 位，已统一 |

## 5. 残余项

- [ ] 真实模式冒烟测试（`@pytest.mark.real`）尚未落成 pytest 用例（当前由 `e2e_fr70.py` 覆盖）
- [ ] Java 侧 Langfuse 上报未在真实链路中单独断言（trace 已含 Java span，但未逐条校验）
- [ ] 上游额度极紧 → 5 平台全量生成耗时可能 >10 分钟，实际使用建议单平台或分批
- [ ] 平台 DNA 依据仍未调研（独立任务，见 `docs/26` task 的 Out 说明）
