"""进程生命周期与**崩溃取证**（`openspec/changes/service-crash-forensics`）。

**为什么存在**：2026-10-31 实测，uvicorn 进程以 `0xC0000409`（`STATUS_STACK_BUFFER_OVERRUN`）
退出，日志最后一行是 `GET /favicon.ico 404` —— 之后**什么都没有**：
没有 Python traceback、没有 dump、没有退出码。一个本地服务无声消失，
用户看到的是"点了没反应"，会去怀疑自己的网络或 Key ✗。

这里做三件事：
1. **致命信号留栈** —— `faulthandler`（标准库、零依赖），并且 **必须写文件**：
   默认写 stderr，而 `.bat` 那侧的 stderr 是一个**窗口**不是文件 ✗。
2. **启动/正常退出留痕** —— pid、argv、退出码，事后不用考古。
3. **诚实的边界** —— 崩溃时 `atexit` **不会**执行，所以"退出码"只能由监管者
   （`scripts/start.sh`）记；这里只负责"进程自己还能写的时候写下来"。
"""
from __future__ import annotations

import atexit
import faulthandler
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_state: dict = {"enabled": False, "path": None, "file": None}


def crash_log_path() -> Path:
    """崩溃日志路径：`POLYFACE_CRASH_LOG` > `<data 的同级>/logs/crash-python.log`。

    默认放在**数据目录的同级** `logs/` —— 与 `scripts/start.sh` 的 `$ROOT/logs` 同一个位置，
    用户只需要记住一个地方。
    """
    explicit = (os.getenv("POLYFACE_CRASH_LOG") or "").strip()
    if explicit:
        return Path(explicit)
    base = Path(os.getenv("POLYFACE_DATA_DIR", "../data")).resolve()
    return base.parent / "logs" / "crash-python.log"


def enable_crash_log(path: str | Path | None = None) -> Path | None:
    """打开崩溃日志并启用 `faulthandler`。**幂等**；失败只告警，绝不影响服务启动。

    返回实际使用的路径（失败时 `None`）。
    """
    if _state["enabled"]:
        return _state["path"]
    try:
        p = Path(path) if path is not None else crash_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        # 行缓冲 + 追加：崩溃时不会被缓冲吞掉，多次崩溃也不覆盖前一次
        f = p.open("a", encoding="utf-8", buffering=1)
        faulthandler.enable(file=f, all_threads=True)
        _state.update(enabled=True, path=p, file=f)
        logger.info("崩溃取证已启用：%s（faulthandler 已挂到这个文件）", p)
        return p
    except Exception as e:  # noqa: BLE001 — 取证失败不能拖垮服务
        logger.warning("崩溃取证启用失败（服务照常启动）：%s", e)
        return None


def _write(line: str) -> None:
    f = _state.get("file")
    if f is None:
        return
    try:
        f.write(line.rstrip("\n") + "\n")
        f.flush()
    except Exception:  # noqa: BLE001
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_startup(argv: list[str] | None = None, port: int | None = None) -> None:
    """记一条启动记录（pid / argv / python / 端口）。"""
    args = " ".join(argv if argv is not None else sys.argv)
    _write(f"[{_now()}] START pid={os.getpid()} py={sys.version.split()[0]} "
           f"port={port if port is not None else '-'} argv={args[:300]}")


def log_shutdown(reason: str = "正常退出", exit_code: int = 0) -> None:
    """记一条退出记录。

    ⚠️ **崩溃时这里不会跑** —— 致命信号 / 原生崩溃不经过 `atexit`。
    那种情况下的"退出码 + 谁退的"由监管者（`scripts/start.sh`）记。
    """
    _write(f"[{_now()}] EXIT pid={os.getpid()} code={exit_code} reason={reason}")


def install_atexit() -> None:
    """注册正常退出的钩子（幂等：重复调用只注册一次）。"""
    if _state.get("atexit") is not None:
        return
    atexit.register(lambda: log_shutdown("atexit（正常退出）", 0))
    _state["atexit"] = True
