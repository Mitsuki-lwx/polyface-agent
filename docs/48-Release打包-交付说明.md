# Release 打包 交付说明（M5）

日期：2026-09-16 · 状态：✅ 完成（含 1 项**未实机验证**的显式声明，见 §6）

对应任务书：`docs/23-Release打包-task.md`（含 §9 v1.1 修订）· 规格：`docs/24` · 验收：`docs/25`

## 1. 本次目标

把"能跑通"变成"**别人也能装上跑通**"。具体三件事：

1. **发布包**：解压 → 双击 → 能用。用户侧不需要 Maven、不需要手动装依赖。
2. **出问题能自诊断**：环境不对时给出"缺什么 + 怎么装"，而不是白屏或一句 `ClassNotFoundException`。
3. **不把用户数据/密钥打进去**：打包脚本必须能自动拦住这类事故。

## 2. 验收结果（实测证据）

| 项 | 结果 | 证据 |
|---|---|---|
| Python 测试 | ✅ **174 通过**（任务开始前 76） | `pytest -q`，2026-09-16 复核；见 §2.1 |
| Java 测试 | ✅ **43 通过** | `mvn test`，`java.version=17` 下编译通过 |
| 发布包产出 | ✅ `dist/polyface-0.4.0.zip`（32.3 MB） | `build_release.py --no-build` |
| 包内容校验 | ✅ 无违禁内容 | `build_release.py --check-only` |
| **隔离环境（中文+空格路径）** | ✅ 全流程跑通 | 见 §3 |
| 数据落盘位置 | ✅ 落在包内，包外无残留 | `data/polyface.db` 在包根；`D:\最终 验证\data` 不存在 |
| 跨域收紧（生产验证） | ✅ 带恶意 Origin 请求**无** `Access-Control-Allow-Origin` | `curl -H "Origin: https://evil.example.com"` → 200 但无放行头 |
| 端口冲突自检 | ✅ 报出占用 PID 并指向 `stop.bat` | `doctor.py` 输出 |
| 重启后数据保留 | ✅ material 1 行 / draft 2 行 | 结束进程 → 重启 → 直接查 SQLite |

### 2.1 数字订正说明（2026-09-16 复核）

初版本节写的是「Python 165 通过」。**该数字不可复现**，两次订正：

| | 初版声明 | 复核实测 |
|---|---|---|
| 总数 | 165 | **174** |
| 结果 | 全通过 | 初版当时实为 **170 passed / 1 failed** |

**为什么必须记下来**：`tests/test_build_release.py::test_repo_scripts_dir_is_clean`
是一个**必然失败**的测试 —— 它用 `importlib` 加载 `scripts/build_release.py`，
而「加载」这个动作本身就会写出 `scripts/__pycache__/*.pyc`，随后该测试又断言
`scripts/` 下不得存在 `__pycache__`/`.pyc`。**从干净检出跑也必红。**

它还有第二层错误：注释称这些文件"会被原样打进包"，但 `build_release.py:128` 的
`shutil.ignore_patterns(*FORBIDDEN_DIRS, *FORBIDDEN_FILES, "*.pyc", "*.pyo", "*.log")`
已经把它们排除，`_prune()` 再兜底删除 —— **发布包实际上是干净的**
（`--check-only` 实测 `[OK] 未发现不应分发的内容` 佐证）。

> 这正是本项目一直在防的「质检误报通过」的**镜像：误报失败**。
> 一个常亮的红灯会训练所有人忽略红灯，比没有测试更糟。

已修：把仓库自检的判定改为**镜像"实际会被打包的内容"**（容忍字节码缓存，
其余零容忍，特别是 `.env` 家族），并补 3 项定向测试（正向抓密钥 / 反向容忍字节码 /
边界仍抓 `.venv`）。变异测试验证：植入 `scripts/.env.local` → 该测试**确实变红**。

发布包判据不变：`find_forbidden()` 对**发布包**仍然零容忍（含字节码）。

## 3. 隔离环境验证（本次最关键的一步）

在**不是仓库目录**的位置做端到端验证，避免"在我机器上能跑"的假阳性：

```
D:\最终 验证\polyface-0.4.0\      ← 中文 + 空格路径（最容易翻车的路径形态）
```

步骤与结果：

