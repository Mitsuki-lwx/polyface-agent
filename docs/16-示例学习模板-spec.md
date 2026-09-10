# 规格说明书：示例自动学习模板（FR-63）

- 文档类型：spec（事前三件套之一）
- 版本：v1.0 · 日期：2026-09-10
- 配套：`15-示例学习模板-task.md`、`17-示例学习模板-checklist.md`
- 需求来源：`05-SRS` FR-63；用例来源：`07` UC-16

---

## 1. 需求规格

### 1.1 功能需求

| 编号 | 原文 | 本期实现范围 |
|---|---|---|
| FR-63 | 喂 1 篇自有爆款/示例片 → AI 拆解结构参数 → 生成"草稿模板"，**人工确认后才启用** | 示例**文本**拆解（示例片留 FR-51/FR-61） |

### 1.2 非功能约束

| 编号 | 约束 | 本期要求 |
|---|---|---|
| NFR-08 | 可测试 | mock 模式用规则启发式拆解，无 Key 可离线验证 |
| NFR-02 | 数据本地 | 只存拆解结果与备注，**不存示例原文**（降低版权/隐私风险） |
| ADR-014 | 人工确认后才启用 | 拆解产物一律 `status=draft`，未确认不进生成选项 |

### 1.3 关键约束（来自 UC-16 验收）

- 草稿模板**必须能预览其结构**
- **未确认的模板不得出现在 UC-15（生成时套用）的选择列表**
- 确认后可编辑

---

## 2. 数据模型

### 2.1 `template` 表加列（幂等）

```sql
ALTER TABLE template ADD COLUMN status      TEXT NOT NULL DEFAULT 'active';
ALTER TABLE template ADD COLUMN source_note TEXT;
```

- `status`：`draft`（草稿，待确认）/ `active`（已启用，可用）
- `source_note`：示例来源备注（用户自填，如"9月小红书爆款"）；**不存原文**
- 迁移策略：老库无此列 → `PRAGMA table_info` 检测后 ALTER；**存量模板默认 `active`**（不改变现有行为）

### 2.2 状态机

```
[示例文本] --learn--> draft --activate--> active
                        |
                        +--(编辑，沿用 PUT，仍为 draft)
                        +--(丢弃 = DELETE)
```

### 2.3 字段映射（拆解产物 → template 表）

| 拆解字段 | template 列 | 说明 |
|---|---|---|
| name | name | 自动生成（如"学习：9月爆款"），可改 |
| voice | voice | 语气/人设 |
| opening | opening | 开头句式 |
| structure[] | structure_json | 正文结构 |
| closing | closing | 结尾互动 |
| tag_style | tag_style | 标签风格 |
| taboo[] | taboo_json | 避雷（多数为空） |
| rationale | —（不在列中） | 拆解说明，仅随响应返回供用户核对，不入库 |

---

## 3. 接口契约

### 3.1 学习（示例 → 草稿模板）

```
POST /api/templates/learn
```

请求：
```json
{
  "sample_text": "（用户粘贴的示例正文）",
  "source_note": "9月小红书爆款"
}
```

响应 `201`：
```json
{
  "id": 21,
  "kind": "content",
  "status": "draft",
  "name": "学习：9月小红书爆款",
  "voice": "真诚分享",
  "opening": "我以前也踩过这个坑……",
  "structure": ["痛点场景", "转折时刻", "方法", "邀请分享"],
  "closing": "你遇到过吗？评论区聊聊。",
  "tag_style": "",
  "taboo": [],
  "source_note": "9月小红书爆款",
  "version": 1,
  "builtin": false,
  "rationale": "识别到首句为共鸣式开场、正文含4个递进段落、尾句为提问式互动"
}
```

`schema` 校验：
- `sample_text` 必填且 ≥ 50 字（太短拆不出结构）
- 校验失败 → `400`

### 3.2 确认启用

```
POST /api/templates/{id}/activate
```

- 仅 `draft` 可启用；已是 `active` → 幂等返回 200
- 不存在 → `404`
- 响应：更新后的模板对象（含 `status: "active"`）

### 3.3 列表过滤（安全默认）

```
GET /api/templates?status=active|draft|all
```

| 参数 | 行为 |
|---|---|
| 不传 / `active` | **只返回 active**（默认，符合 UC-16「未确认不进选择列表」） |
| `draft` | 只返回草稿 |
| `all` | 全部（模板库管理界面用） |

> **兼容性说明**：M5 的 `GET /api/templates` 不带参返回全部；本期改为默认只返回 `active`。
> 前端模板库改用 `?status=all`，**生成页选择器沿用默认（active）** —— 这是 UC-16 的服务端保障。

### 3.4 丢弃

