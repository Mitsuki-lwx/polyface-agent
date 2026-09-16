"""启动前体检（`scripts/doctor.py`）的测试。

**为什么测它**：启动脚本的探测逻辑原本写在批处理里 —— 不可测、易错。
现改为「批处理只找 Python，逻辑全在 doctor.py」，于是这里可以真正验证。

覆盖三类容易出错的地方：
1. **版本号解析**：`java -version` 的输出格式随厂商/年代变化（`1.8.0_301` vs `21.0.7`）
2. **jar 查找顺序**：发布包布局 vs 开发布局，找错就会"明明有 jar 却说没有"
3. **LLM 模式判定**：必须与 ADR-017 的数据边界一致，不能把"回退 mock"报成"真实生成"
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

DOCTOR_PATH = Path(__file__).resolve().parents[2] / "scripts" / "doctor.py"


def _load_doctor():
    """从 scripts/ 加载 doctor.py。

    必须先注册进 sys.modules —— 否则 `@dataclass` 解析注解时
    查 `sys.modules[cls.__module__]` 会拿到 None。
    """
    name = "polyface_doctor"
    spec = importlib.util.spec_from_file_location(name, DOCTOR_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


doctor = _load_doctor()


# ---------------------------------------------------------------- Java 版本解析

@pytest.mark.parametrize("output,expected", [
    ('openjdk version "21.0.7" 2025-04-15 LTS', 21),
    ('openjdk version "17.0.11" 2024-04-16', 17),
    ('java version "1.8.0_301"', 8),
    ('java version "11.0.20" 2023-08-17 LTS', 11),
    ('openjdk version "23-ea" 2024-09-17', 23),
    ('java version "24"', 24),
    ('Picked up JAVA_TOOL_OPTIONS: -Xmx2g\nopenjdk version "21.0.7"', 21),
])
def test_parse_java_major(output, expected):
    assert doctor.parse_java_major(output) == expected


@pytest.mark.parametrize("output", ["", "not a version line", "command not found"])
def test_parse_java_major_returns_none_when_unparseable(output):
    """解析不出必须返回 None —— 不能猜一个版本号糊弄过去。"""
    assert doctor.parse_java_major(output) is None


def test_parse_java_major_does_not_confuse_18_with_1_8():
    """回归：'1.8' 是 Java 8，不是 Java 1；'18' 就是 Java 18。"""
    assert doctor.parse_java_major('java version "1.8.0_301"') == 8
    assert doctor.parse_java_major('java version "18.0.2"') == 18


# ---------------------------------------------------------------- Python 版本解析

@pytest.mark.parametrize("output,expected", [
    ("Python 3.13.14", (3, 13)),
    ("Python 3.11.0", (3, 11)),
    ("Python 3.9.13", (3, 9)),
])
def test_parse_python_version(output, expected):
    assert doctor.parse_python_version(output) == expected


def test_parse_python_version_returns_none_when_unparseable():
    assert doctor.parse_python_version("no version here") is None


def test_version_ok_boundaries():
    assert doctor.version_ok((3, 11), (3, 11)) is True, "等于下限应通过"
    assert doctor.version_ok((3, 10), (3, 11)) is False
    assert doctor.version_ok((3, 12), (3, 11)) is True
    assert doctor.version_ok(None, (3, 11)) is False, "解析不出不得当成通过"


# ---------------------------------------------------------------- jar 查找

def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_find_jar_prefers_release_layout(tmp_path):
    """发布包布局优先：<root>/polyface.jar"""
    rel = _touch(tmp_path / "polyface.jar")
    _touch(tmp_path / "java-backend" / "target" / "polyface.jar")
    assert doctor.find_jar(tmp_path) == rel


def test_find_jar_falls_back_to_dev_layout(tmp_path):
    dev = _touch(tmp_path / "java-backend" / "target" / "polyface.jar")
    assert doctor.find_jar(tmp_path) == dev


def test_find_jar_returns_none_when_absent(tmp_path):
    assert doctor.find_jar(tmp_path) is None


# ---------------------------------------------------------------- venv 查找

def test_find_venv_python_windows_layout(tmp_path):
    p = _touch(tmp_path / "python-service" / ".venv" / "Scripts" / "python.exe")
    assert doctor.find_venv_python(tmp_path) == p


def test_find_venv_python_posix_layout(tmp_path):
    p = _touch(tmp_path / "python-service" / ".venv" / "bin" / "python")
    assert doctor.find_venv_python(tmp_path) == p


def test_find_venv_python_none(tmp_path):
    assert doctor.find_venv_python(tmp_path) is None


# ---------------------------------------------------------------- LLM 模式（与 ADR-017 对齐）

def _write_env(root: Path, body: str) -> None:
    d = root / "python-service"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".env").write_text(body, encoding="utf-8")


def test_llm_mode_no_env_file(tmp_path):
    c = doctor.check_llm_mode(tmp_path)
    assert c.ok is True and c.blocking is False
    assert "离线演示" in c.detail


def test_llm_mode_explicit_mock(tmp_path):
    _write_env(tmp_path, "LLM_MOCK=true\nLLM_API_KEY=sk-whatever\n")
    c = doctor.check_llm_mode(tmp_path)
    assert "离线演示" in c.detail, "显式 mock 必须报离线演示，不能因为填了 Key 就报真实"


def test_llm_mode_missing_key_reports_fallback_not_real(tmp_path):
    """核心：没 Key 时报的是"回退离线演示"，**绝不能报成真实生成**。"""
    _write_env(tmp_path, "LLM_MOCK=false\nLLM_API_KEY=\n")
    c = doctor.check_llm_mode(tmp_path)
    assert "回退" in c.detail and "离线演示" in c.detail
    assert "真实生成" not in c.detail


def test_llm_mode_real_mentions_data_boundary(tmp_path):
    """报真实模式时必须带出网提示（ADR-017），否则就是又一次"数据不出本机"式误导。"""
    _write_env(tmp_path, "LLM_MOCK=false\nLLM_API_KEY=sk-not-real\n")
    c = doctor.check_llm_mode(tmp_path)
    assert "真实生成" in c.detail
    assert "发送到" in c.detail, "真实模式必须提示正文会出网"


# ---------------------------------------------------------------- venv / pip

def _stub_venv(monkeypatch, tmp_path, *, pip_ok: bool, import_ok: bool):
    """把 check_venv 依赖的两个外部动作替换掉：找 python.exe、跑子进程。"""
    fake_py = tmp_path / "python.exe"
    fake_py.write_text("", encoding="utf-8")
    monkeypatch.setattr(doctor, "find_venv_python", lambda root: fake_py)

    def fake_run(cmd, timeout=None):
        if "pip" in cmd:
            return (0, "pip 24.0") if pip_ok else (1, "No module named pip")
        return (0, "ok") if import_ok else (1, "ModuleNotFoundError: fastapi")

    monkeypatch.setattr(doctor, "_run", fake_run)


def test_check_venv_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "find_venv_python", lambda root: None)
    c = doctor.check_venv(tmp_path)
    assert not c.ok and "未创建" in c.detail


def test_check_venv_pip_missing_is_reported_as_pip_problem(monkeypatch, tmp_path):
    """venv 有 python.exe 但没 pip 时，必须报「没有 pip」而不是「缺依赖」。

    报「缺依赖」会把用户引向「去装依赖」—— 但没有 pip 根本装不了，
    是个死循环。这个分支是实测撞出来的（`python -m venv` 在部分环境下
    不会装 pip），所以钉一条测试。
    """
    _stub_venv(monkeypatch, tmp_path, pip_ok=False, import_ok=False)
    c = doctor.check_venv(tmp_path)
    assert not c.ok
    assert "没有 pip" in c.detail, f"应指明 pip 问题，实际：{c.detail}"
    assert "ensurepip" in (c.hint or ""), "提示里要给出 ensurepip 修复命令"


def test_check_venv_missing_deps(monkeypatch, tmp_path):
    _stub_venv(monkeypatch, tmp_path, pip_ok=True, import_ok=False)
    c = doctor.check_venv(tmp_path)
    assert not c.ok and "缺依赖" in c.detail


def test_check_venv_ok(monkeypatch, tmp_path):
    _stub_venv(monkeypatch, tmp_path, pip_ok=True, import_ok=True)
    c = doctor.check_venv(tmp_path)
    assert c.ok


# ---------------------------------------------------------------- 汇总与输出

def test_run_all_covers_core_checks(tmp_path):
    names = [c.name for c in doctor.run_all(tmp_path)]
    for expected in ("Java 运行时", "Python 运行时", "Python 依赖环境", "Java 后端 jar"):
        assert expected in names, f"体检应包含「{expected}」"
    assert any("8080" in n for n in names) and any("8000" in n for n in names)


def test_render_quiet_hides_passing_checks():
    checks = [doctor.Check("甲", True, "没问题"), doctor.Check("乙", False, "有问题", "这样修")]
    loud = doctor.render(checks, quiet=False)
    quiet = doctor.render(checks, quiet=True)
    assert "甲" in loud and "乙" in loud
    assert "甲" not in quiet, "--quiet 应隐藏通过项"
    assert "乙" in quiet and "这样修" in quiet


def test_blocking_failure_yields_nonzero_exit(tmp_path, capsys):
    """缺 jar 属阻塞项 → 退出码必须非 0（脚本据此中止）。"""
    rc = doctor.main(["--root", str(tmp_path), "--quiet"])
    assert rc == 1
