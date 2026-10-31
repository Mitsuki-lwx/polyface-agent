# 验收清单（checklist）：LLM 运行时加固 + 全链路可观测

- 文档类型：checklist（事前三件套之三）
- 版本：v1.0 · 日期：2026-09-11
- 配套：`26-LLM运行时加固与可观测-task.md`、`27-LLM运行时加固与可观测-spec.md`
- 判定方式：每条独立判定，全部 `[x]` 方可交付

---

## A. LLM 调用加固

- [x] A1 超时抛 `LLMTimeout`
      `test_classify_buckets` ✓（`Request timed out` → `LLMTimeout`）；
      超时值来自 `llm_timeout_sec`（默认 60，可配 ✓）
- [x] A2 `429 tpm/rpm` → `RateLimited`（可重试）
      `test_classify_buckets` ✓ / `test_retryable_flags_match_the_policy` ✓
- [x] A3 `429 insufficient_quota` → `QuotaExhausted`（不重试）
      `test_classify_buckets` ✓；**且额度优先于限流** ✓（两条特征同时出现时，
      否则会把"没额度"当"限流"白等三轮）—— `test_quota_beats_rate_limit_when_both_features_present` ✓
- [x] A4 `model route not found` → `QuotaExhausted`
      `test_classify_buckets` ✓（`model is not found` 也是 ✓）
- [x] A5 连接失败 → `LLMNetworkError`（可重试）
      `test_classify_buckets` ✓（`connection error` ✓、上游 5xx ✓）
- [x] A6 退避 = `min(2^n, 8) + jitter`
      `test_backoff_series_is_capped_at_8s_plus_jitter` ✓（每档取 20 次，
      都落在 `[2^n, 2^n+0.5)` 或 `[8, 8.5)` 里 ✓）
- [x] A7 重试上限 3
      `test_retries_up_to_max_attempts_then_moves_on` ✓：`llm_max_attempts=3` →
      **同一个模型恰好试 3 次**，然后抛 `RateLimited`（消息含"已尝试"）✓
- [x] A8 额度耗尽不重试、立即换模型
      `test_quota_does_not_retry_same_model` ✓：`calls == ["m1", "m2"]` ——
      m1 **只试一次** ✓
- [x] A9 全耗尽 → 可读错误 + 模型列表
      `test_all_models_exhausted_error_lists_what_was_tried` ✓：
      `所有模型均不可用（已尝试：m1、m2、m3）` ✓，且真的试过三个 ✓
- [x] A10 mock 模式不介入
      `test_mock_mode_never_touches_the_hardening` ✓ —— 分流在**调用方**
      （`if llm.is_mock():` 走模板），`run_brief` 在 mock 下**一次请求都不发** ✓
      （⚠️ 第一版我测的是 `chat()` 层，测错层了 ✗ —— mock 分流本来就不在 `chat` 里）

## B. 模型降级链

- [x] B1 降级链的结构：**主模型 + 追加项（去重保序）**
      ⚠️ **具体默认模型已变** ✗：现在主模型是 `.env` 里的 `cline-pass/deepseek-v4.1-flash`，
      追加项 `deepseek-v4-pro, glm-5.2, sensenova-6.8-flash-lite`
      （`config.py` 写明理由：**kimi-k3 常限流、sensenova-u1-* 报 model is not found，已不作备选**）。
      判据里的三个名字是 2026-09 的；**链的机制没变** ✓、可配 ✓
- [x] B2 链可覆盖
      `test_chain_is_primary_then_fallbacks_deduped` ✓（`b, c ,a` → `[a,b,c]`，去重保序 ✓）
- [x] B3 主模型耗尽 → 切备用并成功
      `test_fallback_records_the_model_that_actually_worked` ✓（返回 `ok`，`calls == ["main","backup"]`）
- [x] B4 用量里看得见 model 变化
      同上测试 ✓：`models == ["main", "backup"]`，且第一条 `ok=False`（带 error_type）✓
- [x] B5 显式 model 不走链
      `test_explicit_model_bypasses_the_chain` ✓

