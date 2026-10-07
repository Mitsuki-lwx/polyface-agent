# tasks —— 生成任务异步化：复用任务底座（M8 第二片）

版本：2026-10-07　　方案：`docs/spec_async_generate.md`　　验收：`docs/checklist_async_generate.md`

---

> 每个任务能在一次专注会话内做完。顺序即依赖顺序。
> 「参考资料定位」给到文件/函数，实现时直接去读，不要凭记忆写。

---

## T1 · Python 侧：让生成过程发出阶段事件

- **做什么**：`/generate` 支持一个可选的事件文件路径；生成时把「理解 / 每平台 策略→成稿→质检」写成阶段事件
- **影响文件**：`python-service/app/pipeline/generate.py`、`python-service/app/schemas_gen.py`、`main.py`
- **依赖**：无
- **参考资料**：
  - **底座已经在了**：`python-service/app/pipeline/runlog.py`（`RunLog.stage()` / `progress()` / 事件 schema 带 version）
  - 生成的主流程在 `generate.py:393` 起（`generate()`）；单平台在 `_generate_one`；
    `confirmed_facts` 非空时**跳过理解**（`generate.py:402`）
  - ⚠️ **观测不得改变业务**：整段包 try/except，上报失败只告警（`runlog.py` 的 `_safe` 已实现）
  - ⚠️ 传了事件路径但写不进去时，**生成必须照常完成**
- **完成判据**：给一个路径调 `/generate`，该文件里出现「理解」「平台:xx:成稿」等阶段事件；
  不给路径时**不创建任何文件**（向后兼容）

## T2 · Java 侧：生成任务编排（理解一次 + 每平台一次）

- **做什么**：新增生成任务类型；任务线程里先拿"已确认事实"，再逐平台调 `/generate`
- **影响文件**：新模块（建议 `java-backend/src/main/java/com/polyface/backend/job/GenerateRunner.java`）
- **依赖**：T1
- **参考资料**：
  - **骨架照抄** `job/RoughcutRunner.java`：起任务 / 落终态 / 读进度 / 登记产物
  - 生成请求的形状：`python-service/app/schemas_gen.py::GenerateRequest`
    —— **`confirmed_facts` 非空会跳过理解**，这正是"理解只跑一次"的关键
  - Java 侧既有调用：`client/PythonClient.java`（`generate` 与 `analyze`）
  - 已确认事实的取用：`Store` 的 `material.facts_json` + `facts_confirmed`（FR-34）
  - ⚠️ 既有超时预算：`polyface.llm.timeout-sec`（默认 240）—— 拆成按平台后每次调用都该落在它之内
- **完成判据**：
  - 一个 3 平台的任务，**理解只发生一次**（事件文件里只有一条"理解"）
  - 每平台各一次调用；任一平台失败不影响其他平台

## T3 · 进度：平台级 + 阶段级

- **做什么**：把两层的进度合成一份可读状态（"第 2/3 个平台 · 成稿中"）
- **影响文件**：`job/JobProgress.java`（复用 + 小改）、`GenerateRunner`
- **依赖**：T2
- **参考资料**：
  - `JobProgress.read()` 已经在读事件文件（阶段 / 百分比 / 已完成阶段 / 卡死）
  - Java 自己写的平台边界事件要**用同一套字段**，否则读取端认不出来
  - ⚠️ 事件文件是**两个进程追加同一个文件**（spec §5），读取端必须容忍坏行 —— 已有
- **完成判据**：任务详情能同时看到"第几个平台"与"当前阶段"；事件文件里两边的行都能被解析

## T4 · 任务接口：新增生成类型 + 重试失败平台

- **做什么**：`POST /api/jobs` 支持 `kind=generate`；新增"只重跑失败平台"的入口
- **影响文件**：`web/JobController.java`
- **依赖**：T2
- **参考资料**：
  - 现有的发起 / 列表 / 详情 / 取消在 `JobController`（粗剪那套）
  - `kind` 目前只认 `roughcut`（`JobController.start` 里有校验）
  - 重试的语义：拿原任务失败的平台列表 → 新建一个只含这些平台的任务
  - ⚠️ **重试必须是新任务**，不能改原任务的记录（否则"上次跑了什么"就查不到了）
- **完成判据**：发起 / 详情 / 重试各有单测；非法的 kind → 400

## T5 · 产物与稿件体系对接

- **做什么**：任务完成后，各平台的草稿照旧进 `draft` 表；任务详情给出"生成了哪几篇"的可跳转入口
- **影响文件**：`GenerateRunner` + `MaterialController` 的既有落库逻辑
- **依赖**：T2
- **参考资料**：
  - **既有落库逻辑**在 `MaterialController`（`/api/materials/{id}/generate` 之后那段）
  - ⚠️ 不要复制一份落库代码 —— 抽出来共用，否则两套逻辑必然漂移
  - 失败平台**不产草稿**，但要在任务详情里留原因
- **完成判据**：跑完一个 3 平台任务 → 稿件页能看到 3 篇（或失败的那些带原因）

## T6 · 前端：生成也走任务面板

- **做什么**：把"一键生成多平台稿件"从同步 fetch 改成发起任务 + 轮询；进度显示平台级 + 阶段级；
  失败平台给"重试"按钮
- **影响文件**：`java-backend/src/main/resources/static/index.html`
- **依赖**：T4
- **参考资料**：
  - 任务面板已有实现（粗剪那套）：`pollJob()` / `renderActiveJob()` / `loadJobs()` / `stopPolling()`
  - 现有生成入口：`#btnGenerate` 与 `#genStatus`（同步 fetch）
  - ⚠️ **轮询要停**（任务结束必须 `stopPolling()`）
  - ⚠️ **不要用 `alert()`**（headless 下阻塞页面，本轮踩过）
- **完成判据**：浏览器里能看到"第 n/m 个平台 · 当前阶段"；失败平台出现重试按钮且点了能重跑

## T7 · 接入主流程（**必须有**）

- **做什么**：串起 T1–T6；旧的同步路径**要么删掉、要么明确标注为内部使用**，不留两条并行的入口
- **影响文件**：`MaterialController` / `JobController` / `index.html` / `application.yml`
- **依赖**：T1–T6
- **参考资料**：
  - 既有同步端点：`POST /api/materials/{id}/generate`
  - ⚠️ 项目一贯要求**干净切换**：不留"新旧两条路"（既有 `CLAUDE.md` 的交付约定）
- **完成判据**：工作台只有一条生成路径；旧的同步端点若保留，必须在文档里写明它给谁用

## T8 · 端到端验证（**必须有**）

- **做什么**：新写 `scripts/e2e_async_generate.py`，逐条对 `docs/checklist_async_generate.md`
- **影响文件**：`scripts/e2e_async_generate.py`（新）
- **依赖**：T7
- **参考资料**：
  - 既有 e2e 风格：`scripts/e2e_roughcut_jobs.py`（API + 浏览器双层）
  - ⚠️ 既有教训：**空断言**（`[].every` 恒真）、**误匹配**（"已完成"里含"完成"）、
    **跨轮次脆弱**（靠上一轮遗留文件碰巧通过）—— 三条都踩过，写断言时避开
- **完成判据**：
  - 3 平台任务：进度可见、3 篇稿件落库、任务终态正确
  - **一个平台失败**（构造出来）→ 其他平台成果保留 + 任务标记为"部分失败" + 重试只跑失败那个
  - **理解只发生一次**（事件文件里数一数）
  - 浏览器实测含截图
  - **变异测试 ≥1 次**：故意让"理解只跑一次"变成"每平台都跑一次" → 确认 e2e 变红 → 还原
