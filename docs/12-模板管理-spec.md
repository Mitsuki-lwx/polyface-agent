# 规格说明书：模板管理完整版（FR-62 + FR-64）

- 文档类型：spec（事前三件套之一）
- 版本：v1.0 · 日期：2026-09-10
- 配套：`11-模板管理-task.md`（任务书）、`13-模板管理-checklist.md`（验收清单）
- 需求来源：`05-SRS` FR-62 / FR-64；用例来源：`07` UC-14 / UC-15

---

## 1. 需求规格

### 1.1 功能需求（引用 SRS 编号）

| 编号 | 原文 | 本期实现范围 |
|---|---|---|
| FR-62 | 模板管理：新建/编辑/复制/删除、预置模板库、导入导出 | 全部（内容模板） |
| FR-64 | 生成页模板选择器 + "本次用模板"记录可追踪（模板版本号随稿存档） | 全部 |

### 1.2 非功能约束

| 编号 | 约束 | 本期要求 |
|---|---|---|
| NFR-02 | 数据本地 | 模板仅存本地 SQLite，无云同步 |
| NFR-07 | 可扩展 | `kind` 字段预留 `clip`，为 FR-61 成片模板留口 |
| NFR-08 | 可测试 | mock 模式可离线验证全部 CRUD |
| NFR-09 | 合规红线 | 模板**不可**覆盖平台 limits/红线（QA 仍强制） |

### 1.3 优先级与边界

- 优先级：S（Should）
- 模板优先级：**用户模板 > 平台 DNA（结构/风格）**；平台 limits/红线恒强制（ADR-014）

---

## 2. 数据模型

### 2.1 新增表 `template`

```sql
CREATE TABLE IF NOT EXISTS template (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  kind           TEXT NOT NULL DEFAULT 'content',  -- content | clip(预留)
  name           TEXT NOT NULL,
  voice          TEXT,          -- 语气/人设
  opening        TEXT,          -- 固定开头句式
  structure_json TEXT,          -- 正文结构要点 JSON 数组
  closing        TEXT,          -- 固定结尾/互动句式
  tag_style      TEXT,          -- 标签风格偏好
  taboo_json     TEXT,          -- 不想要的内容 JSON 数组
  builtin        INTEGER NOT NULL DEFAULT 0,  -- 1=预置（不可删）
  origin_id      INTEGER,       -- 复制来源 id（可追溯）
  version        INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT,
  updated_at     TEXT
);
```

### 2.2 `draft` 表加列（幂等）

```sql
-- 仅在缺列时执行，避免二次启动 duplicate column 报错
ALTER TABLE draft ADD COLUMN template_id      INTEGER;
ALTER TABLE draft ADD COLUMN template_version INTEGER;
```

幂等实现：启动时 `PRAGMA table_info(draft)` 取列名集合，缺失才逐条 ALTER。

### 2.3 字段映射（Java ↔ Python）

| template 表字段 | Python `UserTemplate` 字段 | 说明 |
|---|---|---|
| name | name | 直接 |
| voice | voice | 直接 |
| opening | opening | 直接 |
| structure_json | structure（list[str]） | JSON 数组反序列化 |
| closing | closing | 直接 |
| tag_style | tag_style | 直接 |
| taboo_json | taboo（list[str]） | JSON 数组反序列化 |

---

## 3. 接口契约

### 3.1 模板 CRUD

| 方法 | 路径 | 说明 | 成功码 |
|---|---|---|---|
| GET | `/api/templates?kind=content` | 列表（内置在前，我的在后） | 200 |
| POST | `/api/templates` | 新建 | 201 |
| GET | `/api/templates/{id}` | 详情 | 200 / 404 |
| PUT | `/api/templates/{id}` | 编辑（version+1） | 200 / 404 |
| DELETE | `/api/templates/{id}` | 删除（builtin → 409） | 204 / 404 / 409 |
| POST | `/api/templates/{id}/duplicate` | 复制为我的 | 201 / 404 |
| GET | `/api/templates/export` | 导出全部（含元信息） | 200 |
| POST | `/api/templates/import` | 导入（body=导出结构） | 200 |

#### 请求体（新建/编辑）

```json
{
  "kind": "content",
  "name": "老张的干货开场",
  "voice": "直接、不废话",
  "opening": "大家好，我是老张，今天说点干的。",
  "structure": ["先说结论", "给证据", "行动建议"],
  "closing": "关注老张，下期继续。",
  "tag_style": "偏好短标签",
  "taboo": ["不要AI味", "不要标题党"]
}
```

#### 响应示例（列表）

```json
{
  "templates": [
    {
      "id": 1, "kind": "content", "name": "结论前置·清单体",
      "builtin": true, "version": 1, "origin_id": null,
      "voice": "理性干货", "opening": "先说结论：", "closing": "有用就收藏。",
      "structure": ["抛出结论", "3条论据", "行动建议"],
      "tag_style": "", "taboo": [],
      "created_at": "2026-09-10T12:00:00", "updated_at": "2026-09-10T12:00:00"
    }
  ]
}
```

#### 导出格式

```json
{
  "polyface_templates": 1,
  "exported_at": "2026-09-10T12:30:00",
  "templates": [ { "...同上，去掉 id/builtin/origin_id..." } ]
}
```

