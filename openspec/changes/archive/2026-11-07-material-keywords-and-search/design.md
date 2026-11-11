# Design

## Context

事实基础（已核对代码）：
- `material` 表无索引字段；`asset` 表有 `tags_json`（`DEFAULT '[]'`）—— 有先例可循 ✓
- 素材解析链路：前端 ① 素材 → 解析 → **确认并解析为素材**（此时落库）
- 长文拆分已有 `parent_id` / `angle` / `format` 字段 ✓
- 存储是 **Java 侧**的 `Store.java` 拥有 DDL；Python 侧不直接建表
- FTS5：**已实测**（`SqliteFts5CapabilityTest`，任务 0.1）—— 见下方 D2 的实测结论

## Goals / Non-Goals

**Goals**：关键词可确认地落库、两层挂载、两档检索、查到即可复用。

**Non-Goals**

- **语义检索（embedding）** ✗ —— 要新依赖与新索引，与"本地单文件"定位冲突，后置
- **MinIO / 对象存储** ✗ —— 文件已在 `data/media`，索引在 SQLite；加服务只增加运维面 ✗
- 不做关键词的同义词归一（"AI"/"人工智能"合并）—— 先观察真实用法再决定
- 不迁移历史素材的关键词（空就是空，不猜 ✗）

## Decisions

### D1：关键词存 `material.tags_json`，与 `asset` 同一形态

`asset` 已经用 `tags_json TEXT DEFAULT '[]'` ✓ —— 复用同一个形态，前端与查询代码可以借鉴，
不新造第二种表示法 ✗。

被否：单独的 `material_tag(material_id, tag)` 关系表 —— 查询更规范，但当前量级远未到
需要联表的程度，多一张表就多一份写入一致性要维护 ✗。

### D2：全文用 FTS5 外部内容表 + 触发器；不可用则退 `LIKE`

**实测结论（2026-11-07，sqlite-jdbc 3.46.1.0，见 `SqliteFts5CapabilityTest`）：走 `LIKE`。**

| 检查 | 结果 |
|---|---|
| `CREATE VIRTUAL TABLE ... USING fts5(...)` | ✓ 可用 |
| 默认分词器搜「复用」（2 字） | ✗ 搜不到 —— unicode61 把整句当一个 token |
| `tokenize='trigram'` 搜「一稿多」（3 字） | ✓ 命中 |
| `tokenize='trigram'` 搜「复用」（2 字） | ✗ 命中不了 —— trigram 最小 3 字 |

中文检索里最常搜的恰恰是**两字词**（复用/周报/选题）→ FTS5 在这里**结构性不可用** ✗。
自定义分词器（simple/jieba）需要重新编译 SQLite 或换驱动 ✗，收益不足以承担这个依赖。

**因此：`WHERE (title LIKE ? OR raw_text LIKE ?) AND ...`**，功能正确、性能差一档 —— 在
一万条素材量级上差异可以忽略（全表扫描几 MB）✓。这条结论由能力测试钉住 ✓：
哪天驱动支持了更好的中文分词，测试会先红，提醒重新评估。

### D3：候选关键词随**已有**的解析调用一起产出

`understand` 本来就要模型理解素材 ✓ —— 在同一份输出里多要 3~8 个关键词，
**不新增一次调用**（省额度，也避免"两次理解不一致"）。

### D4：确认发生在**保存前**

前端拿到解析结果（含候选关键词）→ 显示为可编辑的标签 → 用户确认 → 才保存 ✓。
这样"未经确认不落库"是**结构保证**，不是流程纪律 ✗。

### D5：查询参数放素材列表，不新建端点

`GET /api/materials?q=&tags=` —— 复用现有列表端点，避免前端两份列表逻辑 ✗。
`tags` 用逗号分隔、交集语义。

## Risks / Trade-offs

- [FTS5 在 Java 驱动不可用] → 退回 LIKE；**必须**在 tasks 0 里先证伪，不能假设 ✗
- [自动关键词质量差] → 落库前必须人工确认 ✓；且允许一键清空后手填
- [关键词散乱（同义不同词）] → 先记录真实使用，再决定要不要归一；现在不做 ✗

## Migration Plan

- DDL 用 `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN`（SQLite 支持加列）✓
- 历史素材 `tags_json` 默认 `'[]'` —— 空索引，检索时不会命中，**不猜** ✓
- 回滚：删掉列与虚表；代码回退即可，无数据依赖

## Open Questions

- `needs-validation`：sqlite-jdbc 3.46.1.0 的 FTS5 支持（tasks 0 验证）
- 关键词上限 8 是否合适 —— 先按 8 实现，观察后再调（配置项）