## C. trace 上下文贯穿

- [x] C1 Java 生成 traceId
      `scripts/e2e_fr70.py` 的实测记录 ✓：响应与响应头都带 `X-Trace-Id` ✓
- [x] C2 Java → Python 带 `X-Trace-Id`
      同上 ✓（Java 侧透传，Python 侧中间件读出 ✓）
- [x] C3 Python 中间件存进 `contextvars`
      `trace.set_trace_id()` + `_trace_id: ContextVar` ✓
- [x] C4 各场景都能取到
      e2e 记录 ✓（Langfuse 里一次生成 **13 个 observation** ✓，含 qa/draft span ✓）
- [x] C5 线程池内不丢 trace_id —— **原先它是坏的** ✗→✓
      **实测（2026-10-30）**：子线程里 `current_trace_id()` 会**现生成一个新的** ✗。
      根因：旧实现 `run_in_context` 是在**子线程**里 `copy_context()` —— 拷到的是子线程的空上下文 ✗。
      修法：新 `bind_in_context()` **在调用方线程**复制好上下文、返回可调用再交出去 ✓。
      回归测试 `test_trace_id_survives_thread_pool` ✓ + 反证 `test_thread_pool_without_run_in_context_loses_it` ✓。
      ⚠️ 一直没暴露是因为 `llm_parallel` **默认 false**（串行不走线程）✓
- [x] C6 响应体含 trace_id
      e2e 记录 ✓：`PASS 2 响应含 trace_id`
- [x] C7 全局一致
      e2e 记录 ✓：`PASS 2 trace_id 前后一致`（响应体、响应头、用量记录三处相同 ✓）
- [x] C8 无 header → 自行生成
      `test_missing_header_generates_one_instead_of_failing` ✓（32 位 hex ✓）
      `test_set_and_read_roundtrip` ✓

## D. 本地用量落库

- [x] D1 每次调用一行 JSONL
      `test_every_call_appends_one_jsonl_line` ✓（3 次 → 3 行，每行都是合法 JSON）
- [x] D2 字段齐全
      `test_record_has_the_required_fields` ✓
- [x] D3 字符数 + token
      同上 ✓；**拿不到 token 时留 `null`**，不编一个数 ✓
- [x] D4 不含正文（脱敏红线）
      `test_jsonl_never_contains_prompt_or_response_text` ✓（正文与手机号都查不到 ✓）
- [x] D5 聚合齐全
      `test_summary_aggregates` ✓（total 3 / failures 1 / retries 2 / avg 200 /
      by_model / by_scene / recent 倒序 ✓）
- [x] D6 Java 转发 `/api/usage`
      e2e 记录 ✓：`PASS 3 /api/usage 可用` + `有调用记录 total=598` ✓
- [x] D7 不进 git
      `.gitignore` 里有 `data/`（默认位置）+ `*.log`；用量文件在 `data/llm-usage.jsonl` ✓
      （`test_usage.py` 用 `tmp_path`，不碰仓库 ✓）
- [x] D8 坏行容错
      `test_corrupt_lines_do_not_break_aggregation` ✓（半行 + 非 JSON → 跳过，
      剩下 2 条照常聚合 ✓）
- [x] D9 mock 标出来
      `test_mock_calls_are_marked` ✓（`mock=true`、`model="mock"` —— 不伪装成真模型名 ✓）

## E. Langfuse 集成

- [x] E1 关掉时零开销
      `test_langfuse_disabled_means_enabled_is_false` ✓ +
      `test_openai_client_returns_plain_client_when_disabled` ✓
      （拿到的是**普通 `OpenAI` 客户端**，不是 Langfuse 包装 ✓）+ 缺一个 key 也算关闭 ✓
- [x] E2 Python 侧能上报（**2026-10-20 实测**，本轮 Langfuse 不可达 ✗ 未复验）
      记录：一次生成在 Langfuse 里有 **13 个 observation**，含
      `GENERATION OpenAI-generation`、`SPAN qa`、`SPAN draft` ✓
