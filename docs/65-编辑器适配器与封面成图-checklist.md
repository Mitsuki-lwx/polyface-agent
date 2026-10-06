# M6-1 编辑器适配器与封面成图 — 验收清单

日期：2026-10-05 · 对应 `docs/63` task / `docs/64` spec
勾选规则：**只有实测通过的才勾**。无法在本机验证的，勾选时必须附限定说明。

---

## A. 前置：能力验证（先证伪，再动手）

- [x] gimpish 可安装（本机 Node 24.12.0 / npm 11.6.2，`npm i gimpish` 118 包 27s）
- [x] gimpish 可**无 GUI** 渲染：写 `scene.json` → `gimpish -C <dir> export --out x.png` 产出 PNG
- [x] 中文文本渲染正常（无乱码/豆腐块）
- [x] OpenCut 不可后台驱动（证据：其 desktop README「just a window that opens」；headless/API/MCP 均为 roadmap）
- [x] 渲染耗时实测：**474ms / 张**（1080×1440）

## B. Python 适配器

- [x] `app/pipeline/cover.py`：声明式 `build_scene()`（纯函数）
- [x] 平台画布正确：xhs 1080×1440 / douyin 1080×1920 / bilibili 1920×1080
- [x] 每行文本独立成层（不依赖 `\n` 语义）
- [x] 字号自适应：长标题确实缩小（`test_long_title_shrinks_font_size`）
- [x] 中文禁则：行首/行尾不出现禁则标点
- [x] 超长标题截断并以 `…` 结尾
- [x] 空标题 → `CoverInvalid`；未知平台 → `CoverInvalid`
- [x] 产物目录：`covers/<stem>/{scene.json,cover.png}`（scene 与图同目录，可 `gimpish serve` 手改）
- [x] `file_stem` 白名单安全化
- [x] 工具定位优先级：`GIMPISH_PATH` > `POLYFACE_GIMPISH` > PATH
- [x] `.js` 入口自动用 `node` 执行

## C. 降级矩阵（核心不变量）

- [x] gimpish 缺失 → `needs_manual` + `hint`（含 `npm install -g gimpish`），**非 5xx**
- [x] 端点层同样返回 **200 + needs_manual**（`test_api_missing_tool_returns_200_needs_manual`）
- [x] 渲染失败 rc≠0 → `needs_manual` + stderr 尾部
- [x] 渲染超时 → `needs_manual` + 说明
- [x] Java 侧 Python 不可达 → 502（`CoverControllerTest.pythonUnreachableReturns502`）

## D. HTTP 端点

- [x] `POST /compose/cover` 存在且返回 `CoverResponse`
- [x] 空标题 → 422（pydantic）
- [x] 未知平台 → 422
- [x] 真实渲染经端点走通（`test_real_render_*` 3 项，实测 gimpish）
- [x] 真实 HTTP 进程实测（uvicorn :18321）：xhs 464ms / douyin 1279ms / 非法平台 422
- [x] 渲染矩阵 3 平台 × 3 主题 + 超长标题 + 含标点标题：**11/11 全过**，尺寸全对

## E. Java 透传 + 前端

- [x] `POST /api/drafts/{id}/cover` 从草稿取标题/平台并透传（单测断言了发给 Python 的请求体契约）
- [x] `needs_manual` 走 200 且带 `hint`；Python 不可达 → 502
- [x] `GET /api/media/**` 托管产物，**防目录穿越**（`MediaDir.resolveSafe` 统一拦截，含 `\` 归一化）
- [x] 前端草稿页「生成封面」按钮 + 图片展示
- [x] 降级时显示安装指引（**不是**红色报错）
- [x] 浏览器实测：点按钮 → 看到图片（`scripts/e2e_cover.py`，**8/8**，含 `naturalWidth>0` 与字节校验）

## F. 测试与防线

- [x] `tests/test_cover.py` **18** 项全绿（含 3 项真实 gimpish 集成）
- [x] Java 测试 `CoverControllerTest` 5 项全绿（Java 全量 **48 passed**）
- [x] 变异测试 **3 次注入全部被检出**：①去掉禁则修正 ②恢复截断式测量 ③缺失工具改抛错
      （①第一次注入**没被抓住**，说明原用例是空的 → 已改成"标点必然落在边界"的构造，见 §F 备注）
- [x] Python 全量回归无退化（**283 passed**）
- [x] Java 全量回归无退化（48 passed，此前 43）

> **§F 备注（这次变异测试的真实收获）**：第一版 `test_wrap_never_starts_or_ends_line_with_forbidden_punctuation`
> 用任意长句测，标点碰巧不落在行边界，**去掉修复逻辑仍然全绿** —— 测试形同虚设。
> 改成"字号 100 / 宽度 700 = 每行恰好 7 个全角字、第 7 字是逗号"的**构造性**用例后才真正生效。
> 这正是本项目坚持变异测试的原因：绿不等于有效。

## G. 文档

- [x] `docs/08` ADR-019（含 gimpish/OpenCut 的一手证据表）
- [x] `docs/63` task / `docs/64` spec / 本 checklist
- [x] `docs/66` 交付说明（含「残余项 / 未验证」一节）
- [x] README：撤销「无需 Node / 无需数据库」表述；新增封面能力与 gimpish 依赖说明
- [x] `docs/08` ADR-002 加备注标注被 ADR-019 修订（核查后确认 `docs/48` 无冲突表述）

## H. 交付

- [ ] 全量 `git status` 复核后提交
- [ ] 推送到 `origin/main`（https://github.com/Mitsuki-lwx/polyface-agent）

---

## 明确不做（Out of scope，见 `docs/63` §2）

- 桌面壳（M6-2）· 视频剪辑适配器/OpenCut（M6-3）· 素材原图入封面（M6-1b）· TTS（ADR-018 Q3 未决）
