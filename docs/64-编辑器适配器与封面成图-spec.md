# M6-1 编辑器适配器与封面成图 — 规格说明

日期：2026-10-06 · 对应任务：`docs/63` · 上游决策：`docs/08` ADR-019

---

## 1. 架构位置

```
浏览器 SPA ──► Java :8080 ──► Python :8000 ──► gimpish（Node 子进程，后台，无 GUI）
                   │                │
              data/media/covers ◄───┘   scene.json + cover.png
```

- **编排在 Java**（草稿/存储/托管），**渲染在 Python 适配器**，**编辑在 gimpish**。
- 三者只通过**官方接口**耦合：Java→Python 走 HTTP，Python→gimpish 走 **CLI**。
  **不 import 对方源码、不 fork**（ADR-019 决策 3）。

## 2. 接口契约（冻结）

### 2.1 Python `POST /compose/cover`

请求：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str(1..60) | ✅ | 封面主标题 |
| `subtitle` | str(0..80) | | 副标题 |
| `platform` | `xhs`\|`douyin`\|`bilibili` | | 默认 `xhs`；决定画布 |
| `theme` | str | | `violet`\|`ink`\|`sunset`，默认 `violet` |
| `out_dir` | str | | 输出根目录（绝对路径）；留空用 `{POLYFACE_DATA_DIR}/media/covers` |
| `file_stem` | str | | 文件名前缀；会被安全化（非 `[0-9A-Za-z_.-]` → `-`） |

响应 200：

```json
{ "status": "ok", "editor": "gimpish",
  "path": "…/covers/xhs-标题-ab12cd/cover.png",
  "scene_path": "…/covers/xhs-标题-ab12cd/scene.json",
  "width": 1080, "height": 1440, "elapsed_ms": 474, "hint": "" }
```

### 2.2 Java `POST /api/drafts/{id}/cover`

请求体可选：`{"platform": "...", "theme": "..."}`（覆盖草稿默认）。

响应 200：`{"status", "url", "path", "width", "height", "elapsed_ms", "hint"}`
其中 `url` = `/api/media/covers/<stem>/cover.png`。

### 2.3 Java `GET /api/media/**`

托管 `{mediaDir}` 下的文件。**必须** normalize + `startsWith` 校验防目录穿越。

## 3. 降级矩阵（**这是本任务的核心不变量**）

| 情形 | 行为 |
|---|---|
| gimpish 未安装 | **200** + `status=needs_manual` + `hint`（含 `npm install -g gimpish`） |
| 渲染失败（rc≠0） | **200** + `needs_manual` + stderr 尾部（截 400 字符） |
| 渲染超时（>120s） | **200** + `needs_manual` + 超时说明 |
| 标题为空 / 平台未知 | **400**（入参非法，不是降级） |
| Python 不可达 | Java 侧 **502** |

> 为什么工具缺失不算错误：它与"素材没配 Key"同类 —— 是**能力缺失**而非**调用失败**。
> 沿用 `asr.py`（无 faster-whisper）、`ingest.py`（无 ffmpeg）已验证过的同一策略。

## 4. 场景模型（gimpish `scene.json` v1）

由我们**声明式**产出（不逐条调用 CLI 动词，省 4 次 node 冷启动）：

```json
{ "version": 1,
  "canvas": {"width":1080,"height":1440,"background":"#0b0b16ff"},
  "layers": [ /* 数组顺序 = 绘制顺序，index 0 在最底 */ ] }
```

| 层 | type | 用途 |
|---|---|---|
| `bg` | `gradient` | 线性渐变底（`anchor: top-left`） |
| `title1..n` | `text` | 标题，每行**独立一层**（不依赖 `\n` 语义，位置完全确定） |
| `sub1..n` | `text` | 副标题（可选） |
| `accent` | `shape`/`rect` | 强调条，最上层 |

**为什么每行一层**：`\n` 的多行语义依赖渲染器实现，而我们要求版式**逐像素确定、可单测**。

## 5. 排版规则（确定性，可单测）

1. **画布**：`xhs` 1080×1440（3:4）· `douyin` 1080×1920（9:16）· `bilibili` 1920×1080（16:9）
2. **边距**：左右各 `round(width × 0.083)`（1080 → 90）
3. **字号自适应**：标题基准 `round(width × 0.111)`（1080 → 120），逐级 −6 直到**在 3 行内放得下**，下限 44
4. **字宽估算**：CJK/全角 ≈ 1em，大写 ≈ 0.62em，小写 ≈ 0.53em，空格 ≈ 0.28em
5. **折行**：ASCII 优先在空格断行，CJK 逐字断行
6. **中文禁则**（`_fix_punctuation`）：`，。、：；！？）」』】》—·` 不得落在**行尾**；
   `，。、：；！？）』】》…—·` 不得落在**行首**。行尾的 `…` 除外（那是截断标记）