- [x] E3 Java 侧能上报
      同上记录 ✓（`SPAN generate:xhs` 是 Java 建的 root ✓）
- [x] E4 同一 traceId
      同上记录 ✓（用 e2e 的 trace_id 去 `GET /api/public/traces/{id}` 查到了整棵树 ✓）
- [x] E5 上报失败不影响业务 —— **本轮实测到了** ✓
      **2026-10-30**：Langfuse 没起（`连接被拒`），每次跑分只打一行
      `Langfuse 不可达（<urlopen error …>）：观测本次运行降级为关闭` ✓，
      评测照跑完、结果正常 ✓✓ —— 这就是 E5 的活证据
- [x] E6 链路在 Langfuse 里可见（2026-10-20 实测，本轮不可达未复验）
      同上记录 ✓
- [x] E7 不含 API Key
      上报走 SDK 的公开参数（trace/observation 的 name/input/output），**不传 key** ✓；
      `usage` 那条脱敏红线另有测试 ✓
- [ ] **无法核实** ✗ —— 这是"过程要求"（实现前查过文档），
      **没有可观测的痕迹**（没有记录指到哪份文档、哪个 SDK 版本）。
      记录里的 observation 名字（`OpenAI-generation`）说明用的是 **Langfuse v4 的 OpenAI 集成** ✓，
      但"事前查过"这件事本身验不了。判据原样保留未勾。

## F. 限流串行化降级

- [ ] **与实现相反（如实记）** ✗
      `llm_parallel: bool = False` —— **默认是串行**。`config.py` 写明了理由：
      「实测上游 tpm/rpm 极紧（连续两次调用即可能 429），4 并发会被大面积拒绝；
      串行 + 间隔才跑得完」。
      `test_parallel_is_off_by_default_and_interval_is_configurable` ✓ 把这个现状钉住了。
      **不打算改回并行**（改了就是重走一遍 429）。判据原样未勾。
- [ ] **没实现** ✗
      是**静态配置**（`llm_parallel` 开/关），没有"运行中检测到 429 再自动转串行"的逻辑。
      判据要的是**自适应**，实现给的是**配置**。原样未勾。
- [x] F3 串行有最小间隔
      `generate.py`：`if i > 0 and interval > 0: time.sleep(interval)` ✓；
      默认 `llm_min_interval_ms = 1500` ✓
- [x] F4 串行能跑完 5 平台（真实环境）
      **2026-10-30**：真实 Key、5 平台 × 1 条 → **5/5 通过**、408.4s、0 调用失败 ✓
      （评测集那一轮测的 ✓，走的就是默认串行 ✓）
- [x] F5 可配
      `test_parallel_is_off_by_default_and_interval_is_configurable` ✓（`4000` 生效 ✓）

## G. 前端

- [x] G1 摘要齐全
      `index.html` 的 `showTraceSummary()`：
      `📊 模型 … · 调用 N 次 · 重试 M 次 · trace {前 8 位}` ✓
- [x] G2 不含正文
      源码注释写明"**不展示 prompt 正文**（避免误截图泄漏素材）"，且只读 `/api/usage` ✓
      —— 而用量文件里本来就没有正文 ✓（D4 有测试 ✓）
- [x] G3 mock 显示 "mock"
      `const label = genResult.used_mock ? "mock" : …` ✓
- [x] G4 失败可读
      `renderFailures()` 逐平台显示 `f.error` ✓（那正是分类后的错误文本 ✓，
      如 `QuotaExhausted: 所有模型均不可用（已尝试：…）`）

## H. 测试

- [x] H1 Python 单元测试全绿
      **992 passed / 3 skipped**（`-m "not real"`）✓
      —— 其中 **33 条是本轮新加的**（`test_llm_hardening.py` 18 + `test_usage.py` 7 +
      `test_trace_context.py` 8），原先这三块**一条都没有** ✗
- [x] H2 Java 单元测试全绿
      **134 passed** ✓
