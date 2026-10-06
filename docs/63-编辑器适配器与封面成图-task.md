# M6-1 编辑器适配器与封面成图 — 任务书

日期：2026-10-05 · 上游决策：`docs/08` **ADR-019**（交付形态改向）

---

## 1. 为什么做这个

ADR-019 把 polyface 的形态定为「**桌面壳 + 后台编辑器 + agent 编排**」：
由 polyface 驱动成熟的开源编辑器在后台完成做图与剪辑，自己专注编排与 LLM 环节。

本任务**不是**那个桌面壳，而是**先验证壳里最关键、也最不确定的那条链路**：

> **agent 能不能真的在后台驱动一个开源图像编辑器，产出一张能用的图？**

若这条不成立，后面做壳、做剪辑都是空谈。所以先做**最薄的一片**：稿件标题 → 平台封面 PNG。

## 2. 范围

### In（本次做）

| # | 项 |
|---|---|
| 1 | **Python 侧编辑器适配器**：`app/pipeline/cover.py` —— 声明式产出 gimpish 的 `scene.json`，调用 `gimpish export` 光栅化 |
| 2 | **HTTP 端点**：`POST /compose/cover`（Python :8000） |
| 3 | **Java 透传**：`POST /api/drafts/{id}/cover` + `GET /api/media/**`（托管产物） |
| 4 | **前端**：草稿详情区「生成封面」按钮 + 结果展示 |
| 5 | 降级：编辑器缺失/渲染失败 → `needs_manual` + 安装指引，**不抛 5xx** |
| 6 | 测试与文档（四件套 + ADR-019） |

### Out（本次**不做**，但已明确归属）

| 不做 | 归属 |
|---|---|
| 桌面壳（进程编排 / 窗口 / 打包） | M6-2 |
| **视频剪辑适配器**（含 OpenCut） | M6-3；OpenCut 当前**不可驱动**（ADR-019 有证据） |
| 用素材原图做背景（本次只有渐变底 + 文字） | M6-1b |
| TTS / 配音 | 未决（ADR-018 Q3） |
| 第三关（真实作者验证） | 与 M6 并行，不取消 |

## 3. 关键判断（已实测，不是设想）

| 判断 | 证据 |
|---|---|
| gimpish 可后台驱动 | 本机 `npm i gimpish` 后，写 `scene.json` → `gimpish -C <dir> export --out x.png`，**474ms** 产出 1080×1440 PNG，中文正常 |
| OpenCut **不可**后台驱动 | 其桌面端 README 原文「Very early. Right now this is just a window that opens.」；headless/Editor API/MCP 全在 roadmap；classic 已归档且导出正在重写 |
| 不需要新增 Python 依赖 | 光栅化交给 gimpish（sharp）；本模块只用标准库 |

## 4. 依赖与前置

- Node.js ≥ 20.19（本机 24.12.0 ✅）
- `gimpish`（npm，MIT）。开发期用 `POLYFACE_GIMPISH` 指向入口 js；产品化由 M6-2 负责随包分发
- 无数据库改动、无 Java 依赖新增

## 5. 验收（可观测）

1. `POST /compose/cover` 用真实 gimpish 产出 PNG，尺寸与平台画布一致（1080×1440 / 1080×1920 / 1920×1080）
2. gimpish 不可用时返回 **200 + `needs_manual` + 可执行的安装指引**（不是 500）
3. 浏览器实测：草稿页点「生成封面」能看到图片
4. `pytest` 与 `mvn test` 全绿，且**新增用例能抓住真实缺陷**（变异测试至少 1 次）
5. 产物落在 `{data-dir}/media/covers/<stem>/`，`scene.json` 与 `cover.png` 同目录
   （**用户可用 `gimpish serve` 打开继续手改** —— 这是选"声明式场景"而非"直接拼像素"的理由）

## 6. 风险

| 风险 | 应对 |
|---|---|
| gimpish 仍是 0.1.x，schema 可能变 | `scene.json` 由 zod 校验，**变了一跑就报错**（fail loud）；集成测试真跑 gimpish，不 mock 渲染 |
| 封面版式是"审美判断"，无法自动验收 | 只承诺**确定性 + 中文排版基本规则**（禁则标点）；版式由模板/主题演进，不承诺"好看" |
| 引入 Node 与 ADR-002「本地一键启动」冲突 | **已由 ADR-019 显式撤销**该承诺；M6-2 负责把 Node 依赖做成"随包/可选 + 降级" |
