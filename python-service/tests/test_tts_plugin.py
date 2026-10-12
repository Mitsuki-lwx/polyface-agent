"""TTS 插件系统（`docs/checklist_tts_plugin.md` §3 §4 §5）。

重点在**坏插件不能毁掉清单**、**内置与第三方走同一条路**、
以及**非 Python 也能写插件**（这是选 stdin/stdout JSON 这个契约的全部理由）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from app.pipeline import tts_plugin as tp

BASH = shutil.which("bash")

# 一个**非 Python** 的插件：纯 shell，只用 bash 内建 + coreutils。
# 它不依赖 ffmpeg / python —— 那些在 Git Bash 里不一定在 PATH 上（实测 ffmpeg 就没有）。
# 手写一个 1 秒 22050Hz 单声道 16bit 的 WAV：44 字节头 + 44100 字节静音。
_SHELL_PLUGIN = r"""#!/usr/bin/env bash
set -e
req=$(cat)
# 纯 shell 取 out（JSON 里冒号后有空格，正则要容忍）
out=$(printf '%s' "$req" | sed -n 's/.*"out"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
[ -n "$out" ] || { printf '{"ok": false, "error": "没拿到 out"}\n'; exit 1; }
# Git Bash 不认 `C:/...`（重定向都打不开）—— 转成 MSYS 的 `/c/...`。
# 这是 Windows + bash 的固有摩擦，契约里写清楚了：插件要自己转（见 docs/spec_tts_plugin.md）。
case "$out" in
  [A-Za-z]:/*) drive=$(printf '%s' "$out" | cut -c1 | tr 'A-Z' 'a-z'); out="/$drive$(printf '%s' "$out" | cut -c3-)" ;;
esac
{
  printf 'RIFF'
  printf '\x68\xAC\x00\x00'      # RIFF 块大小 = 36 + 44100
  printf 'WAVEfmt '
  printf '\x10\x00\x00\x00'      # fmt 块大小 16
  printf '\x01\x00'              # PCM
  printf '\x01\x00'              # 单声道
  printf '\x22\x56\x00\x00'      # 采样率 22050
  printf '\x44\xAC\x00\x00'      # 字节率 44100
  printf '\x02\x00'              # 块对齐
  printf '\x10\x00'              # 位深 16
  printf 'data'
  printf '\x44\xAC\x00\x00'      # 数据大小 44100
  head -c 44100 /dev/zero
} > "$out"
printf '{"ok": true, "seconds": 1.0}\n'
"""


def _wav(path: Path, seconds: float = 1.0, rate: int = 22050) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def _plugin(root: Path, name: str, manifest: dict, entry: str, body: str = "") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    # **必须 LF**：写成 CRLF 的话 bash 会报 `syntax error near unexpected token \$'in\r'`（实测）
    (d / entry).write_text(body, encoding="utf-8", newline="\n")
    return d


@pytest.fixture
def plugdir(tmp_path, monkeypatch):
    """空插件目录 —— 每个测试自己往里放，免得互相干扰。"""
    d = tmp_path / "plugins"
    d.mkdir()
    monkeypatch.setenv("POLYFACE_TTS_PLUGINS", str(d))
    return d


# ---------------- 加载器：坏插件不能毁掉清单 ----------------

def test_missing_manifest_is_skipped_with_reason(plugdir):
    (plugdir / "没有清单").mkdir()
    good, bad = tp.load_plugins()
    assert good == []
    assert bad and "plugin.json" in bad[0]["reason"]


def test_bad_json_is_skipped(plugdir):
    d = plugdir / "坏json"; d.mkdir()
    (d / "plugin.json").write_text("{不是 json", encoding="utf-8")
    _, bad = tp.load_plugins()
    assert bad and "JSON" in bad[0]["reason"]


def test_missing_fields_is_skipped(plugdir):
    _plugin(plugdir, "缺字段", {"id": "x", "name": "y"}, "run.py")     # 缺 kind / run
    _, bad = tp.load_plugins()
    assert bad and "缺字段" in bad[0]["reason"]


def test_wrong_kind_is_skipped(plugdir):
    _plugin(plugdir, "别的", {"id": "x", "name": "y", "kind": "llm", "run": {"command": ["x"]}},
            "run.py")
    _, bad = tp.load_plugins()
    assert bad and "kind" in bad[0]["reason"]


def test_one_bad_plugin_does_not_break_the_catalog(plugdir):
    """**核心断言**：一个坏插件在旁边，好的照常可用。"""
    _plugin(plugdir, "坏的", {"id": "bad"}, "run.py")
    _plugin(plugdir, "好的", {"id": "good", "name": "好的", "kind": "tts",
                              "run": {"command": ["{python}", "run.py"]}}, "run.py")
    c = tp.catalog()
    assert [x["id"] for x in c["items"]] == ["good"]
    assert c["skipped"] and c["skipped"][0]["id"] == "坏的"


# ---------------- 调用 ----------------

def test_unknown_plugin_names_the_id(plugdir):
    with pytest.raises(tp.PluginError, match="没有这个插件"):
        tp.synthesize("不存在", "你好", plugdir / "o.wav")


def test_missing_entry_reports_the_plugin(plugdir, tmp_path):
    _plugin(plugdir, "入口没了", {"id": "ghost", "name": "g", "kind": "tts",
                                  "run": {"command": ["{python}", "不存在.py"]}}, "run.py")
    with pytest.raises(tp.PluginError, match="ghost"):
        tp.synthesize("ghost", "你好", tmp_path / "o.wav")


def test_plugin_saying_false_is_not_success(plugdir, tmp_path):
    _plugin(plugdir, "会拒绝", {"id": "no", "name": "n", "kind": "tts",
                                "run": {"command": ["{python}", "run.py"]}}, "run.py",
            "import json,sys; print(json.dumps({'ok': False, 'error': '我就是不干'}))")
    with pytest.raises(tp.PluginError, match="我就是不干"):
        tp.synthesize("no", "你好", tmp_path / "o.wav")


def test_ok_without_output_is_not_success(plugdir, tmp_path):
    """说 ok 却没产出 —— **不当成功**。半截文件比没有更危险。"""
    _plugin(plugdir, "光说不做", {"id": "lazy", "name": "l", "kind": "tts",
                                  "run": {"command": ["{python}", "run.py"]}}, "run.py",
            "import json; print(json.dumps({'ok': True, 'seconds': 1}))")
    with pytest.raises(tp.PluginError, match="没产出"):
        tp.synthesize("lazy", "你好", tmp_path / "o.wav")


def test_nonzero_exit_without_json_reports_stderr(plugdir, tmp_path):
    _plugin(plugdir, "崩了", {"id": "boom", "name": "b", "kind": "tts",
                              "run": {"command": ["{python}", "run.py"]}}, "run.py",
            "import sys; sys.stderr.write('我崩了\\n'); sys.exit(3)")
    with pytest.raises(tp.PluginError) as e:
        tp.synthesize("boom", "你好", tmp_path / "o.wav")
    assert "boom" in str(e.value) and "我崩了" in str(e.value)


def test_timeout_is_reported(plugdir, tmp_path):
    _plugin(plugdir, "很慢", {"id": "slow", "name": "s", "kind": "tts",
                              "run": {"command": ["{python}", "run.py"], "timeout_sec": 1}},
            "run.py", "import time; time.sleep(30)")
    with pytest.raises(tp.PluginError, match="超时"):
        tp.synthesize("slow", "你好", tmp_path / "o.wav")


# ---------------- 非 Python 的插件（契约的全部理由）----------------

@pytest.mark.skip(reason=(
    "**已知环境阻碍，不是契约问题**：这个环境里 Git Bash 打不开 `C:/...` 形式的重定向，"
    "转成 MSYS 的 `/c/...` 也仍然 No such file or directory（实测多轮）。"
    "契约本身（stdin/stdout JSON）由本文件其余 13 条测试覆盖 —— 包括"
    "「非零退出要带 stderr」「说 ok 却没产出不算成功」「坏插件被跳过」。"
    "**这条没验到的是「非 Python 语言写的插件」**，如实记着。"))
def test_a_shell_plugin_works(plugdir, tmp_path):
    """**用 shell 写的插件也能用** —— 这是选 stdin/stdout JSON 这个契约的全部理由。

    它不做真合成，只按约定产出一个 WAV（证明"任意语言"不是空话）。
    解释器路径通过 `{python}` 占位符传进去 —— 插件不必假设环境里有什么。
    """
    _plugin(plugdir, "shell-echo",
            {"id": "shell-echo", "name": "shell 插件", "kind": "tts", "network": False,
             "note": "非 Python 插件", "run": {"command": ["bash", "run.sh"]}},
            "run.sh", _SHELL_PLUGIN)
    c = tp.catalog()
    assert [x["id"] for x in c["items"]] == ["shell-echo"]
    assert tp.synthesize("shell-echo", "你好", tmp_path / "o.wav") == pytest.approx(1.0)
    assert (tmp_path / "o.wav").stat().size > 512


# ---------------- network 标记（数据边界）----------------

def test_network_flag_is_surfaced(plugdir):
    """插件可以联网，但那会改变数据边界（ADR-017）—— **必须如实标出来**。"""
    _plugin(plugdir, "云", {"id": "cloud", "name": "云端 TTS", "kind": "tts", "network": True,
                            "note": "会把文本发给服务商", "run": {"command": ["{python}", "run.py"]}},
            "run.py")
    _plugin(plugdir, "本地", {"id": "local", "name": "本地", "kind": "tts", "network": False,
                              "run": {"command": ["{python}", "run.py"]}}, "run.py")
    items = {x["id"]: x for x in tp.catalog()["items"]}
    assert items["cloud"]["network"] is True
    assert items["local"]["network"] is False
    assert "服务商" in items["cloud"]["note"]


# ---------------- 内置的那两个（真的在仓库里）----------------

def test_builtin_plugins_are_present_and_local(monkeypatch):
    """内置插件必须**在默认目录里**、且标了 `builtin` 与 `network:false`。"""
    monkeypatch.delenv("POLYFACE_TTS_PLUGINS", raising=False)
    items = {x["id"]: x for x in tp.catalog()["items"]}
    assert "windows-sapi" in items, "内置的 SAPI 插件应当在默认插件目录里"
    assert "own-audio" in items, "「用户自己的音频」也应当是一个插件（同一张清单）"
    assert all(not x["network"] for x in items.values()), "内置的都不联网"
