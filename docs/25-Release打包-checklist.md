# 验收清单（checklist）：M5 Release 打包（干净机器开箱即用）

- 文档类型：checklist（事前三件套之三）
- 版本：v1.0 · 日期：2026-09-11 · **执行日期：2026-09-16**
- 配套：`23-Release打包-task.md`（含 §9 v1.1）、`24-Release打包-spec.md`
- 对应验收矩阵：`07` M5 行
- 判定方式：每条独立判定，全部 `[x]` 方可交付

> **判定口径**：`[x]` = 已实测；`[~]` = 逻辑已实现但**未实机执行**（须在交付说明中显式标注）；`[ ]` = 未做。
> 本次执行的完整证据见 `48-Release打包-交付说明.md`。

---

## A. `start.bat`

- [x] A1 启动前检查 java 是否存在 —— `doctor.py` 的 `check_java()`
- [x] A2 启动前检查 java 版本 ≥ 17，不满足则明确报错 —— 实测隔离环境报 `21（OK）`
- [x] A3 启动前检查 python 是否存在 —— `start.bat` 自己找（doctor 需要 Python 才能跑）
- [x] A4 启动前检查端口 8080 / 8000 是否空闲 —— 实测占用时报阻塞
- [x] A5 端口被占用时报出占用 PID 与换端口方法，且**不自动杀进程** —— 实测报出 `PID 38088 / 39824`
- [x] A6 无 `python-service\.venv` 时提示「请先运行 setup.bat」，不静默退出 —— 实测命中
- [x] A7 无 fat jar 时提示「请先运行 setup.bat」，不静默退出 —— `check_jar()` 命中
- [x] A8 用 **`java -jar`** 启动 Java（**不依赖 Maven**） —— 实测在隔离环境启动成功
- [x] A9 用 venv 内 python 启动 uvicorn —— 实测启动成功
- [~] A10 轮询直到就绪（不固定 sleep 死等） —— **实现为轮询 TCP 端口**而非 `/health`。
      原因：Java 的 `/health` 在 Python 不可达时仍返回 200，只看它会误判就绪（脚本内有注释说明）。
      轮询逻辑本身未在 `.bat` 内实跑；同逻辑的 Python 版已实测
- [x] A11 就绪后打开浏览器到**应用首页 `/`**（不是 /health） —— `start "" "http://127.0.0.1:8080"`
- [x] A12 脚本内**无任何硬编码绝对路径**（全部基于 `%~dp0` 推导） —— 已复核
- [x] A13 中文不乱码（`chcp 65001`） —— 已设置
- [x] A14 失败时结尾 `pause`，用户能看到错误信息 —— 已复核

## B. `stop.bat`

- [x] B1 列出监听 8080/8000 的进程（PID + 名称） —— 已复核 `netstat`+`tasklist` 取词；`tasklist` 输出格式实测为 `"java.exe","38088",...`，与解析一致
- [x] B2 结束前要求用户确认（choice） —— 本次补上：`choice /C YN /T 60 /D N`
- [x] B3 用户取消时不结束任何进程 —— `if errorlevel 2` 直接 `exit /b 0`
- [x] B4 确认后按 PID 精确结束，并输出结果 —— 只结束 `java/javaw/python/pythonw`；其它程序跳过
- [x] B5 结束后复查端口是否释放 —— 本次补上 `:verify_port` 子过程

## C. `setup.bat`

- [x] C1 前置检查 java / python —— Python 在前（脚本自身依赖），Java 由收尾体检覆盖
- [x] C2 不存在 venv 时自动创建 —— 已复核
- [x] C3 安装 `python-service/requirements.txt` 依赖 —— 等价操作在隔离环境实测成功
- [x] C4 `.env` 不存在时从 `.env.example` 复制并提示 —— 已复核
- [x] C5 **探测 Maven**（`where mvn`），存在才构建 jar —— 已复核
- [x] C6 无 Maven 时给出正确指引（Release 包已有 jar / 源码需装 Maven） —— 已复核
- [x] C7 **无硬编码 Maven 路径** —— 已复核（`.bat` 内无 `D:\apache-maven-*`）
- [x] C8 *(新增)* venv 建好后校验 pip 是否存在，缺失则 `ensurepip` 修复 —— 实测发现 `python -m venv` 可能建出无 pip 的 venv，见交付说明 §5-3

