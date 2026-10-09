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
from datetime import date
from pathlib import Path

# 最低版本要求（与 README / pom.xml 保持一致）
MIN_JAVA = 17
MIN_PY = (3, 11)
DEFAULT_JAVA_PORT = 8080
DEFAULT_PY_PORT = 8000

REQUIRED_PY_PKGS = ("fastapi", "uvicorn", "pydantic_settings")

# 已验证的封面编辑器版本（ADR-020 / docs/68 §6）。升它必须重跑 scripts/e2e_editor.py。
VERIFIED_GIMPISH = "0.1.0"


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
    """占用端口的进程 PID。查不到返回 None（不猜）。

    Windows 上同时看 LISTENING 与 BOUND —— 后者虽未 listen，但已占住端口，
    bind 会失败（见 port_in_use 的说明）。
    """
    if sys.platform == "win32":
        code, out = _run(["netstat", "-ano", "-p", "tcp"])
        if code != 0:
            return None
        for line in out.splitlines():
            up = line.upper()
            if "LISTENING" not in up and "BOUND" not in up:
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
    """端口能否被本进程**绑定**。

    ⚠️ 这里刻意用 bind 而不是 connect。曾经用 `connect_ex(...) == 0`，
    它只探测「有没有活跃的监听者」，于是**漏掉两类真实占用**：
      · 别人已 bind 但还没 listen（Windows 上就是 `BOUND` 状态，
        某些游戏平台/加速器会一次性 bind 掉一整段端口）
      · 落在 Windows 保留段（`netsh int ipv4 show excludedportrange`）里的端口，
        bind 会报 WinError 10013
    这两种情况下 connect_ex 都返回「连不上」，旧实现据此判定"空闲"，
    于是体检放行、start.sh 却卡满 120 秒才报错 —— 体检的意义就没了。

    bind 到 127.0.0.1 是可用的最强判据：能绑上就是真能用。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


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


def check_ports(java_port: int = DEFAULT_JAVA_PORT,
                py_port: int = DEFAULT_PY_PORT) -> list[Check]:
    out: list[Check] = []
    for label, port in {"Java 后端": java_port, "Python LLM 服务": py_port}.items():
        pid = port_pid(port)
        if port_in_use(port):
            # 查不到 PID 不等于没人占：可能是别人 bind 了还没 listen，
            # 或端口落在系统保留段。这时给「换端口」而不是「杀进程」，
            # 因为大概率不是本应用的进程（本应用的进程用 stop 脚本更安全）。
            if pid:
                detail = f"已被占用（PID {pid}）"
                hint = ("先运行 scripts\\stop.bat 结束旧进程；若该 PID 不是 Polyface，"
                        "换端口启动：")
            else:
                detail = "无法绑定（未查到监听进程，可能是端口被保留或已被占用）"
                hint = ("换一个端口启动（下方命令；Windows 用 set 而不是 export）：")
            out.append(Check(
                f"端口 {port}（{label}）", False, detail,
                hint + f"POLYFACE_JAVA_PORT=18080 POLYFACE_PY_PORT=18000",
                blocking=True))
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


# platform-dna 多久算过期。平台规则会变，而 DNA 是**静态文件** —— 没有这个提醒，
# 改规则了也没人知道（实测：5 个平台全部停在 2026-09-12）。
DEFAULT_DNA_MAX_AGE_DAYS = 90


def check_platform_dna(root: Path, max_age_days: int = DEFAULT_DNA_MAX_AGE_DAYS) -> Check:
    """平台 DNA 的新鲜度。**非阻塞** —— 过期不会让服务起不来，但值得看一眼。

    刻意用正则而不是 yaml：doctor 是独立脚本（无第三方依赖），且这里只读一个字段。
    """
    d = root / "platform-dna"
    files = sorted(d.glob("*.yaml"))
    if not files:
        return Check("平台 DNA", False, f"未找到 {d}/*.yaml", blocking=False)

    today = date.today()
    ages: list[tuple[str, int | None]] = []
    for f in files:
        m = re.search(r"^updated_at:\s*(\S+)", f.read_text(encoding="utf-8", errors="replace"), re.M)
        raw = (m.group(1).strip() if m else "")
        try:
            ages.append((f.stem, (today - date.fromisoformat(raw)).days))
        except (ValueError, TypeError):
            ages.append((f.stem, None))

    known = [(n, a) for n, a in ages if a is not None]
    unknown = [n for n, a in ages if a is None]
    if not known:
        return Check("平台 DNA", False, f"{len(files)} 个平台，但都没有可解析的 updated_at",
                     "在 platform-dna/*.yaml 里补 updated_at: YYYY-MM-DD", blocking=False)

    newest = min(a for _, a in known)
    oldest_name, oldest = max(known, key=lambda x: x[1])
    detail = f"{len(files)} 个平台，最新 {newest} 天前"
    if unknown:
        detail += f"（{len(unknown)} 个没标 updated_at：{', '.join(unknown[:3])}）"
    if oldest > max_age_days:
        return Check("平台 DNA", False, detail,
                     f"最旧的是 {oldest_name}（{oldest} 天前），已超过 {max_age_days} 天 —— "
                     f"平台规则会变，建议对照官方文档核对一遍；"
                     f"可用 POLYFACE_DNA_MAX_AGE_DAYS 调整阈值",
                     blocking=False)
    return Check("平台 DNA", True, detail)


def check_langfuse(root: Path) -> Check:
    """观测：配置说"开着"、实际却关着 —— 这类**静默降级**必须报出来。

    实测（2026-10-09）：`.env` 里 `LANGFUSE_ENABLED=true` 且 key 都填了，
    `observability.enabled()` 因此返回 True，但 venv 里**根本没装 `langfuse`** ——
    于是每次上报都 ImportError 被吞掉，只在日志留一句 warning。
    用户看到的是"我配了观测，Langfuse 里却什么都没有"。

    所以这里不看配置，**直接问 venv 能不能 import**。
    """
    env = root / "python-service" / ".env"
    if not env.is_file():
        return Check("观测(Langfuse)", True, "未配置 .env，跳过", info_only=True)
    text = env.read_text(encoding="utf-8", errors="replace")
    enabled = re.search(r"^LANGFUSE_ENABLED\s*=\s*(\S+)", text, re.M)
    if not enabled or enabled.group(1).strip().strip('"').strip("'").lower() not in ("1", "true", "yes", "on"):
        return Check("观测(Langfuse)", True, "未启用（LANGFUSE_ENABLED 非 true）", info_only=True)

    vpy = find_venv_python(root)
    if vpy is None:
        return Check("观测(Langfuse)", True, "venv 未建，跳过（先跑 setup）", info_only=True,
                     blocking=False)
    rc, _ = _run([str(vpy), "-c", "import langfuse"], timeout=30)
    if rc != 0:
        return Check("观测(Langfuse)", False,
                     "已启用，但 venv 里**没装** langfuse —— 上报会被静默吞掉，"
                     "你在 Langfuse 里看不到任何 trace",
                     f"装它：{vpy} -m pip install langfuse",
                     blocking=False)
    return Check("观测(Langfuse)", True, "已启用且 langfuse 可用")


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


def find_gimpish(root: Path) -> Path | None:
    """定位 gimpish：`.env` 的 GIMPISH_PATH > 环境变量 POLYFACE_GIMPISH > PATH。

    与 Python 侧 `app/pipeline/cover.py::_gimpish_bin` 保持**同一优先级**，
    否则会出现"封面能生成、但体检说没装"这种自相矛盾的报告。
    """
    candidates: list[Path] = []
    env_file = root / "python-service" / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("GIMPISH_PATH="):
                value = line.split("=", 1)[1].strip()
                if value:
                    candidates.append(Path(value))
    from_env = os.environ.get("POLYFACE_GIMPISH", "").strip()
    if from_env:
        candidates.append(Path(from_env))

    for cand in candidates:
        if cand.is_file():
            return cand
        for name in ("gimpish.cmd", "gimpish", "gimpish.js"):
            if (cand / name).is_file():
                return cand / name
    found = shutil.which("gimpish")
    return Path(found) if found else None


def gimpish_version(exe: Path) -> str:
    """从 `node_modules/gimpish/package.json` 读版本。

    为什么不跑 `gimpish --version`：实测 0.1.0 **没有**该选项（会报 unknown option）。
    """
    for parent in exe.parents:
        if parent.name == "gimpish":
            pkg = parent / "package.json"
            if pkg.is_file():
                try:
                    return json.loads(pkg.read_text(encoding="utf-8")).get("version", "")
                except (OSError, ValueError):
                    return ""
    return ""


def check_editor(root: Path) -> Check:
    """封面编辑器（gimpish）：**可选依赖** —— 缺失只让封面功能降级，不影响其余功能。"""
    exe = find_gimpish(root)
    if exe is None:
        return Check("封面编辑器", True,
                     "未检测到 gimpish → 「生成封面 / 在编辑器里改封面」会降级为提示安装",
                     f"npm install -g gimpish（需 Node ≥ 20.19）；本项目已验证版本 "
                     f"{VERIFIED_GIMPISH}", blocking=False)
    version = gimpish_version(exe)
    detail = f"{exe}" + (f"（版本 {version}）" if version else "（版本未知）")
    if version and version != VERIFIED_GIMPISH:
        return Check("封面编辑器", True, detail,
                     f"已验证版本为 {VERIFIED_GIMPISH}；其他版本的**内嵌编辑器 UI 未验证**，"
                     f"升级后请重跑 scripts/e2e_editor.py", blocking=False)
    return Check("封面编辑器", True, detail, blocking=False)


def dna_max_age_days() -> int:
    """阈值可配（项目规矩：任何可调值都要能从环境变量到达）。"""
    try:
        return max(1, int(os.getenv("POLYFACE_DNA_MAX_AGE_DAYS", "").strip()
                          or DEFAULT_DNA_MAX_AGE_DAYS))
    except ValueError:
        return DEFAULT_DNA_MAX_AGE_DAYS


def run_all(root: Path | None = None, java_port: int = DEFAULT_JAVA_PORT,
            py_port: int = DEFAULT_PY_PORT) -> list[Check]:
    root = root or project_root()
    checks = [check_java(), check_python(), check_venv(root), check_jar(root)]
    checks += check_ports(java_port, py_port)
    checks += [check_data_dir(root), check_llm_mode(root), check_editor(root),
               check_langfuse(root),
               check_platform_dna(root, dna_max_age_days())]
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
    ap.add_argument("--java-port", type=int, default=DEFAULT_JAVA_PORT,
                    help=f"Java 后端端口（默认 {DEFAULT_JAVA_PORT}）")
    ap.add_argument("--py-port", type=int, default=DEFAULT_PY_PORT,
                    help=f"Python LLM 服务端口（默认 {DEFAULT_PY_PORT}）")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else project_root()
    checks = run_all(root, java_port=args.java_port, py_port=args.py_port)

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