7. **截断**：超过 3 行 → 保留 3 行，末行以 `…` 结尾（宽度回退到放得下为止）
8. **垂直**：文本块以 `0.40 × height` 为视觉中心；副标题距标题 `0.035 × height`；
   强调条在文本块下方 `0.045 × height`

> 规则 3 有个**已修的陷阱**：若用截断后的行去判断"放得下"，永远为真，字号永不缩小。
> 测量必须用**不截断**的折行（`max_lines=99`）。见 `test_long_title_shrinks_font_size`。

## 6. 目录与配置

```
{data-dir}/media/covers/
└── <platform>-<safe-title>-<6位hex>/
    ├── scene.json    ← gimpish 文档（可 `gimpish -C <dir> serve` 打开手改）
    └── cover.png     ← 产物
```

| 配置 | 位置 | 默认 | 说明 |
|---|---|---|---|
| `GIMPISH_PATH` | `.env` | 空 | gimpish 可执行文件/入口 js |
| `POLYFACE_GIMPISH` | 环境变量 | 空 | 同上，优先级更高 |
| `POLYFACE_COVER_DIR` | 环境变量 | `{data-dir}/media/covers` | 产物根目录 |

`.js` 入口会被自动用 `node` 执行；`.cmd`/可执行文件直接执行。

## 7. 安全边界

- 产物只写本机 `{data-dir}`；**不出网**（gimpish 全程本地渲染）
- `file_stem` 经白名单正则安全化，**不接受**用户给的路径（`out_dir` 由 Java 侧固定为 `{mediaDir}/covers`）
- `GET /api/media/**` 必须防穿越（`docs/63` 验收项）

## 8. 未覆盖 / 待验证

- **真实作者审美**：本规格只保证确定性与排版基本规则，**不保证"好看"**
- 素材原图入封面（抠图/背景去除，gimpish 有 `remove-bg`，未接）
- gimpish 的 `serve`/MCP 路径未接（当前只走 CLI，最小依赖面）

## 9. 附：gimpish `serve` 的 HTTP 面（M6-2 将用到，2026-10-06 实测）

M6-2 要内嵌它的编辑器，这里是先探明的接口面，**含一个必须记住的坑**。

| 端点 | 作用 |
|---|---|
| `GET /api/scene` | 完整场景 JSON |
| `GET /api/geometry` | 每层包围盒 + `move`/`rotate`/`scale` 标志（可直接做命中测试） |
| `GET /api/history` | `{undo, redo}` 栈深 |
| `GET /api/bundle` | 打包 `.gimpish`（场景 + 资产） |
| `POST /api/import?name=<f>` | 原始字节导入为图层 |
| `POST /api/layer/:id/transform` | 改位置（**见下方坑**） |
| `POST /api/layer/:id/order` | 调整层序（body `{index}`） |
| `DELETE /api/layer/:id` | 删层 |
| `POST /api/undo` / `POST /api/redo` | 撤销/重做 |

**其它实测结论**

- 首页响应头**无** `X-Frame-Options`、**无** CSP → **可以 iframe 内嵌**。
- 只监听 `127.0.0.1`（实测 URL 形态 `http://127.0.0.1:<port>`），无鉴权 —— 与 polyface 现有姿态一致。
- `gimpish serve --port <n> --scene <dir-or-file>`，进程监视场景目录，外部改动会推送给页面。

### ⚠️ 坑：transform **只认增量**，且**静默忽略**未知字段

```
起点 (540,700)
POST {"dx":60,"dy":-100}  → (600,600)  ✅ 生效
POST {"x":5,"y":5}        → (600,600)  ⚠️ 未生效，但**照样返回 {"ok":true}**
POST {"dx":0,"dy":0}      → (600,600)  无变化
```

**两条纪律**（M6-2 实现必须遵守）：

1. 改位置要**自己记当前坐标算增量**（`dx = 目标 - 当前`），不能直接下发绝对坐标；
2. **不能靠 `{"ok":true}` 判断是否生效** —— 必须回读 `GET /api/scene` 校验。
