# 规格说明书：质检加固 + 观测修复

- 文档类型：spec（事前三件套之一）
- 版本：v1.0 · 日期：2026-09-15
- 配套：`35-质检加固与观测修复-task.md`、`37-质检加固与观测修复-checklist.md`
- 关联：FR-60 / FR-70 / FR-71 / ADR-009 / ADR-016

---

## 1. 质检判定规格（FR-60 加固）

### 1.1 判定原则：fail-closed

```
只有当【所有来源均无阻断问题】且【结构完整】时，才允许 passed=true
解析不确定 → 判不通过（而非默认通过）
```

### 1.2 三类问题源统一合并

| 来源 | 内容 | 处置 |
|---|---|---|
| **规则 issues** | 标签超限/格式非法/标题超长/红线词等 | **阻断** → passed=false |
| **规则 warnings** | 正文含素材无依据的数字等 | 保留在 warnings，**必须在报告与 UI 可见**（不阻断，但不得丢弃） |
| **结构 issues**（新增） | 正文空、标题空、结构异常 | **阻断** → passed=false |
| **LLM issues** | 模型自评发现的问题 | **阻断** → passed=false（原实现未参与判定，属 bug） |
| **LLM warnings** | 模型自评告警 | 并入 warnings |

### 1.3 严格布尔解析（修复 `bool("false") == True`）

```python
def _strict_bool(value) -> tuple[bool, str | None]:
    """严格解析 LLM 返回的 passed 字段。

    返回 (值, 错误说明)。仅接受真正的布尔（含 JSON true/false）。
    字符串/数字/None 一律不合约 —— **不得**依赖 Python 的真值转换。
    """
    if isinstance(value, bool):
        return value, None
    return False, f"模型返回的 passed 字段类型不合约：{type(value).__name__}={value!r}"
```

**判定**：类型不合约 → **passed=false**，并把说明记入 issues（用户可见）。

### 1.4 结构校验（新增）

```python
def _structure_issues(draft) -> list[str]:
    out = []
    body = (draft.body or "").strip()
    if not body:
        out.append("正文为空：不可交付")
    if not any((t or "").strip() for t in (draft.titles or [])):
        out.append("标题为空：不可交付")
    return out
```

> 依据：评估明确指出「正文为空，标题和标签合法 → 规则质检通过」是最基本的可交付性没被守住。

### 1.5 统一判定函数

```python
def _merge_qa(rule_issues, rule_warnings, structure_issues,
              llm_passed, llm_type_error, llm_issues, llm_warnings) -> QaReport:
    blocking = list(rule_issues) + list(structure_issues) + list(llm_issues)
    if llm_type_error:
        blocking.append(llm_type_error)
    passed = (not blocking) and llm_passed
    warnings = list(rule_warnings) + list(llm_warnings)     # ← 修复：规则 warnings 不再丢弃
    return QaReport(passed=passed, issues=blocking, warnings=warnings)
```

**关键点**：
- `llm_passed` **仅影响**能否通过；**不能覆盖**任何阻断问题
- `rule_warnings` **必须**保留
- 三源 issues 全部出现在 `QaReport.issues` 中

## 2. 观测修复规格（FR-71 加固）

### 2.1 `trace_span` 的正确实现

**缺陷**（现实现）：
```python
try:
    with lf.start_as_current_observation(...) as span:
        yield span          # ← 业务异常在此抛出
except Exception as e:
    logger.warning(...)
    yield None              # ← 再次 yield：RuntimeError: generator didn't stop after throw()
                            #   → 业务异常被替换为 RuntimeError，再被 classify() 归为普通 LLMError
                            #   → **限流重试语义被破坏**
```

