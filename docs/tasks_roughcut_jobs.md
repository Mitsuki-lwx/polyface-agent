# tasks —— 粗剪任务化：异步任务 + 工作台入口

版本：2026-10-07　　方案：`docs/spec_roughcut_jobs.md`　　验收：`docs/checklist_roughcut_jobs.md`

---

> 每个任务能在一次专注会话内做完。顺序即依赖顺序。
> 「参考资料定位」给到文件/函数，实现时直接去读，不要凭记忆写。

---

## T1 · job 表与状态机

- **做什么**：建 `job` 表（状态 / 类型 / 参数 / 输入 / 产物 / 事件文件 / 时间戳 / 错误），
  以及最小的状态迁移（排队 → 运行 → 成功|失败|已取消）
- **影响文件**：`java-backend/src/main/java/com/polyface/backend/store/Store.java`
- **依赖**：无
- **参考资料**：
  - `Store.initSchema()` —— DDL 是**字符串数组、逐条 execute**（sqlite-jdbc 单次只执行首条）
  - 既有表的字段风格（`asset` / `draft` 的 `created_at` 等）
- **完成判据**：状态迁移有单测；重启进程后能读回上次的任务

## T2 · 起子进程跑 CLI，并识别"跑完了没有"

- **做什么**：Java 起 `python scripts/roughcut.py <输入> --yes --events <路径>`，
  跟踪退出码；**进程意外死亡要能识别**（不能一直显示"运行中"）
- **影响文件**：新模块（建议 `java-backend/src/main/java/com/polyface/backend/job/RoughcutRunner.java`）
- **依赖**：T1
- **参考资料**：
  - **进程管理的现成范例**：`com.polyface.backend.editor.EditorProcess`
    （定位可执行文件 / 启动 / 健康检查 / `destroyDescendants` 杀进程树 / 退出清理）
  - ⚠️ 那套里踩过的坑：Windows 上 cmd 包装器会留 node 孤儿，必须杀**进程树**
  - CLI 的开关在 `scripts/roughcut.py`（`--yes` / `--events` / `--level` / `-o` / `--workdir`）
- **完成判据**：正常跑完 → 退出码 0；把输入换成不存在的文件 → 退出码非 0 且能读到原因

## T3 · 读事件文件算进度

- **做什么**：tail 事件 JSONL，算出"当前阶段 + 最近一次百分比 + 已完成阶段列表"；
  并识别**卡死**（长时间没有任何新事件）
- **影响文件**：同上模块
- **依赖**：T2
- **参考资料**：
  - 事件格式与 schema 版本：`python-service/app/pipeline/runlog.py`（`StageEvent` / `START/END/PROGRESS/FAIL`）
  - 事件里 `stage` / `kind` / `elapsed_ms` / `fields.pct` / `message` 都有，**不用另写解析规则**
- **完成判据**：
  - 一次真跑的产物里，能报出 ≥3 个阶段与至少一次百分比
  - 事件文件缺失/半行（写到一半被杀）时**不崩**，只是进度未知

## T4 · 任务接口

- **做什么**：发起 / 列表 / 详情 / 取消
- **影响文件**：`java-backend/src/main/java/com/polyface/backend/web/JobController.java`（新）
- **依赖**：T1–T3
- **参考资料**：
  - 控制器风格与错误码：`web/CoverController.java`、`web/AssetController.java`
  - 素材库的资产定位：`asset/AssetService.java`（发起时要按 asset id 找到视频文件）
- **完成判据**：四个端点各有单测；发起不存在的资产 → 404；取消已结束的任务 → 幂等

## T5 · 产物自动入素材库

- **做什么**：任务成功后，把成片 / 字幕 / 剪点登记为资产，并**关联到源视频**
- **影响文件**：`RoughcutRunner` + `asset/AssetService.java` 的复用
- **依赖**：T4
- **参考资料**：
  - `AssetService.register(relPath, source, name, tags)` 与 `link(assetId, ownerKind, ownerId)`
  - ⚠️ **登记失败绝不能影响任务成功**（沿用 M7 的既有约定：包 try/catch 只 warn）
  - 源视频与产物的关联：`asset_link` 现有 `owner_kind` 只认 `material|draft` ——
    需要新增一种 owner（如 `asset`，即"产物挂在源视频上"）
- **完成判据**：跑完一次，素材库里出现成片与字幕，且能看到它们关联到哪个源视频

## T6 · 前端：素材库视频行 →「粗剪」

- **做什么**：视频资产行加「粗剪」按钮；点开是一个**参数弹层**（默认值来自后端），
  确认后发起任务
- **影响文件**：`java-backend/src/main/resources/static/index.html`
- **依赖**：T4
- **参考资料**：
  - 素材库面板现有实现（M7-1）：`loadAssets()` / `bindAssetHandlers()` 与 `#assetList` 渲染
  - 参数契约在 Python 侧 `roughcut.py::RoughcutParams`；后端要把它**暴露出来**给前端填默认值
  - ⚠️ 现有工作台是**单文件无构建 SPA**（ADR-015），不要引入任何前端框架
- **完成判据**：能改参数并发出任务；未装 ffmpeg / ASR 时给出可执行的指引而不是报错

## T7 · 前端：进度与任务列表

- **做什么**：轮询任务状态，显示当前阶段 + 百分比 + 心跳时间；任务列表可查；
  完成后提示产物已入素材库
- **影响文件**：同上
- **依赖**：T6
- **参考资料**：
  - ⚠️ **headless 浏览器里 `alert()` 会阻塞页面**（本轮踩过）—— 提示不要用 alert
  - ⚠️ 轮询要**停**：任务结束后必须停止轮询，否则会一直打接口
- **完成判据**：浏览器实测能看到阶段推进；任务结束后轮询停止

## T8 · 接入主流程（**必须有**）

- **做什么**：把 T1–T7 串起来；`--help`/配置项齐全；**CLI 单独用仍然可用**（工作台不是唯一入口）
- **影响文件**：`JobController` / `RoughcutRunner` / `application.yml`（新增 python 与脚本路径配置）
- **依赖**：T1–T7
- **参考资料**：
  - 配置风格：`application.yml` 里 `polyface.editor.*` 与 `polyface.gimpish.*`（含环境变量回退）
  - ⚠️ 路径要能在**发布包**里成立（包根 + `python-service/.venv` + `scripts/`），不能写死开发机路径
- **完成判据**：服务起来后，工作台能完整跑完一次粗剪；命令行也仍能独立跑

## T9 · 端到端验证（**必须有**）

- **做什么**：新写 `scripts/e2e_roughcut_jobs.py`，逐条对 `docs/checklist_roughcut_jobs.md`
- **影响文件**：`scripts/e2e_roughcut_jobs.py`（新）
- **依赖**：T8
- **参考资料**：
  - 既有 e2e 风格：`scripts/e2e_assets.py`（浏览器 CDP）、`scripts/e2e_roughcut.py`（CLI）
  - ⚠️ 既有教训：**跨轮次脆弱**的断言（靠上一轮遗留文件"碰巧通过"）→ 一律**现拍快照**
  - ⚠️ 既有教训：headless 下 `alert()` 阻塞 → 测试开头把 `window.alert` 换成记录桩
- **完成判据**：
  - 浏览器实测：素材库发起 → 看到进度 → 完成 → 产物出现在素材库（含截图）
  - 取消任务后**进程真的没了**（不是只改了状态）
  - 服务重启后任务列表**还在**
  - **变异测试 ≥1 次**：故意让子进程退出码不被处理 → 确认 e2e 变红 → 还原
