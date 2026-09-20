"""启动脚本静态校验器的测试。

两条主线：

1. **每条规则都要有反例** —— 只测"好脚本通过"等于没测。
2. **对真实脚本零误报** —— 这是最要紧的一条。校验器误报会逼着人
   去改本来正确的脚本（`docs/56` §2.2 明令禁止），最后没人再信它。

`scripts/check_scripts.py` 不是包，用 importlib 按文件路径加载。
"""
from __future__ import annotations

import importlib.util
import sys
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


cs = _load("polyface_check_scripts", "check_scripts.py")


# ---------------------------------------------------------------- 工具

GOOD_BAT = "@echo off\nchcp 65001 >nul\nsetlocal\necho hi\n"
GOOD_SH = "#!/usr/bin/env bash\nset -euo pipefail\necho hi\n"


def write_script(tmp_path: Path, name: str, body: str, *, crlf: bool) -> Path:
    """按指定行尾符写脚本。行尾符必须显式控制 —— 它是被检项之一。"""
    data = body.encode("utf-8")
    if crlf:
        data = data.replace(b"\n", b"\r\n")
    p = tmp_path / name
    p.write_bytes(data)
    return p


def rules(issues) -> set[str]:
    return {i.rule for i in issues}


def check(tmp_path: Path, name: str, body: str, *, crlf: bool | None = None):
    """默认按扩展名给正确行尾符，避免每条规则测试都被 B1/S1 干扰。"""
    if crlf is None:
        crlf = name.endswith((".bat", ".cmd"))
    return cs.check_file(write_script(tmp_path, name, body, crlf=crlf))


# ---------------------------------------------------------------- 正例

def test_good_bat_has_no_issues(tmp_path):
    assert check(tmp_path, "ok.bat", GOOD_BAT) == []


def test_good_sh_has_no_issues(tmp_path):
    assert check(tmp_path, "ok.sh", GOOD_SH) == []


# ---------------------------------------------------------------- "是否生效代码"

def test_drive_path_in_comment_is_not_flagged(tmp_path):
    """注释里的示例路径不是"生效的硬编码路径"。

    真实场景：`mvn.sh` 的帮助文本里会写
    `set POLYFACE_MAVEN_HOME=D:\\apache-maven-3.9.11` 作为示例 ——
    那是给用户看的文档，不是脚本行为。误报会逼着人删掉有用的帮助文本。
    """
    body = GOOD_SH + "# 例如：export POLYFACE_MAVEN_HOME=D:\\apache-maven-3.9.11\n"
    assert "G4" not in rules(check(tmp_path, "c.sh", body))


def test_drive_path_in_heredoc_is_not_flagged(tmp_path):
    """heredoc 正文是输出给用户看的文本，同样不算生效代码。"""
    body = GOOD_SH + 'cat <<\'EOF\'\n  set POLYFACE_MAVEN_HOME=D:\\apache-maven-3.9.11\nEOF\n'
    assert "G4" not in rules(check(tmp_path, "h.sh", body))


def test_drive_path_in_code_is_flagged(tmp_path):
    body = GOOD_SH + 'MVN_HOME="D:\\apache-maven-3.9.11"\n'
    assert "G4" in rules(check(tmp_path, "d.sh", body))


def test_rem_comment_not_treated_as_code_in_bat(tmp_path):
    """`.bat` 里 `rem` 行提到 enabledelayedexpansion 不能算"用了它"。

    真实场景：`setup.bat` 有一行 `rem 注：不使用 enabledelayedexpansion`。
    """
    body = GOOD_BAT + "rem 注：不使用 enabledelayedexpansion —— 避免路径含 ! 时被吞\n"
    assert "B8" not in rules(check(tmp_path, "r.bat", body))


# ---------------------------------------------------------------- 通用规则

def test_g1_empty(tmp_path):
    assert "G1" in rules(check(tmp_path, "e.bat", "\n"))


def test_g2_missing_trailing_newline(tmp_path):
    assert "G2" in rules(check(tmp_path, "t.bat", "@echo off\nchcp 65001 >nul\nsetlocal\necho hi"))


def test_g3_continuation_with_trailing_space(tmp_path):
    """续行符后带尾随空白 —— 续行失效，且肉眼几乎看不出来。"""
    bat = "@echo off\nchcp 65001 >nul\nsetlocal\necho a ^ \necho b\n"
    assert "G3" in rules(check(tmp_path, "g.bat", bat))
    sh = "#!/usr/bin/env bash\nset -euo pipefail\necho a \\ \necho b\n"
    assert "G3" in rules(check(tmp_path, "g.sh", sh))


def test_g3_clean_continuation_ok(tmp_path):
    sh = "#!/usr/bin/env bash\nset -euo pipefail\necho a \\\necho b\n"
    assert "G3" not in rules(check(tmp_path, "g2.sh", sh))


