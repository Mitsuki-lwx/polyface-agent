# Polyface · 同源万面

> **一份素材进，千面出。** 把一份原创素材，自动改写为符合各平台调性、有机会获得流量的完整可发布稿件。

面向自媒体博主的**本地一键启动工具**（下载即用，数据不出本机）。灵感源于 Creative Minds Jam（Content repurposing across platforms），但**完全独立自研**，不依赖任何外部智能体平台。

## 核心能力

- 📥 输入一份素材（长文 / 口播稿 / 大纲 / 笔记）
- 🧬 按**平台 DNA**（语言风格 / 结构模板 / 标题机制 / 标签策略 / 红线词）逐平台改写 —— 不只是换格式，而是换"灵魂"
- 🛡️ **事实约束**：成稿中的数值/故事必须能在素材"事实清单"中找到依据，QA 拦截 AI 自加戏
- 🧠 **记忆与复盘**：创作者画像 + 效果回填 + 复盘报告，越用越懂你
- 🔒 **本地优先**：素材与稿件只存在你的电脑；LLM Key 由你自己填

## 平台支持

| 阶段 | 平台 |
|---|---|
| v1 | 小红书 · 抖音 · 微信公众号 · 知乎 · B站 |
| 规划中 | X(Twitter) · Instagram · Facebook · YouTube |

## 架构

```
浏览器(127.0.0.1:8080)
   └─ Java 后端 :8080 (Spring Boot)   ← 业务编排/任务状态/本地存储(SQLite)
        └─ Python LLM 服务 :8000 (FastAPI)  ← 素材解析/平台策略/成稿/QA
             └─ LLM (OpenAI 兼容，默认 SenseNova 商汤)
```

## 快速开始

> 需要：Java 17+ / Python 3.11+ / 浏览器。

```bash
# 1. 配置 LLM Key（商汤 SenseNova 示例；支持任意 OpenAI 兼容接口）
cp python-service/.env.example python-service/.env
# 编辑 .env 填入 LLM_API_KEY；未填 Key 时默认 LLM_MOCK=true，可离线体验

# 2. Windows 一键启动
scripts\start.bat

# 或手动分别启动：
#   python-service: uvicorn app.main:app --port 8000
#   java-backend:   mvn spring-boot:run
# 浏览器打开 http://127.0.0.1:8080
```

## 目录结构

```
polyface/
├── java-backend/      # Spring Boot :8080 —— 业务编排 + 存储 + Web 托管
├── python-service/    # FastAPI :8000 —— LLM 管线（解析/策略/成稿/QA）
├── platform-dna/      # 各平台 DNA（YAML，可编辑可升级）
├── scripts/           # 一键启动脚本
└── docs/              # 方案与蓝图文档
```

## 路线图

- [x] M0 方案与蓝图
- [x] M1 骨架 + 素材解析跑通
- [x] M2 单平台（小红书）成稿 + QA 全链路 + SQLite 落库
- [ ] M3 5 平台 DNA + Web 工作台 + **剪辑单**（抖音/B站/YT 分镜执行单）
- [ ] M3.5 视频/音频 → 文字入料（转录后复用解析管线）
- [ ] M4 效果回填 + 复盘 + 画像记忆闭环
- [ ] M5 开源 Release 打包 + **本地自动成片专项**（B1 图文成片 + B2 智能剪已有视频：去停顿/字幕/分镜）

## 文档体系（docs/）

| 阶段 | 文档 |
|---|---|
| 方案 | 01 总体方案设计 |
| 建模 | 02 领域建模与产品蓝图（含竞品快研） |
| 需求 | 05 需求规格说明书 SRS |
| 可行性 | 06 可行性分析报告（技术/经济/市场/运营/合规） |
| 用例 | 07 用例模型与验收标准 |
| 决策 | 08 技术决策记录 ADR |
| 交付 | 03 / 04 里程碑交付说明 |

## 合规声明

本工具**只处理你自有或已获授权的素材**；产出"改写建议稿"而非搬运；**不提供自动发布**，
发布行为由你本人在各平台完成并遵守其原创与 AI 内容规范。

## License

[MIT](./LICENSE)
