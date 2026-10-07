"""`scripts/stop.sh` 的**行为**测试（不是静态校验 —— 那是 test_check_scripts.py）。

为什么单独测这个：`-y` 非交互开关（`docs/62` §8 跟进项）是给脚本/CI 用的，
而它的反面是**默认交互式询问**。这两条都只体现在"读不读 stdin"上，
静态校验器完全看不见 —— 有人在改动确认块时很容易把它碰坏而无人察觉。

**安全性**：测试只跑"确认"这一段（在任何端口查找之前），并且刻意把
`POLYFACE_JAVA_PORT` / `POLYFACE_PY_PORT` 指到**当场探测到的空闲端口**，
确保即使脚本继续往下走，也不会杀掉开发机上真在跑的服务。
"""
from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STOP_SH = REPO_ROOT / "scripts" / "stop.sh"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="没有 bash，跳过（脚本本身是 bash 脚本）")


def _free_port() -> int:
    """拿一个当前空闲的端口。

    bind 到 0 让内核挑，再关掉 —— 有极小的 TOCTOU 窗口，但对本测试足够：
    我们只需要一个"几乎不可能恰好被别人占着"的端口。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_stop(*args: str) -> subprocess.CompletedProcess:
    """跑 stop.sh，**stdin 关闭**（模拟脚本 / CI 环境）。

    刻意用 `stdin=DEVNULL`：这正是回归要防的场景 ——
    没有 `-y` 时它会去读 stdin，拿到 EOF 就取消。
    """
    env = {
        "PATH": "/usr/bin:/bin:/mingw64/bin",
        "POLYFACE_JAVA_PORT": str(_free_port()),
        "POLYFACE_PY_PORT": str(_free_port()),
    }
    import os
    full_env = {**os.environ, **env}
    return subprocess.run(
        [BASH, str(STOP_SH), *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30, env=full_env,
        cwd=str(REPO_ROOT),
    )


# ---------------------------------------------------------------- 正例

def test_dash_y_skips_confirmation_without_stdin():
    """`-y` 必须**不读 stdin** 就往下走 —— 这是给脚本/CI 用的全部意义。"""
    p = run_stop("-y")
    assert p.returncode == 0, p.stderr
    assert "跳过确认" in p.stdout
    assert "已取消" not in p.stdout


def test_long_form_yes_also_skips():
    p = run_stop("--yes")
    assert p.returncode == 0, p.stderr
    assert "跳过确认" in p.stdout


def test_help_exits_zero_and_shows_usage():
    p = run_stop("-h")
    assert p.returncode == 0
    assert "用法" in p.stdout
    assert "-y" in p.stdout


# ---------------------------------------------------------------- 反例

def test_no_args_still_prompts_and_cancels_on_eof():
    """**默认行为不许变**：不带参数时仍然要问，且 stdin 关闭 = 取消。

    这条是 `-y` 的对照 —— 少了它，把 stop.sh 改成"永远不询问"也能全绿。
    """
    p = run_stop()
    assert p.returncode == 0
    assert "确认继续" in p.stdout
    assert "已取消" in p.stdout
    assert "跳过确认" not in p.stdout


def test_unknown_arg_is_rejected():
    """认错的参数不能**静默当成默认行为** —— 否则 CI 里拼错 `-Y` 会变成
    "没人应答 → 取消 → 退出码 0"，看起来像成功。"""
    p = run_stop("--nope")
    assert p.returncode == 2
    assert "未知参数" in p.stdout + p.stderr