def test_g5_dangerous_command(tmp_path):
    assert "G5" in rules(check(tmp_path, "x.bat", GOOD_BAT + "del /s /q *.*\n"))


def test_g6_unclosed_quote_in_bat(tmp_path):
    assert "G6" in rules(check(tmp_path, "q.bat", GOOD_BAT + 'echo "unclosed\n'))


def test_g6_not_applied_to_sh_multiline_string(tmp_path):
    """bash 字符串可以跨行 —— 逐行数引号必然误报，所以 .sh 不查 G6。

    真实场景：`mvn.sh` 的 `die "第一行\\n第二行"` 就是跨行的。
    """
    body = '#!/usr/bin/env bash\nset -euo pipefail\ndie "第一行\n第二行"\n'
    issues = check(tmp_path, "m.sh", body)
    assert "G6" not in rules(issues)
    # 而 bash -n 认为它是合法的（不应报 S7）
    assert "S7" not in rules(issues)


# ---------------------------------------------------------------- 行尾符

def test_b1_bat_must_be_crlf(tmp_path):
    assert "B1" in rules(check(tmp_path, "l.bat", GOOD_BAT, crlf=False))


def test_b1_bat_crlf_ok(tmp_path):
    assert "B1" not in rules(check(tmp_path, "l2.bat", GOOD_BAT, crlf=True))


def test_s1_sh_must_be_lf(tmp_path):
    assert "S1" in rules(check(tmp_path, "c.sh", GOOD_SH, crlf=True))


# ---------------------------------------------------------------- .bat 专属

@pytest.mark.parametrize(
    "body,rule",
    [
        ("echo hi\n", "B2"),                                    # 缺 @echo off
        ("@echo off\nsetlocal\necho hi\n", "B3"),               # 缺 chcp 65001
        ("@echo off\nchcp 65001 >nul\necho hi\n", "B4"),        # 缺 setlocal
    ],
)
def test_bat_required_directives(tmp_path, body, rule):
    assert rule in rules(check(tmp_path, "m.bat", body))


def test_b5_dangling_goto(tmp_path):
    body = GOOD_BAT + "goto :nowhere\n"
    issues = check(tmp_path, "g.bat", body)
    assert "B5" in rules(issues)
    assert any("nowhere" in i.message for i in issues)


def test_b5_existing_label_ok(tmp_path):
    body = GOOD_BAT + "goto :done\n:done\necho ok\n"
    assert "B5" not in rules(check(tmp_path, "g2.bat", body))


def test_b5_goto_eof_is_builtin(tmp_path):
    """`goto :eof` 是 cmd 内建目标，不要求本文件定义。"""
    body = GOOD_BAT + "call :sub\nexit /b 0\n:sub\ngoto :eof\n"
    assert "B5" not in rules(check(tmp_path, "g3.bat", body))


def test_b6_unbalanced_parens(tmp_path):
    body = GOOD_BAT + "if exist x (\n  echo found\n"
    assert "B6" in rules(check(tmp_path, "p.bat", body))


def test_b6_escaped_parens_and_var_parens_ok(tmp_path):
    """转义括号 `^)` 与变量名里的括号 `%ProgramFiles(x86)%` 不参与配平计数。"""
    body = GOOD_BAT + "echo 1^) 安装 Maven\necho %ProgramFiles(x86)%\n"
    assert "B6" not in rules(check(tmp_path, "p2.bat", body))


def test_b7_typo_variable(tmp_path):
    body = GOOD_BAT + 'set "ROOT=C:\\x"\necho %ROOTT%\n'
    issues = check(tmp_path, "v.bat", body)
    assert "B7" in rules(issues)
    assert any("ROOTT" in i.message for i in issues)


def test_b7_set_a_variable_is_recognised(tmp_path):
    """`set /a WAITED=0` 也是 set —— 不认它会把 %WAITED% 误报成拼错。

    真实场景：`start.bat` 用 `set /a WAITED=0` 做轮询计数。
    """
    body = GOOD_BAT + "set /a N=0\nset /a N+=1\necho %N%\n"
    assert "B7" not in rules(check(tmp_path, "v2.bat", body))


def test_b7_known_env_var_ok(tmp_path):
    assert "B7" not in rules(check(tmp_path, "v3.bat", GOOD_BAT + "echo %PATH%\n"))


def test_b9_nested_quotes_in_cmd_k(tmp_path):
    body = GOOD_BAT + 'start "t" cmd /k "cd /d "C:\\x" && echo hi"\n'
    assert "B9" in rules(check(tmp_path, "n.bat", body))


