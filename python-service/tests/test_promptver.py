"""提示词版本化（`docs/tasks_promptver.md` T1/T2/T4）。

要害：版本号必须**按提示词分别算**（改一个不动别的）、**内容变了就变**、**跨进程一致**。
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from app.pipeline import promptstore, prompts


def test_every_registered_scene_has_a_fingerprint():
    fps = prompts.fingerprints()
    assert fps, "注册表是空的"
    for scene, fp in fps.items():
        assert fp and len(fp) == 12, f"{scene} 的版本号不对：{fp!r}"


def test_all_scenes_used_in_code_are_registered():
    """**不能漏网**：代码里出现过的 scene 都要在注册表里。

    漏一个就等于那个提示词没有版本 —— 这正是本功能要防的事。
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    used = set()
    for p in list(root.glob("app/**/*.py")) + list(root.parent.glob("scripts/*.py")):
        txt = p.read_text(encoding="utf-8", errors="replace")
        for m in __import__("re").finditer(r'scene="([a-z_]+)"', txt):
            used.add(m.group(1))
    missing = sorted(used - set(prompts.SCENE_BUILDERS))
    assert not missing, f"这些 scene 没有登记版本号：{missing}"


def test_unknown_scene_returns_empty_not_raise():
    assert prompts.fingerprint_for_scene("不存在的东西") == ""
    assert prompts.fingerprint_for_scene("") == ""
    assert prompts.template_for_scene("不存在的东西") == ""


def test_same_source_gives_same_hash_across_processes():
    """**跨进程一致** —— 哈希只依赖源码文本，不含时间戳/路径。"""
    here = prompts.fingerprint_for_scene("draft")
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0,'python-service');"
         "from app.pipeline import prompts; print(prompts.fingerprint_for_scene('draft'))"],
        capture_output=True, text=True, cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]))
    assert (out.stdout or "").strip() == here, f"子进程算出 {out.stdout!r}，本进程 {here!r}"


def test_fingerprint_is_per_prompt_not_per_file():
    """**按提示词分别算**：draft 与 understand 的哈希必须不同（它们是不同的构造函数）。"""
    fps = prompts.fingerprints()
    assert fps["draft"] != fps["understand"]


def test_shared_builder_shares_fingerprint():
    """共用同一个构造函数的两处，版本号应当**相同**（这是对的，不是 bug）。"""
    fps = prompts.fingerprints()
    assert fps["qa"] == fps["qa_verify"]
    assert fps["understand"] == fps["eval_corpus"]


def test_template_does_not_contain_material():
    """存的是**模板**，不该含素材正文（素材是运行时拼进去的）。"""
    tpl = prompts.template_for_scene("draft")
    assert tpl and "def build_draft_prompt" in tpl


# ---------------------------------------------------------------- 存储


def test_save_is_idempotent(tmp_path, monkeypatch):
    """同版本只存一份（内容寻址）。"""
    monkeypatch.setattr(promptstore, "templates_dir", lambda: tmp_path)
    assert promptstore.save("abc123", "模板内容") is True
    assert promptstore.save("abc123", "模板内容") is False      # 第二次不写
    assert promptstore.load("abc123") == "模板内容"


def test_save_failure_does_not_raise(monkeypatch):
    """**写失败不影响调用** —— 观测产物不该阻断业务。"""
    def boom():
        raise OSError("磁盘满了")
    monkeypatch.setattr(promptstore, "templates_dir", boom)
    assert promptstore.save("x", "y") is False                  # 不抛错
    assert promptstore.load("x") == ""


def test_load_missing_returns_empty():
    assert promptstore.load("绝对不存在的哈希") == ""
