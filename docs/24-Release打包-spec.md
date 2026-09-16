# 规格说明书：M5 Release 打包（干净机器开箱即用）

- 文档类型：spec（事前三件套之一）
- 版本：v1.0 · 日期：2026-09-11
- 配套：`23-Release打包-task.md`、`25-Release打包-checklist.md`
- 需求来源：`05-SRS` NFR-01/NFR-03；验收矩阵：`07` M5 行

---

## 1. 需求规格

### 1.1 目标状态

```
下载/解压 Release 包
   └─ scripts\setup.bat   （一次）创建 Python venv + 安装依赖 + 构建 jar（若环境有 Maven）
        └─ scripts\start.bat  启动 Python + Java，等就绪后自动开浏览器
              └─ 浏览器 http://127.0.0.1:8080 可用
   └─ scripts\stop.bat   干净结束
```

### 1.2 终端用户依赖基线

| 组件 | 必需 | 版本 | 缺失时行为 |
|---|---|---|---|
| JDK | ✅ | 17+ | 前置检查拦截，给出下载指引 |
| Python | ✅ | 3.11+ | 前置检查拦截，给出下载指引 |
| ffmpeg | ⭕ | 任意较新版本 | 仅提示「音视频入料不可用」，**不阻塞启动** |
| Maven | ❌ | — | **完全不要求**（仅 setup 构建 jar 时若存在则用） |
| Node | ❌ | — | 前端为零构建静态 SPA |

### 1.3 非功能约束

| 编号 | 约束 | 落地 |
|---|---|---|
| NFR-01 | 本地一键启动 | start.bat 单入口 |
| NFR-03 | 仓库零密钥 | 打包校验排除 `.env`、`data/` |
| ADR-002 | 本地优先、免构建链 | 终端用户侧零构建（`java -jar`） |

---

## 2. `start.bat` 设计

```
[1] 前置检查
    ├ java 存在？版本 ≥17？
    ├ python 存在？
    └ 8080 / 8000 端口空闲？ → 占用则打印占用 PID + 换端口方法，退出
[2] 产物检查
    ├ python-service\.venv\Scripts\python.exe 存在？ → 否则提示先跑 setup.bat
    └ java-backend\target\*.jar 存在？            → 否则提示先跑 setup.bat
[3] 启动（两个独立窗口，便于分别看日志）
    ├ start "polyface-python" cmd /k "cd /d <root>\python-service && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
    └ start "polyface-java"   cmd /k "java -jar <jar> --server.port=8080"
[4] 就绪探测（轮询 curl http://127.0.0.1:8080/health，最多 30 次 × 1s）
[5] 打开浏览器 → http://127.0.0.1:8080/      （注意：是应用首页，不再是 /health）
```

关键实现点：

- **不用 Maven**：`java -jar` 直接跑 Spring Boot fat jar（`spring-boot-maven-plugin` 已在 pom 中）
- **jar 发现**：`for %%f in ("%ROOT%\java-backend\target\polyface-backend-*.jar") do set JAR=%%f`，取第一个且排除 `*.original`
- **无硬编码绝对路径**：所有路径基于 `%~dp0` 推导
- **中文编码**：`chcp 65001`
- **不静默失败**：每类失败都有 `[错误]`/`[提示]` 前缀 + 处置建议；结尾 `pause` 让用户看到

## 3. `stop.bat` 设计

```
[1] 列出监听 8080 / 8000 的进程（PID + 进程名）
[2] 打印将要结束的进程，要求用户确认（choice /m）
[3] taskkill /F /PID <pid>（逐个，去重）
[4] 复查端口是否释放，输出结果
```

> **不自动杀**：避免误杀用户其他占用同端口的程序；确认制更安全。

## 4. `setup.bat` 设计

```
[1] 前置检查 java / python（同 start.bat）
[2] Python venv
    ├ 不存在则 python -m venv .venv
    └ .venv\Scripts\python.exe -m pip install -r requirements.txt
[3] .env
    └ 不存在则从 .env.example 复制，并提示「填 Key 或保持 LLM_MOCK=true 离线体验」
[4] 构建 jar（Maven 仅此处、且仅当探测到）
    ├ where mvn 成功 → mvn -DskipTests package
    └ 否则 → 打印：
        「未检测到 Maven，跳过 jar 构建。
          若你下载的是 Release 包，jar 已随包提供，可直接 start.bat；
          若是源码 clone，请安装 Maven 后重跑本脚本。」
[5] 汇总结果 + 下一步指引
```

