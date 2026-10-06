# M6-2 编辑器进程编排与工作台内嵌 — 规格说明

日期：2026-10-06 · 对应任务：`docs/67` · 上游：`docs/08` ADR-019 / ADR-020

---

## 1. 架构位置

```
浏览器 SPA ──► Java :8080 ──┬─► Python :8000 ──► gimpish CLI（一次性渲染，M6-1）
                            │
                            └─► gimpish serve :8765（**受管长驻子进程**，本任务）
                                   └─ 工作台用 <iframe> 内嵌它的编辑器页面
```

- **进程归 Java 管**（Java 是编排者与生命周期所有者）。
- Python 侧**不碰**这个进程 —— 它只做一次性渲染（M6-1），职责不混。

## 2. 接口契约（冻结）

### 2.1 `GET /api/editor/status`

```json
{ "available": true, "running": false, "url": "", "port": 8765,
  "scene": "", "version": "0.1.0", "hint": "" }
```

| 字段 | 含义 |
|---|---|
| `available` | 本机是否找得到 gimpish（找不到 → `hint` 给安装指引） |
| `running` | serve 进程是否活着**且**健康检查通过 |
| `url` | `http://127.0.0.1:<port>`，仅在 running 时非空 |
| `scene` | 当前 serve 指向的场景目录（绝对路径） |
| `version` | gimpish 版本（探测不到则为空） |
| `hint` | 不可用/异常时的**可执行**指引 |

### 2.2 `POST /api/editor/open`

请求：`{"path": "<封面文档目录 或 scene.json 的绝对路径>"}`

- `path` 必须落在**媒体根目录内**（`MediaDir.resolveSafe` 校验），越界 → **400**
- 路径不存在 / 目录内无 `scene.json` → **404**
- 已有 serve 且指向同一目录 → **直接复用**（不重启）
- 已有 serve 但指向别的目录 → **重启**指向新目录
- 无 serve → 启动

响应 200：

```json
{ "status": "ok" | "needs_manual", "url": "http://127.0.0.1:8765",
  "port": 8765, "scene": "<绝对目录>", "version": "0.1.0", "hint": "", "elapsed_ms": 1200 }
```

- `status=needs_manual`（**仍是 200**）：gimpish 未安装 / 端口被占 / 启动超时 —— 附 `hint`
- **只有入参非法才是 400/404**

### 2.3 `POST /api/editor/stop`

```json
{ "status": "stopped" | "not_running" }
```

幂等：没在跑也返回 200。

## 3. 进程生命周期

| 阶段 | 行为 |
|---|---|
| 定位可执行文件 | 配置 `polyface.gimpish.path` > 环境变量 `POLYFACE_GIMPISH` > PATH（`gimpish.cmd`/`gimpish`/`gimpish.js`）；`.js` 入口自动前置 `node` |
| **端口前置探测** | spawn **之前**先自己 `bind` 一次编辑器端口；被占 → **立即** `needs_manual`（点名端口 + 怎么改），**不 spawn、不等健康预算** |
| 启动 | `gimpish serve --port <port> --scene <dir>`，工作目录 = 场景目录，stdout/stderr 收进环形缓冲（供 `hint` 引用） |
| 健康检查 | 轮询 `GET http://127.0.0.1:<port>/api/scene` 直到 200；**超时 15s** → 杀进程 + `needs_manual`；若进程中途死掉则**提前**降级 |
| 复用 | 目标目录 == 当前目录且健康 → 直接返回 |
| 重启 | 目标目录 != 当前目录 → 停旧的（destroy + 等端口释放，最多 5s）再起新的 |
| 退出清理 | JVM `shutdownHook` 杀子进程；`stop` 接口手动停 |
| 并发 | `open` 用**互斥锁**串行化（避免两个请求同时起两个进程） |

> 为什么健康检查打 `/api/scene` 而不是 `/`：前者能证明**场景已加载**，后者只能证明 HTTP 起来了。
>
> **为什么必须有"端口前置探测"**（2026-10-06 实测补上）：`gimpish serve` 在端口被占时
> **不会退出**（照样活着），所以"进程死了就快速降级"这条救不了端口冲突 ——
> 只会白等满 15s 健康预算。实测：加前置探测前 `open` 要 **15.3s** 才降级，加完 **<1s**。
> 探测用 `bind` 而非 `connect`（判据与 `scripts/doctor.py::port_in_use` 一致），
> 且**刻意不设** `SO_REUSEADDR` —— Windows 上它会允许绑到别人已监听的端口，等于没测。

## 4. 前端

- 封面生成成功（M6-1 的 `status=ok`）后，结果区增加「✏️ 在编辑器中打开」按钮
- 点击 → `POST /api/editor/open {path: <该封面的目录>}` → 成功后在按钮下方渲染
  `<iframe src=url>`（高度 ~720px，`border-radius` 与页面一致）
- 再次点击可收起（避免页面被 iframe 撑太长）
- `needs_manual` → 显示 `hint` 文本，**中性样式**，不标红
- 未装 gimpish 时按钮**仍然显示**（点了给安装指引），这样用户知道有这功能

## 5. 安全边界

- `path` 一律经 `MediaDir.resolveSafe`（防目录穿越，含 `\` 归一化）
- serve 只监听 `127.0.0.1`，**无鉴权** —— 与 polyface 现有姿态一致；不新增对外暴露面
- iframe 跨源（:8080 → :8765）：我们只展示，不读取其内部 DOM（也不应假装能读）

## 6. 版本与兼容

| 项 | 值 |
|---|---|
| 已验证 gimpish 版本 | **0.1.0**（npm） |
| 已知接口坑 | `POST /api/layer/:id/transform` 只认增量 `dx/dy`，传绝对 `x/y` **静默忽略仍返回 ok**（`docs/64` §9） |
| 升级纪律 | 升 gimpish 必须重跑本任务的 E2E（`scripts/e2e_editor.py`），因为内嵌的是它的 UI |

## 7. 配置

| 变量 | 位置 | 默认 | 说明 |
|---|---|---|---|
| `polyface.gimpish.path` / `POLYFACE_GIMPISH` | application.yml / 环境变量 | 空 | gimpish 入口；空则找 PATH |
| `polyface.editor.port` / `POLYFACE_EDITOR_PORT` | 同上 | `8765` | serve 监听端口 |
| `polyface.editor.enabled` / `POLYFACE_EDITOR_ENABLED` | 同上 | `true` | 关掉则接口直接返回 `needs_manual`，不尝试起进程 |

## 8. 未覆盖 / 待验证

- **真正的桌面窗口**（M6-2b）：本任务仍是"浏览器里的工作台 + 内嵌编辑器"
- **随包分发 Node/gimpish**（M6-2b）：当前要求用户自装
- 编辑结果**回灌到稿件**（比如改了封面尺寸后重新生成）：未做，属于后续
- gimpish 的 `remove-bg`（抠图）未接
