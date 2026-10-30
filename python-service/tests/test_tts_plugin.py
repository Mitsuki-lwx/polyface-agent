"""TTS 插件系统（`docs/checklist_tts_plugin.md` §3 §4 §5）。

重点在**坏插件不能毁掉清单**、**内置与第三方走同一条路**、
以及**非 Python 也能写插件**（这是选 stdin/stdout JSON 这个契约的全部理由）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
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

NODE = shutil.which("node")

# **非 Python 的插件**：Node 写一个 440Hz 提示音。
# 为什么要换掉 shell 那版：本机 Git Bash 打不开 `C:/...` 形式的重定向
# （转成 MSYS 的 `/c/...` 也仍然 No such file or directory，多轮实测），
# 那一版只能天天 skip —— **skip 掉的断言等于没有**。Node 在本机稳，能真跑。
_NODE_PLUGIN = r"""
const fs = require("fs");
const readline = require("readline");
const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const req = JSON.parse(line);
  const rate = 16000, secs = 1.0, n = Math.floor(rate * secs);
  const data = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++) {
    data.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * 440 * i / rate)), i * 2);
  }
  const h = Buffer.alloc(44);
  h.write("RIFF", 0); h.writeUInt32LE(36 + data.length, 4); h.write("WAVE", 8);
  h.write("fmt ", 12); h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22);
  h.writeUInt32LE(rate, 24); h.writeUInt32LE(rate * 2, 28); h.writeUInt16LE(2, 32);
  h.writeUInt16LE(16, 34); h.write("data", 36); h.writeUInt32LE(data.length, 40);
  fs.writeFileSync(req.out, Buffer.concat([h, data]));
  process.stdout.write(JSON.stringify({ ok: true, seconds: secs }) + "\n");
});
"""


@pytest.mark.skipif(NODE is None, reason="本机没有 node —— 换任何别的非 Python 解释器同理")
def test_a_non_python_plugin_works(plugdir, tmp_path):
    """**用 Node 写的插件也能用** —— 这是选 stdin/stdout JSON 这个契约的**全部理由**。

    它不做真合成，只按约定产出一个 WAV（证明"任意语言"不是空话）。
    和内置的那两个走的是**同一条 `_Worker` 路径** —— 这正是契约要证明的事。
    """
    _plugin(plugdir, "node-tone",
            {"id": "node-tone", "name": "Node 提示音", "kind": "tts", "network": False,
             "note": "非 Python 插件", "run": {"command": ["node", "run.js"]}},
            "run.js", _NODE_PLUGIN)
    assert [x["id"] for x in tp.catalog()["items"]] == ["node-tone"]
    assert tp.synthesize("node-tone", "你好", tmp_path / "o.wav") == pytest.approx(1.0)
    assert (tmp_path / "o.wav").stat().st_size > 512
    tp.shutdown()


def test_timeout_message_names_non_json_output(plugdir, tmp_path):
    """**别把"答非所问"报成"卡死"**。

    插件打印一句普通文本（不以 `{` 开头）→ 加载器把它当**日志**跳过（设计如此），
    于是等不到回复、最后超时。但只说"超时"会把人带偏：它**早就回过话**了。
    实测踩过：报"超时（5s）"，去查了半天"为什么挂住"。
    """
    _plugin(plugdir, "garbage", {"id": "garbage", "name": "只吐垃圾", "kind": "tts",
                                 "run": {"command": ["{python}", "run.py"], "timeout_sec": 1}},
            "run.py", "import sys, time\nsys.stdin.readline()\n"
                      "print('随便打点东西', flush=True)\ntime.sleep(5)\n")
    with pytest.raises(tp.PluginError) as e:
        tp.synthesize("garbage", "你好", tmp_path / "o.wav")
    msg = str(e.value)
    assert "超时" in msg and "随便打点东西" in msg, f"要说清它回过话：{msg}"
    tp.shutdown()


def test_real_slow_timeout_has_no_noise_note(plugdir, tmp_path):
    """真·慢的时候**不该**冒出"有输出但不是 JSON"—— 两种失败要能分开。"""
    _plugin(plugdir, "silent", {"id": "silent", "name": "真慢", "kind": "tts",
                                "run": {"command": ["{python}", "run.py"], "timeout_sec": 1}},
            "run.py", "import time\ntime.sleep(5)\n")
    with pytest.raises(tp.PluginError) as e:
        tp.synthesize("silent", "你好", tmp_path / "o.wav")
    assert "不是 JSON" not in str(e.value)
    tp.shutdown()


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


# ---------------- 常驻进程（一问一答型的老插件必须照常能用）----------------

def test_oneshot_plugin_is_respawned(plugdir, tmp_path):
    """**回完就退出的插件照样能用** —— 加载器发现 EOF 就重开。

    这条是"常驻加速"能落地的关键：契约没变，老插件一行都不用改。
    """
    _plugin(plugdir, "one-shot", {"id": "one-shot", "name": "一问一答", "kind": "tts",
                                  "run": {"command": ["{python}", "run.py"]}}, "run.py",
            "import json,sys,wave\n"
            "req=json.loads(sys.stdin.readline())\n"
            "with wave.open(req['out'],'wb') as w:\n"
            "    w.setnchannels(1);w.setsampwidth(2);w.setframerate(22050)\n"
            "    w.writeframes(b'\\x00\\x00'*22050)\n"
            "print(json.dumps({'ok':True,'seconds':1.0}))\n")
    for i in range(3):
        d = tp.synthesize("one-shot", f"第{i}次", tmp_path / f"o{i}.wav")
        assert d == pytest.approx(1.0), f"第 {i+1} 次失败 —— 重开逻辑没生效"
    tp.shutdown()


def test_respawn_does_not_leak_old_process_replies(plugdir, tmp_path):
    """**重开之后，旧进程的输出不许串进新调用**（队列必须与进程成对）。

    实测过的错误形态：旧读线程往"当前队列"里塞，于是新请求 `DDD` 拿回了
    旧进程的 `CCC` —— 一个**静默的错误结果**（时长/音色会全串），比报错危险得多。
    """
    _plugin(plugdir, "echoer", {"id": "echoer", "name": "回显", "kind": "tts",
                                "run": {"command": ["{python}", "run.py"]}}, "run.py",
            "import json,sys,wave\n"
            "for line in sys.stdin:\n"
            "    req=json.loads(line)\n"
            "    with wave.open(req['out'],'wb') as w:\n"
            "        w.setnchannels(1);w.setsampwidth(2);w.setframerate(22050)\n"
            "        w.writeframes(b'\\x00\\x00'*22050)\n"
            "    print(json.dumps({'ok':True,'seconds':1.0,'echo':req['text']}), flush=True)\n")
    w = tp._worker_for(tp.load_plugins()[0][0])
    assert w.call(_req(tmp_path, "a", "AAA"))["echo"] == "AAA"

    old = w.proc                                  # 抓住旧进程，再模拟"死了要重开"
    w._spawn()
    old.stdin.write(json.dumps(_req(tmp_path, "c", "CCC")) + "\n")
    old.stdin.flush()
    _drain(w, seconds=1.0)                        # 让旧进程的回复有机会进队列

    assert w.call(_req(tmp_path, "d", "DDD"))["echo"] == "DDD", \
        "拿到了旧进程的回复 —— 队列没跟着进程换"
    w.kill()
    tp.shutdown()


def test_write_failure_is_retried_not_fatal(plugdir, tmp_path):
    """**写失败必须走"重开重试"**，不能直接判整镜失败。

    实测过的错误形态：Windows 上插件正在退出时写 stdin 会偶发 `[Errno 22]`；
    而 `_once` 当时把它包成 `PluginError`（RuntimeError），`call()` 的
    `except (BrokenPipeError, OSError)` **接不到** —— 一次成片 11 个分镜里
    **3 个丢了旁白**（成片被静音垫片顶住，所以没人发现，除了记录里的 `tts.failed`）。
    """
    _plugin(plugdir, "echoer2", {"id": "echoer2", "name": "回显", "kind": "tts",
                                 "run": {"command": ["{python}", "run.py"]}}, "run.py",
            "import json,sys,wave\n"
            "for line in sys.stdin:\n"
            "    req=json.loads(line)\n"
            "    with wave.open(req['out'],'wb') as w:\n"
            "        w.setnchannels(1);w.setsampwidth(2);w.setframerate(22050)\n"
            "        w.writeframes(b'\\x00\\x00'*22050)\n"
            "    print(json.dumps({'ok':True,'seconds':1.0,'echo':req['text']}), flush=True)\n")
    w = tp._worker_for(tp.load_plugins()[0][0])
    w._spawn()

    class _DeadPipe:
        """模拟"进程正在退出"时的 stdin：写就报 `[Errno 22]`。"""

        def write(self, _s: str) -> None:
            raise OSError(22, "Invalid argument")

        def flush(self) -> None:
            pass

    w.proc.stdin = _DeadPipe()                    # 只坏这一次：重开时 _spawn() 会给真 stdin
    assert w.call(_req(tmp_path, "e", "EEE"))["echo"] == "EEE", "写失败没有重试"
    w.kill()
    tp.shutdown()


def _req(tmp_path: Path, name: str, text: str) -> dict:
    return {"text": text, "out": str(tmp_path / f"{name}.wav"),
            "voice": None, "rate": 0, "volume": 100}


def _drain(w: tp._Worker, seconds: float) -> None:
    """给读线程一点时间把旧进程的输出塞进队列（不这么做测试会偶发）。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and w.queue.empty():
        time.sleep(0.02)


