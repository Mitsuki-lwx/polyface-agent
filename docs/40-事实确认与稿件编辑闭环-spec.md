# 规格说明书：事实确认与稿件编辑闭环

- 文档类型：spec（事前三件套之一）
- 版本：v1.0 · 日期：2026-09-15
- 配套：`39-事实确认与稿件编辑闭环-task.md`、`41-事实确认与稿件编辑闭环-checklist.md`
- 关联：FR-20 / FR-34 / FR-42 / ADR-009 / ADR-014

---

## 1. 数据模型（增量迁移，不重建表）

沿用既有 `ensureXxxColumns()` 惯例（`ALTER TABLE ADD COLUMN`，幂等）。

### material 表新增

| 列 | 类型 | 含义 |
|---|---|---|
| `facts_confirmed` | INTEGER DEFAULT 0 | 1=用户已确认事实 |
| `facts_confirmed_at` | TEXT | 确认时间（ISO） |

`facts_json` 已存在，保存**当前生效的事实清单**（确认后即为用户改过的版本）。

### draft 表新增

| 列 | 类型 | 含义 |
|---|---|---|
| `edited_at` | TEXT | 人工编辑时间；非空表示"人工改过" |

正文仍存 `payload_json.body`（编辑即覆盖该字段），**不新增正文字段**，避免双份数据不一致。

## 2. API 契约

### 2.1 保存并确认事实

```
PUT /api/materials/{id}/facts
Request:
{
  "core_message": "…",          // 可选，缺省沿用原值
  "tone": "…",
  "audience": "…",
  "facts": [                     // 全量替换
    {"type": "data", "text": "2023年裸辞，写作月入从0到3万"}
  ],
  "confirm": true                // true=标记已确认；false=仅暂存
}
Response 200:
{ "id": 1, "facts_confirmed": true, "facts_confirmed_at": "2026-09-15T…", "facts": [ … ] }
```
校验：`facts[].text` 非空；`type` 限 `fact|data|quote|…`（复用既有集合），非法则 400。

### 2.2 保存编辑后的稿件

```
PUT /api/drafts/{id}
Request:
{
  "titles": ["…"],
  "body": "…",
  "tags": ["…"],
  "interaction_line": "…",       // 可选
  "cover_suggestion": "…"        // 可选
}
Response 200:
{ "id": 12, "edited_at": "2026-09-15T…", "draft": { …更新后的 payload… } }
```
规则：
- 只更新**允许编辑**的字段；`qa` / `brief` / `clip_sheet` / `rationale` **不可通过此接口改**（防止绕过质检）
- 写入 `edited_at`
- **是否重跑 QA**：本接口**不**自动重跑（避免用户等待与额外额度消耗）；响应中回传 `qa` 原值并在 UI 提示"编辑后建议自行核对"

### 2.3 导出稿件

```
GET /api/drafts/{id}/export?format=md|txt
Response 200 (text/plain; charset=utf-8, Content-Disposition: attachment)
```
- `md`（默认）：标题（主/备选）+ 正文 + 标签 + 互动引导
- `txt`：仅标题 + 正文
- 内容取**当前生效**的 `payload_json`（含人工编辑）

### 2.4 读取单稿（既有，用于历史补齐）

```
GET /api/drafts/{id}   → 完整 payload + qa（已存在，前端此前未调用）
```

## 3. Java → Python：生成时传已确认事实

### 请求体扩展（`POST /api/materials/{id}/generate` 内部转发）

```json
{
  "raw_text": "…",
  "platforms": ["xhs"],
  "confirmed_facts": {                   // ← 新增，可选
    "core_message": "…", "tone": "…", "audience": "…",
    "facts": [{"type":"data","text":"…"}]
  }
}
```

**取值规则**：
- 若 `material.facts_confirmed = true` → 传 `confirmed_facts`（来自 `facts_json`）
- 否则不传（Python 走原路径，重新理解）

## 4. Python 侧改动

### 4.1 请求模型

```python
class GenerateRequest(BaseModel):
    ...
    confirmed_facts: StructuredMaterial | None = None   # 新增，可选
```

### 4.2 跳过重复理解

```python
def generate(req):
    if req.confirmed_facts is not None:
        structured = req.confirmed_facts          # 直接用用户确认的版本
        used_mock = llm.is_mock()                 # 不再调用 understand
    else:
        structured, used_mock = run_understand(...)   # 原路径
```

**必须记录日志**：`user-confirmed facts used; skipping understand`（便于测试与观测断言）。
**观测**：跳过时**不产生** understand 的 LLM 调用（可在 usage 里验证）。

### 4.3 事实校验照常

即使使用已确认事实，QA 的"正文数字 vs 事实清单"校验**照常执行**（事实清单即依据）。

## 5. 前端改动（`index.html`）

| 区域 | 改动 |
|---|---|
| 素材解析结果 | 事实清单改为**可编辑**（每行可改文本、可删；底部"添加一条"）；新增「保存并确认」按钮；已确认时显示 ✅ 标记与时间 |
| 生成按钮 | 未确认事实时提示："建议先核对事实——可提高准确性并**少一次 AI 调用**" |
| 成稿面板 | 正文/标题/标签改为可编辑；新增「保存修改」「导出 .md」「导出 .txt」；已编辑显示「人工编辑于 …」 |
| 历史稿 | 点击后**补读** `GET /api/drafts/{id}` 再渲染（修复正文为空） |
| 表述 | 沿用 `docs/38` 的"未发现阻断问题"与免责说明 |

## 6. 兼容性

| 场景 | 期望 |
|---|---|
| 用户不确认事实，直接生成 | **行为与改动前完全一致**（走 understand） |
| 旧数据（无新列） | 迁移自动加列；`facts_confirmed` 默认 0 |
| 旧草稿（无 `edited_at`） | 显示为"未编辑" |
| 既有 API 响应 | 只增字段，不改既有字段语义 |

## 7. 测试规格

### 7.1 Java（`MaterialControllerTest` / 新增）

| # | 场景 | 期望 |
|---|---|---|
| 1 | PUT facts 保存 + confirm | 200，`facts_confirmed=true` |
| 2 | PUT facts 空 text | 400 |
| 3 | PUT facts 非法 type | 400 |
| 4 | 未确认时 generate 透传 | 请求体**不含** `confirmed_facts` |
| 5 | 已确认时 generate 透传 | 请求体**含** `confirmed_facts` 且内容一致 |
| 6 | PUT draft 更新正文 | 200，`edited_at` 非空 |
| 7 | PUT draft 试图改 qa | 被忽略（qa 不变） |
| 8 | export md | 含标题+正文+标签 |
| 9 | export txt | 仅标题+正文 |
| 10 | GET /api/drafts/{id} | 返回完整 payload |

### 7.2 Python

| # | 场景 | 期望 |
|---|---|---|
| 11 | 传 confirmed_facts | **不调用** understand（monkeypatch 断言） |
| 12 | 不传 confirmed_facts | 调用 understand（原路径） |
| 13 | 传 confirmed_facts 且正文含无依据数字 | warnings 仍产生（QA 照常） |

### 7.3 端到端 + 浏览器

| # | 场景 |
|---|---|
| 14 | mock 全链路：建素材 → 改事实 → 确认 → 生成（断言日志含 skipping understand） |
| 15 | 浏览器：事实增删改保存、稿件编辑保存、导出、历史稿正文完整 |
