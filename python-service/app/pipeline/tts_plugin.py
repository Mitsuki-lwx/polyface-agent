"""TTS 插件：**目录 + manifest + stdin/stdout JSON**（`docs/spec_tts_plugin.md`）。

契约只有一条 —— 所以 Python/Node/Go/shell 都能写插件：

    程序 → 插件（stdin 一行 JSON）:  {"text","voice","out","rate","volume","params"}
    插件 → 程序（stdout 一行 JSON）: {"ok": true, "seconds": 1.23}
                                     或 {"ok": false, "error": "..."}

插件负责把**WAV**写到 `out`，其余不管。

三条不变量：

1. **内置与第三方走同一条路。** 内置的那几个因此成了接口的第一批真实用户 ——
   接口定错会立刻暴露，而不是等第一个第三方插件来踩。
2. **坏插件不能毁掉整个清单。** 解析失败的跳过并**记下原因**，其余照常可用。
3. **失败要说清是哪个插件。** 一句"配音失败"没法排查；要带上插件 id 与它的 stderr 片段。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "tts-plugins"
DEFAULT_TIMEOUT = 120

REQUIRED = ("id", "name", "kind", "run")


class PluginError(RuntimeError):
    """插件不可用/调用失败。**消息里必须带插件 id**。"""


def plugins_dir() -> Path:
    return Path(os.environ.get("POLYFACE_TTS_PLUGINS") or DEFAULT_DIR)


def load_plugins() -> tuple[list[dict], list[dict]]:
    """扫描目录。返回 `(可用插件, 被跳过的插件)` —— **后者带原因**，不是静默消失。"""
    root = plugins_dir()
    good: list[dict] = []
    bad: list[dict] = []
    if not root.is_dir():
        return good, [{"id": "(目录不存在)", "path": str(root), "reason": "插件目录不存在"}]

    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest = d / "plugin.json"
        if not manifest.is_file():
            bad.append({"id": d.name, "path": str(d), "reason": "没有 plugin.json"})
            continue
        try:
            meta = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception as e:                               # noqa: BLE001
            bad.append({"id": d.name, "path": str(d), "reason": f"plugin.json 不是合法 JSON：{e}"})
            continue
        missing = [k for k in REQUIRED if not meta.get(k)]
        if missing:
            bad.append({"id": d.name, "path": str(d), "reason": f"缺字段：{missing}"})
            continue
        if meta.get("kind") != "tts":
            bad.append({"id": d.name, "path": str(d),
                        "reason": f"kind 不是 tts（本版只支持 tts）：{meta.get('kind')!r}"})
            continue
        meta["_dir"] = str(d)
        good.append(meta)
    return good, bad


def catalog() -> dict:
    """**一张清单**（spec 关键决定四）：内置的、插件的、自己的音频，全在里面。

    每条都带 `network` —— 插件可以联网，但那会改变数据边界（ADR-017），
    所以必须**如实转述**给用户。
    """
    good, bad = load_plugins()
    items = []
    for m in good:
        items.append({
            "id": m["id"],
            "name": m.get("name") or m["id"],
            "kind": "tts",
            "source": "builtin" if m.get("builtin") else "plugin",
            "languages": m.get("languages") or [],
            "voices": m.get("voices") or [],
            "network": bool(m.get("network")),      # ← 必须如实标出来
            "note": m.get("note") or "",
            "params": m.get("params") or [],
        })
    return {"dir": str(plugins_dir()), "items": items, "skipped": bad}


def synthesize(plugin_id: str, text: str, out: str | Path, *,
               voice: str | None = None, rate: int = 0, volume: int = 100,
               params: dict | None = None) -> float:
    """调一个插件合成一段。返回**实际时长**（秒）。

    :raises PluginError: 找不到插件 / 超时 / 非零退出 / stdout 不是 JSON / 没产出 WAV
    """
    good, _ = load_plugins()
    meta = next((m for m in good if m["id"] == plugin_id), None)
    if meta is None:
        raise PluginError(f"没有这个插件：{plugin_id!r}（可用：{[m['id'] for m in good]}）")

    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()                                     # 免得拿上一次的产物当成功

    run = meta.get("run") or {}
    cmd = _resolve_cmd(run, meta)
    timeout = int(run.get("timeout_sec") or DEFAULT_TIMEOUT)
    # **交给插件的路径一律用正斜杠**：Windows API 认 `/`，而 bash 会把 `\` 当转义。
    # 实测踩过：`out` 带反斜杠时 shell 插件把文件写到了被改坏的路径上，
    # 然后报"说成功但没产出" —— 这种坑不该让每个插件作者各踩一次。
    req = {"text": text, "voice": voice, "out": out.as_posix(),
           "rate": int(rate), "volume": int(volume), "params": params or {}}

    try:
        cp = subprocess.run(cmd, input=json.dumps(req, ensure_ascii=False),
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout,
                            cwd=meta["_dir"])
    except subprocess.TimeoutExpired:
        raise PluginError(f"插件 {plugin_id} 超时（{timeout}s）") from None
    except Exception as e:                               # noqa: BLE001
        raise PluginError(f"插件 {plugin_id} 起不来：{e}") from None

    last = ""
    for line in reversed((cp.stdout or "").splitlines()):
        if line.strip().startswith("{"):
            last = line.strip()
            break
    if not last:
        raise PluginError(f"插件 {plugin_id} 没有返回 JSON（退出码 {cp.returncode}）："
                          f"{(cp.stderr or cp.stdout or '')[-200:]}")
    try:
        resp = json.loads(last)
    except Exception:                                    # noqa: BLE001
        raise PluginError(f"插件 {plugin_id} 的 stdout 不是合法 JSON：{last[:160]}") from None

    if not resp.get("ok"):
        raise PluginError(f"插件 {plugin_id} 报错：{resp.get('error') or '(没给原因)'}")
    if not out.is_file() or out.stat().st_size < 512:
        # 说 ok 却没产出 —— **不当成功**（半截文件比没有更危险）
        raise PluginError(f"插件 {plugin_id} 说成功但没产出可用的 WAV：{out}")
    try:
        return float(resp.get("seconds") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _resolve_cmd(run: dict, meta: dict) -> list[str]:
    """入口命令。支持 `{python}` 占位符（用当前解释器）与相对路径（相对插件目录）。"""
    raw = run.get("command")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise PluginError(f"插件 {meta['id']} 的 run.command 不合法：{raw!r}")
    out = []
    for i, part in enumerate(raw):
        # 同理用正斜杠：入口路径里带反斜杠的话，bash 起的插件会 command not found
        s = (str(part).replace("{python}", Path(sys.executable).as_posix())
             .replace("{dir}", Path(meta["_dir"]).as_posix()))
        out.append(s)
    exe = out[0]
    if not (Path(exe).is_file() or shutil.which(exe)):
        raise PluginError(f"插件 {meta['id']} 的入口不存在：{exe}")
    return out