def test_serve_plugin_is_reused(plugdir, tmp_path):
    """**常驻插件只起一次进程** —— 靠数"进程启动了几次"来证明。

    这正是 Kokoro 的加速来源：模型加载一次 ~25s，之后每次只要 ~1s。
    """
    _plugin(plugdir, "serve", {"id": "serve", "name": "常驻", "kind": "tts",
                               "run": {"command": ["{python}", "run.py"]}}, "run.py",
            "import json,sys,wave\n"
            "for line in sys.stdin:\n"
            "    req=json.loads(line)\n"
            "    with wave.open(req['out'],'wb') as w:\n"
            "        w.setnchannels(1);w.setsampwidth(2);w.setframerate(22050)\n"
            "        w.writeframes(b'\\x00\\x00'*22050)\n"
            "    print(json.dumps({'ok':True,'seconds':1.0}), flush=True)\n")
    ws = tp._worker_for(tp.load_plugins()[0][0])
    for i in range(4):
        assert tp.synthesize("serve", f"第{i}次", tmp_path / f"s{i}.wav") == pytest.approx(1.0)
        assert ws.alive(), f"第 {i+1} 次之后进程不该退"
    tp.shutdown()


def test_shutdown_kills_workers(plugdir, tmp_path):
    """收工要把常驻进程关掉，不然留孤儿。"""
    _plugin(plugdir, "serve2", {"id": "serve2", "name": "常驻", "kind": "tts",
                                "run": {"command": ["{python}", "run.py"]}}, "run.py",
            "import json,sys,wave\n"
            "for line in sys.stdin:\n"
            "    req=json.loads(line)\n"
            "    open(req['out'],'wb').write(b'\\x00'*4096)\n"
            "    print(json.dumps({'ok':True,'seconds':1.0}), flush=True)\n")
    tp.synthesize("serve2", "x", tmp_path / "a.wav")
    ws = tp._worker_for(tp.load_plugins()[0][0])
    assert ws.alive()
    tp.shutdown()
    assert not ws.alive(), "shutdown 之后进程应当已经关掉"