def test_b9_recommended_start_d_pattern_ok(tmp_path):
    """`start "标题" /d "目录" cmd /k "简单命令"` 是**推荐**写法。

    整行有 6 个引号，但 cmd /k 的参数内部没有引号 —— 按整行引号总数判断
    会把规则想推的方向判成错，这正是误报的典型来源。
    """
    body = GOOD_BAT + 'start "t" /d "C:\\x" cmd /k "echo hi"\n'
    assert "B9" not in rules(check(tmp_path, "n2.bat", body))


# ---------------------------------------------------------------- .sh 专属

def test_s2_missing_shebang(tmp_path):
    assert "S2" in rules(check(tmp_path, "s.sh", "set -euo pipefail\necho hi\n"))


def test_s2_env_shebang_ok(tmp_path):
    assert "S2" not in rules(check(tmp_path, "s2.sh", "#!/usr/bin/env bash\necho hi\n"))


def test_s2_bin_sh_shebang_ok(tmp_path):
    assert "S2" not in rules(check(tmp_path, "s3.sh", "#!/bin/sh\necho hi\n"))


def test_s3_missing_set_e(tmp_path):
    assert "S3" in rules(check(tmp_path, "s4.sh", "#!/usr/bin/env bash\necho hi\n"))


def test_s4_backticks(tmp_path):
    body = "#!/usr/bin/env bash\nset -euo pipefail\nX=`date`\n"
    assert "S4" in rules(check(tmp_path, "s5.sh", body))


def test_s5_bare_mvn(tmp_path):
    body = "#!/usr/bin/env bash\nset -euo pipefail\nmvn -q test\n"
    assert "S5" in rules(check(tmp_path, "s6.sh", body))


@pytest.mark.parametrize("line", [
    'mvn_bin="$(command -v mvn 2>/dev/null || true)"',
    'p="$(which mvn)"',
    'x="$(where mvn)"',
])
def test_s5_lookup_is_not_a_call(tmp_path, line):
    """`command -v mvn` / `which mvn` 是**查**mvn，不是裸调用它。

    真实场景：`mvn.sh` 自己就要反推 mvn 位置。
    """
    body = f"#!/usr/bin/env bash\nset -euo pipefail\n{line}\n"
    assert "S5" not in rules(check(tmp_path, "s7.sh", body))


def test_s6_bare_cd_parent(tmp_path):
    body = "#!/usr/bin/env bash\nset -euo pipefail\ncd ..\necho hi\n"
    assert "S6" in rules(check(tmp_path, "s8.sh", body))


def test_s7_syntax_error_detected(tmp_path):
    """真语法检查：`if` 缺 `then` 必须被检出。

    这比"逐行数引号"强得多 —— bash 的语法规则不是数引号能覆盖的。
    """
    body = "#!/usr/bin/env bash\nset -euo pipefail\nif true\n  echo hi\nfi\n"
    issues = check(tmp_path, "s9.sh", body)
    assert "S7" in rules(issues)
    assert all(i.level == "ERROR" for i in issues if i.rule == "S7")


def test_s7_clean_script_passes(tmp_path):
    assert "S7" not in rules(check(tmp_path, "s10.sh", GOOD_SH))


# ---------------------------------------------------------------- S8（POSIX 路径交给原生程序）

def test_s8_posix_path_to_native_program_is_flagged(tmp_path):
    """`python -m venv /d/x` 在关闭 MSYS 路径转换时会**静默**建到 `D:\\d\\x`。

    这是实机验证中真实踩到的缺陷：`python -m venv` 返回 0，脚本却找不到 venv，
    于是报出「创建成功但找不到 python」这种让人摸不着头脑的错。
    """
    body = (GOOD_SH
            + 'ROOT="/x"\n'
            + 'VENV_DIR="$ROOT/python-service/.venv"\n'
            + '"$PY" -m venv "$VENV_DIR"\n')
    assert "S8" in rules(check(tmp_path, "s8a.sh", body))


def test_s8_native_path_wrapped_is_ok(tmp_path):
    body = (GOOD_SH
            + 'ROOT="/x"\n'
            + 'VENV_DIR="$ROOT/python-service/.venv"\n'
            + '"$PY" -m venv "$(native_path "$VENV_DIR")"\n')
    assert "S8" not in rules(check(tmp_path, "s8b.sh", body))


def test_s8_native_envvar_is_flagged(tmp_path):
    """环境变量的值会交给 Java（原生程序）—— 必须是 Windows 形式。"""
    body = (GOOD_SH
            + 'ROOT="/x"\n'
            + 'export POLYFACE_DATA_DIR="$ROOT/data"\n')
    assert "S8" in rules(check(tmp_path, "s8c.sh", body))


def test_s8_native_envvar_wrapped_is_ok(tmp_path):
    body = (GOOD_SH
            + 'ROOT="/x"\n'
            + 'export POLYFACE_DATA_DIR="$(native_path "$ROOT/data")"\n')
    assert "S8" not in rules(check(tmp_path, "s8d.sh", body))