| 步骤 | 结果 |
|---|---|
| 解压 zip | ✅ 单层顶层目录，无文件散落 |
| `python scripts\doctor.py`（未建 venv） | ✅ 报 1 项阻塞、退出码 1，提示指向 `setup.bat` |
| 建 venv + 装依赖 | ✅ fastapi/uvicorn/openai/pydantic-settings/pyyaml 就绪 |
| `doctor.py`（就绪后） | ✅ 8 项全绿、退出码 0 |
| 启动 Python :8000 + Java :8080 | ✅ 分别 0.0s / 4.5s 就绪 |
| `GET /health` | ✅ `version: 0.4.0`、`mock: true`、降级链正确 |
| `GET /api/platforms` | ✅ 5 平台（DNA 在发布包布局下定位正常） |
| 建素材 → 生成（mock） | ✅ `status: qa_passed`、`ok_count: 1`、`trace_id` 正常 |
| 数据目录位置 | ✅ `D:\最终 验证\polyface-0.4.0\data\polyface.db` |
| **包外是否被写数据** | ✅ `D:\最终 验证\data` 不存在（`POLYFACE_DATA_DIR` 生效） |
| 带恶意 Origin 请求 :8000 | ✅ 200 但无 `Access-Control-Allow-Origin` |
| 结束进程 → 重启 → 查库 | ✅ material/draft 数据仍在 |

> 路径含中文与空格这一点是**故意**选的：早期脚本里若有未加引号的 `%ROOT%` 拼接，或 Python 侧用 `parents[N]` 硬推目录，都会在这里暴露。本次未出现。

## 4. 新增 / 修改文件

**新增**

| 文件 | 作用 |
|---|---|
| `scripts/doctor.py` | 启动前体检（纯标准库，可在 venv 建好前运行）。Java/Python 版本、venv+pip+依赖、jar、端口占用、数据目录、LLM 模式 |
| `scripts/build_release.py` | 组装 + **违禁内容校验** + 压缩。校验不过直接失败，不产出可疑 zip |
| `scripts/stop.bat` | 按端口精确结束本应用进程；非本应用进程只提示不动手 |
| `VERSION` | 版本号**唯一来源** |
| `python-service/tests/test_doctor.py` | 33 项 |
| `python-service/tests/test_cors.py` | 20 项 |
| `python-service/tests/test_version.py` | 9 项 |
| `python-service/tests/test_build_release.py` | 27 项 |

**修改**

| 文件 | 改动 |
|---|---|
| `scripts/setup.bat` | 重写：无机器相关硬编码路径；venv 建好后**校验 pip 是否存在**，缺失则 `ensurepip` 修复 |
| `scripts/start.bat` | 重写：`java -jar`（运行时不需要 Maven）；显式设 `POLYFACE_DATA_DIR`；分别轮询 8000/8080 就绪后才开浏览器 |
| `python-service/app/config.py` | 新增 `cors_origins`（默认空 = 不启用跨域）、`parse_cors_origins()`、`service_version()` / `read_version_file()` |
| `python-service/app/main.py` | CORS 改为**默认不注册**，抽出具名函数 `install_cors()` 以便单测；版本号改从 `VERSION` 读取；`/health` 增加 `version` |
| `python-service/.env.example` | 从 8 行补全为完整注释模板（CORS / 超时 / 并行 / 降级链 / ffmpeg / ASR / Langfuse），并标明变量名前缀差异 |
| `java-backend/pom.xml` | 版本 `0.1.0`→`0.4.0`；`java.version` 21→**17**（实测无 21 专属 API）；`<finalName>polyface</finalName>` |
| `README.md` | 重写快速开始（发布包 / 源码两条路径）、配置速查、FAQ；**修正路线图**（M3/M3.5/M4 早已完成却标着未做） |
| `.gitignore` | 增加 `outputs/browser-shots/`（二进制证据不入库） |

## 5. 本次实测中发现并修复的问题

都是**真跑出来的**，不是设想：

