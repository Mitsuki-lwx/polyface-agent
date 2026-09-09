# 「同源万面 · Polyface」解决方案与技术设计文档 v0.1

> 灵感来源：Creative Minds Jam #1: Hong Kong（DoraHacks，Animoca Brands / Minds 主办）
> 选题：**Content repurposing across platforms（跨平台内容再利用）**
> 本方案为**独立自研实现，不依赖 Minds 平台**，但完整吸收其核心概念：Memory（记忆）、Continuity（连续性）、Autonomous follow-up（自主复盘）。

---

## 1. 背景与定位

### 1.1 我们要解决什么问题

自媒体创作者（或迁移中的作者）手握一份优质素材，想在不同平台发布。现状是：

- 手动逐平台改写，工作量大、质量不稳；
- 市面"一键分发"工具只会**改格式**（换行、字数裁剪），不会**改灵魂**——每个平台的
  语言风格、叙事结构、标题逻辑、话题标签、爆款机制完全不同；
- 创作者不清楚"为什么同样的内容在 A 平台火、在 B 平台没人看"；
- 缺乏对创作者自身品牌声音、历史效果的记忆，每次都要重新描述。

### 1.2 一句话定位

> **一份素材进，千面出**：把同一份原始素材，自动改写为符合各平台调性、
> 有机会获得流量（爆款结构 × 平台热点 × 标签策略）的完整可发布稿件。

### 1.3 目标用户

| 用户 | 场景 |
|---|---|
| 跨平台迁移作者 | 已有一套内容体系（如公众号/小红书），要开新号到抖音/B站/X/YT，素材复用 + 风格重铸 |
| 多平台运营者 | 每周把核心观点同步到全平台，追求效率与每平台质量 |
| 工作室/矩阵号 | 统一素材库，批量产出各平台变体，沉淀"什么风格能火"的账号记忆 |

### 1.4 目标平台（v1 覆盖 9 个）

| 区域 | 平台 |
|---|---|
| 中文 | 小红书、抖音、微信公众号、知乎、B站 |
| 海外 | X(Twitter)、Instagram、Facebook、YouTube |

> 微博/快手/Threads 等通过同一"平台 DNA 插件"机制扩展，不写死。

---

## 2. 核心产品能力（MVP：素材 → 多平台成稿全链路）

用户输入一份素材，系统输出 **9 个平台各自的可发布成稿**，每个成稿包括：

- 主标题（含 2~3 个备选，带不同标题策略）
- 正文 / 脚本 / 帖子内容（按平台形态）
- 话题标签（按平台规则）
- 封面文案 / 首屏钩子（针对视频与图文平台）
- 互动引导语（评论/收藏/转发/三连/关注）
- 「改写说明」：该平台为什么这样改、踩了哪些爆款机制

### 2.1 平台 DNA（差异化的关键，不是格式而是灵魂）

每个平台一个 profile 文件，结构化描述，作为改写提示词的"平台人格"：

| 维度 | 示例（小红书 vs 抖音 vs X vs YouTube） |
|---|---|
| 内容形态 | 图文笔记 / 口播脚本+分镜 / Threads 串推 / 长视频脚本+简介 |
| 语言风格 | 真诚种草、口语化 / 强钩子快节奏、悬念 / 观点锋利、短句 / 信息密度+故事化 |
| 结构模板 | 痛点→方案→体验→收藏引导 / 前3秒钩子→展开→反转/结果→引导 / hook→论点→论据→互动 / hook→价值预告→章节→CTA |
| 标题机制 | 20字内+关键词前置 / 数字+悬念+不剧透 / 观点鲜明可转述 / 搜索关键词前置 |
| 标签/话题 | 5~8 个垂类词+热度词 / 蹭热点话题 / 少量精准话题 / 描述区关键词 SEO |
| 热点逻辑 | 生活方式热点、季节节点 / 热点BGM+挑战赛 / 全球新闻趋势(需谨慎) / 搜索与推荐双轨 |
| 爆款指标 | 收藏率>点赞率、搜索流量 / 完播率、转发 / 转推、互动率 / 观看时长、CTR |

每个平台的 DNA 还包含：**违禁/红线词、推荐格式参数（字数上限、话题数量上限）、
算法偏好**。这些规则由「平台运营知识」沉淀，v1 先人工编纂（结合公开运营方法论），
后续可通过**效果数据反哺**调参（见 §5 记忆系统）。

### 2.2 同源改编 vs 搬运（红线）

- 系统产出的每一篇都是基于素材"观点层"的重写，不是简单翻译/换行；
- 输出内容仍以用户自有素材为唯一事实源，**不引入模型幻觉事实**（见 §4 事实约束）；
- 用户须确保素材为其原创或已获授权；发布时遵守各平台原创与 AI 内容规范（见 §8 合规）。

---

## 3. 端到端工作流

