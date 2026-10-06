# M6-2 编辑器进程编排与工作台内嵌 — 验收清单

日期：2026-10-06 · 对应 `docs/67` task / `docs/68` spec
勾选规则：**只有实测通过的才勾**。无法在本机验证的，勾选时必须附限定说明。

> **补验证记录（2026-10-06 晚）**：首轮交付时有 4 项未验证（下方标注 ⚠️ 的历史项）。
> 本轮逐项补上，并**顺带发现一个真实缺陷**：`gimpish serve` 在端口被占时**不会退出**，
> 所以原来只靠"进程死了就快速降级"救不了端口冲突 —— 实测要白等满 15s 健康预算。
> 已加**端口前置探测**（spawn 前先 bind 一次），降级耗时 **15.3s → 0.0s**。

---

## A. 进程编排（Java）

- [x] 定位 gimpish：配置 > `POLYFACE_GIMPISH` > PATH；`.js` 入口自动前置 `node`
- [x] 未安装 → `available=false` + **可执行的**安装指引，不抛异常（`EditorControllerTest`）
- [x] 懒启动：未点「打开」前**不**起进程（E2E 的 B2 冷启动 0.9s 即证据：进程是点出来的）
- [x] 单实例：同一时刻只有一个 serve 进程（`sameDirIsReused` / `differentDirRestarts`）
- [x] 健康检查打 `/api/scene`（不是 `/`），超时 15s（`probeNeverReadyDegradesAfterTimeout`）
- [x] 同目录复用：重复 open 同一封面**不重启**（单测 + E2E `elapsed_ms` 极小）
- [x] 换目录重启：open 另一张封面 → 指向新目录，旧进程被杀（`differentDirRestarts`）
- [x] **端口被占 → `needs_manual`**（**已补验证**）：
      单测 `occupiedPortDegradesImmediatelyWithoutSpawning`（真实 bind 探测，**不 spawn**、点名端口）；
      E2E `G1/G2/G3` 用真占端口复现 → `200 + needs_manual`，**降级耗时 0.0s**
- [x] **`open` 并发被互斥锁串行化**（**已补验证**）：`concurrentOpenLaunchesOnlyOneProcess` ——
      4 线程同时 open 同一目录，launcher 只被调用 **1** 次，4 个结果都是 ok
- [x] JVM 退出时子进程被杀 —— **实测**：`stop` 掉 Java 服务后 `netstat` 无 18765 LISTENING，PID 已消失
- [x] 场景目录内保留 `scene.json`，gimpish 写回同一文件（E2E D1 回读校验）

## B. 接口

- [x] `GET /api/editor/status` 形状符合 `docs/68` §2.1（`EditorControllerTest` 断言键集合恰为 7 个）
- [x] `POST /api/editor/open` 成功 → `status=ok` + 可用 url（E2E B1/C1）
- [x] `POST /api/editor/open` 未装 gimpish → **200 + needs_manual + hint**（不是 5xx）
- [x] 越界路径（`../../`）→ **400**（含绝对路径在媒体根之外的情形）
- [x] 不存在路径 / 目录内无 `scene.json` → **404**
- [x] `POST /api/editor/stop` 幂等（没在跑也 200，`{"status":"not_running"}`）
- [x] `version` 字段真的能探到（修了探测方式后实测 `"version":"0.1.0"`）

## C. 前端

- [x] 封面生成成功后出现「✏️ 在编辑器中打开」
- [x] 点击 → iframe 真的加载（E2E B1：src 指向编辑器）
- [x] **再次点击可收起**（**已补验证**）：E2E `D2` —— iframe 被移除且按钮文案还原为「✏️ 在编辑器中打开」
- [x] `needs_manual` → 中性提示 + 安装指引（**不是**红色报错）
- [x] **未装 gimpish 时按钮仍在**（**已补验证**）：E2E 降级模式 `H2` ——
      封面走 `needs_manual` 时**编辑器入口仍渲染**；`H3` 点击后给出 `npm install -g gimpish`；
      `H4` 不显示为红色报错
      > 实现上顺手修了一处：原先入口只在封面成功分支渲染 → 缺工具时"功能凭空消失"。
      > 已改为**与封面成败无关**，失败时点了把原因讲清楚（`docs/68` §4 本来就要求这样）。

## D. 测试与防线

- [x] Java 单测：可用性探测 / 越界 400 / 不存在 404 / status 形状 / stop 幂等（`EditorControllerTest` 7 项）
- [x] 健康检查逻辑用**真实本地 HTTP stub**（`com.sun.net.httpserver`）验证成功与超时两条路径
      —— 且**变异测试证明它有效**（见下）
- [x] 变异测试 2 次注入均被检出：
      ① `open` 绕过 `MediaDir.resolveSafe` → `EditorControllerTest` 1 failed；
      ② 健康探测恒真（方法入口短路）→ `probeNeverReadyDegradesAfterTimeout` failed
- [x] Python 全量回归无退化（**289 passed**）
- [x] Java 全量回归无退化（**66 passed**，此前 48）
- [x] 浏览器端到端 `scripts/e2e_editor.py`：
      **正常模式 15/15** + **降级模式 4/4**（含截图）

> **§D 备注（一次"假变异"的教训）**：第一次做的 M2 是把
> `return client.send(...).statusCode() == 200` 改成 `send(...); return true` ——
> 它**没被抓住**。查下来不是测试漏洞，而是**等价变异**：超时场景下 `send` 会抛异常，
> 根本走不到那个 `return`。把变异改成"方法入口直接 return true"才真正骗过超时路径，随即被检出。
> **教训：变异没被抓住时，先判断它是不是等价变异，再怀疑测试。**

## E. 文档

- [x] `docs/08` ADR-020（HTTP + 内嵌，MCP 暂不用，含证据）
- [x] `docs/64` §9（serve 接口面 + `ok:true` 静默忽略的坑）
- [x] `docs/67` task / `docs/68` spec（§3 已补"端口前置探测"）/ 本 checklist
- [x] `docs/70` 交付说明（含「残余项 / 未验证」）
- [x] README：新增"在编辑器里改封面"用法 + 配置项 + 版本钉死说明
- [x] `scripts/doctor.py` 新增「封面编辑器」检查（含版本不符时提示重跑 E2E），+6 项测试

## F. 交付

- [x] 全量 `git status` 复核后提交
- [x] 推送到 `origin/main`

---

## 明确不做（Out of scope，见 `docs/67` §2）

- 桌面窗口 / Tauri / Electron 打包（M6-2b）
- 随包分发 Node 运行时（M6-2b）
- 视频剪辑适配器（M6-3）
- LLM 自主改图（工具调用 / MCP）—— ADR-020 已定：前提不成立