- [x] H3 真实模式冒烟存在、可独立跑
      **本轮补的** ✓：`python-service/tests/test_real_smoke.py` +
      `conftest.pytest_configure` 注册 `real` 标记。
      实测：`pytest -m real tests/test_real_smoke.py` → **2 passed** ✓
      （真调一次 LLM ✓、落用量 ✓、脱敏 ✓）
- [x] H4 无 Key 自动 skip
      实测 `if not (get_settings().llm_api_key or "").strip(): pytest.skip(...)` ✓；
      另有 `test_is_mock_falls_back_when_key_missing` ✓ 钉住"没 Key 回退 mock"。
      ⚠️ 第一版用 `os.getenv("LLM_API_KEY")` 判 —— **有 Key 也 skip** ✗（Key 在 `.env` 里，
      经 settings 读，不在 `os.environ`）
- [x] H5 端到端
      **2026-10-30**：真实 5 平台 → 5/5 通过 ✓；
      同轮报告里每格都有 `_meta.tokens`（`18 次 · 33325+59409 tok`）✓
- [x] H6 429 → 重试后成功，attempt 可查
      `test_retries_up_to_max_attempts_then_moves_on` ✓（用量记录里每次 `attempt` 递增 ✓）；
      `test_llm_json_retry.py` 也覆盖了"第一次坏、第二次好 → 成功且真调了两次" ✓
- [x] H7 mock 全链路无回归
      **992 passed** ✓（远超当年的 61 条）；评测集那一轮 5 平台跑的就是真实链路 ✓

## I. 文档与交付

- [x] I1 SRS 有 NFR-10 与 FR-70~72
      `grep -c NFR-10` = 1 ✓、`FR-70/71/72` 命中 3 处 ✓
- [x] I2 ADR-015 在
      `grep -c "ADR-015" docs/08-技术决策记录-ADR.md` = 1 ✓
- [x] I3 交付说明在 ✓（`docs/29-…-交付说明.md`）
- [x] I4 交付说明含实测记录
      （本文档末尾的 `scripts/e2e_fr70.py` 记录 + Langfuse 独立复核 ✓；
      ⚠️ 本轮又补了一份更新的：5 平台 × 1 条 / 408.4s / 5-5 通过 ✓）
- [x] I5 已提交、`.env` 未入库
      `git status` 干净 ✓；`.gitignore` 有 `.env` ✓

---

## 验收结论

| 项 | 结果 | 备注 |
|---|---|---|
| A~I 全部勾选 | **58 / 61** | 3 条未勾，见下 |
| 真实链路验证 | ✅ | 5 平台 × 1 条全过（408.4s，真实 Key，非 mock） |
| mock 回归 | ✅ | `992 passed / 3 skipped`（`-m "not real"`） |
| Langfuse 可见性 | ✅（2026-10-20 实测；本轮不可达） | 一次生成 13 个 observation |
| 未通过项 | 3 | E8 / F1 / F2 —— 都不是"漏做"，是"没做/与判据相反"，见下 |

**3 条未勾的原因**：

- **E8**（实现前查过 Langfuse 官方文档）—— 过程要求，**没有可观测痕迹** ✗。验不了。
- **F1**（默认多平台并行）—— **与实现相反** ✗：`llm_parallel=False` 默认**串行**，
  理由写在 `config.py`（实测上游 tpm/rpm 极紧，4 并发会被大面积拒绝）。
- **F2**（检测到 `RateLimited` 后**自动**转串行）—— **没实现** ✗：
  现在是**静态配置**，没有运行时自适应。

---

## 实机验证记录：`scripts/e2e_fr70.py`（2026-10-20）

```
===== FR-70/71/72 端到端验收 (RUN=170108) =====
  PASS 1 建素材成功
  PASS 2 生成成功
  PASS 2 响应含 trace_id（FR-71）  | 414897af6032450b8833d4b078503722
  PASS 2 响应头含 X-Trace-Id      | 414897af6032450b8833d4b078503722
  PASS 2 trace_id 前后一致
     · 生成耗时 107s，trace=414897af6032450b8833d4b078503722
  PASS 3 /api/usage 可用
  PASS 3 有调用记录  | total=598
  PASS 3 记录含本 trace
  PASS 3 按场景分组
  PASS 3 不含 prompt 正文（脱敏）  | 无正文泄漏
===== Java/Python 链路：10/10 通过 =====
```