```
[1] 素材输入           [2] 素材解析            [3] 平台策略生成
粘贴文本 / 上传文件     抽取:核心观点/事实清单/   按平台 DNA 规划:
(长文/口播稿/大纲/      语气/受众/可复用片段      每个平台写一份"创作指令"
随手笔记)                                    (角度、钩子、结构、标签方向)
        │                     │                     │
        ▼                     ▼                     ▼
[6] 人工确认/编辑 ◄── [5] 质量门(QA) ◄── [4] 逐平台成稿生成
(Web UI 逐平台过目)    LLM自评+规则校验:    每平台独立调用 LLM,
        │             字数/标签数上限、      使用 DNA+策略+素材
        ▼             事实一致性、红线词     (并行执行)
[7] 导出与分发               │
复制/下载(各平台格式)        ▼
        │              [8] 效果反馈回填(用户填或接入数据)
        ▼              [9] 复盘:生成"该账号/该平台什么有效"
[10] 记忆沉淀 → 优化下次生成的平台 DNA 参数与创作者画像
```

### 3.1 核心管线（逐平台独立，可并发）

```
素材文本
  │  1. understand  : 素材理解（核心观点 / 事实清单 / 语气 / 受众画像）
  ▼
结构化素材 JSON
  │  2. brief       : 对每个平台各生成一份 brief（角度+钩子+结构+标签方向）
  ▼
9 份 brief (JSON)
  │  3. draft       : 每平台一次 LLM 调用生成完整成稿（可并行）
  ▼
9 份成稿 (JSON)
  │  4. qa          : 规则校验 + LLM 自评（爆款清单打分）+ 事实一致性比对
  ▼
通过 → 输出 / 未通过 → 带反馈重写一轮（max_retry=1）
```

---

## 4. 关键设计：事实约束（防幻觉）

素材解析阶段生成 **fact list（事实清单）**：

```json
{ "facts": [ {"text":"周阅读量 3 万", "type":"data"},
             {"text":"2023 年从大厂裸辞做自由职业", "type":"story"} ] }
```

- 成稿阶段约束：**一切数值、头衔、故事细节必须能在 fact list 中找到依据**；
- QA 阶段：让 LLM 对照 fact list 检查成稿，**发现无依据的新增断言即拦截**并提示改写；
- 保证"同一份素材"的可信度，避免 AI 自行加戏导致作者不敢直接发布。

---

## 5. 记忆系统（对应 Minds 的 Memory/Continuity/Autonomy）

> 不用 Minds，但"记忆"是产品护城河，也是黑客松评审最看重的能力。自研实现：

### 5.1 记忆分四层

| 层 | 存什么 | 例子 |
|---|---|---|
| ① 平台 DNA 库 | 每平台风格/规则/参数 | 小红书字数上限、X 的梗文化 |
| ② 创作者画像 | 品牌声音、领域、受众、账号矩阵 | "语言犀利带自嘲，主攻职场与自由职业" |
| ③ 素材库 | 历次素材 + 产出稿件 + 改写记录 | 素材 A 曾产出 9 篇，其中小红书篇收藏率高 |
| ④ 效果档案 | 发布后反馈（点赞/收藏/评论/播放），可手动回填 | 小红书教程类>情绪类 |

### 5.2 连续性（Continuity）

- 同一素材的 9 平台稿件生成**可断点续跑**（已有结果缓存，未生成平台继续）；
- 素材→稿件的**全部上下文在下次会话可恢复**，不必重新描述需求。

### 5.3 自主复盘（Autonomous follow-up）

- 生成完成 N 天后（可配置），系统基于效果档案对该素材做**复盘**：
  - 哪个平台转化最好？哪个标题策略胜出？哪类内容适合复用？
- 复盘结论回写创作者画像 + 平台 DNA 调参建议，**下次生成自动带上历史经验**。

---

## 6. 技术方案

### 6.1 技术选型（推荐路线 A）

> 说明：你已有 Spring AI + pgvector 的技术栈经验，但本项目是新的独立产品，
> 且需要大量 LLM 编排与快速迭代，因此推荐 Python 快速成型；若你希望统一到
> Java 生态也可（见备选 B），最终待你拍板。

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | Python 3.11+ / FastAPI | LLM 编排、异步并发、类型友好 |
| LLM 接入 | 适配层（多 Provider） | DeepSeek / 通义 / OpenAI 兼容接口，通过环境变量切换，见 §6.2 |
| 前端 | React + Vite（轻量工作台） | 素材输入→逐平台成稿查看/编辑/复制 |
| 存储 | PostgreSQL（主） + pgvector（可选） | 素材/稿件/画像存 SQL；pgvector 用于"相似素材复用"检索（v1.1） |
| 编排 | 纯代码管线（v1） | 暂不引入 LangGraph 等重框架，逻辑清晰、可控、少依赖 |
| 任务 | asyncio 并发调用 LLM | 9 平台 brief/draft 并行 |

### 6.2 LLM Provider 适配

