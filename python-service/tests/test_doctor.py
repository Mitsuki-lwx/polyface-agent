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
import socket
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


# ---------------------------------------------------------------- 端口检测

def _listen_on_free_port():
    """占住一个系统分配的空闲端口，返回 (socket, port)。

    不写死端口号：CI/开发机上固定端口可能已被占用或落在系统保留段里，
    那样测试会因环境而假红/假绿。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.listen(1)
    return s, port


def test_port_in_use_true_for_listening_socket():
    s, port = _listen_on_free_port()
    try:
        assert doctor.port_in_use(port) is True
    finally:
        s.close()


def test_port_in_use_false_after_release():
    s, port = _listen_on_free_port()
    s.close()
    assert doctor.port_in_use(port) is False


def test_port_in_use_true_for_bound_but_not_listening():
    """**回归测试**：只 bind、不 listen 的端口也必须报「占用」。

    这是实机踩到的缺陷。旧实现用 `connect_ex(...) == 0` 判断，
    它只能发现「有人在 listen」；对「已 bind 但未 listen」的端口返回连不上，
    于是被判成"空闲"。
    Windows 上这类占用很常见：某些游戏平台/加速器会一次性 bind 掉一整段端口，
    `netstat` 里显示为 BOUND。结果是体检放行 → 真正启动时 bind 失败 →
    用户白等 120 秒才看到报错（体检的意义就没了）。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    try:                       # 刻意不调用 listen()
        assert doctor.port_in_use(port) is True, (
            "已 bind 未 listen 的端口必须视为占用 —— "
            "否则体检会放过一个绑不上的端口")
    finally:
        s.close()


def test_check_ports_respects_custom_ports():
    """check_ports 必须检查传入的端口，而不是写死的 8080/8000。"""
    s, port = _listen_on_free_port()
    try:
        checks = doctor.check_ports(java_port=port, py_port=port)
        assert len(checks) == 2
        assert all(c.ok is False for c in checks), "两个检查都应报占用"
        assert all(str(port) in c.name for c in checks), "检查项名字里应是指定端口"
    finally:
        s.close()


def test_check_ports_hint_mentions_override():
    """端口被占时，提示里要给出「怎么换端口」，不能只说"被占用"。"""
    s, port = _listen_on_free_port()
    try:
        c = doctor.check_ports(java_port=port, py_port=12345)[0]
        assert "POLYFACE_JAVA_PORT" in c.hint and "POLYFACE_PY_PORT" in c.hint
    finally:
        s.close()


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


# ---------------------------------------------------------------- 封面编辑器（M6-2）

def _fake_gimpish(root: Path, version: str = "0.1.0") -> Path:
    """造一个 node_modules/gimpish 布局的假安装（只要 package.json 版本可读即可）。"""
    pkg = root / "node_modules" / "gimpish"
    (pkg / "bin").mkdir(parents=True, exist_ok=True)
    (pkg / "bin" / "gimpish.js").write_text("// stub\n", encoding="utf-8")
    (pkg / "package.json").write_text('{"name":"gimpish","version":"%s"}' % version, encoding="utf-8")
    return pkg / "bin" / "gimpish.js"


def test_find_gimpish_prefers_env_over_dotenv(tmp_path, monkeypatch):
    _write_env(tmp_path, "GIMPISH_PATH=/from/dotenv\n")
    from_env = _fake_gimpish(tmp_path / "env")
    monkeypatch.setenv("POLYFACE_GIMPISH", str(from_env))
    monkeypatch.setattr(doctor.shutil, "which", lambda _n: None)
    assert doctor.find_gimpish(tmp_path) == from_env, "环境变量优先级必须高于 .env"


