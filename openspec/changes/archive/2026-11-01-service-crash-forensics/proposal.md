# Proposal

## Why

**Python 服务会无声消失。**

2026-10-31 实测：`uvicorn` 进程以 `0xC0000409`（`STATUS_STACK_BUFFER_OVERRUN`）退出，
日志的最后一行是 `GET /favicon.ico 404` —— 之后**什么都没有** ✗：
没有 Python traceback、没有 dump、没有退出码（产品侧不记）。

同一轮勘察确认了四件事，每一件都让"下次也会一样"：

| 现状 | 后果 |
|---|---|
| 没有 `faulthandler`、没有信号处理器、没有 `atexit` | 原生层崩溃 → **零证据** ✗ |
| `scripts/start.sh` 末尾是裸 `wait`（等**全部**子进程）| 一个服务死了，脚本**还在等另一个**，界面照常显示"运行中" ✗ |
| 产品侧不记录子进程退出码 / pid | 事后只能靠终端回滚或任务管理器 ✗ |
| `doctor.py` 只查环境（java / venv / jar / 端口），**不查"服务现在活着吗"** | 用户遇到的问题永远是"点了没反应" ✗ |

这条链路的本钱是"用户自己填 Key、自己付额度"。一个会在半夜无声死掉的本地服务，
第一次表现是**一次看起来卡死的生成** —— 用户会以为是自己网络或 Key 的问题 ✗。

## What Changes

- **崩溃留证据**：Python 服务启用 `faulthandler`（致命信号 → 把 Python 栈写进日志），
  并在启动/退出时记录 pid、启动参数、退出码与原因。
- **子进程退出被看见**：`start.sh` / `start.bat` 对**每个**子进程分别 wait，
  任一退出就**大声报出来**（是哪个、退出码是什么、日志在哪），并停止另一个 ——
  不再"静默地半死"。
- **`doctor` 查存活**：新增"两个服务现在活着吗"的检查（不看进程名，**真的打一次请求**）。
- **不自动重启**：崩溃要**被看见**，不要被重启掩盖。

## Capabilities

### New Capabilities

- `service-crash-forensics`: 本地服务的**崩溃取证与可见性** —— 定义"服务死掉时留下什么证据"、
  "谁在什么时候发现"、"用户看到什么"。覆盖 Python 服务与启动脚本两侧。

### Modified Capabilities

（无。既有的 `local-service-guard` 管的是"谁能打进来"，与"死掉时留什么"无关。）

## Impact

- **代码**：`python-service/app/main.py`（或一个小模块）加 `faulthandler` + 生命周期日志；
  `scripts/start.sh` / `start.bat` 改子进程监管；`scripts/doctor.py` 加一个检查。
- **API**：不变。
- **配置**：新增 `POLYFACE_CRASH_LOG`（崩溃日志路径，默认跟着现有的 log 目录）。
- **风险**：`faulthandler` 默认只写 stderr；必须让它进**文件**，否则用户看不到 ——
  这是本 change 的主要验证点。