## D. `build-release`

- [~] D1 版本号来源 —— **改为 `VERSION` 文件为唯一来源**（不解析 pom），并新增
      `assert_versions_consistent()` 断言 pom.xml 与之一致，不一致直接失败。
      比"从 pom 提取"更能防止两处漂移
- [x] D2 构建出 Spring Boot fat jar —— 实测 34.2 MB
- [x] D3 组装目录含：jar、python-service、platform-dna、scripts、README、LICENSE、docs —— 实测 64+ 条目，7 个顶层项
- [x] D4 排除 `.venv` / `__pycache__` / `.env` / `*.pyc` —— 实测包内无
- [x] D5 排除 `data/` / `*.db` —— 实测包内无
- [x] D6 打包前**断言**敏感内容未被打入（命中则中止） —— `find_forbidden()`，前后各校验一次
- [x] D7 产出 zip 并输出路径与大小 —— `dist/polyface-0.4.0.zip`（32.3 MB）
- [x] D8 *(新增)* `.env*` 家族除 `.env.example` 外一律拦截 —— 实测发现只列 `.env` 精确名会漏掉 `.env.local`，见交付说明 §5-2
- [ ] D9 有 `.sh` 版本（macOS/Linux） —— **未完成**。仅有既有的 `scripts/start.sh` / `mvn.sh`；
      `setup.sh` / `stop.sh` 未提供。跨平台验证亦未做（见交付说明 §6）

## E. README

- [x] E1 「快速开始」区分 Release 包 / 源码 clone 两条路径 —— 方式 A / 方式 B
- [x] E2 明确「先跑 setup.bat」的前置步骤 —— 两条路径都写了
- [x] E3 明确终端用户**不需要 Maven** —— 「发布包自带预构建 jar，不需要装 Maven」
- [x] E4 能力清单含 M3.5/M4 新增能力（音视频入料、模板管理、示例学习、复盘闭环） —— 已列
- [x] E5 说明 ffmpeg 为可选依赖及其影响范围 —— FAQ 有专条（含 ASR 降级行为）
- [x] E6 说明「Key 可留空走 mock 离线体验」 —— 独立小节 + 数据边界
- [x] E7 FAQ 含端口占用 / 无 ffmpeg / 无 Maven / 如何更新 —— 8 条 FAQ
- [x] E8 **路线图修正为实际状态** —— M3/M3.5/M4 由 `[ ]` 改为 `[x]`；
      **M5+ 自动成片明确标注「尚未实现」**（原文未区分，易被读成已有能力）
- [x] E9 合规边界章节（不自动发布、不爬数据、本地优先） —— 保留并强化
- [x] E10 可选增强说明（faster-whisper / POLYFACE_FFMPEG / POLYFACE_ASR_MODEL） —— 配置速查表 + FAQ

## F. 干净环境模拟验证

- [x] F1 解压 Release zip 到空目录 —— 实测解压到 `D:\最终 验证\polyface-0.4.0`（**中文 + 空格**路径）
- [x] F2 确认解压后无 `.venv` / `.env` / `data/` —— 实测无
- [~] F3 在**移除 Maven 的 PATH** 下跑 `setup.bat` 成功 —— `.bat` 未实机执行；
      但**等价操作**（建 venv + 装依赖）已在隔离环境实测成功，且运行时确实不需要 Maven
- [~] F4 在**移除 Maven 的 PATH** 下跑 `start.bat` 成功启动 —— `.bat` 未实机执行；
      但 `java -jar polyface.jar` 手动启动实测成功