**正确实现**（只在"创建阶段"容错；业务异常原样传播）：
```python
@contextlib.contextmanager
def trace_span(name, trace_id=None, **attrs):
    if not enabled():
        yield None
        return
    lf = _get_client()
    if lf is None:
        yield None
        return
    try:
        kwargs = {"as_type": "span", "name": name}
        if trace_id:
            kwargs["trace_context"] = {"trace_id": trace_id}
        ctx = lf.start_as_current_observation(**kwargs)
    except Exception as e:            # 仅创建失败才降级
        logger.warning("Langfuse span 创建失败：%s", e)
        yield None
        return
    with ctx as span:                 # 业务异常由此原样抛出，contextlib 负责传播
        yield span
```

**不变式**：`trace_span` 包裹的代码抛出的异常，**类型与内容必须与不使用观测时完全一致**。

### 2.2 `flush()` 先判开关

```python
def flush() -> None:
    if not enabled():        # ← 未启用绝不触碰客户端
        return
    lf = _get_client()
    ...
```

同时 **`get_client()` 不得在未启用时被调用**（回归测试需覆盖）。

## 3. 测试规格（≥20 个确定性场景）

### 3.1 质检场景（`tests/test_qa_hardening.py`）

| # | 场景 | 期望 |
|---|---|---|
| 1 | 正文含素材无依据数字 | passed 可 true，但 **warnings 非空且包含该数字** |
| 2 | LLM 返回 `"passed": "false"`（字符串） | **passed=False** + issues 含"类型不合约" |
| 3 | LLM 返回 `"passed": 1`（数字） | passed=False + 类型说明 |
| 4 | LLM 返回 `"passed": true` 但 issues 非空 | **passed=False** |
| 5 | 正文为空 | **passed=False** + "正文为空" |
| 6 | 正文为纯空白 | passed=False |
| 7 | 标题全为空 | passed=False + "标题为空" |
| 8 | 标签超上限 | passed=False |
| 9 | 标签含 `#` | passed=False |
| 10 | 规则 issues 非空 + LLM passed=true | passed=False |
| 11 | 全部干净 | passed=True |
| 12 | LLM 返回缺 `passed` 字段（None） | passed=False |
| 13 | LLM 返回 warnings 非空 | 并入报告 warnings |
| 14 | 规则 warnings 与 LLM warnings 同时存在 | **两者都在**（验证不丢） |
| 15 | mock 模式空正文 | passed=False（规则也要守） |

### 3.2 观测场景

| # | 场景 | 期望 |
|---|---|---|
| 16 | `trace_span` 内抛 `RateLimited` | **抛出的仍是 `RateLimited`**（不被替换为 RuntimeError） |
| 17 | `trace_span` 内抛任意业务异常 | 异常类型与消息**完全一致** |
| 18 | span 创建失败（模拟 `start_as_current_observation` 抛错） | 降级为 no-op，业务正常执行 |
| 19 | `LANGFUSE_ENABLED=false` 时调用 `flush()` | **不初始化客户端**（`_get_client` 未被调用） |
| 20 | `LANGFUSE_ENABLED=false` 时 `trace_span` | 直接 yield None，零开销 |
| 21 | 正常路径 | span 被正确创建与结束 |

### 3.3 回归

| # | 场景 |
|---|---|
| 22 | 既有 Python 38 测试全绿 |
| 23 | 既有 Java 28 测试全绿 |

## 4. 表述边界（避免误导）

| 位置 | 现状 | 改为 |
|---|---|---|
| UI 生成结果 | "✅ 完成" | 保留，但 QA 未通过时明确显示 **"⚠️ 质检未通过：N 个问题"** 并列出 |
| UI 质检通过 | 「QA 通过」 | **「未发现阻断问题（仍需人工终审）」** |
| 文档 | 未区分"未发现错误"与"保证正确" | 明确：质检是**启发式筛查**，不构成事实保证 |

> 依据评估建议：**"没有发现错误"应与"保证正确"明确区分。**

## 5. 兼容性

- `QaReport` 结构不变（`passed` / `issues` / `warnings`），仅**判定更严格**、`warnings` 更完整
- mock 模式同步加严（空正文等结构问题在 mock 下也拦）
- 接口契约不变，前端无需改动即可获得更准确的 QA 状态（UI 文案微调除外）
