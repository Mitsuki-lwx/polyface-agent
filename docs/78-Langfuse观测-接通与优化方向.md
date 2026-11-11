# Langfuse 自托管观测 · 接通情况与优化方向

> 2026-11-10。自托管实例跑在 `F:\langfuse`（Docker），**3050 端口** → 容器 3000。

## 一、现状

| 项 | 值 |
|---|---|
| 版本 | Langfuse **4.76.0**（**events_only** 模式）|
| 项目 | `polyface` / org `polyface`，project id `cmvw5kt21000gky07kbjmb87j` |
| 容器 | `langfuse-web`(3050) · `langfuse-worker` · `clickhouse` · `postgres` · `redis` · `minio` —— 全部 healthy |
| 应用配置 | `python-service/.env`：`LANGFUSE_ENABLED=true`、`LANGFUSE_HOST=http://localhost:3050` |
| SDK | `langfuse 4.17.0`，`langfuse.openai` drop-in 可用 |

## 二、接通验证（实测）

1. `GET /api/public/projects` → 200，返回项目 `polyface`（key 有效）
2. 跑一次 `llm.chat(scene="langfuse.flush-probe")` → SDK 无报错，`flush()` 成功
3. ClickHouse `events_core` 里出现该 trace（`trace_id=c5b1f6f9…`），并带 **GENERATION** 子节点
4. UI `http://localhost:3050` 登录页正常（本机配置的账号，密码只在用户手里，**未代填**）

> ⚠️ **坑**：v4 events_only 模式下 `/api/public/traces` 返回
> 「not available on deployments running in v4 events_only mode」，`/api/public/v2/traces` 也是 404。
> **数据在 ClickHouse `events_core`**，不要拿老接口判断"没上报"（我一开始就这么误判了）。

查最近调用：

```bash
docker exec langfuse-clickhouse-1 clickhouse-client --query "
select trace_name, type, round(dateDiff('millisecond',start_time,end_time)/1000,1) sec
from events_core where project_id='cmvw5kt21000gky07kbjmb87j'
order by start_time desc limit 20 format TSV"
```

## 三、真实数据（69 次生成）

| scene | 次数 | 平均 | p95 | 输入 tok | 输出 tok | 失败 |
|---|---:|---:|---:|---:|---:|---:|
| **draft** | 3 | **103.0s** | **209.7s** | 8.8k | 3.5k | 0 |
| generate:xhs | 5 | 28.6s | 49.3s | 7.8k | 3.5k | 0 |
| generate:gzh | 12 | 24.4s | 49.2s | 26.0k | 8.7k | 0 |
| qa | 3 | 22.2s | 31.5s | 6.5k | 1.0k | 0 |
| generate:zhihu | 4 | 19.6s | 28.4s | 8.0k | 2.5k | 0 |
| brief | 3 | 14.9s | 20.7s | 2.2k | 2.4k | 0 |
| generate:bilibili | 4 | 14.8s | 20.8s | 6.3k | 3.0k | 0 |
| understand | 10 | 6.7s | 14.9s | 8.0k | 5.6k | 0 |
| （无 trace 名）| 24 | 10.1s | 35.6s | 18.4k | 9.2k | 2 |

## 四、五个优化方向（按证据强度排序）

### 1. 思考 token 是主要成本，且关不掉

| scene | 推理 token | 可见输出 |
|---|---:|---:|
| draft（最慢那次 227.6s）| **37,818** | 1,445 |
| draft（48.3s）| 4,807 | 1,095 |
| qa（32.7s）| 4,308 | 462 |
| understand | 167 / 172 | 1,098 / 1,181 |

同一个简单问题试了四种关思考的写法，**全部无效**：

| 参数 | 延迟 | 推理 token |
|---|---:|---:|
| 基线 | 2.8s | 77 |
| `chat_template_kwargs.thinking=false` | 1.0s | 70 |
| `reasoning_effort=none` | 1.1s | 60 |
| `enable_thinking=false` | 2.9s | 93 |
| `thinking_budget=0` | 1.0s | 62 |

→ **不能靠参数解决**。可行方向：
- 机械场景（`qa_verify`、`pause_review`、`disfluency`）换**非推理模型**（要试别的模型，不是改参数）
- `draft` 拆步：先出骨架 → 再逐段补写；227s 那一次等于"一个 prompt 里做了一个完整构思"
- 给 `draft` 设输出上限 + 超时（现在 `LLM_TIMEOUT_SEC=240`，一次慢调用就吃掉 4 分钟）

### 2. draft 是长尾之王

平均 103s、p95 210s，**也是唯一接近超时上限的环节**。
→ 并行化（多平台同时生成已经能做，但单平台内的 draft 仍串行）；或按段落并发补写。

### 3. 连接错误 5–9%，重试可能全挂

- 观测里 `APIConnectionError: Connection error.` 出现 2 次；按小时统计 **1/11、1/12**
- 实测 `tests/test_factguard.py` 连续两次因它变红，第三次绿 —— **不是代码回归**
- 现在：3 次尝试，退避 1s/2s/4s，`LLM_FALLBACK_MODELS` **为空**

→ 提高尝试次数到 5、退避拉到 15s，并配一个 fallback 模型（哪怕只是另一个免费通道）。

### 4. 402 额度耗尽会打穿整条链

观测里抓到 `402 insufficient_credits`（`qa_verify` 与一次生成）。
这正是 `cline-pass/` 免费通道存在的原因；但付费模型一旦被配进去，就会以 402 失败。

**已修**（读代码确认：`classify()` 原来只认 `insufficient_quota`，402 落进通用 `LLMError` →
在同一个模型上白重试 3 次）。现在 `insufficient_credits` 与 `status == 402` 都归 `QuotaExhausted`，
会直接切下一个模型；补了两条真实报文样本的单测。

### 5. 缓存有命中，但没吃满

`input_cached_tokens` 出现 1536 / 2350 的命中 —— 说明网关侧有前缀缓存。
→ 把不变的 system prompt + 平台 DNA 放最前面，动态内容放后面，可以继续吃缓存。

## 五、没验证的

- **UI 内翻 trace**（需要登录密码；我只验了登录页可达 + ClickHouse 有数据）
- 自建看板 / 数据集评估（Langfuse 的 eval 功能没碰）
- 402：已确认并修复（见 §4），但**没有真跑一次余额耗尽来验证切换**（余额现在是 0.0099 美元，
  不敢拿来赌）。