def test_find_gimpish_reads_dotenv_and_resolves_js_in_dir(tmp_path, monkeypatch):
    exe = _fake_gimpish(tmp_path / "vendor")
    _write_env(tmp_path, f"GIMPISH_PATH={exe.parent}\n")   # 指向**目录**，应解析到其中的 gimpish.js
    monkeypatch.delenv("POLYFACE_GIMPISH", raising=False)
    monkeypatch.setattr(doctor.shutil, "which", lambda _n: None)
    found = doctor.find_gimpish(tmp_path)
    assert found is not None and found.name == "gimpish.js", "目录应解析到其中的 gimpish.js"


def test_gimpish_version_reads_package_json(tmp_path):
    exe = _fake_gimpish(tmp_path, version="9.9.9")
    assert doctor.gimpish_version(exe) == "9.9.9"


def test_check_editor_missing_is_non_blocking_with_install_hint(tmp_path, monkeypatch):
    monkeypatch.delenv("POLYFACE_GIMPISH", raising=False)
    monkeypatch.setattr(doctor.shutil, "which", lambda _n: None)
    c = doctor.check_editor(tmp_path)
    assert c.ok is True and c.blocking is False, "可选依赖缺失不能阻塞启动"
    assert "npm install -g gimpish" in c.hint


def test_check_editor_warns_when_version_differs_from_verified(tmp_path, monkeypatch):
    exe = _fake_gimpish(tmp_path, version="9.9.9")
    monkeypatch.setenv("POLYFACE_GIMPISH", str(exe))
    c = doctor.check_editor(tmp_path)
    assert c.ok is True and c.blocking is False
    assert doctor.VERIFIED_GIMPISH in c.hint, "版本不符时必须点名已验证版本"
    assert "e2e_editor" in c.hint, "版本不符时必须提示重跑内嵌编辑器的端到端"


def test_check_editor_verified_version_has_no_warning(tmp_path, monkeypatch):
    exe = _fake_gimpish(tmp_path, version=doctor.VERIFIED_GIMPISH)
    monkeypatch.setenv("POLYFACE_GIMPISH", str(exe))
    c = doctor.check_editor(tmp_path)
    assert c.ok is True and c.hint == ""


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


# ---------------------------------------------------------------- 平台 DNA 新鲜度

def test_platform_dna_reports_freshness(tmp_path):
    d = tmp_path / "platform-dna"
    d.mkdir()
    (d / "x.yaml").write_text("platform: x\nupdated_at: 2099-01-01\n", encoding="utf-8")
    c = doctor.check_platform_dna(tmp_path)
    assert c.ok and "1 个平台" in c.detail


def test_platform_dna_warns_when_stale(tmp_path):
    """平台规则会变，DNA 是静态文件 —— 过期要**提示**（非阻塞）。"""
    d = tmp_path / "platform-dna"
    d.mkdir()
    (d / "x.yaml").write_text("platform: x\nupdated_at: 2000-01-01\n", encoding="utf-8")
    c = doctor.check_platform_dna(tmp_path, max_age_days=90)
    assert not c.ok and not c.blocking
    assert "x" in c.hint and "超过" in c.hint


def test_platform_dna_handles_missing_updated_at(tmp_path):
    d = tmp_path / "platform-dna"
    d.mkdir()
    (d / "x.yaml").write_text("platform: x\n", encoding="utf-8")
    c = doctor.check_platform_dna(tmp_path)
    assert not c.ok and not c.blocking
    assert "updated_at" in c.hint


def test_platform_dna_missing_dir_is_not_blocking(tmp_path):
    c = doctor.check_platform_dna(tmp_path)
    assert not c.ok and not c.blocking


def test_dna_max_age_is_configurable(monkeypatch):
    monkeypatch.setenv("POLYFACE_DNA_MAX_AGE_DAYS", "30")
    assert doctor.dna_max_age_days() == 30
    monkeypatch.setenv("POLYFACE_DNA_MAX_AGE_DAYS", "abc")   # 乱填退回默认
    assert doctor.dna_max_age_days() == doctor.DEFAULT_DNA_MAX_AGE_DAYS
    monkeypatch.setenv("POLYFACE_DNA_MAX_AGE_DAYS", "0")     # 非正数退回 1
    assert doctor.dna_max_age_days() == 1
