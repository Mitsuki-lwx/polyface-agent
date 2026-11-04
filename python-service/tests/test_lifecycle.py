"""进程生命周期与崩溃取证（`app/lifecycle.py`）的测试。

**为什么测它**：这套东西的**唯一用途**就是"出事之后还留得下证据"。
它平时完全不显眼，所以坏了也没人发现 —— 直到又一次无声崩溃。
因此这里直接验证**救命路径**：真的发一个致命信号，看日志里有没有栈。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from app import lifecycle  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_lifecycle_state(monkeypatch):
    """每个用例从干净状态开始（模块级单例）。"""
    monkeypatch.setattr(lifecycle, "_state", {"enabled": False, "path": None, "file": None})
    yield


# ---------------------------------------------------------------- 路径与幂等

def test_crash_log_path_honours_env_override(monkeypatch, tmp_path):
    target = tmp_path / "custom" / "crash.log"
    monkeypatch.setenv("POLYFACE_CRASH_LOG", str(target))
    assert lifecycle.crash_log_path() == target


def test_crash_log_path_defaults_next_to_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("POLYFACE_CRASH_LOG", raising=False)
    monkeypatch.setenv("POLYFACE_DATA_DIR", str(tmp_path / "data"))
    assert lifecycle.crash_log_path() == (tmp_path / "logs" / "crash-python.log").resolve()


def test_enable_is_idempotent(tmp_path):
    log = tmp_path / "crash.log"
    first = lifecycle.enable_crash_log(log)
    second = lifecycle.enable_crash_log(tmp_path / "other.log")
    assert first == log and second == log, "重复启用必须复用同一个文件，不能把 faulthandler 指向别处"


def test_enable_failure_does_not_raise(tmp_path):
    """取证失败不能拖垮服务启动（例如路径不可写）。"""
    bad = tmp_path / "file.txt"
    bad.write_text("x", encoding="utf-8")
    # 把日志路径设成一个**文件**，父目录 mkdir 会失败
    assert lifecycle.enable_crash_log(bad / "sub" / "crash.log") is None


# ---------------------------------------------------------------- 生命周期记录

def test_startup_and_shutdown_records(tmp_path):
    log = tmp_path / "crash.log"
    lifecycle.enable_crash_log(log)
    lifecycle.log_startup(argv=["uvicorn", "app.main:app"], port=8000)
    lifecycle.log_shutdown("测试", 0)

    text = log.read_text(encoding="utf-8")
    assert f"START pid={os.getpid()}" in text
    assert "port=8000" in text
    assert "uvicorn app.main:app" in text
    assert f"EXIT pid={os.getpid()} code=0" in text


def test_writes_are_appended_not_truncated(tmp_path):
    """多次运行不能覆盖前一次的崩溃栈（否则第二次崩溃就把第一次的证据删了）。"""
    log = tmp_path / "crash.log"
    lifecycle.enable_crash_log(log)
    lifecycle.log_shutdown("第一次", 1)

    lifecycle._state = {"enabled": False, "path": None, "file": None}
    lifecycle.enable_crash_log(log)
    lifecycle.log_shutdown("第二次", 2)

    text = log.read_text(encoding="utf-8")
    assert "第一次" in text and "第二次" in text


def test_normal_exit_leaves_no_crash_stack(tmp_path):
    log = tmp_path / "crash.log"
    lifecycle.enable_crash_log(log)
    lifecycle.log_startup()
    lifecycle.log_shutdown("atexit（正常退出）", 0)

    text = log.read_text(encoding="utf-8")
    assert "Traceback" not in text
    assert "Fatal Python error" not in text
    assert "Windows fatal exception" not in text


# ---------------------------------------------------------------- 真·致命信号

def _run_crashing_child(tmp_path: Path, body: str) -> tuple[int, str]:
    log = tmp_path / "crash.log"
    code = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(SERVICE_ROOT)!r})
        from app import lifecycle
        lifecycle.enable_crash_log({str(log)!r})
        lifecycle.log_startup()
        {body}
    """)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    return proc.returncode, (log.read_text(encoding="utf-8", errors="replace") if log.is_file() else "")


def test_fatal_signal_writes_python_stack(tmp_path):
    """**这是本 change 的核心**：原生层崩溃必须留下栈，而不是什么都没有。"""
    rc, text = _run_crashing_child(tmp_path, "os.abort()")
    assert rc != 0
    assert 'File "' in text, f"崩溃日志里没有栈：{text!r}"
    assert "Fatal Python error" in text or "fatal exception" in text


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 专有的访问违例路径")
def test_native_access_violation_writes_stack(tmp_path):
    rc, text = _run_crashing_child(tmp_path, "import ctypes; ctypes.string_at(0)")
    assert rc != 0
    assert "fatal exception" in text.lower(), text
    assert 'File "' in text