复用 M5 端点：
```
DELETE /api/templates/{id}
```
草稿与普通模板一致（非 builtin 即可删）。

新增错误场景：

| 场景 | 状态码 | body |
|---|---|---|
| 示例过短（<50 字） | 400 | `{"detail":"示例文本过短，至少 50 字才能拆解出结构"}` |
| 启用不存在的模板 | 404 | `{"detail":"template not found"}` |
| 启用 builtin 模板 | 409 | `{"detail":"内置模板无需启用"}` |

---

## 4. 拆解逻辑

### 4.1 数据结构（Python）

```python
class LearnedTemplate(BaseModel):
    name: str
    voice: str
    opening: str
    structure: list[str]
    closing: str
    tag_style: str
    taboo: list[str]
```

### 4.2 真实模式（LLM）

新增 `python-service/app/pipeline/learn.py` + `prompts.LEARN_SYSTEM`：

```
你是内容结构分析师。分析用户提供的示例文本，拆解出可复用的写作模板参数。
只输出一个 JSON 对象：
{
  "voice": "语气/人设（如：真诚分享/理性干货/犀利观点）",
  "opening": "开头句式（摘出或归纳，≤40字）",
  "structure": ["正文结构要点1", "要点2", ...],   // 3~6 条，按出现顺序
  "closing": "结尾/互动句式（≤40字）",
  "tag_style": "标签风格描述（可空）",
  "taboo": []                                      // 可空
}
约束：
- 只归纳"可复用的结构套路"，不要复述具体内容细节（避免把示例的私有信息带进模板）
- structure 用短语概括（每条 ≤15 字），不要抄整句
```

> 隐私要点：prompt 明确要求**只归纳结构、不复述内容**，降低示例私有信息泄漏到后续生成的模板里。

### 4.3 mock 模式（规则启发式）

保证无 Key 可演示、可测试：

| 字段 | 规则 |
|---|---|
| opening | 取第一段，截断 60 字 |
| closing | 末段若含互动标记（`？`/`评论`/`关注`/`收藏`/`点赞`）则取之；否则空 |
| structure | 中间段落按顺序取，每条提取首句/前 15 字，最多 6 条 |
| voice | 关键词判定：`我觉得/我曾经` → 真诚分享；`数据/方法/步骤` → 理性干货；`不对/错了` → 犀利观点；否则"通用" |
| tag_style / taboo | 空 |
| name | `学习：{source_note 或"示例"}（{MM-DD}）` |
| rationale | 描述识别依据（段数/开场类型/结尾类型），供用户核对 |

拆分依据：按连续空行分段；无空行时按换行分段。

### 4.4 name 生成

`学习：{source_note}` → 若无备注则 `学习：示例（MM-DD）`；重名由 `uniqueName` 自动加后缀。

---

## 5. 前端设计（static/index.html）

### 5.1 模板库卡片调整

```
⑤ 模板库   [✨ 从示例学习] [+ 新建模板] [导出] [导入]

📝 草稿（待确认）  N 个
  ├ 学习：9月爆款   编辑 / 确认启用 / 丢弃
  └ ...

内置模板（3）
  └ ...
我的模板（M）
  └ ...
```

- AI 拆解项显示 `rationale` 摘要 + 「AI 拆解，请核对后启用」提示
- 「确认启用」→ `POST /api/templates/{id}/activate` → 刷新
- 「丢弃」→ 复用 `confirm` 二次确认 + `DELETE`

### 5.2 学习弹窗

新增 `#modalLearn`：
- 示例文本 textarea（必填，提示"请仅使用自有或已获授权的示例；仅用于归纳结构，不会保存原文"）
- 来源备注 input（可选）
- 学习按钮 → loading → 成功后关闭弹窗、刷新列表并把新草稿滚动到视野

### 5.3 生成页选择器

只渲染 `status === "active"` 的模板（过滤在 `fillTplSelect` 内）。

---

## 6. 测试方案

| 层 | 内容 | 数量 |
|---|---|---|
| Python 单元 | mock 拆解：分段/开头/结尾识别/structure 条数上限/voice 判定 | +3 |
| Python 单元 | learn prompt payload 含示例与约束 | +1 |
| Java 单元 | Store：status 过滤（active/draft/all）、activate、幂等 ALTER 兼容老库 | +4 |
| 端到端 | learn → 草稿不在 active 列表 → activate → 进入 active → 生成可用；过短示例 400；丢弃 | 5 条链路 |

---

## 7. 兼容与迁移

- 存量 `template` 行 `status` 默认 `active`（迁移即生效，不改变 UI 表现）
- `GET /api/templates` 默认值由「全部」改为「active」——前端模板库改用 `?status=all` 适配
- 现有 M5 测试中依赖"列表返回全部"的断言需同步更新（若使用默认参数）
