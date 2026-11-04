# Tasks

## 1. Python 侧：崩溃留证据

- [x] 1.1 新增 `python-service/app/lifecycle.py`：`enable_crash_log()`（`faulthandler.enable(file=…)` + 追加模式）与 `log_startup()` / `log_shutdown()`（pid、argv、退出码）
- [x] 1.2 `main.py` 在 import 期调用 `enable_crash_log()`；`POLYFACE_CRASH_LOG` 可覆盖默认路径（`<data 父级>/logs/crash-python.log`）
- [x] 1.3 用 `atexit` 记正常退出；**明确注释**"崩溃时 `atexit` 不跑，退出码由 start.sh 记"

## 2. 启动脚本：子进程退出被看见

- [x] 2.1 `scripts/start.sh`：把裸 `wait` 改成 `wait -n "${PIDS[@]}"`；拿到退出码后**指出是哪个服务**、打日志路径、`cleanup` 收掉另一个、以非零退出
- [x] 2.2 兼容：探测 bash 是否支持 `wait -n`；不支持则退回"轮询 + 尽力拿退出码"，并打印一句"本 bash 不支持 wait -n"
- [x] 2.3 `scripts/start.bat`：逻辑对齐（一个服务窗口退出 → 提示并结束另一个）；⚠️ 交付时声明**未实机验证**

## 3. doctor：服务还活着吗

- [x] 3.1 新增 `check_services_alive()`：**真的打一次请求**（Python `GET /health`；Java `GET /api/materials?limit=1`，**带回环 Host**），超时 2 秒，`info_only=True`
- [x] 3.2 不可达时输出里给出**日志路径**与"用 `scripts/start.sh` 重启"

## 4. 测试

- [x] 4.1 Python：`tests/test_lifecycle.py` —— 崩溃日志路径可覆盖；`enable_crash_log()` 幂等；启动/退出记录含 pid 与退出码；正常退出**不产生崩溃栈**
- [x] 4.2 Python：`test_doctor.py` 补 `check_services_alive` 的三态（都活 / 死一个 / 都不活），用本地起的小 HTTP 服务当靶子
- [x] 4.3 既有套件不被误伤（Python + Java 全量）

## 5. 端到端（**真起进程**）

- [x] 5.1 起 `start.sh` → 两个服务就绪
- [x] 5.2 `kill -9` 掉 Python 服务 → `start.sh` **当场**打印"Python 服务已退出（退出码 …）"并收掉 Java
- [x] 5.3 再起 → `doctor` 报两个服务都活；单独杀 Python → `doctor` 报"不可达"并给日志路径
- [x] 5.4 触发一次**致命信号**（`SIGABRT`）→ 崩溃日志里出现 Python 栈；如实记录 Windows 上 `0xC0000409` 型崩溃能否被抓到

## 6. 文档

- [x] 6.1 `README.zh-CN.md` / `README.md` FAQ 补："服务没反应？先跑 doctor 看它是否还活着"
- [x] 6.2 交付说明（`docs/`）记：实测抓到什么 / 没抓到什么、`0xC0000409` 仍是 needs-validation
- [x] 6.3 归档本 change
