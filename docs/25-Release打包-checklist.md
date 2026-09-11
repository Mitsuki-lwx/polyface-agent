# 验收清单（checklist）：M5 Release 打包（干净机器开箱即用）

- 文档类型：checklist（事前三件套之三）
- 版本：v1.0 · 日期：2026-09-11
- 配套：`23-Release打包-task.md`、`24-Release打包-spec.md`
- 对应验收矩阵：`07` M5 行
- 判定方式：每条独立判定，全部 `[x]` 方可交付

---

## A. `start.bat`

- [ ] A1 启动前检查 java 是否存在
- [ ] A2 启动前检查 java 版本 ≥ 17，不满足则明确报错
- [ ] A3 启动前检查 python 是否存在
- [ ] A4 启动前检查端口 8080 / 8000 是否空闲
- [ ] A5 端口被占用时报出占用 PID 与换端口方法，且**不自动杀进程**
- [ ] A6 无 `python-service\.venv` 时提示「请先运行 setup.bat」，不静默退出
- [ ] A7 无 fat jar 时提示「请先运行 setup.bat」，不静默退出
- [ ] A8 用 **`java -jar`** 启动 Java（**不依赖 Maven**）
- [ ] A9 用 venv 内 python 启动 uvicorn
- [ ] A10 轮询 `/health` 直到就绪或超时（不固定 sleep 死等）
- [ ] A11 就绪后打开浏览器到**应用首页 `/`**（不是 /health）
- [ ] A12 脚本内**无任何硬编码绝对路径**（全部基于 `%~dp0` 推导）
- [ ] A13 中文不乱码（`chcp 65001`）
- [ ] A14 失败时结尾 `pause`，用户能看到错误信息

## B. `stop.bat`

- [ ] B1 列出监听 8080/8000 的进程（PID + 名称）
- [ ] B2 结束前要求用户确认（choice）
- [ ] B3 用户取消时不结束任何进程
- [ ] B4 确认后按 PID 精确结束，并输出结果
- [ ] B5 结束后复查端口是否释放

## C. `setup.bat`

- [ ] C1 前置检查 java / python
- [ ] C2 不存在 venv 时自动创建
- [ ] C3 安装 `python-service/requirements.txt` 依赖
- [ ] C4 `.env` 不存在时从 `.env.example` 复制并提示
- [ ] C5 **探测 Maven**（`where mvn`），存在才构建 jar
- [ ] C6 无 Maven 时给出正确指引（Release 包已有 jar / 源码需装 Maven）
- [ ] C7 **无硬编码 Maven 路径**

## D. `build-release`

- [ ] D1 从 pom.xml 提取版本号
- [ ] D2 构建出 Spring Boot fat jar
- [ ] D3 组装目录含：jar、python-service、platform-dna、scripts、README、LICENSE、docs
- [ ] D4 排除 `.venv` / `__pycache__` / `.env` / `*.pyc`
- [ ] D5 排除 `data/` / `*.db`
- [ ] D6 打包前**断言**敏感内容未被打入（命中则中止）
- [ ] D7 产出 zip 并输出路径与大小
- [ ] D8 有 `.sh` 版本（macOS/Linux）

## E. README

- [ ] E1 「快速开始」区分 Release 包 / 源码 clone 两条路径
- [ ] E2 明确「先跑 setup.bat」的前置步骤
- [ ] E3 明确终端用户**不需要 Maven**
- [ ] E4 能力清单含 M3.5/M4/M5b 新增能力（音视频入料、模板管理、示例学习、复盘闭环）
- [ ] E5 说明 ffmpeg 为可选依赖及其影响范围
- [ ] E6 说明「Key 可留空走 mock 离线体验」
- [ ] E7 FAQ 含端口占用 / 无 ffmpeg / 无 Maven / 如何更新 jar
- [ ] E8 **路线图修正为实际状态**（M3/M3.5/M4/M5a/M5b 已完成）
- [ ] E9 合规边界章节（不自动发布、不爬数据、本地优先）
- [ ] E10 可选增强说明（faster-whisper / POLYFACE_FFMPEG / POLYFACE_ASR_MODEL）

## F. 干净环境模拟验证

- [ ] F1 解压 Release zip 到空目录
- [ ] F2 确认解压后无 `.venv` / `.env` / `data/`
- [ ] F3 在**移除 Maven 的 PATH** 下跑 `setup.bat` 成功
- [ ] F4 在**移除 Maven 的 PATH** 下跑 `start.bat` 成功启动
- [ ] F5 `curl :8080/health` 与 `:8000/health` 均就绪
- [ ] F6 浏览器打开首页可正常使用（素材解析 → 生成 至少一条链路）
- [ ] F7 `stop.bat` 能干净结束，端口全部释放
- [ ] F8 二次 `start.bat` 仍可用（幂等，无残留状态问题）

## G. 失败路径验证

- [ ] G1 无 jar 时启动 → 明确提示先跑 setup
- [ ] G2 无 venv 时启动 → 明确提示先跑 setup
- [ ] G3 模拟 java 不存在 → 前置检查拦截 + 下载指引
- [ ] G4 模拟 python 不存在 → 前置检查拦截 + 下载指引
- [ ] G5 8080 被占用 → 报 PID + 换端口方法
- [ ] G6 无 ffmpeg → 启动不受影响（仅提示音视频入料不可用）

## H. 文档与交付

- [ ] H1 `07` M5 行更新为 ✅
- [ ] H2 `05-SRS` NFR-01 状态更新（如适用）
- [ ] H3 `docs/26-Release打包-交付说明.md` 产出（含**模拟验证 vs 未真机验证**的明确区分）
- [ ] H4 交付说明含「快速开始」验证记录与产物清单
- [ ] H5 代码与脚本已提交，工作区干净

---

## 验收结论

| 项 | 结果 | 备注 |
|---|---|---|
| A~H 全部勾选 | ⬜ 待执行 | |
| 启动链路验证 | ⬜ 待执行 | |
| 失败路径验证 | ⬜ 待执行 | |
| 未通过项 | — | 若有，须逐条说明原因；**未真机验证项须显式标注** |