- [x] F5 `:8080` 与 `:8000` 均就绪 —— 实测 4.5s / 0.0s
- [~] F6 浏览器打开首页可正常使用（素材解析 → 生成 至少一条链路） —— **未做浏览器验证**（本环境不支持）。
      改用 HTTP 直连验证等价链路：建素材 → 2 平台生成 → 均 `qa_passed`，抖音有剪辑单、小红书无（DNA 差异生效）
- [~] F7 `stop.bat` 能干净结束，端口全部释放 —— `.bat` 未实机执行；
      按其逻辑用 `taskkill /PID` 手动结束，端口复查为「空闲」
- [x] F8 二次启动仍可用（幂等） —— 实测结束进程后重启，material/draft 数据仍在

## G. 失败路径验证

- [x] G1 无 jar 时启动 → 明确提示先跑 setup —— `check_jar()` 命中
- [x] G2 无 venv 时启动 → 明确提示先跑 setup —— 实测命中
- [x] G3 模拟 java 不存在 → 前置检查拦截 + 下载指引 —— `check_java()` 分支 + 单测
- [x] G4 模拟 python 不存在 → 前置检查拦截 + 下载指引 —— `start.bat` 分支
- [x] G5 8080 被占用 → 报 PID + 换端口方法 —— 实测命中
- [x] G6 无 ffmpeg → 启动不受影响（仅提示音视频入料不可用） —— ffmpeg 只在 `/probe`、`/transcribe` 时探测
- [x] G7 *(新增)* venv 内无 pip → 报「没有 pip」并给 `ensurepip` 命令 —— 实测发现，见交付说明 §5-4
- [x] G8 *(新增)* VERSION 与 pom.xml 不一致 → 打包直接失败 —— 单测覆盖

## H. 文档与交付

- [x] H1 `07` M5 行更新为 ✅ —— 见交付说明；README 路线图同步
- [x] H2 `05-SRS` NFR 状态更新 —— 数据边界表与 NFR-01/02/04 已在上一轮修正（ADR-017）
- [x] H3 交付说明产出（含**模拟验证 vs 未真机验证**的明确区分） —— `docs/48`，其中 §6 专门列残余项
- [x] H4 交付说明含「快速开始」验证记录与产物清单 —— `docs/48` §3、§4
- [x] H5 代码与脚本已提交，工作区干净 —— 见提交记录

---

## 验收结论

| 项 | 结果 | 备注 |
|---|---|---|
| A~H 全部勾选 | ✅ 除 D9 外全部完成 | D9（`.sh` 脚本）明确未做 |
| 启动链路验证 | ✅ 通过 | 隔离环境（中文+空格路径）端到端跑通 |
| 失败路径验证 | ✅ 通过 | G1~G8 均命中预期分支 |
| 单元测试（2026-09-17 复核） | ✅ **Python 199 / Java 43 全绿** | 含 09-17 FR-60 加固新增 19 项。初版 `docs/48` 写的「165 通过」不可复现（当时实为 1 项**必然失败**的测试，已修，详见 `docs/48` §2.1） |
| 发布包内脚本行尾符（2026-09-16 复核） | ✅ `.bat` = CRLF，`.sh` = LF | 复核时发现包内 3 个 `.bat` **全为 LF**（批处理解释器解析 `goto`/多行 `if`/`for /f` 会出错）。已三层修复并加测试守卫，见 `docs/48` §5 第 8 项 |
| **真实 LLM 模式**（2026-09-17 复核） | ✅ **已补验证：真实链路 E2E 14/14** | `glm-5.2`；素材理解 20.7s / 生成 95.4s / QA passed；见 `docs/54`。⚠️ **未在发布包内**跑，且只覆盖小红书 1 平台 |
| **仍未真机验证项** | ⚠️ **已显式标注** | ① `scripts/*.bat` 本身未执行（本环境两个执行工具都硬拦批处理解释器，**不可能**代跑）② 浏览器 UI 回归 ③ 音视频入料 ④ 跨平台。详见 `docs/48` §6 |

**判定**：可交付，但 §6 的残余项须随包一并告知使用者 —— 尤其是"批处理脚本未经实机运行"这一条，不能因为 `docs/25` 打了勾就当它验证过了。
