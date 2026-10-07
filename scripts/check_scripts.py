"""启动脚本静态校验：把"人眼容易漏、机器判定确定"的错误交给机器。

## 为什么需要它

`docs/48` §6 把"`scripts/*.bat` 未实机执行"列为交付时最该补的缺口 ——
本环境的两处工具都硬性拦截 `cmd.exe`，批处理**不可能**被真跑。
而 `docs/48` §5 第 8 项已经证明过一件事：**逐行复核看不出行尾符问题**
（`.bat` 被写成 LF，`core.autocrlf=true` 恰好掩盖了它，直到打包才漏出去）。

同一类"看不出来"的错误还有很多：悬空的 `goto :label`、未闭合的引号、
`%ROOT%` 拼成 `%ROOTT%`、多行 `if (...)` 括号不配平、续行符后多了个空格。
这些**都能机械判定**，不该继续靠人盯。

## 它不做什么（重要）

它**不是**批处理解释器，**不能**替代实机执行。它只降低风险，不消除风险。
交付说明里必须保持这个区分。

## 设计要点

- **只在"生效代码"上跑逐行规则**：注释、heredoc 正文都不算代码。
  否则帮助文本里写一句 `set POLYFACE_MAVEN_HOME=D:\apache-maven-3.9.11` 示例
  就会被判成"硬编码路径"—— 而 `docs/56` §2.2 明令：
  **不允许为了规则好看去改本来正确的脚本**。
- **`.sh` 不查逐行引号**：bash 字符串可以跨行（`die "第一行\n第二行"`）。
  改用 `bash -n` 做真正的语法检查（S7），比数引号强得多。
- **豁免机制**：`check-scripts:allow <RULE> <原因>`。
  没有豁免口子的规则，噪声迟早会被所有人无视。

## 用法

    python scripts/check_scripts.py             # 扫 scripts/ 下的 *.bat / *.sh
    python scripts/check_scripts.py --root DIR
    python scripts/check_scripts.py --strict    # WARN 也算失败

退出码：0 = 无 ERROR；1 = 有 ERROR。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# 常见系统环境变量 —— 引用它们不需要在本脚本内 set，避免误报
KNOWN_ENV = {
    "PATH", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "USERNAME", "USERDOMAIN",
    "COMPUTERNAME", "OS", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "APPDATA",
    "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "COMMONPROGRAMFILES",
    "CD", "ERRORLEVEL", "RANDOM", "DATE", "TIME", "HOMEDRIVE", "HOMEPATH",
    "PROCESSOR_ARCHITECTURE", "PYTHONPATH", "PYTHONHOME", "LANG", "HOME", "PWD",
}

# 破坏性命令（启动脚本不该需要）
DANGEROUS = (
    (r"\bdel\s+/[sq]", "del /s 或 /q（递归/静默删除）"),
    (r"\brd\s+/s", "rd /s（递归删目录）"),
    (r"\brmdir\s+/s", "rmdir /s（递归删目录）"),
    (r"\bformat\s+[a-z]:", "format（格式化）"),
    (r"\brm\s+-rf\s+/", "rm -rf /（递归删根）"),
)

DRIVE_RE = re.compile(r"(?<![A-Za-z0-9_%])[A-Za-z]:[\\/]")
LABEL_DEF_RE = re.compile(r"^\s*:([A-Za-z_][\w]*)", re.M)
GOTO_RE = re.compile(r"\b(?:goto|call)\s+:?([A-Za-z_]\w*)", re.I)
# set "VAR=..." / set VAR=... / set /a VAR=... / set /p VAR=...
SET_RE = re.compile(r'\bset\s+(?:/[ap]\s+)?"?([A-Za-z_]\w*)="?', re.I)
VAR_USE_RE = re.compile(r"%([A-Za-z_]\w*)%")
SHEBANG_RE = re.compile(r"^#!.*\b(?:bash|sh)\b")
# `command -v mvn` / `which mvn` / `where mvn` 不是"裸调用 mvn"
MVN_LOOKUP_RE = re.compile(r"\b(?:command\s+-v|which|where|type)\s+mvn\b")
BARE_MVN_RE = re.compile(r"(^|[\s;&|])mvn\s")

# ---- S8：POSIX 路径被直接交给原生程序（java / python）----------------------
#
# 为什么必须拦：Git Bash(MSYS) 会自动把「看起来像 POSIX 路径」的**参数**翻成
# Windows 形式，但这个自动转换可以被用户关掉（MSYS_NO_PATHCONV=1 /
# MSYS2_ARG_CONV_EXCL=* —— 为了 docker/kubectl 全局设置的人不少）。一旦关掉：
#   · `python /d/x/a.py`      -> 去找 `D:\d\x\a.py`（报"文件不存在"）
#   · `python -m venv /d/x`   -> **静默**建到 `D:\d\x`，且返回 0
# 所以这类路径必须经 `native_path` 显式转换（见 scripts/setup.sh 的注释）。
#
# 刻意保守，只认两类：
#   (a) 命令词是原生程序（java / python / $PY / $VPY），参数是路径形态的变量；
#   (b) 赋值给「会被原生程序消费的环境变量」（见 NATIVE_ENVVARS）。
# bash / cp / mkdir 这些 MSYS 自带工具吃 POSIX 路径，不能误报。
NATIVE_CMD_RE = re.compile(
    r'(?<![\w./-])(?:java|java\.exe|python|python3|python\.exe'
    r'|"\$\{?(?:PY|VPY)\}?"|\$\{?(?:PY|VPY)\}?)\s')
# 路径形态的变量：$ROOT / $JAR_DIR / $VENV_PATH / ${DATA_FILE} ...
# 用捕获组：S8 需要知道「是哪个变量」，才能查它是不是已经转换过了。
# 尾部的 [^"]* 让 `"$ROOT/data"` 这种「变量 + 后缀」也能命中 ——
# 只认「引号里只有一个变量名」会漏掉最常见的那种写法。
PATH_VAR_ARG_RE = re.compile(
    r'"\$\{?([A-Za-z_]*'
    r'(?:DIR|PATH|FILE|JAR|HOME|ROOT|VENV)'
    r'[A-Za-z_]*)\}?[^"]*"')
# S9：原生 Windows 程序的**斜杠参数**（`/FI` `/FO` `/NH` …）会被 Git Bash
# 当成**路径**转换掉（`/FI` → `D:/Git/FI`），程序随即报「无效参数/选项」。
# 与 S8 同源（都是 MSYS 路径转换），区别是：S8 管"路径参数"，S9 管"斜杠参数"。
# 实测（2026-10-07）：`stop.sh` 的 `tasklist /FI` 在**默认 Git Bash** 下必现，
# 只有刻意关掉转换时才碰巧能用 —— 所以藏了很久。
# 修法是给那条命令加 `MSYS2_ARG_CONV_EXCL='*'` 前缀（两种模式都稳，
# 比写 `//FI` 强：`//FI` 只在转换开着时才对）。
NATIVE_SLASH_CMD_RE = re.compile(
    r'(?<![\w./-])(?:tasklist|netstat|wmic|findstr|robocopy|ipconfig|netsh'
    r'|schtasks|attrib|xcopy|reg|sc)\s')
# 形如 /FI、/FO、/NH、/c:、/r 的短斜杠参数；排除路径（后面跟更多斜杠的）
SLASH_FLAG_RE = re.compile(r'(?<![\w/])/[A-Za-z]{1,4}(?=[\s:\"\']|$)')
MSYS_EXCL = "MSYS2_ARG_CONV_EXCL"

# 会被**原生程序**读取的环境变量：值不参与 argv 转换，必须自己转
NATIVE_ENVVARS = ("POLYFACE_DATA_DIR",)
NATIVE_ENVVAR_RE = re.compile(
    r'(?:^|\s|export\s+)(?:' + "|".join(NATIVE_ENVVARS) + r')=')

# heredoc：<<EOF / <<-EOF / <<'EOF' / <<"EOF"
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
# 豁免标记：`# check-scripts:allow RULE 原因`（.sh）/ `rem check-scripts:allow RULE 原因`（.bat）
ALLOW_RE = re.compile(r"check-scripts:allow\s+([A-Z]\d+)\s*(.*)")
BUILTIN_LABELS = {"eof"}

LEVEL_ORDER = {"ERROR": 0, "WARN": 1}


@dataclass(frozen=True)
class Issue:
    rule: str
    level: str
    file: str
    line: int
    message: str

    def render(self) -> str:
        tag = {"ERROR": "[错误]", "WARN": "[警告]"}[self.level]
        loc = f"{self.file}:{self.line}" if self.line else self.file
        return f"  {tag} {self.rule} {loc}  {self.message}"


def read_lines(path: Path) -> list[str]:
    """按通用换行切分（\\r\\n 与 \\n 都吃），供逐行规则使用。

    ⚠️ 行尾符检查**不能**用这个函数 —— 它会把 CRLF 归一化掉，
    那正是我们要检出的东西。行尾符单独走 `check_line_endings`。
    """
    return path.read_bytes().decode("utf-8", errors="replace").splitlines()


def is_comment(line: str, bat: bool) -> bool:
    s = line.strip()
    if bat:
        return s.lower().startswith("rem ") or s.lower() == "rem" or s.startswith("::")
    return s.startswith("#")


def code_line_indices(lines: list[str], bat: bool) -> set[int]:
    """返回**生效代码**行的 1-based 行号集合（排除注释与 heredoc 正文）。

    逐行规则都跑在这个集合上。不这么做的话，帮助文本里的示例路径
    会被判成"硬编码路径"，逼着人把文档改坏。
    """
    code: set[int] = set()
    heredoc_end: str | None = None
    for i, line in enumerate(lines, 1):
        if heredoc_end is not None:
            # 终止符必须单独成行（去空白后完全相等）
            if line.strip() == heredoc_end:
                heredoc_end = None
            continue
        if is_comment(line, bat):
            continue
        code.add(i)
        if not bat:
            m = HEREDOC_RE.search(line)
            if m:
                heredoc_end = m.group(2)
    return code


def collect_allowances(lines: list[str]) -> dict[str, str]:
    """收集豁免声明：规则 ID -> 原因。"""
    out: dict[str, str] = {}
    for line in lines:
        m = ALLOW_RE.search(line)
        if m:
            out[m.group(1)] = m.group(2).strip() or "（未写原因）"
    return out


# ---------------------------------------------------------------- 通用规则

def check_common(path: Path, code: set[int]) -> list[Issue]:
    """G1~G6：`.bat` 与 `.sh` 都适用。逐行规则只看 code。"""
    out: list[Issue] = []
    name = path.name
    data = path.read_bytes()
    lines = read_lines(path)
    is_bat = path.suffix.lower() in (".bat", ".cmd")
    cont = "^" if is_bat else "\\"

    if not data.strip():
        out.append(Issue("G1", "ERROR", name, 0, "文件为空"))
        return out

    if not data.endswith(b"\n"):
        out.append(Issue("G2", "WARN", name, len(lines), "文件末尾缺少换行"))

    for i, line in enumerate(lines, 1):
        if i not in code:
            continue
        # G3 续行符后带尾随空白 —— 续行会失效，且几乎看不出来
        stripped = line.rstrip()
        if stripped != line and stripped.endswith(cont):
            out.append(Issue(
                "G3", "ERROR", name, i,
                f"续行符 {cont} 之后有尾随空白，续行会失效"
                f"（该行以 {len(line) - len(stripped)} 个空白字符结尾）"))

        # G6 引号配对 —— **仅 .bat**。
        # bash 字符串可以跨行（`die "第一行\n第二行"`），逐行数引号必然误报；
        # .sh 的语法正确性交给 S7（bash -n），比数引号强得多。
        if is_bat and line.count('"') % 2:
            out.append(Issue("G6", "ERROR", name, i,
                             f'双引号未闭合（本行有 {line.count(chr(34))} 个）'))

        # G4 硬编码盘符路径
        if DRIVE_RE.search(line):
            out.append(Issue("G4", "ERROR", name, i,
                             "出现硬编码盘符绝对路径 —— 换台机器就会失效"))

        # G5 破坏性命令
        for pat, desc in DANGEROUS:
            if re.search(pat, line, re.I):
                out.append(Issue("G5", "WARN", name, i, f"出现 {desc}"))
    return out


# ---------------------------------------------------------------- 行尾符

def check_line_endings(path: Path) -> list[Issue]:
    """B1 / S1：`.bat` 必须 CRLF，`.sh` 必须 LF。

    这条单独实现，因为 `read_lines()` 会把行尾符吃掉。
    这是本项目**真实发生过**的缺陷（`docs/48` §5 第 8 项）：
    `.bat` 是 LF 时，cmd.exe 解析 `goto`、`for /f`、多行 `if` 会出错。
    """
    name = path.name
    data = path.read_bytes()
    crlf = data.count(b"\r\n")
    bare_lf = data.count(b"\n") - crlf
    bare_cr = data.count(b"\r") - crlf
    out: list[Issue] = []

    if path.suffix.lower() in (".bat", ".cmd"):
        if bare_lf:
            out.append(Issue(
                "B1", "ERROR", name, 0,
                f"有 {bare_lf} 处裸 LF 行尾 —— Windows 批处理必须 CRLF，"
                f"否则 goto / for /f / 多行 if 会出错"))
        if bare_cr:
            out.append(Issue("B1", "ERROR", name, 0, f"有 {bare_cr} 处裸 CR 行尾"))
    elif crlf:
        out.append(Issue(
            "S1", "ERROR", name, 0,
            f"有 {crlf} 处 CRLF 行尾 —— shell 脚本必须 LF，"
            f"否则 shebang 失效、bash script.sh 报错"))
    return out


# ---------------------------------------------------------------- .bat 专属

def _cmd_k_inner_quote(line: str) -> bool:
    """`cmd /k "..."` 的引号参数**内部**是否又套了引号。

    只看 cmd /k 的参数本身，不看整行的引号总数 ——
    `start "标题" /d "目录" cmd /k "简单命令"` 是**推荐**写法（6 个引号），
    按总数判断会把它误报成风险，正好把规则想推的方向判成错。
    """
    idx = line.lower().find("cmd /k")
    if idx < 0:
        return False
    rest = line[idx + len("cmd /k"):].strip()
    if rest.count('"') < 2 or not rest.startswith('"'):
        return False
    return '"' in rest[1:rest.rfind('"')]


def check_bat(path: Path, code: set[int]) -> list[Issue]:
    """B2~B9。"""
    out: list[Issue] = []
    name = path.name
    lines = read_lines(path)
    code_text = "\n".join(l for i, l in enumerate(lines, 1) if i in code)

    # B2/B3/B4 必备指令
    if not lines or lines[0].strip().lower() != "@echo off":
        out.append(Issue("B2", "ERROR", name, 1, "首行应为 @echo off"))
    if "chcp 65001" not in code_text:
        out.append(Issue("B3", "ERROR", name, 0, "缺少 chcp 65001（中文会乱码）"))
    if not re.search(r"^\s*setlocal\b", code_text, re.M | re.I):
        out.append(Issue("B4", "ERROR", name, 0, "缺少 setlocal（会污染调用者环境）"))

    # B5 标签引用必须存在
    defined = {m.group(1).lower() for m in LABEL_DEF_RE.finditer(code_text)}
    for i in sorted(code):
        for m in GOTO_RE.finditer(lines[i - 1]):
            target = m.group(1).lower()
            if target in BUILTIN_LABELS or target in defined:
                continue
            out.append(Issue(
                "B5", "ERROR", name, i,
                f"{m.group(0).strip()} 指向不存在的标签 :{m.group(1)}"
                f"（本文件已定义：{', '.join(sorted(defined)) or '无'}）"))

    # B6 括号配平
    # 先剥掉两类会干扰计数的东西：
    #   ^( ^)   —— 转义的字面括号，如 echo 1^) xxx
    #   %VAR%   —— 变量名里可能带括号，如 %ProgramFiles(x86)%
    sanitized = re.sub(r"\^[()]", "", code_text)
    sanitized = re.sub(r"%[^%\n]*%", "%", sanitized)
    open_n, close_n = sanitized.count("("), sanitized.count(")")
    if open_n != close_n:
        out.append(Issue(
            "B6", "ERROR", name, 0,
            f"括号不配平：{open_n} 个 ( 对 {close_n} 个 )"
            f" —— 多行 if (...) 块会提前结束"))

    # B7 变量引用疑似拼错（只在"既没 set 过、也不是常见环境变量"时报）
    set_vars = {m.group(1).upper() for m in SET_RE.finditer(code_text)}
    for i in sorted(code):
        for m in VAR_USE_RE.finditer(lines[i - 1]):
            var = m.group(1)
            if var.upper() in set_vars or var.upper() in KNOWN_ENV:
                continue
            out.append(Issue(
                "B7", "WARN", name, i,
                f"引用 %{var}% 但本文件从未 set 它，也不是常见环境变量 —— 疑似拼错"))

    # B8 enabledelayedexpansion（只看代码，不看注释）
    if "enabledelayedexpansion" in code_text.lower():
        out.append(Issue("B8", "WARN", name, 0,
                         "启用了 enabledelayedexpansion —— 路径含 ! 时变量会被吞"))

    # B9 cmd /k 参数内部再套引号
    for i in sorted(code):
        if _cmd_k_inner_quote(lines[i - 1]):
            out.append(Issue(
                "B9", "WARN", name, i,
                'cmd /k "..." 的参数内部又套了引号，解析依赖 cmd 的剥离规则；'
                '建议改用 start /d "目录" cmd /k "简单命令"'))
    return out


# ---------------------------------------------------------------- .sh 专属

def find_bash() -> str | None:
    """找一个能用的 bash，**排除 WSL 启动器**。

    Windows 上 `bash` 可能是 `C:\\Windows\\System32\\bash.exe`（WSL 的入口），
    它在受限环境里会被安全策略拦截，而且它面对 Windows 路径的行为与 Git Bash 不同。
    用错这个会把 S7 变成"环境问题导致的假报错"。
    """
    cand = shutil.which("bash")
    if cand:
        low = cand.lower()
        if "system32" not in low and "wsl" not in low:
            return cand
    for p in (r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files\Git\usr\bin\bash.exe",
              "/bin/bash", "/usr/bin/bash"):
        if Path(p).is_file():
            return p
    return None


def converted_vars(lines: list[str], code: set[int]) -> set[str]:
    """文件内「已经是 Windows 形式」的变量名集合。

    S8 要判断"这个变量交给原生程序时是不是 POSIX 形式"，光看名字不够 ——
    `mvn.sh` 的 `$JAR` 拼自 `$MVN_HOME_WIN`，它本来就是 Windows 形式。
    不认识这一点就会误报，而误报会逼着人去改**本来正确**的脚本。

    判定来源（都是本仓库既有的约定）：
      · 赋值右侧出现 native_path / to_win_path / cygpath
      · 变量名以 _WIN 结尾
      · 由上述变量拼接而成（传递闭包，如 JAR="$MVN_HOME_WIN\\boot\\x"）
    """
    conv: set[str] = set()
    assigns: list[tuple[str, str]] = []
    for i in sorted(code):
        line = lines[i - 1].split("#", 1)[0]
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_]\w*)=(.*)", line)
        if not m:
            continue
        var, rhs = m.group(1), m.group(2)
        assigns.append((var, rhs))
        if re.search(r"native_path|to_win_path|cygpath", rhs) or var.endswith("_WIN"):
            conv.add(var)
    for _ in range(3):                       # 传递闭包；脚本里的赋值链很短
        for var, rhs in assigns:
            if var in conv:
                continue
            if any(u in conv for u in re.findall(r"\$\{?([A-Za-z_]\w*)\}?", rhs)):
                conv.add(var)
    return conv


def check_sh(path: Path, code: set[int]) -> list[Issue]:
    """S2~S8。"""
    out: list[Issue] = []
    name = path.name
    lines = read_lines(path)
    code_text = "\n".join(l for i, l in enumerate(lines, 1) if i in code)
    converted = converted_vars(lines, code)

    if not lines or not SHEBANG_RE.match(lines[0].strip()):
        out.append(Issue("S2", "ERROR", name, 1,
                         "首行应为 #!/usr/bin/env bash 或 #!/bin/bash"))

    if not re.search(r"^\s*set\s+-[a-z]*e", code_text, re.M):
        out.append(Issue("S3", "WARN", name, 0,
                         "未设置 set -e（命令失败会被吞掉）"))

    for i in sorted(code):
        c = lines[i - 1].split("#", 1)[0]
        if "`" in c:
            out.append(Issue("S4", "WARN", name, i,
                             "使用反引号命令替换，建议改 $(...)"))
        if BARE_MVN_RE.search(c) and not MVN_LOOKUP_RE.search(c):
            out.append(Issue(
                "S5", "WARN", name, i,
                "裸调用 mvn —— Windows/Git Bash 下会报 "
                "ClassNotFoundException，应走 scripts/mvn.sh"))
        if re.search(r"^\s*cd\s+\.\.", c):
            out.append(Issue("S6", "WARN", name, i,
                             "裸 cd .. 依赖当前目录，建议用绝对路径"))
        # S9：原生程序的斜杠参数被 Git Bash 当路径转换（见常量区说明）
        if (NATIVE_SLASH_CMD_RE.search(c) and SLASH_FLAG_RE.search(c)
                and MSYS_EXCL not in c):
            out.append(Issue(
                "S9", "WARN", name, i,
                "原生 Windows 程序的斜杠参数（/FI /FO …）会被 Git Bash 当成路径转换，"
                "程序会报「无效参数/选项」—— 该行应加前缀 "
                "MSYS2_ARG_CONV_EXCL='*'（比写 //FI 稳：那种写法只在转换开着时才对）"))

        # S8：见文件上方常量区的说明
        if "native_path" not in c:
            unsafe = [v for v in PATH_VAR_ARG_RE.findall(c) if v not in converted]
            why = ""
            if unsafe and NATIVE_CMD_RE.search(c):
                why = f"原生程序（java/python）的路径参数 ${unsafe[0]}"
            elif unsafe and NATIVE_ENVVAR_RE.search(c):
                why = f"会被原生程序读取的环境变量（值来自 ${unsafe[0]}）"
            if why:
                out.append(Issue(
                    "S8", "WARN", name, i,
                    f"{why}是未转换的 POSIX 路径 —— Git Bash 关闭自动路径转换时"
                    f"（MSYS_NO_PATHCONV / MSYS2_ARG_CONV_EXCL）会指到错误位置，"
                    f"应写成 $(native_path \"...\")"))

    # S7 真语法检查（比逐行数引号强得多）
    #
    # 调用方式刻意保守：cwd 切到脚本所在目录、只传文件名。
    # 传完整 Windows 路径时 bash 会因反斜杠与空格产生歧义；
    # 并且**不用 text=True**（中文 Windows 的 locale 是 GBK，
    # bash 的中文报错会让解码抛异常，把"语法错误"变成"环境故障"）。
    bash = find_bash()
    if bash is None:
        out.append(Issue("S7", "WARN", name, 0,
                         "未找到可用的 bash，跳过语法检查（不等于通过）"))
        return out
    try:
        p = subprocess.run([bash, "-n", path.name], cwd=str(path.parent),
                           capture_output=True)
    except OSError as e:
        out.append(Issue("S7", "WARN", name, 0, f"无法运行 bash -n（{e}），跳过检查"))
        return out
    if p.returncode != 0:
        err = (p.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
        detail = err[0] if err else f"退出码 {p.returncode}（无错误输出）"
        out.append(Issue("S7", "ERROR", name, 0, f"bash -n 语法检查未通过：{detail}"))
    return out


# ---------------------------------------------------------------- 汇总

def check_file(path: Path) -> list[Issue]:
    lines = read_lines(path)
    bat = path.suffix.lower() in (".bat", ".cmd")
    code = code_line_indices(lines, bat)

    issues = check_common(path, code) + check_line_endings(path)
    if bat:
        issues += check_bat(path, code)
    elif path.suffix.lower() == ".sh":
        issues += check_sh(path, code)

    allowed = collect_allowances(lines)
    return [i for i in issues if i.rule not in allowed]


def find_scripts(root: Path) -> list[Path]:
    d = root / "scripts" if (root / "scripts").is_dir() else root
    return sorted([*d.glob("*.bat"), *d.glob("*.cmd"), *d.glob("*.sh")])


def check_dir(root: Path) -> tuple[list[Path], list[Issue]]:
    files = find_scripts(root)
    issues: list[Issue] = []
    for f in files:
        issues += check_file(f)
    return files, issues


def render(files: list[Path], issues: list[Issue]) -> str:
    out = [f"检查 {len(files)} 个脚本："]
    for f in files:
        own = [i for i in issues if i.file == f.name]
        errs = sum(1 for i in own if i.level == "ERROR")
        warns = sum(1 for i in own if i.level == "WARN")
        supp = collect_allowances(read_lines(f))
        extra = f"，{len(supp)} 项已显式豁免" if supp else ""
        mark = "[OK  ]" if not errs else "[错误]"
        out.append(f"  {mark} {f.name}（{errs} 错误 / {warns} 警告{extra}）")
    if issues:
        out += ["", "问题明细："]
        for i in sorted(issues, key=lambda x: (LEVEL_ORDER[x.level], x.file, x.line, x.rule)):
            out.append(i.render())
    out.append("")
    if any(i.level == "ERROR" for i in issues):
        out.append("结论：存在错误。注意 —— 静态校验只能发现可机械判定的问题，"
                   "**不能替代实机执行**。")
    elif issues:
        out.append("结论：无错误，但有警告，建议人工确认。")
    else:
        out.append("结论：未发现可机械判定的问题（仍不等于已实机验证）。")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="启动脚本静态校验")
    ap.add_argument("--root", default=None, help="项目根（默认 scripts/ 的上一级）")
    ap.add_argument("--strict", action="store_true", help="把 WARN 也视为失败")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    files, issues = check_dir(root)
    print(render(files, issues))

    if any(i.level == "ERROR" for i in issues):
        return 1
    if args.strict and issues:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
