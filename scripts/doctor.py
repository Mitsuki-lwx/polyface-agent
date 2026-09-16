"""Polyface 启动前体检（doctor）。

**为什么单独写成 Python**：启动脚本（`setup.bat` / `start.bat`）需要检查
Java/Python 版本、venv、jar、端口占用、数据目录、LLM 模式 —— 这些逻辑写在
批处理里既难读也难测。这里用**纯标准库**实现（必须在 venv 建好之前就能跑），
批处理只做「找到 Python → 调用本脚本」。

用法：
    python scripts/doctor.py            # 人类可读报告
    python scripts/doctor.py --json     # 机器可读（供脚本判断）
    python scripts/doctor.py --quiet    # 只输出问题（供 setup/start 内嵌调用）

退出码：0 = 全部就绪；1 = 有阻塞项；2 = 用法错误

设计约束（docs/23）：
  - 只读：**不修改任何文件**，不做修复动作
  - 不含任何机器相关硬编码路径
  - 不联网
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 最低版本要求（与 README / pom.xml 保持一致）
MIN_JAVA = 17
MIN_PY = (3, 11)
PORTS = {"Java 后端": 8080, "Python LLM 服务": 8000}

REQUIRED_PY_PKGS = ("fastapi", "uvicorn", "pydantic_settings")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    hint: str = ""
    blocking: bool = True
    info_only: bool = False

    def as_dict(self) -> dict:
        return {
            "name": self.name, "ok": self.ok, "detail": self.detail,
            "hint": self.hint, "blocking": self.blocking, "info_only": self.info_only,
        }


# ---------------------------------------------------------------- 解析（纯函数，可测）

def parse_java_major(version_output: str) -> int | None:
    """从 `java -version` 输出解析主版本号。

    'openjdk version "21.0.7" 2025-04-15 LTS' -> 21
    'java version "1.8.0_301"'                 -> 8
    解析不出返回 None（不猜）。
    """
    m = re.search(r'version\s+"?([0-9][0-9._-]*)"?', version_output)
    if not m:
        return None
    raw = m.group(1).strip('"')
    parts = re.split(r"[._\-]", raw)
    if not parts or not parts[0].isdigit():
        return None
    major = parts[0]
    if major == "1" and len(parts) > 1 and parts[1].isdigit():
        return int(parts[1])          # 1.8.0_301 -> 8
    return int(major)


def parse_python_version(version_output: str) -> tuple[int, int] | None:
    """从 `python -V` 输出解析版本。'Python 3.13.14' -> (3, 13)。"""
    m = re.search(r"Python\s+(\d+)\.(\d+)", version_output)
    return (int(m.group(1)), int(m.group(2))) if m else None


def version_ok(found: tuple[int, int] | None, minimum: tuple[int, int]) -> bool:
    return found is not None and found >= minimum


# ---------------------------------------------------------------- 环境探测

def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    """跑一条命令，合并 stdout+stderr。失败返回 (-1, 原因)。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return -1, "命令不存在"
    except subprocess.TimeoutExpired:
        return -1, "命令超时"
    except Exception as e:                      # noqa: BLE001 — 体检不应因探测失败而崩
        return -1, f"{type(e).__name__}: {e}"


def project_root() -> Path:
    """项目根 = scripts/ 的上一级。不依赖当前工作目录。"""
    return Path(__file__).resolve().parent.parent


def find_jar(root: Path) -> Path | None:
    """按「发布包布局 → 开发布局」顺序找 fat jar。"""
    for cand in (root / "polyface.jar", root / "java-backend" / "target" / "polyface.jar"):
        if cand.is_file():
            return cand
    return None


def find_venv_python(root: Path) -> Path | None:
    for rel in (Path("python-service/.venv/Scripts/python.exe"),
                Path("python-service/.venv/bin/python")):
        p = root / rel
        if p.is_file():
            return p
    return None


def port_pid(port: int) -> int | None:
    """占用端口的进程 PID。查不到返回 None（不猜）。"""
    if sys.platform == "win32":
        code, out = _run(["netstat", "-ano", "-p", "tcp"])
        if code != 0:
            return None
        for line in out.splitlines():
            if "LISTENING" not in line.upper():
                continue
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(f":{port}"):
                if parts[-1].isdigit():
                    return int(parts[-1])
        return None
    # POSIX：用 lsof，没有就算了
    code, out = _run(["lsof", "-ti", f"tcp:{port}", "-s", "TCP:LISTEN"])
    if code == 0 and out.strip().split():
        try:
            return int(out.split()[0])
        except ValueError:
            return None
    return None


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


# ---------------------------------------------------------------- 各项检查

def check_java() -> Check:
    exe = shutil.which("java")
    if not exe:
        return Check("Java 运行时", False, "未找到 java",
                     f"需 JDK/JRE {MIN_JAVA}+：https://adoptium.net/ （安装后重开终端）")
    code, out = _run([exe, "-version"])
    major = parse_java_major(out)
    if major is None:
        return Check("Java 运行时", False, f"无法解析版本：{out.strip()[:60]}",
                     f"需 {MIN_JAVA}+，请确认 `java -version` 可正常输出")
    if major < MIN_JAVA:
        return Check("Java 运行时", False, f"版本 {major} 过低",
                     f"需 {MIN_JAVA}+（当前 {major}）：https://adoptium.net/")
    return Check("Java 运行时", True, f"{major}（{exe}）")


