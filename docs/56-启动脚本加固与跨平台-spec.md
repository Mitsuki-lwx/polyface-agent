# 启动脚本加固与跨平台补齐 — spec

日期：2026-09-19 · 配套：`docs/55` task · `docs/57` checklist

---

## 1. 交付物

| 文件 | 类型 | 说明 |
|---|---|---|
| `scripts/check_scripts.py` | 新增 | 脚本静态校验器（纯标准库） |
| `python-service/tests/test_check_scripts.py` | 新增 | 校验器自身的测试（含变异） |
| `scripts/setup.sh` | 新增 | 对齐 `setup.bat` |
| `scripts/stop.sh` | 新增 | 对齐 `stop.bat` |
| `scripts/start.sh` | **重写** | 对齐 `start.bat` |

## 2. 校验器设计

### 2.1 定位

`check_scripts.py` 回答一个问题：**"这些脚本有没有机器能判定的低级错误？"**

它**不是**批处理解释器，**不能**替代实机执行。它只覆盖那些"人眼容易漏、机器判定确定"的规则。
输出分两级：

- **ERROR**：确定是错的（如 `goto` 指向不存在的标签）→ 退出码 1
- **WARN**：可疑但可能是有意的（如出现 `del`）→ 不影响退出码

### 2.2 规则清单

#### 通用（`.bat` 与 `.sh` 都查）

| ID | 规则 | 级别 | 为什么 |
|---|---|---|---|
| G1 | 文件非空 | ERROR | — |
| G2 | 末尾有换行 | WARN | 缺尾换行会让某些工具拼接到下一行 |
| G3 | 不含制表符混排的续行歧义（`^` 结尾在 .bat / `\` 结尾在 .sh 后不得有尾随空白） | ERROR | 尾随空白会让续行符失效 —— 典型"看不出来"的错误 |
| G4 | 不出现硬编码 Windows 盘符绝对路径（`[A-Za-z]:\\`） | ERROR | 违反 `docs/23` §"无机器相关硬编码路径" |
| G5 | 不出现危险破坏性命令（`del /s`、`rd /s`、`format`、`rm -rf /`） | WARN | 启动脚本不该需要它们 |
| G6 | 引号配对：每行双引号数为偶数 | ERROR | 未闭合引号会吞掉后续内容 |

#### `.bat` 专属

| ID | 规则 | 级别 | 为什么 |
|---|---|---|---|
| B1 | 行尾符**必须 CRLF**（不得有裸 LF） | ERROR | LF 会让 `goto` / `for /f` / 多行 `if` 出错（`docs/48` §5-8 的真实缺陷） |
| B2 | 首行是 `@echo off` | ERROR | 否则整屏回显 |
| B3 | 含 `chcp 65001` | ERROR | 否则中文乱码 |
| B4 | 含 `setlocal` | ERROR | 否则污染调用者环境 |
| B5 | 所有 `goto :X` / `call :X` 的 `:X` 必须在本文件中定义 | ERROR | 悬空跳转 → 脚本静默走到文件末尾 |
| B6 | 括号配平（`(` 与 `)` 计数相等） | ERROR | 不配平会让多行 `if` 块提前结束 |
| B7 | 变量引用 `%VAR%` 必须在同文件内出现过 `set "VAR=` | **WARN** | 有的是环境变量（`%PATH%`），故只警告 |
| B8 | 不含 `enabledelayedexpansion` | WARN | 路径含 `!` 时会被吞（`setup.bat` 注释已说明） |

#### `.sh` 专属

| ID | 规则 | 级别 | 为什么 |
|---|---|---|---|
| S1 | 行尾符**必须 LF**（不得有 CRLF） | ERROR | CRLF 会让 shebang 失效、`bash script.sh` 报错 |
| S2 | 首行是 `#!/usr/bin/env bash` 或 `#!/bin/bash` | ERROR | — |
| S3 | 含 `set -euo pipefail`（或至少 `set -e`） | WARN | 不 fail-fast 会让错误被吞 |
| S4 | 用 `$(...)` 而非反引号 | WARN | 反引号不可嵌套、易读性差 |
| S5 | 含 `mvn ` 裸调用 | **WARN** | 本项目 Windows 下必须走 `scripts/mvn.sh`；`.sh` 中若用 `mvn` 需确认目标平台 |
| S6 | 所有 `goto` 无意义 → 不适用；改为：`cd` 后必须能回到确定目录（不出现裸 `cd ..`） | WARN | 相对路径脆弱 |

