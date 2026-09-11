# 规格说明书：LLM 运行时加固 + 全链路可观测

- 文档类型：spec（事前三件套之一）
- 版本：v1.0 · 日期：2026-09-11
- 配套：`26-LLM运行时加固与可观测-task.md`、`28-LLM运行时加固与可观测-checklist.md`
- 新增需求：**NFR-10 可观测性**；新增决策：**ADR-015**（观测本地优先 + 可选 Langfuse）

---

## 1. 需求规格

### 1.1 功能需求

| 编号 | 需求 | 落地 |
|---|---|---|
| FR-70（新） | LLM 调用可重试、可降级、可解释 | 重试退避 + 模型降级链 + 错误分类 |
| FR-71（新） | 一次生成的全链路 trace 可查 | trace_id 贯穿 + 本地落库 + Langfuse |
| FR-72（新） | 用量可见（模型/耗时/token/重试） | `llm_call` 记录 + `/api/usage` |

### 1.2 非功能约束

| 编号 | 约束 | 落地 |
|---|---|---|
| NFR-02 | 数据本地 | 用量落**本地**；Langfuse 可选且指向**用户自己的**实例 |
| NFR-04 | 响应时间 | 超时保护 + 退避上限；不无限重试 |
| NFR-08 | 可测试 | mock 路径不变；真实路径独立冒烟测试 |
| NFR-03 | 密钥安全 | Key 与上报内容脱敏，永不落日志 |

---

## 2. LLM 调用加固（`llm.py`）

### 2.1 错误分类

```python
class LLMError(Exception): ...
class RateLimited(LLMError):      # 429 + tpm/rpm limit
    retryable = True
class QuotaExhausted(LLMError):   # 429 + insufficient_quota（或 model route not found）
    retryable = False             # 重试无意义 → 换模型
class LLMTimeout(LLMError):       # 请求超时
    retryable = True
class LLMNetworkError(LLMError):  # 连接失败
    retryable = True
class LLMParseError(LLMError):    # 返回内容无法解析为预期结构
    retryable = False
```

分类依据（实测报文）：
| 报文特征 | 分类 |
|---|---|
| `"code":"429"` + `tpm/rpm limit` 或 `rate_limit_error` | `RateLimited` |
| `insufficient_quota` | `QuotaExhausted` |
| `model route not found` | `QuotaExhausted`（该模型未开通） |

### 2.2 重试与退避

```
attempts = 3
delay(attempt) = min(2 ** attempt, 8) + random(0, 0.5)   # 1s, 2s, 4s
```

- 仅 `retryable=True` 的错误重试
- `QuotaExhausted` **立即切下一个模型**（不等待）
- 全部模型耗尽 → 抛 `QuotaExhausted`，由上层返回明确的用户可读错误

### 2.3 模型降级链

```python
MODEL_CHAIN = ["deepseek-v4-flash", "kimi-k3", "sensenova-u1-fast"]   # 可由 .env 覆盖
```

- 主模型 = `LLM_MODEL`；链 = `LLM_MODEL` + `LLM_FALLBACK_MODELS`（逗号分隔）
- 实测依据：商汤网关各模型**配额独立**（`deepseek-v4-flash`/`kimi-k3` 可用，`glm-5.2` 额度耗尽）

### 2.4 超时

- 单次请求超时 `LLM_TIMEOUT_SEC=60`（可配）
- 生成场景 prompt 较长，默认放宽

### 2.5 mock 路径不变

`is_mock()` 语义与现有完全一致；mock 时不走网络、不记录用量（或记录为 `mock`）。

---

## 3. trace 上下文传播

```
[SPA] 生成请求
   ↓ (Java 生成 traceId = UUID)
[Java] MaterialController.generate()
   ├─ 设置 TraceContext.set(traceId)
   ├─ PythonClient 每个请求带 header: X-Trace-Id: <traceId>
   └─ 响应体附带 trace 摘要
   ↓
[Python] main.py 中间件读取 X-Trace-Id → contextvars.set(trace_id)
   ↓ 贯穿 understand / brief / draft / clip / qa / learn
[LLM] 每次调用带上 trace_id 记录
```

实现：
- Java：`ThreadLocal<String> TraceContext`（Controller 层 set/clear）
- Python：`contextvars.ContextVar("trace_id")`（中间件 set，天然支持并发/线程池继承）

> 线程池注意：`ThreadPoolExecutor` 不会自动继承 `contextvars` —— 需在提交任务时显式传递
> （`copy_context()` 或闭包捕获），这点在实现时必须处理，否则子线程 trace 丢失。

---

## 4. 本地用量落库

### 4.1 存储位置（保持架构边界）

**Python 不碰数据库**（既有边界）。故：

```
Python 写入  →  <data-dir>/llm-usage.jsonl     （追加写，零依赖）
Python 暴露  →  GET /usage/summary             （读 JSONL 聚合）
Java 转发    →  GET /api/usage                 （透传）
```

