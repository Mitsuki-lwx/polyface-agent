"""发布包组装与**违禁内容校验**的测试。

为什么这个测试比其它测试更要紧：打包脚本是唯一可能把
「用户的素材库（data/*.db）」「填了真实 Key 的 .env」误发出去的地方。
一旦发出去就是不可撤回的泄露。所以校验逻辑必须可测、且真的会失败。

`scripts/build_release.py` 不是包，用 importlib 按文件路径加载。
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
REPO_ROOT = SCRIPTS.parent


def _load(name: str, filename: str):
    """按路径加载 scripts/ 下的脚本。

    ⚠️ 必须先塞进 `sys.modules` 再 exec：`@dataclass` 会通过
    `sys.modules[cls.__module__]` 回查模块，不注册就会拿到 None 并报
    `AttributeError: 'NoneType' object has no attribute '__dict__'`。
    """
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


br = _load("polyface_build_release", "build_release.py")


# ============================================================ read_version

def test_read_version_ok(tmp_path):
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    assert br.read_version(tmp_path) == "1.2.3"


def test_read_version_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        br.read_version(tmp_path)


def test_read_version_blank(tmp_path):
    (tmp_path / "VERSION").write_text("  \n", encoding="utf-8")
    with pytest.raises(ValueError):
        br.read_version(tmp_path)


# ============================================================ find_forbidden

def test_find_forbidden_clean_package(tmp_path):
    pkg = tmp_path / "polyface-1.0.0"
    (pkg / "python-service" / "app").mkdir(parents=True)
    (pkg / "python-service" / "app" / "main.py").write_text("x", encoding="utf-8")
    (pkg / "polyface.jar").write_bytes(b"jar")
    (pkg / "VERSION").write_text("1.0.0", encoding="utf-8")
    assert br.find_forbidden(pkg) == []


@pytest.mark.parametrize(
    "relpath,kind",
    [
        ("python-service/app/__pycache__", "dir"),
        ("python-service/.venv", "dir"),
        ("java-backend/target", "dir"),
        (".git", "dir"),
        ("python-service/app/main.pyc", "file"),
        ("python-service/.env", "file"),
        ("data/polyface.db", "file"),
        ("python-service/polyface.db", "file"),
        ("python-service/run.log", "file"),
    ],
)
def test_find_forbidden_catches(tmp_path, relpath, kind):
    pkg = tmp_path / "polyface-1.0.0"
    pkg.mkdir()
    target = pkg / relpath
    if kind == "dir":
        target.mkdir(parents=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    bad = br.find_forbidden(pkg)
    assert any(relpath.replace("\\", "/") in b.replace("\\", "/") for b in bad), \
        f"{relpath} 未被识别为违禁内容，实际：{bad}"


@pytest.mark.parametrize(
    "name",
    [".env", ".env.local", ".env.production", ".env.bak", ".env.b73b4f0b"],
)
def test_env_variants_are_forbidden(tmp_path, name):
    """`.env` 家族除 `.env.example` 外都要拦。

    只列 `.env` 精确名会漏：用户把真实 Key 写进 `.env.local` 是很常见的做法，
    而 `.gitignore` 里的 `.env.*` 只防 git，防不住打包脚本。
    """
    pkg = tmp_path / "polyface-1.0.0" / "python-service"
    pkg.mkdir(parents=True)
    (pkg / name).write_text("LLM_API_KEY=sk-real-secret", encoding="utf-8")
    assert br.find_forbidden(pkg.parent) != []


def test_env_example_is_allowed(tmp_path):
    """`.env.example` 必须能进包 —— 用户要照它填 Key。"""
    pkg = tmp_path / "polyface-1.0.0" / "python-service"
    pkg.mkdir(parents=True)
    (pkg / ".env.example").write_text("LLM_API_KEY=\n", encoding="utf-8")
    assert br.find_forbidden(pkg.parent) == []


def test_find_forbidden_catches_top_level_data_dir(tmp_path):
    """用户素材库目录 `data/` 出现在包顶层 —— 最严重的一种，必须拦下。"""
    pkg = tmp_path / "polyface-1.0.0"
    (pkg / "data").mkdir(parents=True)
    (pkg / "data" / "polyface.db").write_bytes(b"sqlite")
    bad = br.find_forbidden(pkg)
    assert "data" in bad


def test_find_forbidden_missing_dir_is_not_an_error(tmp_path):
    assert br.find_forbidden(tmp_path / "nope") == []


# ============================================================ assemble

def _fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "python-service" / "app").mkdir(parents=True)
    (root / "python-service" / "app" / "main.py").write_text("x", encoding="utf-8")
    (root / "python-service" / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")
    (root / "python-service" / ".env.local").write_text("LLM_API_KEY=secret2", encoding="utf-8")
    (root / "python-service" / ".env.example").write_text("LLM_API_KEY=", encoding="utf-8")
    (root / "python-service" / "__pycache__").mkdir()
    (root / "python-service" / "__pycache__" / "main.cpython-313.pyc").write_bytes(b"pyc")
    (root / "platform-dna").mkdir()
    (root / "platform-dna" / "xiaohongshu.yaml").write_text("k: v", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "start.bat").write_text("echo hi", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "08-技术决策记录-ADR.md").write_text("# ADR", encoding="utf-8")
    # 两个 README 都要随包发（英文是默认版，中文版单独一份）
    for f in ("README.md", "README.zh-CN.md", "LICENSE", "VERSION"):
        (root / f).write_text(f"content-{f}", encoding="utf-8")
    (root / "VERSION").write_text("1.0.0", encoding="utf-8")
    # 用户数据：绝不能进包
    (root / "data").mkdir()
    (root / "data" / "polyface.db").write_bytes(b"sqlite")
    jar = root / "java-backend" / "target"
    jar.mkdir(parents=True)
    (jar / "polyface.jar").write_bytes(b"JARBYTES")
    return root


def test_assemble_excludes_secrets_and_caches(tmp_path):
    root = _fake_root(tmp_path)
    pkg = br.assemble(root, "1.0.0", tmp_path / "dist",
                      root / "java-backend" / "target" / "polyface.jar")

    assert (pkg / "polyface.jar").read_bytes() == b"JARBYTES"
    assert (pkg / "VERSION").read_text(encoding="utf-8") == "1.0.0"
    assert (pkg / "platform-dna" / "xiaohongshu.yaml").is_file()
    assert (pkg / "scripts" / "start.bat").is_file()
    assert (pkg / "docs" / "08-技术决策记录-ADR.md").is_file()
    # 关键：密钥与缓存不能出现
    assert not (pkg / "python-service" / ".env").exists()
    assert not (pkg / "python-service" / ".env.local").exists()
    assert not (pkg / "python-service" / "__pycache__").exists()
    assert not (pkg / "data").exists()
    # 但模板必须留着
    assert (pkg / "python-service" / ".env.example").is_file()
    assert br.find_forbidden(pkg) == []


def test_assemble_is_idempotent(tmp_path):
    """重复打包不应因目录已存在而失败（会先删旧目录）。"""
    root = _fake_root(tmp_path)
    jar = root / "java-backend" / "target" / "polyface.jar"
    a = br.assemble(root, "1.0.0", tmp_path / "dist", jar)
    (a / "stale.txt").write_text("leftover", encoding="utf-8")
    b = br.assemble(root, "1.0.0", tmp_path / "dist", jar)
    assert not (b / "stale.txt").exists()


def test_assemble_missing_dir_raises(tmp_path):
    root = _fake_root(tmp_path)
    import shutil

    shutil.rmtree(root / "platform-dna")
    with pytest.raises(FileNotFoundError):
        br.assemble(root, "1.0.0", tmp_path / "dist",
                    root / "java-backend" / "target" / "polyface.jar")


# ============================================================ make_zip

def test_make_zip_has_single_top_level_dir(tmp_path):
    root = _fake_root(tmp_path)
    dist = tmp_path / "dist"
    pkg = br.assemble(root, "1.0.0", dist, root / "java-backend" / "target" / "polyface.jar")
    zp = br.make_zip(pkg, dist)

    assert zp.name == "polyface-1.0.0.zip"
    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
    # 解压后应当只多一层 polyface-1.0.0/，不能把文件散落到当前目录
    tops = {n.split("/")[0] for n in names}
    assert tops == {"polyface-1.0.0"}
    assert any(n.endswith("polyface.jar") for n in names)


def test_make_zip_overwrites_existing(tmp_path):
    root = _fake_root(tmp_path)
    dist = tmp_path / "dist"
    jar = root / "java-backend" / "target" / "polyface.jar"
    pkg = br.assemble(root, "1.0.0", dist, jar)
    first = br.make_zip(pkg, dist)
    first_size = first.stat().st_size
    (pkg / "extra.txt").write_text("x" * 5000, encoding="utf-8")
    second = br.make_zip(pkg, dist)
    assert second.stat().st_size != first_size


# ============================================================ 版本一致性

def _write_pom(root: Path, version: str, *, parent: bool = True) -> None:
    d = root / "java-backend"
    d.mkdir(parents=True, exist_ok=True)
    parent_block = ("<parent><groupId>org.springframework.boot</groupId>"
                    "<artifactId>spring-boot-starter-parent</artifactId>"
                    "<version>3.3.5</version></parent>") if parent else ""
    (d / "pom.xml").write_text(
        f'<?xml version="1.0"?><project>{parent_block}'
        f"<groupId>com.polyface</groupId><artifactId>java-backend</artifactId>"
        f"<version>{version}</version></project>",
        encoding="utf-8")


def test_read_pom_version_skips_parent(tmp_path):
    """必须取 project 自己的 version，不能被 parent 的 3.3.5 抢先匹配。"""
    _write_pom(tmp_path, "0.4.0")
    assert br.read_pom_version(tmp_path) == "0.4.0"


def test_read_pom_version_missing(tmp_path):
    assert br.read_pom_version(tmp_path) is None


def test_assert_versions_consistent_ok(tmp_path):
    _write_pom(tmp_path, "0.4.0")
    br.assert_versions_consistent(tmp_path, "0.4.0")   # 不应抛


def test_assert_versions_consistent_detects_drift(tmp_path):
    """pom 与 VERSION 不一致必须直接失败 —— 版本号只能有一个来源。"""
    _write_pom(tmp_path, "0.1.0")
    with pytest.raises(ValueError) as e:
        br.assert_versions_consistent(tmp_path, "0.4.0")
    assert "0.1.0" in str(e.value) and "0.4.0" in str(e.value)


def test_assert_versions_consistent_without_pom(tmp_path):
    br.assert_versions_consistent(tmp_path, "0.4.0")   # 不应抛


def test_repo_versions_are_consistent():
    """仓库自身必须一致（防止改了 VERSION 忘了改 pom）。"""
    br.assert_versions_consistent(REPO_ROOT, br.read_version(REPO_ROOT))


# ============================================================ maven 命令选择

def test_maven_cmd_honours_override(tmp_path):
    """显式 --mvn 覆盖时原样返回。"""
    assert br.maven_cmd(tmp_path, "/opt/mvn/bin/mvn") == ["/opt/mvn/bin/mvn"]


def test_maven_cmd_falls_back_when_no_wrapper(tmp_path):
    """没有 mvn.sh 时回退到裸 mvn。"""
    assert br.maven_cmd(tmp_path) == ["mvn"]


@pytest.mark.skipif(os.name != "nt", reason="Windows 专有分支")
def test_maven_cmd_uses_which_bash_not_bare_name():
    """回归①：必须用 `which("bash")` 的**绝对路径**，不能写裸 `"bash"`。

    裸名交给 CreateProcess 解析时，`System32\\bash.exe`（**WSL bash**）会先被命中。
    那是另一个文件系统视图：看不到 `D:/...`，还会**吃掉路径里的反斜杠**。
    实测报错原文：`/bin/bash: C:UserslwxAppDataLocalTemp...: No such file or directory`。

    这一条是"仅改路径形式不够"的证据 —— 只把 `D:\\` 换成 `D:/` 仍然失败。
    """
    if not shutil.which("bash"):
        pytest.skip("本机无 bash")
    cmd = br.maven_cmd(REPO_ROOT)
    assert cmd[0] == shutil.which("bash"), \
        f"必须用 which() 的绝对路径（裸 'bash' 会落到 WSL bash），实际 {cmd[0]!r}"


@pytest.mark.skipif(os.name != "nt", reason="Windows 专有分支")
def test_maven_cmd_gives_bash_a_posix_path():
    """回归②：交给 bash 的脚本路径必须是 POSIX 形式（不能用 `D:\\...`）。"""
    if not shutil.which("bash"):
        pytest.skip("本机无 bash")
    cmd = br.maven_cmd(REPO_ROOT)
    assert "mvn.sh" in cmd[1], f"应指向仓库自带的 mvn.sh，实际 {cmd}"
    assert "\\" not in cmd[1], f"bash 收到反斜杠路径会解析失败：{cmd[1]}"


@pytest.mark.skipif(os.name != "nt", reason="Windows 专有分支")
def test_maven_cmd_path_is_actually_resolvable_by_bash():
    """回归③（最关键）：让**将要被执行的那个 bash** 亲自解析该路径。

    前两条只检查"命令长什么样"，这条才证明"真的能跑"——
    正是它在上一步抓出了"只改路径形式还不够"。
    """
    if not shutil.which("bash"):
        pytest.skip("本机无 bash")
    cmd = br.maven_cmd(REPO_ROOT)
    # 关键：用 cmd[0]（即将被执行的同一个 bash），而不是另找一个
    r = subprocess.run([cmd[0], "-c", 'test -f "$1" && echo OK || echo MISSING', "_", cmd[1]],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "OK" in r.stdout, \
        f"{cmd[0]} 无法解析 {cmd[1]}（stdout={r.stdout.strip()[:80]} stderr={r.stderr.strip()[:120]}）"


# ============================================================ 行尾符

def test_normalize_eol_fixes_bat(tmp_path):
    """LF 的 .bat 必须被转成 CRLF，且内容不变。"""
    (tmp_path / "setup.bat").write_bytes(b"@echo off\necho hi\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "STOP.BAT").write_bytes(b"@echo off\necho bye\n")
    br._normalize_eol(tmp_path)
    assert (tmp_path / "setup.bat").read_bytes() == b"@echo off\r\necho hi\r\n"
    assert (tmp_path / "sub" / "STOP.BAT").read_bytes() == b"@echo off\r\necho bye\r\n"


def test_normalize_eol_is_idempotent(tmp_path):
    """已经 CRLF 的文件不得被改成 CRCRLF。"""
    p = tmp_path / "a.bat"
    p.write_bytes(b"@echo off\r\necho hi\r\n")
    br._normalize_eol(tmp_path)
    assert p.read_bytes() == b"@echo off\r\necho hi\r\n"
    br._normalize_eol(tmp_path)
    assert p.read_bytes() == b"@echo off\r\necho hi\r\n"


def test_normalize_eol_leaves_sh_alone(tmp_path):
    """`.sh` 必须保持 LF（CRLF 会让 shebang 失效）。"""
    p = tmp_path / "run.sh"
    p.write_bytes(b"#!/bin/sh\necho hi\n")
    br._normalize_eol(tmp_path)
    assert p.read_bytes() == b"#!/bin/sh\necho hi\n"


def test_repo_bat_files_are_crlf():
    """仓库里的 `.bat` 必须是 CRLF —— 打包时虽有兜底，源码也不该是错的。"""
    bad = []
    for p in SCRIPTS.rglob("*"):
        if p.is_file() and p.name.lower().endswith(br.BAT_SUFFIXES):
            data = p.read_bytes()
            if data.count(b"\n") != data.count(b"\r\n"):
                bad.append(p.name)
    assert not bad, f"这些 .bat 含 LF 行尾（cmd.exe 解析会出错）：{bad}"


def test_repo_bat_files_have_no_bom():
    """`.bat` 不能带 BOM：BOM 会被 cmd.exe 当成第一个命令的一部分。"""
    bad = [p.name for p in SCRIPTS.rglob("*.bat")
           if p.is_file() and p.read_bytes().startswith(b"\xef\xbb\xbf")]
    assert not bad, f"这些 .bat 带 UTF-8 BOM：{bad}"


def test_repo_bat_files_switch_codepage():
    """含中文的 `.bat` 必须 `chcp 65001`，否则中文 Windows 下输出全是乱码。

    这正是 `docs/48` §6 里"批处理未实机执行"最可能踩的坑：
    提示信息乱码会让"出问题能自诊断"这个卖点直接失效。
    """
    bad = []
    for p in SCRIPTS.rglob("*.bat"):
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        if any(ord(c) > 127 for c in text) and "chcp 65001" not in text:
            bad.append(p.name)
    assert not bad, f"这些 .bat 含中文却没有 chcp 65001（会乱码）：{bad}"


# ============================================================ 真实仓库自检

# 字节码缓存是「跑一次 Python 就会产生」的正常副产物，且 assemble() → _prune()
# 会在组装发布包时删除它们（find_forbidden() 对**发布包**仍然零容忍）。
# 因此仓库自检必须容忍它们，否则本测试会因「加载 build_release 模块」这一
# 动作本身而必然失败 —— 一个自污染的假告警，比没有测试更糟：
# 红灯常亮会训练所有人忽略红灯。
BYTECODE_DIRS = {"__pycache__"}
BYTECODE_SUFFIXES = (".pyc", ".pyo")


def scripts_dir_violations(root: Path) -> list[str]:
    """列出 `root` 下的敏感/异常文件名。

    与 `br.find_forbidden()` 的唯一差别：容忍字节码缓存（见上方注释）。
    其它一律同源 —— 包括 `.env` 家族（`is_env_leak`），因为密钥文件
    误放进 `scripts/` 是真会随包分发的风险。
    """
    bad: list[str] = []
    for p in root.rglob("*"):
        if p.is_dir():
            if p.name in br.FORBIDDEN_DIRS and p.name not in BYTECODE_DIRS:
                bad.append(p.name)
            continue
        if p.name.endswith(BYTECODE_SUFFIXES):
            continue
        if (p.name in br.FORBIDDEN_FILES
                or p.name.endswith(br.FORBIDDEN_SUFFIXES)
                or br.is_env_leak(p.name)):
            bad.append(p.name)
    return sorted(set(bad))


def test_scripts_dir_violations_catches_secret(tmp_path):
    """正向：`.env` 家族（含 `.env.local`）必须被抓。"""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / ".env.local").write_text("KEY=real", encoding="utf-8")
    (tmp_path / "scripts" / "polyface.db").write_bytes(b"sqlite")
    assert scripts_dir_violations(tmp_path) == [".env.local", "polyface.db"]


def test_scripts_dir_violations_tolerates_bytecode(tmp_path):
    """反向：字节码缓存不得触发告警（否则测试自污染）。"""
    d = tmp_path / "scripts" / "__pycache__"
    d.mkdir(parents=True)
    (d / "build_release.cpython-313.pyc").write_bytes(b"\x00")
    assert scripts_dir_violations(tmp_path) == []


def test_scripts_dir_violations_still_flags_venv(tmp_path):
    """反向边界：只放过字节码，`.venv` 之类仍要抓。"""
    (tmp_path / "scripts" / ".venv").mkdir(parents=True)
    assert scripts_dir_violations(tmp_path) == [".venv"]


def test_repo_scripts_dir_is_clean():
    """仓库 `scripts/` 里不应残留敏感文件（密钥、数据库、虚拟环境）。

    这是提交前的自检，不是发布包的判据 —— 发布包的判据由
    `find_forbidden()`（零容忍，含字节码）在打包流程中执行。
    """
    bad = scripts_dir_violations(SCRIPTS)
    assert not bad, f"scripts/ 下有敏感/异常文件：{bad}"
