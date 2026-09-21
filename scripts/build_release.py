"""Polyface Release 打包（组装 + 校验 + 压缩）。

**为什么用 Python**：打包最容易出的事故是「把用户数据或密钥打进发布包」。
这类校验必须可测试、可重复，写在批处理里做不到。

用法：
    python scripts/build_release.py            # 构建并打包到 dist/
    python scripts/build_release.py --no-build # 跳过 Maven，只用现有 jar
    python scripts/build_release.py --check-only   # 只校验已有目录，不打包

产物：dist/polyface-<version>.zip

设计约束（docs/23 §5 风险表）：
  - **绝不打包**：`data*/`、`.env`（及 `.env.*` 里除 `.env.example` 外的全部）、
    `.venv`、`*.db`、`__pycache__`、`*.pyc`、`target/`
  - 打包前后各校验一次；发现违禁文件**直接失败**，不产出可疑 zip
  - 目录结构保持 `python-service/` 与 `platform-dna/` 同级
    （`app/dna.py` 用 `parents[2]/platform-dna` 定位，改布局会找不到 DNA）
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# 不应进入发布包的文件/目录名（精确名或后缀）
FORBIDDEN_DIRS = {".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache",
                  "target", "node_modules", ".git", "dist"}
FORBIDDEN_FILES = {".env", "polyface.db", "polyface.db-shm", "polyface.db-wal"}
FORBIDDEN_SUFFIXES = (".pyc", ".pyo", ".log", ".db", ".sqlite", ".sqlite3")
# 顶层不应出现的目录（用户数据）
FORBIDDEN_TOP = ("data",)

# 以 `.env` 开头的文件默认全部拦下 —— 用户很可能把真实 Key 写在
# `.env.local` / `.env.production` 里，只列 `.env` 精确名会漏。
# 例外：`.env.example` 是**必须**分发的模板（只有占位符，无真实 Key）。
ENV_PREFIX = ".env"
ENV_ALLOWED = {".env.example"}


def is_env_leak(name: str) -> bool:
    """`.env` 家族里除白名单外一律视为密钥文件。"""
    return name.startswith(ENV_PREFIX) and name not in ENV_ALLOWED

# 要复制的顶层内容
COPY_DIRS = ("python-service", "platform-dna", "scripts", "docs")
COPY_FILES = ("README.md", "LICENSE", "VERSION")
JAR_REL = Path("java-backend/target/polyface.jar")


def read_version(root: Path) -> str:
    f = root / "VERSION"
    if not f.is_file():
        raise FileNotFoundError(f"缺少 VERSION 文件：{f}")
    v = f.read_text(encoding="utf-8").strip()
    if not v:
        raise ValueError("VERSION 文件为空")
    return v


def read_pom_version(root: Path) -> str | None:
    """取 pom.xml 的 `<version>`（第一个，即 project 自身版本）。取不到返回 None。"""
    pom = root / "java-backend" / "pom.xml"
    if not pom.is_file():
        return None
    text = pom.read_text(encoding="utf-8")
    # 去掉 <parent> 段，否则会先匹配到 spring-boot-starter-parent 的版本
    text = re.sub(r"<parent>.*?</parent>", "", text, flags=re.S)
    m = re.search(r"<version>\s*([^<]+?)\s*</version>", text)
    return m.group(1) if m else None


def assert_versions_consistent(root: Path, version: str) -> None:
    """VERSION 是唯一来源；pom.xml 若存在就必须与之一致。

    不加这道断言的话版本号又变成"两处各写一份"，迟早漂移 ——
    项目此前就出现过 Java 0.4.0 / Python 0.3.0 对不上的情况。
    """
    pom_v = read_pom_version(root)
    if pom_v is not None and pom_v != version:
        raise ValueError(
            f"版本号不一致：VERSION={version} 但 java-backend/pom.xml={pom_v}。"
            f"请把 pom.xml 的 <version> 改为 {version}（或改 VERSION），"
            f"两者必须相同。"
        )


def find_forbidden(pkg: Path) -> list[str]:
    """扫描包内不应分发的文件，返回相对路径列表（已排序）。"""
    bad: list[str] = []
    if not pkg.exists():
        return bad
    for p in pkg.rglob("*"):
        rel = p.relative_to(pkg)
        if p.is_dir():
            if p.name in FORBIDDEN_DIRS:
                bad.append(str(rel))
            continue
        if (p.name in FORBIDDEN_FILES or p.name.endswith(FORBIDDEN_SUFFIXES)
                or is_env_leak(p.name)):
            bad.append(str(rel))
    # 顶层数据目录
    for name in FORBIDDEN_TOP:
        if (pkg / name).exists():
            bad.append(name)
    return sorted(set(bad))


def assemble(root: Path, version: str, dist: Path, jar: Path) -> Path:
    """组装 dist/polyface-<version>/ 目录。"""
    pkg = dist / f"polyface-{version}"
    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True)

    shutil.copy2(jar, pkg / "polyface.jar")

    for d in COPY_DIRS:
        src = root / d
        if not src.is_dir():
            raise FileNotFoundError(f"缺少目录：{src}")
        shutil.copytree(src, pkg / d,
                        ignore=shutil.ignore_patterns(*FORBIDDEN_DIRS,
                                                      *FORBIDDEN_FILES,
                                                      "*.pyc", "*.pyo", "*.log"))
    for f in COPY_FILES:
        src = root / f
        if src.is_file():
            shutil.copy2(src, pkg / f)

    _normalize_eol(pkg)
    _prune(pkg)
    return pkg


BAT_SUFFIXES = (".bat", ".cmd")


def _normalize_eol(pkg: Path) -> None:
    """把 Windows 批处理统一成 CRLF。

    为什么必须在打包阶段做，而不是只依赖源码仓库的行尾：
    cmd.exe 解析 **LF 行尾**的批处理时，`goto :label`、多行 `if (...)`
    块、`for /f` 循环都会出错 —— 而这三种结构 `setup/start/stop.bat`
    大量使用。这个故障只在真实执行时暴露，逐行阅读看不出来。

    "工作区是 LF、于是包也是 LF"是真实发生过的：`core.autocrlf=true`
    会掩盖它（新克隆得到 CRLF，但工具直接写出的文件是 LF）。在这里
    兜底后，无论从哪个平台、什么 git 配置打包，产出都一致。
    """
    for p in pkg.rglob("*"):
        if not (p.is_file() and p.name.lower().endswith(BAT_SUFFIXES)):
            continue
        data = p.read_bytes()
        fixed = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        if fixed != data:
            p.write_bytes(fixed)


def _prune(pkg: Path) -> None:
    """兜底清理：ignore_patterns 覆盖不到嵌套情况时再扫一遍。"""
    for p in list(pkg.rglob("*")):
        if p.is_dir() and p.name in FORBIDDEN_DIRS:
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file() and (p.name in FORBIDDEN_FILES
                              or p.name.endswith(FORBIDDEN_SUFFIXES)
                              or is_env_leak(p.name)):
            p.unlink(missing_ok=True)


def make_zip(pkg: Path, dist: Path) -> Path:
    """用标准库压缩（不依赖外部 zip 命令，跨平台）。"""
    target = dist / f"{pkg.name}.zip"
    target.unlink(missing_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pkg.rglob("*")):
            z.write(p, p.relative_to(pkg.parent))
    return target


def maven_cmd(root: Path, override: str | None = None) -> list[str]:
    """决定用哪个命令跑 Maven。

    Windows 上有坑：Git Bash 的 PATH 里 `mvn` 是个 POSIX shell 脚本，
    直接 `subprocess.run(["mvn"])` 会把 POSIX 路径喂给 java，报
    `ClassNotFoundException: org.codehaus.plexus.classworlds.launcher.Launcher`。
    仓库里的 `scripts/mvn.sh` 就是为绕开这个而写的，所以 Windows 上优先用它。
    """
    if override:
        return [override]
    if os.name == "nt":
        wrapper = root / "scripts" / "mvn.sh"
        bash = shutil.which("bash")
        if wrapper.is_file() and bash:
            # ⚠️ 两件事都必须做，缺一个就在 Windows 上必失败（退出码 127）：
            #
            # ① 用 bash 的**绝对路径**，不要写裸 "bash"。
            #    裸名交给 CreateProcess 解析时，`System32\bash.exe`（**WSL bash**）
            #    会先被命中 —— 那是另一个文件系统视图：它看不到 `D:/...`，
            #    还会**吃掉路径里的反斜杠**（实测报错原文为
            #    `/bin/bash: C:UserslwxAppDataLocalTemp...: No such file or directory`）。
            #    `shutil.which("bash")` 拿到的是 PortableGit 的 bash，才有 D: 盘视图。
            #
            # ② 路径用 **POSIX 形式**（`wrapper.as_posix()`）。
            #    `str(wrapper)` 得到 `D:\...`，bash 不认反斜杠形式。
            #
            # 这与 start.sh / setup.sh 里 `native_path()` 属同类问题的**反方向**：
            # 那边是"给原生程序 POSIX→Windows"，这里是"给 bash 程序 Windows→POSIX"。
            return [bash, wrapper.as_posix()]
    return ["mvn"]


def build_jar(root: Path, mvn: str | None = None) -> Path:
    jar = root / JAR_REL
    cmd = maven_cmd(root, mvn)
    # 只有裸命令才需要 which 检查；显式路径（含 bash 包装）直接跑，失败信息更准确
    if len(cmd) == 1 and not shutil.which(cmd[0]):
        raise RuntimeError(f"未找到 Maven（{cmd[0]}）—— 无法构建 jar；"
                           f"可加 --no-build 使用已有 jar")
    print(f"      Maven 命令：{' '.join(cmd)}")
    p = subprocess.run([*cmd, "-q", "-f", str(root / "java-backend" / "pom.xml"),
                        "-DskipTests", "package"], cwd=str(root))
    if p.returncode != 0:
        raise RuntimeError(f"Maven 构建失败（退出码 {p.returncode}）")
    if not jar.is_file():
        raise RuntimeError(f"Maven 成功但未产出 {jar}（检查 pom 的 finalName）")
    return jar


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Polyface Release 打包")
    ap.add_argument("--root", default=None)
    ap.add_argument("--dist", default=None)
    ap.add_argument("--no-build", action="store_true", help="跳过 Maven，用现有 jar")
    ap.add_argument("--mvn", default=None,
                    help="指定 Maven 命令（默认：Windows 用 scripts/mvn.sh，其它用 mvn）")
    ap.add_argument("--check-only", default=None, metavar="DIR",
                    help="只校验指定目录，不打包")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    dist = Path(args.dist).resolve() if args.dist else root / "dist"

    if args.check_only:
        target = Path(args.check_only).resolve()
        bad = find_forbidden(target)
        if bad:
            print("[失败] 目录内有不应分发的内容：")
            for b in bad:
                print(f"         · {b}")
            return 1
        print(f"[OK] {target} 未发现不应分发的内容")
        return 0

    version = read_version(root)
    print(f"=== 打包 Polyface Release {version} ===")

    try:
        assert_versions_consistent(root, version)
        print(f"[OK] 版本一致（VERSION = pom.xml = {version}）")

        jar = root / JAR_REL
        if args.no_build:
            if not jar.is_file():
                raise RuntimeError(f"--no-build 但找不到 {jar}")
            print(f"[OK] 使用现有 jar：{jar}")
        else:
            print("[..] 构建 fat jar（Maven）...")
            jar = build_jar(root, args.mvn)
            print(f"[OK] jar：{jar}")

        print("[..] 组装发布目录...")
        pkg = assemble(root, version, dist, jar)

        bad = find_forbidden(pkg)
        if bad:
            print("[失败] 发布包内出现不应分发的内容，已中止：")
            for b in bad:
                print(f"         · {b}")
            return 1

        print("[..] 压缩...")
        zp = make_zip(pkg, dist)
        size_mb = zp.stat().st_size / 1024 / 1024
        print(f"[OK] {zp}（{size_mb:.1f} MB）")
        print()
        print("发布包结构：")
        print(f"  polyface-{version}/")
        print("    ├── polyface.jar      ← 直接 java -jar，无需 Maven")
        print("    ├── python-service/")
        print("    ├── platform-dna/")
        print("    ├── scripts/          ← setup.bat / start.bat / stop.bat")
        print("    ├── docs/             ← 设计文档（README 引用的编号文档在此）")
        print("    ├── README.md / LICENSE / VERSION")
        print()
        print("用户侧：解压 → scripts\\setup.bat → scripts\\start.bat")
        return 0
    except Exception as e:                      # noqa: BLE001 — CLI 顶层兜底
        print(f"[失败] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
