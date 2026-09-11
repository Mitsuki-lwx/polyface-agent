# 任务书：M5 Release 打包（干净机器开箱即用）

- 文档类型：task（事前三件套之一）
- 版本：v1.0 · 日期：2026-09-11
- 关联：`05-SRS`（NFR-01 本地一键启动 / NFR-03 仓库零密钥）、`07-用例模型与验收标准.md` M5 行、`08-ADR` ADR-002（本地一键启动）、ADR-012（MIT 开源）
- 前置依赖：M1~M5b + M3.5 全部完成 ✅

---

## 1. 背景与问题

M5 的定义是「**Release 包在干净机器可 `start.bat` 开箱即用**」。对照现状，存在四个硬伤：

| # | 问题 | 影响 |
|---|---|---|
| 1 | `scripts/start.bat` 用 `mvn spring-boot:run` 启动 Java，并**硬编码回退路径** `D:\apache-maven-3.9.11\bin\mvn.cmd` | 终端用户被迫安装 Maven；机器上没有该路径时回退也失效 |
| 2 | 首次使用必须先跑 `setup.bat`，但 README「快速开始」未提及 | 新用户直接跑 start.bat 必然失败 |
| 3 | 无 `stop.bat`、无发布打包脚本 | 关不干净（残留进程占端口）、无法产出可分发产物 |
| 4 | README 路线图**严重滞后**：M3 / M3.5 / M4 实际已完成却标 `[ ]` | 对外展示与事实不符（开源材料失分） |

## 2. 目标

**`setup.bat` 跑一次 → `start.bat` → 浏览器自动打开即可用。**

终端用户只需：

| 必需 | 版本 | 说明 |
|---|---|---|
| JDK | 17+ | **不再需要 Maven** |
| Python | 3.11+ | venv 由 setup 创建 |
| ffmpeg | 可选 | 仅 FR-51 音视频入料需要；缺失不阻塞启动 |
| 浏览器 | 任意现代浏览器 | |

## 3. 范围

### In（本任务做）

| # | 事项 |
|---|---|
| 1 | `start.bat` 改为**优先 `java -jar <fat jar>`**；无 jar 时给出「先跑 setup」的明确指引（而非报错退出） |
| 2 | 清除所有**硬编码绝对路径**（Maven、JDK 等） |
| 3 | **前置检查**：java / python 是否存在与版本；端口 8080/8000 是否被占用 → 明确报错与处置建议 |
| 4 | 新增 `stop.bat`（按端口精确结束本应用进程，不误杀） |
| 5 | `setup.bat` 改造：Python venv + 依赖；**Maven 改为「探测到才构建」**，不硬编码 |
| 6 | 新增 `build-release.bat` / `.sh`：构建 fat jar + 打包 zip（jar + python-service + platform-dna + scripts + README + LICENSE） |
| 7 | README 更新：快速开始（setup → start）、能力清单、路线图、ffmpeg 说明、FAQ |
| 8 | 版本号统一（pom / python / README 一致） |
| 9 | 隔离环境模拟验证 + 交付说明 |

### Out（本任务不做，留后续）

| 事项 | 原因 |
|---|---|
| Docker / 安装器（exe/msi）/ 自动更新 | 与"本地轻量工具"定位不符，收益低 |
| 代码签名、公证 | 无发布渠道需求 |
| CI/CD 自动化流水线 | 单机项目，手工打包足够 |
| 把 Python 也打成单文件 exe | PyInstaller 与 FastAPI/uvicorn 组合易踩坑，留待有明确需求 |

## 4. 任务拆分

| 序号 | 任务 | 产出 | 预估 |
|---|---|---|---|
| T1 | `start.bat` + `stop.bat` 改造（jar 优先、前置检查、端口检查） | scripts/ | 中 |
| T2 | `setup.bat` 改造（去硬编码、mvn 探测） | scripts/ | 小 |
| T3 | `build-release.bat` / `.sh`（构建 + 打包 zip） | scripts/ | 中 |
| T4 | README 重写快速开始 + 路线图/能力更新 | README.md | 中 |
| T5 | 隔离环境模拟验证（解压到空目录、清 venv/.env、最小 PATH） | 验证记录 | 中 |
| T6 | 交付说明 + SRS/用例矩阵 M5 行更新 | `docs/26-Release打包-交付说明.md` | 小 |

## 5. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| **无法真机验证"干净机器"** | 验证不充分 | 用「隔离目录解压 + 删除 venv/.env/data + 最小 PATH」模拟；**在交付说明中明确标注哪些环节是模拟、哪些未真机验证** |
| 用户未装 JDK / Python | 启动失败且不知原因 | 前置检查 + 明确的下载指引（含版本要求） |
| jar 与源码不同步 | 用户改了源码但跑的是旧 jar | `start.bat` 在检测到 jar 比源码旧时给出提示；README 说明更新方式 |
| 端口被其他程序占用 | 启动静默失败 | 启动前检测；提示占用 PID 与换端口方法（**不自动杀进程**） |
| 无 ffmpeg | 音视频入料不可用 | 启动时仅提示不阻塞；README 标注为可选 |
| 打包脚本误把 data/ .env 打进去 | 泄漏用户数据/密钥 | 打包前校验排除 `data/`、`.env`、`.venv`、`node_modules`、`target/*.jar` 之外的内容 |

## 6. 完成定义（DoD）

- [ ] 三件套文档齐备且已确认
- [ ] 隔离目录模拟验证：`setup.bat` → `start.bat` → 页面可用
- [ ] **无 Maven 也能启动**（`java -jar` 路径实测通过）
- [ ] 报错路径验证：缺 java / 缺 python / 端口占用 / 无 jar，各有清晰提示
- [ ] `build-release` 产出的 zip 解压后可直接 setup + start
- [ ] zip 内**不含** data/、.env、.venv
- [ ] README 与实际行为一致；路线图修正
- [ ] 交付说明产出并提交

## 7. 待确认项

| # | 问题 | 建议默认 |
|---|---|---|
| Q1 | Release 形态？ | **源码包为主**（GitHub Release 附 zip：含预构建 jar + 源码 + DNA + scripts）；用户也可自行 build |
| Q2 | 是否保留 `mvn spring-boot:run`？ | **保留**，但仅写在 README「开发者」一节；`start.bat` 不依赖它 |
| Q3 | 端口冲突如何处理？ | **检测 + 提示**（给出占用 PID 与 `--server.port` 换端口方法），**不自动杀进程** |
| Q4 | 是否一并修正 README 滞后的路线图？ | **是**（开源材料准确性） |