| # | 问题 | 影响 | 修法 |
|---|---|---|---|
| 1 | CORS 变量名文档写成 `POLYFACE_CORS_ORIGINS` | `Settings` 无 `env_prefix`，真实变量名是 `CORS_ORIGINS`。用户照文档设置会**静默失效** | 修正注释与 `README`，并加测试**双向锁定**（正确名生效 + 带前缀名不生效） |
| 2 | `.env.local` / `.env.production` 会被打进发布包 | `FORBIDDEN_FILES` 只列 `.env` 精确名。用户把真实 Key 写在 `.env.local` 很常见，**会随包泄露** | 改为 `.env*` 前缀拦截，白名单放行 `.env.example`。变异测试确认 5 项会红 |
| 3 | `setup.bat` 在 venv 缺 pip 时提示"网络不通/公司网络拦截" | 实测撞到：`python -m venv` 建出的 venv 没有 pip，报错是 `No module named pip`，与网络无关。误导用户去查网络 | venv 建好后先校验 pip，缺则 `ensurepip --upgrade` 修复；仍失败才报错并给出精确命令 |
| 4 | `doctor.py` 把"无 pip"报成"缺依赖" | 提示"运行 setup.bat 装依赖" → 但没有 pip 根本装不了，**死循环** | 先查 pip 再查依赖，两种状态给不同提示。加 4 项测试 |
| 5 | `build_release.py` 用 `shutil.which("mvn")` | Windows + Git Bash 下命中的是 POSIX shell 脚本，报 `ClassNotFoundException: ...Launcher` | 新增 `maven_cmd()`：Windows 优先用仓库自带的 `scripts/mvn.sh`；并支持 `--mvn` 覆盖 |
| 6 | 版本号不一致 | Python 服务显示 `0.3.0`、Java 是 `0.4.0` | `VERSION` 作唯一来源，Python 运行时读取；加测试断言 `/health`、FastAPI `app.version` 都与文件一致 |
| 7 | README 路线图与事实不符 | M3 / M3.5 / M4 均有交付说明却标着 `[ ]`，而 M5+ 自动成片未实现却未标明 | 按 `docs/03/04/09/10/22` 实际状态逐项订正；明确标注 B1/B2 **尚未实现** |

## 6. 残余项（**未验证**部分，请勿当成已验收）

这一节按项目既有惯例如实记录 —— "文档里写了"不等于"验证过"。

| 项 | 状态 | 说明 |
|---|---|---|
| `scripts/*.bat` 本身 | ⚠️ **未实机执行** | 本次验证环境无法执行 `.bat`（调用 `cmd.exe` 被安全策略拦截）。脚本的**编排逻辑**已逐行复核，其调用的 `doctor.py` / `build_release.py` 已单测覆盖，但**批处理语法本身没有跑过**。首次在真实 Windows 上使用请留意 |
| `setup.bat` 的 Maven 构建分支 | ⚠️ 未走通 | 该分支只在"没有预构建 jar"时触发；发布包自带 jar，故未触发 |
| `start.bat` 的浏览器自动打开 | ⚠️ 未验证 | 依赖 `start ""` 打开默认浏览器 |
| `start.bat` / `stop.bat` 的 `curl` 与 `tasklist` 解析 | ⚠️ 部分未验证 | `netstat`/`tasklist` 的输出格式已实测确认与脚本解析一致；但脚本内的 `for /f` 取词未实跑 |
| 真实 LLM 模式 | ⚠️ 未验证 | 隔离验证全程 `LLM_MOCK=true`。真实模式的耗时（101~250s/平台）与限流表现引自 `docs/46` 的既有实测 |
| 音视频入料（ffmpeg / ASR） | ⚠️ 未在发布包内验证 | 属 `docs/19-22` 的既有交付范围，本次未回归 |
| 浏览器端 UI 验证 | ⚠️ 未重做 | 本次改动未触及 `index.html`；前端回归证据见 `docs/46`（15/15，mock 模式） |
| 跨平台 | ⚠️ 仅 Windows | `doctor.py` / `build_release.py` 写了 POSIX 分支（`lsof`、`bin/python`）但未在 Linux/macOS 实测 |

## 7. 复现方式

```bash
# 测试
cd python-service && .venv/Scripts/python -m pytest -q        # 174 passed
bash scripts/mvn.sh -B -f java-backend/pom.xml test           # 43 passed

# 打包（含校验）
python scripts/build_release.py                               # 完整构建
python scripts/build_release.py --no-build                    # 用现有 jar
python scripts/build_release.py --check-only dist/polyface-0.4.0   # 只校验

# 体检
python scripts/doctor.py
```

## 8. 下一步建议

1. **在真实 Windows 机器上实跑一遍 `setup.bat` → `start.bat` → `stop.bat`**，补上 §6 第一行那个缺口。这是当前交付里最该补的一块。
2. 真实 LLM 模式下完整走一遍发布包（含 Key 配置、429 重试、部分失败重试）。
3. `application.yml` 的 `polyface.data-dir` 默认值是 `../data`（相对启动目录），手动 `java -jar` 会把数据写到包外。目前靠 `start.bat` 显式覆盖规避；若要彻底解决，应改为相对 **jar 所在目录**解析（需同时评估对开发布局的影响，避免动了默认值后开发环境数据"搬家"）。
4. 跨平台验证（Linux / macOS）。