### 4.2 记录结构（JSONL 每行一条）

```json
{"ts":"2026-09-11T15:20:01Z","trace_id":"...","scene":"draft","platform":"xhs",
 "model":"deepseek-v4-flash","attempt":2,"ok":true,"error_type":null,
 "prompt_chars":1820,"completion_chars":640,
 "prompt_tokens":512,"completion_tokens":180,"duration_ms":4380}
```

**脱敏红线**：**不写 prompt/response 正文**，只写长度与 token。避免本地文件成为泄漏源。

### 4.3 聚合查询

`GET /usage/summary?limit=N` 返回：
```json
{"total_calls":42,"failures":3,"retries":5,"avg_duration_ms":3900,
 "by_model":{"deepseek-v4-flash":38,"kimi-k3":4},
 "by_scene":{"draft":20,"qa":12,"understand":1,"learn":5},
 "recent":[{...最近 N 条...}]}
```

---

## 5. Langfuse 接入

### 5.1 Python 侧

- 依赖：`langfuse`（加入 `requirements.txt`）
- 配置：`LANGFUSE_ENABLED` / `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`（已在 `.env`）
- 埋点位置（`llm.chat()` 内一层）：
  - span `llm.chat`，attributes：`model` / `attempt` / `duration_ms` / `trace_id`
  - generation 记录 input/output（**上报到用户自己的实例**，可含正文）
- **未启用时零开销**（不初始化客户端）

> 实现前须按 `langfuse` skill 要求**先查官方文档**确认当前 SDK 版本与 API 形态，不凭记忆写。

### 5.2 Java 侧

移植 `D:\java\lwx-ai-agent\...\LangfuseReporter.java`：
- 纯 JDK HttpClient，端点 `{host}/api/public/ingestion`
- 认证 `Basic base64(public:secret)`
- `sendAsync` fire-and-forget，失败仅 warn
- 上报 `trace-create`（起点）+ `span-create`（Java 编排阶段耗时）

### 5.3 观测到的链路形态

```
trace: generate-material (traceId)
 ├─ span: java.orchestrate         (查素材/模板/画像/复盘组装)
 ├─ span: python.understand
 ├─ span: python.xhs   ├ brief ├ draft ├ qa
 ├─ span: python.douyin ├ ...
 └─ span: java.persist
```

---

## 6. 串行化降级（应对极紧配额）

实测：**连续两次调用即 429**。多平台并行（4 并发）必然大面积失败。

策略：
```
默认：并行（ThreadPoolExecutor, max_workers=4）
检测到 RateLimited：本轮切换为串行 + 每次调用后固定间隔（可配 LLM_MIN_INTERVAL_MS，默认 1500ms）
```

实现：`generate.py` 的调度器读取一个"限流标志"（本轮内有效），决定并行或串行。

---

## 7. 前端

生成结果区新增一行 trace 摘要（**不暴露 prompt**）：

```
✅ 生成完成 · 模型 deepseek-v4-flash · 耗时 18.4s · 重试 1 次 · trace 3f2a9c1b
```

数据来源：Java 生成响应体新增字段：
```json
{"trace_id":"3f2a9c1b...","usage":{"model":"deepseek-v4-flash","duration_ms":18400,"retries":1}}
```

---

## 8. 测试方案

| 层 | 内容 | 数量 |
|---|---|---|
| Python 单元 | 错误分类（各类报文 → 正确异常类型） | +5 |
| Python 单元 | 退避序列正确、不重试 `QuotaExhausted`、降级链切换 | +4 |
| Python 单元 | JSONL 写入与聚合（含 token 缺失兜底） | +3 |
| Python 单元 | trace_id 上下文传播（含线程池场景） | +2 |
| **真实冒烟** | `@pytest.mark.real`：真实调用 understand + 1 平台 draft | +2（无 Key 自动 skip） |
| Java 单元 | trace header 透传、usage 转发 | +3 |
| 端到端 | 真实链路：素材 → 5 平台生成 → 断言 trace 与 usage 落库 | 1 条 |
| 端到端 | 模拟 429 → 断言重试后成功且 `attempt>=2` | 1 条 |

**真实模式冒烟测试**（关键，防止 mock 掩盖）：
```python
@pytest.mark.real
def test_real_mode_smoke():
    if get_settings().llm_mock or not get_settings().llm_api_key:
        pytest.skip("未配置真实 LLM")
    out = chat("只回复：OK", temperature=0)
    assert out
```
`pytest.ini` 注册 marker；CI 默认跳过，本地显式 `-m real` 运行。

---

## 9. 兼容与边界

- **mock 全链路不受影响**：加固只作用在真实分支
- `POST /api/materials`、`/generate` 响应**新增字段**（向后兼容）
- 用量 JSONL 与 `llm_call` 均**不进 git**（`data*/` 已忽略）
- Langfuse 未启用时**零额外依赖开销**（懒初始化）