```env
LLM_PROVIDER=openai-compatible   # openai | deepseek | dashscope | ollama ...
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-xxxx
LLM_MODEL=deepseek-chat
LLM_QA_MODEL=deepseek-chat       # QA 可用更强模型，可选
```

统一走 OpenAI 兼容 chat/completions，减少 SDK 耦合。

### 6.3 项目结构（v1）

```
polyface/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── api/                 # routes: materials, drafts, memory, feedback
│   │   ├── core/                # config(env), llm adapter, db
│   │   ├── domain/              # models: Material, Draft, PlatformDna, CreatorProfile
│   │   ├── pipeline/
│   │   │   ├── understand.py    # 素材解析 -> 结构化素材
│   │   │   ├── brief.py         # 平台策略
│   │   │   ├── draft.py         # 逐平台成稿
│   │   │   ├── qa.py            # 质量门
│   │   │   └── orchestrator.py  # 管线串联 + 断点续跑
│   │   ├── platforms/           # platform-dna/*.yaml 载入
│   │   └── prompts/             # 各阶段 prompt 模板
│   ├── platforms/dna/           # xiaohongshu.yaml, douyin.yaml, ... youtube.yaml
│   ├── tests/
│   └── requirements.txt
├── web/                         # React 工作台
└── docs/
```

### 6.4 主要 API（草案）

```
POST /api/materials            # 创建素材
GET  /api/materials/{id}/facts # 解析结果(事实清单) 人工可修正
POST /api/drafts               # {material_id, platforms:[...], tone?} 触发管线
GET  /api/drafts/{id}          # 成稿
GET  /api/drafts/{id}/platforms/{code}  # 单平台稿
POST /api/drafts/{id}/qa/rerun # 触发 QA/重写
PUT  /api/feedback             # 回填效果(用于复盘)
GET  /api/reviews?material_id= # 复盘报告
GET  /api/platforms            # 列出可用平台 DNA
```

### 6.5 数据模型（核心表）

```sql
materials(id, creator_id, raw_text, source_kind, facts_json, core_message, tone, status, created_at)
dna_profiles(platform_code, name, version, rules_json, active)         -- 平台 DNA
creator_profiles(id, name, domain, voice, audience, settings_json)     -- 创作者画像
drafts(id, material_id, platform_code, status, payload_json,           -- payload: title[], body,
       qa_json, created_at, updated_at)                                -- tags[], hooks[], rationale
feedback(id, draft_id, platform_code, metrics_json, note, created_at)  -- 播放/点赞/收藏/评论
reviews(id, material_id, summary_json, created_at)                     -- 复盘报告
```

---

## 7. 里程碑与验收

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| M0 | 本文档定稿 + 决策确认 | 用户确认技术栈与范围 |
| M1 | 骨架 + 单平台跑通 | 后端起服务；用一段示例素材成功产出**小红书完整稿件**（含 QA） |
| M2 | 9 平台 DNA + 全链路 | 一键产出 9 平台成稿；QA 拦截事实幻觉；断点续跑生效 |
| M3 | Web 工作台 | 浏览器输入素材→查看/编辑/复制各平台成稿；效果回填 |
| M4 | 记忆与复盘闭环 | 创作者画像生效；二次生成引用历史经验；产出复盘报告 |
| M5（可选） | 演示视频 + README + 参赛材料 | 若参加下一届 Jam 或其他比赛，补齐提交物 |

建议顺序严格执行，每个 M 结束跑通验证再进入下一个。

---

## 8. 合规与风险

| 风险 | 对策 |
|---|---|
| 素材版权不明 | 免责声明 + 只处理用户自有/授权素材 |
| AI 内容规范 | 各平台对 AI 生成内容要求不一，出稿页提示"建议按平台规则标注/自查" |
| 热点抓取合规 | v1 热点由用户手动输入 + 可配置数据源，不擅自爬取平台数据 |
| 平台限流/封号风险 | 工具只产出稿件，发布动作由用户在平台完成，不提供批量自动发布 |
| 幻觉事实 | §4 事实约束 + QA 拦截 |
| 海外平台访问 | 后端仅调用 LLM API 与本地渲染，不需访问海外平台；如需热点数据再评估 |

---

## 9. 待你拍板的开放问题

1. **技术栈**：路线 A（Python FastAPI，推荐）还是路线 B（Spring Boot + Spring AI，贴合你既有栈）？
2. **LLM Provider**：你有哪家 API Key（DeepSeek / 通义 / OpenAI / 其他）？默认按 OpenAI 兼容适配。
3. **首个演示素材形态**：长文 / 口播稿 / 大纲 / 图文要点——影响 understand 阶段打磨重点。
4. **要不要 Web 界面**：M3 的 React 工作台是否本轮就做，还是先纯 API + 命令行查看结果？
5. **是否计划参赛**：若想投下一届 Jam，我会额外维护一份"Memory/Continuity/Autonomy 对照表"用于 Demo 叙事。

> 确认以上 5 点后，我进入 M1：搭建骨架并用一段示例素材跑通"小红书单平台成稿"。