> **零误报原则**：任何规则若在现有脚本上产生误报，必须要么修正规则、要么降级为 WARN。
> 不允许"为了规则好看"去改本来正确的脚本。

### 2.3 接口

```
python scripts/check_scripts.py [--root DIR] [--strict]
```

- 默认扫描 `<root>/scripts/` 下的 `*.bat` / `*.sh`
- `--strict`：把 WARN 也当失败（供未来收紧用）
- 退出码：0 = 无 ERROR；1 = 有 ERROR
- 输出格式与 `doctor.py` 一致（`[OK]` / `[ERROR]` / `[警告]`），便于人读

## 3. `.sh` 脚本行为规格（与 `.bat` 逐项对齐）

### 3.1 `setup.sh`

| 步骤 | 行为 | 对齐 `setup.bat` |
|---|---|---|
| 1 | 定位项目根（`dirname $0/..`，解析为绝对路径） | ✅ |
| 2 | 找 Python：优先 `python3` → `python`；`< 3.11` 报错退出 | ✅ |
| 3 | 建 venv（`python-service/.venv`）；已存在则复用 | ✅ |
| 4 | **校验 pip 存在**；缺失则 `ensurepip --upgrade --default-pip`；仍失败则报错 | ✅（`docs/48` §5-3） |
| 5 | 装 `requirements.txt`；失败时给出镜像源提示 | ✅ |
| 6 | 无 `.env` 则从 `.env.example` 复制并提示 | ✅ |
| 7 | 无 jar 则探测 `mvn` 构建；无 `mvn` 时给出指引（Release 包已自带 jar） | ✅ |
| 8 | 收尾跑 `doctor.py`，有阻塞项则退出码 1 | ✅ |

### 3.2 `start.sh`

| 步骤 | 行为 | 对齐 `start.bat` |
|---|---|---|
| 1 | 找 Python（venv 优先） | ✅ |
| 2 | 先跑 `doctor.py --quiet`，阻塞则中止 | ✅ |
| 3 | `export POLYFACE_DATA_DIR="<root>/data"` | ✅ |
| 4 | 启动 Python：**必须在 `python-service/` 目录下**（`.env` 按 CWD 找） | ✅ |
| 5 | 启动 Java：`java -jar <root>/polyface.jar`（**不用 Maven**） | ✅ |
| 6 | 轮询 8000 → 8080（各 120s 上限），**不是固定 sleep** | ✅ |
| 7 | 就绪后打开 `http://127.0.0.1:8080`（**首页，不是 /health**） | ✅ |
| 8 | `trap` 清理：退出时结束两个子进程 | ✅（Windows 版靠两个独立窗口） |

### 3.3 `stop.sh`

| 步骤 | 行为 | 对齐 `stop.bat` |
|---|---|---|
| 1 | 找 8080 / 8000 的监听 PID | ✅ |
| 2 | 查进程名，**只结束** java / python 系；其它只提示 | ✅ |
| 3 | **结束前确认**（默认 60s 无输入 = 取消） | ✅（`docs/25` B2/B3） |
| 4 | 结束后**复查端口是否释放** | ✅（`docs/25` B5） |

端口 → PID 的获取方式（按平台）：
- Linux：`ss -lptn` 优先，退回 `lsof -ti tcp:PORT -s TCP:LISTEN`
- macOS：`lsof -ti tcp:PORT -s TCP:LISTEN`
- Windows(Git Bash)：`netstat -ano -p tcp` + `LISTENING`

> 与 `doctor.py::port_pid()` 保持同一套探测顺序，避免两处实现不一致。

## 4. 与 `doctor.py` 的关系

**不重复实现**：`.sh` 的 Python 定位、版本判断、pip 检查、端口探测等逻辑，
凡是 `doctor.py` 已有的，`.sh` 只负责调用，不重新写一遍。
`doctor.py` 是唯一实现，`.sh` / `.bat` 都只是编排。

## 5. 残余风险（本 spec 不解决的）

- `.bat` 仍未实机执行 → 静态校验**不能**等价替代
- 本环境是 **Git Bash（Windows 上的 bash）**，不是真 Linux/macOS：
  `lsof` / `ss` 分支、`start`/`open` 打开浏览器分支**仍未实测**
- `setup.sh` 的 Maven 构建分支（无 jar 时）与 `.bat` 同样未走通