def check_python() -> Check:
    v = sys.version_info
    if (v.major, v.minor) < MIN_PY:
        return Check("Python 运行时", False, f"{v.major}.{v.minor} 过低",
                     f"需 {MIN_PY[0]}.{MIN_PY[1]}+：https://www.python.org/downloads/")
    return Check("Python 运行时", True, f"{v.major}.{v.minor}.{v.micro}（{sys.executable}）")


def check_venv(root: Path) -> Check:
    py = find_venv_python(root)
    if py is None:
        return Check("Python 依赖环境", False, "未创建 venv",
                     "运行 scripts\\setup.bat 创建（或手动 python -m venv python-service/.venv）")
    # 先看 pip 在不在：venv 可能建出来却没有 pip（精简版 Python / ensurepip 被禁用）。
    # 不先查的话，下面的 import 检查会报「缺依赖」，把用户引到「装依赖」的死循环里 ——
    # 而没有 pip 根本装不了。
    code, out = _run([str(py), "-m", "pip", "--version"])
    if code != 0:
        return Check("Python 依赖环境", False, "venv 内没有 pip",
                     f"手动修复：{py} -m ensurepip --upgrade --default-pip"
                     f"（或删掉 python-service\\.venv 后重跑 scripts\\setup.bat）")
    code, out = _run([str(py), "-c", "import " + ", ".join(REQUIRED_PY_PKGS)])
    if code != 0:
        missing = [p for p in REQUIRED_PY_PKGS if p not in out]
        return Check("Python 依赖环境", False, f"缺依赖：{', '.join(missing) or '未知'}",
                     "运行 scripts\\setup.bat 安装依赖")
    return Check("Python 依赖环境", True, f"venv 就绪（{py}）")


def check_jar(root: Path) -> Check:
    jar = find_jar(root)
    if jar is None:
        return Check("Java 后端 jar", False, "未找到 polyface.jar",
                     "运行 scripts\\setup.bat 构建（需要 Maven；或从 Release 下载预构建包）")
    size_mb = jar.stat().st_size / 1024 / 1024
    return Check("Java 后端 jar", True, f"{jar}（{size_mb:.1f} MB）")


def check_ports() -> list[Check]:
    out: list[Check] = []
    for label, port in PORTS.items():
        pid = port_pid(port)
        if port_in_use(port):
            out.append(Check(f"端口 {port}（{label}）", False, f"已被占用（PID {pid or '未知'}）",
                             f"先运行 scripts\\stop.bat 结束旧进程；"
                             f"或确认该端口是你要复用的服务", blocking=True))
        else:
            out.append(Check(f"端口 {port}（{label}）", True, "空闲"))
    return out


def check_data_dir(root: Path) -> Check:
    """数据目录：必须在包内，否则用户数据会散落到包外（启动脚本会显式指定）。"""
    d = root / "data"
    db = d / "polyface.db"
    if db.is_file():
        return Check("数据目录", True, f"{d}（已有数据库 {db.stat().st_size / 1024:.0f} KB）")
    return Check("数据目录", True, f"{d}（首次启动时创建）")


def check_llm_mode(root: Path) -> Check:
    """报告真实/mock 模式 —— 与 ADR-017 的数据边界提示一致，不静默。"""
    env = root / "python-service" / ".env"
    if not env.is_file():
        return Check("LLM 模式", True, "未配置 .env → 离线演示模式（mock，不联网）",
                     "要真实生成：复制 .env.example 为 .env 并填入 LLM_API_KEY",
                     blocking=False)
    text = env.read_text(encoding="utf-8", errors="replace")
    key = ""
    mock = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("LLM_API_KEY="):
            key = line.split("=", 1)[1].strip()
        elif line.startswith("LLM_MOCK="):
            mock = line.split("=", 1)[1].strip().lower()
    if mock == "true":
        return Check("LLM 模式", True, "离线演示模式（LLM_MOCK=true，不联网，产出为演示结果）",
                     blocking=False)
    if not key:
        return Check("LLM 模式", True, "未填 LLM_API_KEY → 回退离线演示模式（mock）",
                     "要真实生成：在 python-service/.env 填 LLM_API_KEY", blocking=False)
    return Check("LLM 模式", True, "真实生成（Key 已配置）—— 生成时素材正文会发送到"
                                   "你自己配置的 LLM 服务商（见 README 数据边界）",
                 blocking=False)


def run_all(root: Path | None = None) -> list[Check]:
    root = root or project_root()
    checks = [check_java(), check_python(), check_venv(root), check_jar(root)]
    checks += check_ports()
    checks += [check_data_dir(root), check_llm_mode(root)]
    return checks


# ---------------------------------------------------------------- 输出

def render(checks: list[Check], quiet: bool = False) -> str:
    lines: list[str] = []
    for c in checks:
        if c.info_only:
            continue
        if quiet and c.ok:
            continue
        mark = "OK  " if c.ok else ("阻塞" if c.blocking else "提示")
        lines.append(f"[{mark}] {c.name}: {c.detail}")
        if c.hint and not c.ok:
            lines.append(f"         → {c.hint}")
        elif c.hint and not quiet:
            lines.append(f"         → {c.hint}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Polyface 启动前体检")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--quiet", action="store_true", help="只输出问题")
    ap.add_argument("--root", default=None, help="项目根目录（默认自动推导）")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else project_root()
    checks = run_all(root)

    if args.json:
        blocking = [c for c in checks if not c.ok and c.blocking]
        print(json.dumps({"ok": not blocking, "checks": [c.as_dict() for c in checks]},
                         ensure_ascii=False, indent=2))
    else:
        print(render(checks, quiet=args.quiet))

    blocking = [c for c in checks if not c.ok and c.blocking]
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