导入规则：
- 校验 `polyface_templates` 版本字段，缺失/不匹配 → 400
- 逐条创建为**我的模板**（`builtin=0`）
- 同名策略：追加后缀 `(导入)`，不覆盖既有

#### 错误响应

| 场景 | 状态码 | body |
|---|---|---|
| 模板不存在 | 404 | `{"detail":"template not found"}` |
| 删除内置模板 | 409 | `{"detail":"内置模板不可删除，请使用「复制为我的」"}` |
| name 为空 | 400 | 校验错误 |
| 导入格式非法 | 400 | `{"detail":"invalid template file"}` |

### 3.2 生成接口改造（FR-64）

`POST /api/materials/{id}/generate` 请求体新增可选 `template_id`：

```json
{
  "platforms": ["xhs", "douyin"],
  "tone_override": null,
  "template_id": 3,
  "template": null
}
```

**优先级**：`template_id` > 内联 `template`（内联保留为兼容快捷方式）。

处理流程：
1. 若 `template_id` 非空 → 查库；不存在则 404
2. 查到的模板转为 JSON 对象（字段映射见 §2.3）
3. 透传 Python `/generate` 的 `template` 字段
4. 落库 draft 时写入 `template_id` 与 `template_version`

---

## 4. 预置模板库

位置：`java-backend/src/main/resources/presets/content-templates.json`

Seed 规则：
- 启动时检查 `SELECT COUNT(*) FROM template WHERE builtin=1`
- 为 0 则插入全部预置模板（`builtin=1`, `version=1`）
- 非 0 则跳过（**不覆盖用户改动**，见 task Q1）

预置内容（3 个）：

| 名称 | 语气 | 开头 | 结构 | 结尾 |
|---|---|---|---|---|
| 结论前置·清单体 | 理性干货 | 先说结论： | 抛出结论 → 3条论据 → 行动建议 | 觉得有用就收藏，需要时翻出来看 |
| 故事共鸣体 | 真诚分享 | 我以前也踩过这个坑…… | 痛点场景 → 转折 → 方法 → 邀请 | 你遇到过吗？评论区聊聊 |
| 干货教程体 | 专业清晰 | 今天教你一个能立刻上手的方法 | 问题 → 步骤1-3 → 避坑提示 → 总结 | 关注我，下期讲进阶版 |

---

## 5. 存储层设计（Store.java 增量）

```java
public record TemplateRow(long id, String kind, String name, String voice, String opening,
                          String structureJson, String closing, String tagStyle, String tabooJson,
                          boolean builtin, Long originId, int version,
                          String createdAt, String updatedAt) {}

long insertTemplate(String kind, String name, String voice, String opening,
                    String structureJson, String closing, String tagStyle, String tabooJson,
                    boolean builtin, Long originId, int version);
Optional<TemplateRow> getTemplate(long id);
List<TemplateRow> listTemplates(String kind);          // builtin DESC, id ASC
boolean updateTemplate(long id, ...fields...);         // version = version + 1, updated_at=now
boolean deleteTemplate(long id);                       // 不删 builtin（调用方先判）
long countBuiltinTemplates();
// draft 版本存档
long insertDraft(..., Long templateId, Integer templateVersion);   // 重载旧签名
```

`initSchema` 增量：
1. 加 `template` 建表语句
2. 加 draft 缺列检测 + ALTER

---

## 6. 前端设计（static/index.html）

### 6.1 模板库管理区

位置：左栏新增卡片「⑤ 模板库」（可折叠）

- 分组列表：**内置模板** / **我的模板**
- 每项显示：名称、版本 `v{n}`、内置标记
- 操作按钮：`编辑` / `复制为我的` / `删除`（内置隐藏删除）
- 顶部操作：`+ 新建模板` / `导出 JSON` / `导入 JSON`

### 6.2 新建/编辑表单

复用 M3 已有字段：
- 名称（必填）、语气、开头句式、正文结构（每行一条 → 数组）、结尾、标签风格、避雷（每行一条）

### 6.3 生成页模板选择器

- 位置：原「使用我的内容模板」折叠区 → 替换为下拉选择器
- 选项：`不使用模板（仅平台 DNA）` + 内置模板 + 我的模板（显示 `名称 v{版本}`）
- 保持内联快捷表单可用（折叠为「临时模板（不保存）」）

### 6.4 稿件卡模板标记

成稿审阅每条草稿头部显示：`🧩 模板：{name} v{version}`；若模板已被删除显示 `模板已删除`。

---

## 7. 测试方案

| 层 | 内容 | 数量 |
|---|---|---|
| Java 单元 | Store：模板 CRUD、version 自增、builtin 不可删、draft 版本存档 | +5 |
| Java 单元 | 幂等 ALTER：同一 DB 二次构造 Store 不报错 | +1 |
| Python 回归 | 现有 17 项全绿（模板字段契约不变，应无改动） | 17 |
| 端到端 | 建模板→生成→稿件带版本→编辑模板→版本+1→导出→删除→导入恢复 | 1 条链路 |

---

## 8. 兼容与迁移

- 旧内联 `template` 字段**保留**，行为不变（向后兼容）
- 已存在的 draft 记录 `template_id`/`template_version` 为 NULL，前端显示"未记录模板"
- 现有 `polyface.db` 无需重建，启动自动补表/补列
