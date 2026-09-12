# 规格说明书：平台 DNA 调研与重建

- 文档类型：spec（事前三件套之一）
- 版本：v1.0 · 日期：2026-09-12
- 配套：`30-平台DNA调研-task.md`、`32-平台DNA调研-checklist.md`
- 关联：ADR-007（DNA 文件化）、ADR-010（不爬平台数据）、FR-07

---

## 1. 调研维度（6 维，逐平台填写）

| # | 维度 | 要回答的问题 | 可否核实 |
|---|---|---|---|
| D1 | **内容规格** | 正文/标题字数上限？图片/视频数量与时长？ | ✅ 官方帮助中心 |
| D2 | **标题机制** | 标题长度、是否参与搜索分发、算法偏好 | ✅ 官方 + 经验 |
| D3 | **标签/话题规则** | 数量上限、话题 vs 标签、位置影响 | ✅ 官方为主 |
| D4 | **分发机制** | 推荐流构成、冷启动指标、搜索占比、发布时间影响 | 🔶 官方公开信息有限，多为经验性 |
| D5 | **社区红线** | 禁限内容类别、违禁表述、导流规则 | ✅ 社区公约/运营规范 |
| D6 | **结构范式** | 开头/正文/结尾的通用结构、互动设计 | 🔶 **经验性**（非官方规定） |

**强制区分**：
- `official` —— 平台官方规定（有文档）
- `practice` —— 经验性做法（**不是官方规则**，不得表述为"平台要求"）

## 2. DNA Schema v2（扩展，向后兼容）

```yaml
platform: xhs                    # 平台代码（不变）
display_name: 小红书              # 新增：中文名
version: 2.0                     # 升级
updated_at: 2026-09-12           # 新增：最后核查日期

# ---- 新增：来源清单（每条结论的来源）----
sources:
  - id: xhs-convention
    type: official               # official | help-center | industry-report | practice
    title: 小红书社区公约
    url: https://...
    checked_at: 2026-09-12
    covers: [limits.banned_direction]      # 该来源支撑哪些字段

# ---- 新增：硬规格（可核实的数字，集中放置）----
specs:
  title_chars_max: 20
  body_chars_max: 1000
  tags_max: 10
  images_max: 18
  video_sec_max: 300

# ---- 新增：分发机制 ----
distribution:
  - ref: xhs-algo-notes            # 指向 sources 中的 id
    note: "推荐流为主，搜索流量占比高 → 标题/正文需埋搜索词"

# ---- 原有字段保留（内容不变或按来源校正）----
content_forms: [...]
style: [...]
structure_template: [...]          # 标注为 practice
title_rules: [...]
tags: { count_max: 6, guidance: "..." }
limits: { banned_direction: [...] }
viral_logic: [...]
hooks: [...]

# ---- 新增：核实状态（不假装确定）----
verify:
  verified: [specs.title_chars_max, specs.body_chars_max]
  todo:
    - field: tags.count_max
      note: "官方未明确上限，当前值属经验值，待核实"
```

### 兼容性要求

- **旧 DNA（无新字段）必须仍可正常加载** → 读取侧用 `.get()` 带默认值，不因缺字段报错
- 新增字段**全部可选**
- `version` 从 `0.1/1.0` 升到 `2.0`，但**不改变原有字段语义**（避免破坏 prompt 拼装）

## 3. 来源规范

| 规则 | 要求 |
|---|---|
| 优先官方 | 官方域名 > 官方帮助中心 > 权威行业报告 > 通用方法论 |
| 交叉验证 | 非官方来源须 **≥2 处一致**才写入 `specs`（数字类）；否则标 `verify.todo` |
| 禁止编造 | **绝不虚构 URL 或文档名**；查不到就写"未找到公开来源" |
| 记录时间 | 每个来源记 `checked_at`（平台规则会变） |
| 引用范围 | 只引用**结论与类别**，不复制大段原文；违禁词只列类别不列词表 |

## 4. 验证方法（T5，关键）

**问题**：调研到底有没有用？必须用真实 LLM 对照，不能自说自话。

```
同一素材 + 同一模型 + 同一温度
  ├─ A 组：旧 DNA（当前 0.1 版）
  └─ B 组：新 DNA（调研版 2.0）
人工比对：
  - 标题是否符合该平台字数/机制
  - 结构是否更贴合该平台范式
  - 标签数量与类型是否合理
  - 是否出现新 DNA 明确禁止的表述
```

**诚实要求**：
- 若 B 组**无明显优势** → 如实记录，并分析原因（可能 DNA 不是瓶颈、或 LLM 未充分利用）
- 单次生成有随机性 → 每平台至少跑 2 次，避免以偏概全
- **不夸大**：DNA 是"改写输入约束"，不承诺流量结果

## 5. 与现有管线的关系（零破坏）

| 组件 | 影响 |
|---|---|
| `dna.py` 的 `load_dna()` | 需支持新字段（可选读取），**不改变返回结构** |
| `prompts.py` 的 DNA 拼装 | **不改**（新字段不注入 prompt，避免 prompt 膨胀）；`distribution`/`specs` 的可注入部分单独评估 |
| 前端 DNA 展示 | 如有展示，补 `display_name` |
| 现有测试 | **必须全绿**（向后兼容验证） |

> **决策**：新字段中只有**对生成有直接价值**的才注入 prompt（如 `specs.title_chars_max` 作为硬约束）；
> `sources`/`verify` 属元数据，**不进 prompt**（避免占 token 且对生成无益）。

## 6. 交付物

| 文件 | 内容 |
|---|---|
| `platform-dna/*.yaml` × 5 | 重建后的 DNA v2 |
| `docs/33-平台DNA调研报告.md` | 逐平台结论 + 来源清单 + 不确定项 |
| `docs/34-平台DNA调研-交付说明.md` | 验收结果 + 对照验证结论 + 下一步 |
| `python-service/app/dna.py` | 兼容性调整（如有） |
