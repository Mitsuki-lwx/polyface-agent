# 规格说明书：长任务预算与部分失败保留

- 文档类型：spec（事前三件套之一）
- 版本：v1.0 · 日期：2026-09-16
- 配套：`43-长任务预算与部分失败保留-task.md`、`45-长任务预算与部分失败保留-checklist.md`
- 关联：FR-70 / FR-42 / NFR-01 / ADR-002 / ADR-004

---

## 1. 核心原则

```
多平台生成是「可部分成功」的操作，不是一个原子事务。
单平台失败 → 记录原因并继续；已完成平台的产物必须交付。
```

**反例（当前行为）**：平台 A 成功、平台 B 抛异常 → 整个请求失败 → **A 的成果也丢掉**。

## 2. Python 侧改动

### 2.1 逐平台容错

```python
def generate(req) -> tuple[dict, list[PlatformDraft], bool, list[dict]]:
    """... 返回 (structured, drafts, used_mock, failures)"""
    results: list[PlatformDraft] = []
    failures: list[dict] = []

    for i, code in enumerate(req.platforms):
        if i > 0 and interval > 0:
            time.sleep(interval)
        try:
            results.append(_submit(code))
        except Exception as e:               # ← 单平台失败不致命
            logger.exception("platform %s failed", code)
            failures.append({
                "platform": code,
                "error": f"{type(e).__name__}: {str(e)[:300]}",
            })
    return structured_dict, results, used_mock, failures
```

**关键**：
- 容错**只包在单平台调用外层**，不改 `_generate_one` 内部逻辑
- 失败**必须记录**（日志 + `failures`），不静默吞掉
- 全部失败时仍返回正常结构（`drafts=[]`, `failures` 全列），由上层决定如何呈现

### 2.2 响应模型

```python
class GenerateFailure(BaseModel):
    platform: str
    error: str

class GenerateResponse(BaseModel):
    structured: StructuredMaterial
    drafts: list[PlatformDraft]
    used_mock: bool
    failures: list[GenerateFailure] = Field(default_factory=list)   # 新增
```

**兼容性**：新增字段有默认值 → 老调用方不受影响。

## 3. Java 侧改动

### 3.1 超时可配置

```yaml
polyface:
  llm:
    base-url: http://127.0.0.1:8000
    # 生成属长任务：单平台实测可达 250s（上游限流时）。
    # 这是**显式预算**，不是"越大越好"——默认单平台以控制风险。
    timeout-sec: ${POLYFACE_LLM_TIMEOUT_SEC:240}
```

`PythonClient` 读取该值构造 `HttpClient`；启动时日志打印实际超时值。

### 3.2 部分落库

```java
JsonNode genResp = python.postGenerate(pyBody);
JsonNode draftsNode = genResp.path("drafts");
JsonNode failuresNode = genResp.path("failures");

// 成功的照常落库
for (JsonNode pd : draftsNode) { ... store.insertDraft(...) ... }

// 失败的不阻塞，直接透出
if (failuresNode.isArray() && !failuresNode.isEmpty()) {
    out.set("failures", failuresNode);
    log.warn("material {} 部分平台失败：{}", id, failuresNode);
}
out.put("ok_count", draftsNode.size());
out.put("fail_count", failuresNode.size());
```

**超时处理**：捕获读超时 → **返回已落库的部分** + `failures` 记入"超时未完成"，而**不是** 502。
（注意：Java 侧逐稿落库是在全部返回后才做，若超时则一份都没有。**改进**：Python 已串行生成完才返回整体响应，因此 Java 侧超时意味着**一份都还没落**——故超时响应要明确告知"未完成"，并让用户可重试，**不谎报成功**。）

### 3.3 生成响应扩展

```json
{
  "material_id": 1, "used_mock": false,
  "ok_count": 1, "fail_count": 1,
  "failures": [{"platform": "douyin", "error": "LLMError: ..."}],
  "drafts": [...], "trace_id": "..."
}
```

## 4. 前端改动

| 区域 | 改动 |
|---|---|
| 平台勾选 | **默认只勾选 1 个**（小红书）；勾选 >1 时提示「每个平台约 1~4 分钟，且上游限流时更久」 |
| 生成状态 | `✅ 完成（成功 X / 失败 Y）`；有失败时用醒目色 |
| 失败展示 | 列出「❌ 平台名：原因（截断）」 |
| 重试 | 新增「🔁 重试失败平台」按钮 → 只对失败平台再发一次生成请求 |
| 超时 | 前端请求失败时提示"可能因生成超时，已完成部分已保存，可重试失败平台" |

## 5. 测试规格

### 5.1 Python

| # | 场景 | 期望 |
|---|---|---|
| 1 | 2 平台，第 2 个抛异常 | `drafts` 含第 1 个；`failures` 含第 2 个 |
| 2 | 全部平台失败 | `drafts=[]`；`failures` 全列；**不抛异常** |
| 3 | 单平台成功 | `failures=[]` |
| 4 | 失败信息含异常类型 | `error` 形如 `LLMError: ...` |

### 5.2 Java

| # | 场景 | 期望 |
|---|---|---|
| 5 | 响应含 failures | 透传到 out，`fail_count` 正确 |
| 6 | 成功平台已落库 | `store.draftsByMaterial(id)` 含成功平台 |
| 7 | 全失败 | 返回 200 + `fail_count>0`，**不是 502** |
| 8 | 超时配置可读 | `@Value` 注入生效（config 类测试） |

### 5.3 端到端 + 浏览器

| # | 场景 |
|---|---|
| 9 | `scripts/e2e_partial.py`：1 个合法平台 + 1 个**不存在的平台** → 部分成功保留 |
| 10 | 浏览器：默认单平台；生成后能看到计数；失败时可重试 |

## 6. 文档勘误

| 文档 | 现状 | 改为 |
|---|---|---|
| `docs/29` §3 | "5 平台可能 >10 分钟" | 标注为**预估**，并给出单平台实测区间（100~250s，受上游限流影响） |
| `docs/34` §3.2 | 记录了 250s | 补充说明：**已通过部分失败保留 + 超时可配缓解** |
| README | 未提耗时预期 | 明确"单平台生成通常 1~4 分钟，取决于上游额度" |

## 7. 边界与诚实说明

- **不承诺**"一定能生成完"：上游额度紧张是外部约束，本任务只保证**失败时不留空白、可重试**
- **不做**后台任务队列（评估明确不需要立刻上）；若单平台仍常超时，下一阶段再评估轻量异步方案
- 超时**不是无限延长**，而是显式预算 + 更早失败 + 可重试