### Langfuse 侧**独立复核**（不只信脚本 ✓）

脚本只打印 trace_id；**真的去 Langfuse 查了**（`GET /api/public/traces/{id}`，basic auth 用 `.env` 里的 key）：

```
trace id: 414897af6032450b8833d4b078503722
name: generate:xhs     observations: 13
  - SPAN generate:xhs
  - GENERATION OpenAI-generation | model: vmc/deepseek-v4p1-flash-contributor-fallbacks
  - SPAN qa / SPAN draft / GENERATION …
```

**链路贯穿成立** ✓：一次生成 → 13 个 observation（span + generation 嵌套）落到了 Langfuse。

### 更正一处我自己的错误判断 ✗

我之前说「`e2e_fr70` 要 Langfuse :3000，**本机没起**」—— **这是错的** ✗：
Langfuse 一直在跑（`:3000` 与 `:3030` 均 LISTENING，`.env` 里 `LANGFUSE_ENABLED=true` + key 齐全）。
那是**从脚本 docstring 推断、没有实际探测** ✗ —— 结论先行不等于可以省掉验证。

---

## 本轮验收（2026-10-30）：61 条里 **58 条验掉**，1 个真 bug

> 原先 **0 勾**。这一轮把能验的验掉；A/B/C/D 四节原先**几乎没有专门测试**，本轮补了 **33 条**。

### 挖到并修掉一个真 bug：**线程池里 trace_id 会丢**（C5）

旧实现 `run_in_context()` 是在**子线程**里 `contextvars.copy_context()` ✗ ——
拷到的是**子线程的空上下文**，于是 `current_trace_id()` 会**现生成一个新的** ✗。
实测（新测试直接钉住）：开了 `llm_parallel` 之后，5 个平台会各自生成 5 条互不相干的 trace ✗，
Langfuse 里看不到整棵树 —— 而**不报错、日志照打**。

修法：新的 `bind_in_context()` **在调用方线程**复制上下文、返回可调用再交出去 ✓。
串行路径另拆成 `_call()`（同线程天然继承，不需要绑）✓。

> **为什么一直没暴露**：`llm_parallel` **默认 false**（串行不走线程）✗ ——
> 所以 E2E 记录里那条"trace_id 前后一致"是真的，只是**从没走过并行分支**。

### 补的 33 条测试（原先这三块一条都没有 ✗）

| 文件 | 条数 | 覆盖 |
|---|---|---|
| `test_llm_hardening.py` | **18** | A1~A10（分类/退避/重试上限/换模型/全耗尽/mock）+ B1~B5（链/覆盖/降级/记录/显式）+ F1/F3/F5 |
| `test_usage.py` | **7** | D1~D9（一行一条/字段/token/脱敏/聚合/坏行容错/mock 标记） |
| `test_trace_context.py` | **8** | C3/C5/C8 + E1（关掉 Langfuse 拿到的是普通客户端） |
| `test_real_smoke.py` | **2** | H3/H4（`-m real` 真调一次；无 Key 自动 skip） |

### 本轮**实测**到的 E5（上报失败不影响业务）

Langfuse 在 2026-10-30 **不可达**（连接被拒）—— 每次跑分只打一行
`Langfuse 不可达（…）：观测本次运行降级为关闭`，评测**照跑完、结果正常** ✓✓。
这正好是 E5 要的活证据（比"当年验过一次"更有说服力）。

### F 那一节要说清楚

判据写的是「**默认并行** + 运行中检测到限流转串行」，
实现是「**默认串行 + 可配的最小间隔**」——
两者**不同**，而且现有实现是**实测后改的**（并行会被大面积 429）。
**没有**为了让判据变绿去加"自适应降级"（那是新功能，不是补丁）。