# ---------------- 内置的那两个（真的在仓库里）----------------

def test_builtin_plugins_are_present_and_local(monkeypatch):
    """内置插件必须**在默认目录里**、且标了 `builtin` 与 `network:false`。"""
    monkeypatch.delenv("POLYFACE_TTS_PLUGINS", raising=False)
    items = {x["id"]: x for x in tp.catalog()["items"]}
    assert "windows-sapi" in items, "内置的 SAPI 插件应当在默认插件目录里"
    assert "own-audio" in items, "「用户自己的音频」也应当是一个插件（同一张清单）"
    assert all(not x["network"] for x in items.values()), "内置的都不联网"


# ---------------- Kokoro（真的在仓库里，但依赖外部的模型与 3.12 环境）----------------

def _kokoro_ready() -> bool:
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    return ((root / "kokoro-models" / "zh" / "kokoro-v1_1-zh.pth").is_file()
            and (root / ".venv-kokoro" / "Scripts" / "python.exe").is_file())


@pytest.mark.skipif(not _kokoro_ready(), reason="没下模型 / 没建 3.12 环境")
def test_kokoro_plugin_lists_zh_voices_and_synthesizes(tmp_path):
    """Kokoro 插件：中文音色要列出来，且**真的能合成中文**。

    这条是"提供多种配音方案"从口号变成事实的那一条 —— 之前本机中文只有一个女声。
    """
    items = {x["id"]: x for x in tp.catalog()["items"]}
    assert "kokoro" in items, "下好模型后 Kokoro 应当出现在清单里"
    k = items["kokoro"]
    assert k["network"] is False, "Kokoro 是本地跑的，不该标成联网"
    zh = [v["id"] for v in k["voices"] if v.get("language") == "zh"]
    assert len(zh) >= 5, f"中文音色应当有好几个，实际 {zh}"
    assert any(v.startswith("zf_") for v in zh) and any(v.startswith("zm_") for v in zh), \
        "男女声都要有"

    d = tp.synthesize("kokoro", "你好，这是本地合成。", tmp_path / "k.wav", voice=zh[0])
    assert d > 0.5, f"应当产出有长度的音频，实际 {d}s"
    assert (tmp_path / "k.wav").stat().st_size > 10000