def test_s8_already_windows_variable_is_not_flagged(tmp_path):
    """**误报守卫** —— 对应真实 `mvn.sh` 的写法。

    `mvn.sh` 里 `$JAR` 拼自 `$MVN_HOME_WIN`，它本来就是 Windows 形式。
    不认识这个来源就会误报，而误报会逼着人去改本来正确的脚本。
    """
    body = (GOOD_SH
            + 'H="/opt/maven"\n'
            + 'MVN_HOME_WIN="$(to_win_path "$H")"\n'
            + 'JAR="$MVN_HOME_WIN\\\\boot\\\\x.jar"\n'
            + 'exec java -classpath "$JAR" org.example.Main\n')
    assert "S8" not in rules(check(tmp_path, "s8e.sh", body))


def test_s8_msys_tools_are_not_flagged(tmp_path):
    """bash / cp / mkdir 是 MSYS 自带工具，吃 POSIX 路径 —— 不能误报。"""
    body = (GOOD_SH
            + 'ROOT="/x"\n'
            + 'LOG_DIR="$ROOT/logs"\n'
            + 'mkdir -p "$LOG_DIR"\n'
            + 'cp "$ROOT/a" "$ROOT/b"\n')
    assert "S8" not in rules(check(tmp_path, "s8f.sh", body))


# ---------------------------------------------------------------- 豁免机制

def test_allowance_suppresses_rule(tmp_path):
    body = "#!/usr/bin/env bash\n# check-scripts:allow S3 刻意不用 set -e\necho hi\n"
    assert "S3" not in rules(check(tmp_path, "a.sh", body))


def test_allowance_is_rule_specific(tmp_path):
    """豁免只对指定规则生效，不能顺带放过别的。"""
    body = ("#!/usr/bin/env bash\n"
            "# check-scripts:allow S3 刻意不用 set -e\n"
            "cd ..\n")
    r = rules(check(tmp_path, "a2.sh", body))
    assert "S3" not in r
    assert "S6" in r


def test_allowance_recorded_even_without_reason(tmp_path):
    lines = ["#!/usr/bin/env bash", "# check-scripts:allow S3", "echo hi"]
    assert cs.collect_allowances(lines)["S3"] == "（未写原因）"


# ---------------------------------------------------------------- 退出码

def test_main_returns_zero_for_clean_dir(tmp_path):
    (tmp_path / "scripts").mkdir()
    write_script(tmp_path / "scripts", "a.bat", GOOD_BAT, crlf=True)
    write_script(tmp_path / "scripts", "b.sh", GOOD_SH, crlf=False)
    assert cs.main(["--root", str(tmp_path)]) == 0


def test_main_returns_one_on_error(tmp_path, capsys):
    (tmp_path / "scripts").mkdir()
    write_script(tmp_path / "scripts", "bad.bat", GOOD_BAT, crlf=False)  # LF -> B1
    assert cs.main(["--root", str(tmp_path)]) == 1
    assert "B1" in capsys.readouterr().out


def test_main_strict_fails_on_warning_only(tmp_path):
    (tmp_path / "scripts").mkdir()
    # S3 缺 set -e 只是 WARN
    write_script(tmp_path / "scripts", "w.sh", "#!/usr/bin/env bash\necho hi\n", crlf=False)
    assert cs.main(["--root", str(tmp_path)]) == 0
    assert cs.main(["--root", str(tmp_path), "--strict"]) == 1


# ---------------------------------------------------------------- 真实仓库（最要紧）

def test_finds_all_repo_scripts():
    names = {p.name for p in cs.find_scripts(REPO_ROOT)}
    for expected in ("setup.bat", "start.bat", "stop.bat",
                     "setup.sh", "start.sh", "stop.sh", "mvn.sh"):
        assert expected in names, f"未发现 scripts/{expected}"


def test_repo_scripts_have_zero_issues():
    """**零误报守卫**。

    校验器一旦对真实脚本误报，就会逼着人去改本来正确的脚本 ——
    那比不检查更糟。这条测试把"零误报"钉死；若新增规则导致这里变红，
    应当修正规则或降级为 WARN，而不是改脚本。
    """
    files, issues = cs.check_dir(REPO_ROOT)
    assert files, "没找到任何脚本，测试本身失效"
    assert issues == [], (
        "校验器对真实脚本产生了问题（这多半是规则误报，应改规则而不是改脚本）：\n"
        + "\n".join(i.render() for i in issues)
    )


def test_repo_scripts_have_no_errors_even_in_strict_mode():
    """真实脚本连 WARN 都不该有 —— 有的话说明规则噪声大。"""
    _, issues = cs.check_dir(REPO_ROOT)
    warns = [i for i in issues if i.level == "WARN"]
    assert warns == [], "\n".join(i.render() for i in warns)