**移除**：所有 `D:\apache-maven-3.9.11\...` 硬编码。

## 5. `build-release.bat` / `.sh` 设计

```
[1] 版本号：从 java-backend/pom.xml 提取 <version>
[2] mvn -DskipTests clean package            → 产出 fat jar
[3] 组装 dist/polyface-<version>/
      ├ java-backend/target/polyface-backend-<version>.jar
      ├ python-service/      （排除 .venv / __pycache__ / .env / *.pyc）
      ├ platform-dna/
      ├ scripts/             （start/stop/setup + build-release）
      ├ README.md  LICENSE  .gitignore
      └ docs/*.md            （方案与交付文档，开源材料）
[4] 统一 `*.bat` 行尾为 CRLF（`_normalize_eol`）—— 见下方「行尾符」
[5] 压缩 → dist/polyface-<version>.zip
[6] 输出产物路径 + 大小，并提示「并附校验清单」
```

**排除校验（关键）**：打包前断言以下内容**不在** zip 中——
`data/`、任何 `.env`、`.venv/`、`node_modules/`、`*.db`、`target/`（除 jar）、`__pycache__/`。

**行尾符（2026-09-16 补）**：组装后必须把 `*.bat` / `*.cmd` 统一成 CRLF。
cmd.exe 解析 LF 行尾的批处理时，`goto :label`、多行 `if (...)` 块、`for /f`
会出错，而这三种结构 `setup/start/stop.bat` 全在用。
**不能只依赖源码仓库的行尾**：`core.autocrlf=true` 时新克隆是 CRLF，
但工具直接写出的文件是 LF，而打包直接拷工作区 —— 实测就漏出过 LF 的包。
配套：`.gitattributes` 钉住 `*.bat → crlf` / `*.sh → lf`；6 项测试守卫
（含幂等、`.sh` 不受影响、无 BOM、含中文必须 `chcp 65001`）。
任一命中则中止打包并报错（防泄漏用户数据与密钥）。

## 6. README 结构调整

```markdown
# Polyface · 同源万面
> 一句话定位 + 徽章区

## 这是什么（3 行）
## 核心能力（含 M3.5/M4/M5 新增：音视频入料、模板管理、示例学习、复盘闭环）
## 平台支持
## 架构（三层图）
## 快速开始
   ### 方式 A：Release 包（推荐，无需 Maven）
      1. 解压 → scripts\setup.bat → scripts\start.bat
   ### 方式 B：源码 clone（开发者）
      需要 JDK + Python + Maven
   ### 可选：启用本地转写（faster-whisper）/ 指定 ffmpeg 路径
## 目录结构
## 配置（.env 说明：Key 可留空走 mock）
## 常见问题（端口占用 / 无 ffmpeg / 无 Maven / 如何更新 jar）
## 路线图（**修正为实际状态**）
## 文档体系
## 合规与边界（不自动发布、不爬数据、本地优先）
## License
```

## 7. 验证方案（"干净机器"如何模拟）

无法真机验证，采用**三重模拟**并在交付说明中明确标注：

| 模拟项 | 做法 | 验证目标 |
|---|---|---|
| 干净目录 | 把 Release zip 解压到空目录 `D:/temp/polyface-reltest/` | 无历史残留依赖 |
| 无 venv / 无 .env / 无 data | 解压后确认三者不存在 | setup 能自举 |
| **无 Maven** | 启动前把 Maven 目录从 `PATH` 移除（`set PATH=<过滤后>`） | `java -jar` 路径成立 |

必测的失败路径：

| 场景 | 期望 |
|---|---|
| `start.bat` 时无 jar | 提示「请先运行 setup.bat」，不静默退出 |
| 模拟 java 不存在 | 前置检查拦截 + 下载指引 |
| 8080 被占用 | 报出占用 PID + 换端口方法 |
| stop.bat 确认前取消 | 不结束任何进程 |

## 8. 兼容与边界

- 保留 `mvn spring-boot:run` 作为**开发模式**（写在 README 开发者一节），但 `start.bat` 不依赖
- `scripts/start.sh` 同步改造（macOS/Linux 用户；本次以 Windows 为主验证）
- 版本号单一来源：`java-backend/pom.xml` 的 `<version>`；Python 侧从 `app/main.py` 的 FastAPI version 对齐
