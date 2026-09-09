# Polyface「同源万面」ADR 技术决策记录

- 状态说明：Accepted=已采纳落地；Proposed=提议待确认
- 关联：docs/05 SRS、06 可行性
- 新增决策在此追加，ADR 只追加不修改历史（回退需新 ADR 记录）

---

## ADR-001 独立自研，不使用 Minds 平台
- 状态：Accepted（2026-09-09）
- 背景：灵感源于 Creative Minds Jam（基于 Minds by Animoca 的持久化智能体），但不想被第三方平台约束。
- 决策：完全独立自研；吸收其"Memory/Continuity/Autonomy"三概念为产品自有能力（本地记忆+断点续跑+复盘）。
- 后果：平台绑定风险消除；记忆能力需自行实现（M4）。

## ADR-002 产品形态：本地一键启动 Web 应用
- 状态：Accepted
- 背景：目标用户是个人自媒体博主；"下载即用 + 数据不出本机"是信任卖点。
- 决策：Java(:8080)+Python(:8000) 本地起双服务，浏览器访问；不设云端账号。
- 后果：无服务器成本；分发走 GitHub Releases；单机单用户，简化权限模型。

## ADR-003 进程分工：Java 业务编排 / Python LLM 计算
- 状态：Accepted
- 背景：团队熟悉 Java；LLM 管线 Python 生态更顺；单语言会互拖。
- 决策：Java 负责 REST/任务状态机/本地存储/静态托管；Python 只暴露 LLM 计算端点（analyze/brief/draft/qa/generate）。
- 后果：职责清晰可独立测试；进程间通信成本低（本机回环）。

## ADR-004 Java→Python 通信必须强制 HTTP/1.1
- 状态：Accepted
- 背景：JDK HttpClient 默认尝试 HTTP/2 明文升级(h2c)，uvicorn/h11 不支持 → 连接被重置（`header parser received no bytes`）。
- 决策：`JdkClientHttpRequestFactory` + `HttpClient.Version.HTTP_1_1`；封装于 `PythonClient`。
- 后果：调用稳定；后续若换 gRPC/本地管道需另立 ADR。

## ADR-005 本地存储用 SQLite（非 Postgres/pgvector）
- 状态：Accepted
- 背景：单机单用户、零运维优先；无多用户并发与海量向量检索需求。
- 决策：sqlite-jdbc + 自建 schema（material/draft 两表）；数据目录 `data/`（gitignore）。
- 后果：轻量可移植；若未来要相似素材语义检索（M4+）再引入 pgvector/向量方案（另立 ADR）。
- 经验：sqlite-jdbc 多语句 `execute` 只执行首条 → 逐条执行。

## ADR-006 LLM 接入走 OpenAI 兼容适配层 + mock 回退
- 状态：Accepted
- 背景：开源项目不能内置 Key；不同用户用不同模型。
- 决策：`python-service/.env` 配置 base_url/key/model，未配 Key 自动 mock；.env 不入 git。
- 后果：开箱即用（mock）；真实模式模型可随时切换。

## ADR-007 平台 DNA 用 YAML 文件化，随包升级、可用户扩展
- 状态：Accepted
- 背景：平台规则差异大且会变；写死在代码里难维护且更新要发版。
- 决策：`platform-dna/*.yaml` 描述风格/结构/标题/标签/字数/红线/爆款逻辑；代码按 schema 读取。
- 后果：新增平台=新增 YAML；规则更新走小版本包；DNA 全部自研整理规避侵权。

## ADR-008 生成管线四段式（understand→brief→draft→qa）纯代码编排
- 状态：Accepted
- 背景：可控性、可测试性、可解释性优先；暂不需要 LangGraph 级 DAG 编排。
- 决策：Python 模块内顺序编排，平台间 ThreadPool 并行；qa 未过→带反馈重写一轮。
- 后果：逻辑清晰易测；复杂 agent 场景（M4 复盘自主行动）再评估是否引入框架。

## ADR-009 防幻觉：事实清单作为硬约束
- 状态：Accepted
- 背景：自媒体最怕假数据；LLM 会无中生有。
- 决策：understand 抽事实清单 → brief/draft 提示词限定只能使用事实清单 → qa 校验正文数字是否有依据（无依据→warning/拦截）。
- 后果：稿件可信可直发；代价是改写自由度略降（可接受）。

## ADR-010 合规红线：不自动发布 / 不爬平台 / 只处理自有素材
- 状态：Accepted
- 背景：自动发布与批量搬运触碰平台条款与封号风险；热点爬取有数据合规风险。
- 决策：产品只产出"建议稿"，发布动作留在平台由用户完成；热点由用户手动输入；开源叙事强调合规定位。
- 后果：差异化信任背书；部分效率场景（自动发布）明确不做。

## ADR-011 v1 平台范围：先国内 5，海外后置
- 状态：Accepted
- 背景：国内博主是第一用户群；海外平台内容规范差异大。
- 决策：v1 小红书(✅)/抖音/公众号/知乎/B站；X/IG/FB/YT 通过同一 DNA 机制后期加入。
- 后果：DNA 维护节奏可控；海外扩展零架构改动。

## ADR-012 开源与许可证（待最终确认）
- 状态：Proposed（默认 MIT）
- 背景：以开源作为分发与信任渠道。
- 决策：MIT License + GitHub Releases 分发；README 免责声明；CI（测试/打包）M5 落地。
- 后果：代码可被自由使用；品牌/Pro 增值路径（远期）需另行设计。

---

## 决策记录表（速览）

| ADR | 主题 | 状态 |
|---|---|---|
| 001 | 不用 Minds，独立自研 | ✅ |
| 002 | 本地一键启动 Web 应用 | ✅ |
| 003 | Java 编排 + Python LLM | ✅ |
| 004 | HTTP/1.1 强制 | ✅ |
| 005 | SQLite 本地存储 | ✅ |
| 006 | OpenAI 兼容适配 + mock | ✅ |
| 007 | 平台 DNA YAML 文件化 | ✅ |
| 008 | 四段式管线纯代码编排 | ✅ |
| 009 | 事实清单防幻觉 | ✅ |
| 010 | 合规红线（不自动发布） | ✅ |
| 011 | 先国内 5 平台 | ✅ |
| 012 | MIT 开源 | 🔶 待最终确认 |
