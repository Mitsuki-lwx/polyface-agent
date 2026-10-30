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
4. **读 stdin 必须"读一行"，不能"读到 EOF"。** 加载器会把进程热着（见 `_Worker`：
   模型加载一次 ~25s，一问一答就退出的话每个分镜都白等），stdin 不会关 ——
   Python 里用 `sys.stdin.readline()`，用 `sys.stdin.read()` 会一直等下去直接挂死。
   实测踩过：常驻化之后两个内置插件当场挂死，是测试抓出来的。

回完就退出也行（一问一答型）：加载器发现 EOF 会重开，**老插件不用改**。
想拿加速就循环读行（`for line in sys.stdin:`）—— 常驻插件只起一次进程。
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "tts-plugins"
DEFAULT_TIMEOUT = 120

REQUIRED = ("id", "name", "kind", "run")


class PluginError(RuntimeError):
    """插件不可用/调用失败。**消息里必须带插件 id**。"""


class _Worker:
    """一个常驻的插件进程。

    **为什么要有它**：契约是"一问一答"，但如果每次都新起进程，插件就得每问一次重载一次
    模型 —— Kokoro 实测 **25 秒/次**，10 个分镜白等 4 分钟。

    所以加载器**替插件把进程热着**：
    - 插件选择常驻（读完一行再读下一行）→ 直接白拿加速
    - 插件回完就退出 → 加载器发现 EOF，下次重开（**老插件照常能用，不用改**）

    超时靠读线程 + 队列（Windows 上管道不能 `select`，这是可移植的做法）。
    """

    def __init__(self, meta: dict) -> None:
        self.meta = meta
        self.id = meta["id"]
        self.dir = meta["_dir"]
        self.cmd = _resolve_cmd(meta.get("run") or {}, meta)
        self.timeout = int((meta.get("run") or {}).get("timeout_sec") or DEFAULT_TIMEOUT)
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.queue: queue.Queue = queue.Queue()
        self.tail: list[str] = []          # 出错时给人看的那几行

    def _spawn(self) -> None:
        # **队列与进程必须成对**：读线程若往"当前队列"里塞（`self.queue`），
        # 重开之后新调用就会读到**旧进程的回复** —— 实测：请求 `DDD` 拿到旧进程的 `CCC`，
        # 一个**静默的错误结果**（时长/音色全串了）。所以队列与 tail 都当参数传进去。
        q: queue.Queue = queue.Queue()
        tail: list[str] = []
        self.queue = q
        self.tail = tail
        self.proc = subprocess.Popen(
            self.cmd, cwd=self.dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            bufsize=1)
        threading.Thread(target=self._read_stdout, args=(self.proc, q), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(self.proc, tail), daemon=True).start()

    @staticmethod
    def _read_stdout(p: subprocess.Popen, q: queue.Queue) -> None:
        try:
            for line in p.stdout:                        # type: ignore[union-attr]
                q.put(line)
        except Exception:                                # noqa: BLE001
            pass
        q.put(None)                                      # EOF 标记

    @staticmethod
    def _read_stderr(p: subprocess.Popen, tail: list[str]) -> None:
        try:
            for line in p.stderr:                        # type: ignore[union-attr]
                tail.append(line.rstrip())
                del tail[:-20]                           # 只留最后 20 行
        except Exception:                                # noqa: BLE001
            pass

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def call(self, req: dict) -> dict:
        """一问一答。进程死了会自动重开一次。"""
        with self.lock:
            if not self.alive():
                self._spawn()
            try:
                return self._once(req)
            except (BrokenPipeError, OSError) as first:
                # 插件上次回完就退出了（一问一答型）—— 重开再来
                self._spawn()
                try:
                    return self._once(req)
                except (BrokenPipeError, OSError):
                    # 重开还是立刻退出：多半是入口坏了/一启动就崩。
                    # **要带上它的 stderr**，不然"插件进程已退出"没法排查（实测踩过）。
                    raise PluginError(
                        f"插件 {self.id} 起不来或一启动就退出"
                        f"（退出码 {self.proc.poll() if self.proc else '?'}）："
                        f"{' / '.join(self.tail[-3:])[:300] or '(没有输出)'}"
                        f"；重开前的失败：{first}") from None

    def _once(self, req: dict) -> dict:
        assert self.proc and self.proc.stdin
        try:
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except Exception as e:                           # noqa: BLE001
            # **必须抛 OSError**（`call()` 靠它走"重开重试"）。
            # 早先这里抛 `PluginError`（RuntimeError）—— `call()` 的
            # `except (BrokenPipeError, OSError)` **接不到**，于是
            # "进程正在退出时写不进去"（Windows 上偶发 `[Errno 22]`）
            # 直接变成整镜配音失败。实测：一次成片 11 个分镜里 **3 个丢了旁白**
            # （`tts.failed` 记着原因，成片被静音垫片顶住，但旁白就是没有）。
            raise BrokenPipeError(
                f"插件 {self.id} 写不进去（可能已退出）：{e}") from None

        deadline = time.monotonic() + self.timeout
        noise: list[str] = []                        # 跳过掉的 stdout 噪音（超时时拿来说清情况）
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                self.kill()
                # **别说"超时"就完事**：插件可能早就回过话，只是不回 JSON。
                # 不带出这些行的话，用户会去查"为什么卡住"，而真相是"它答非所问" ✗
                # （实测：插件打印一句普通文本，5s 后报"超时"，看着像挂死）。
                tail = f"；它的 stdout 有输出但不是 JSON：{' / '.join(noise[-3:])[:200]}" if noise else ""
                raise PluginError(f"插件 {self.id} 超时（{self.timeout}s）{tail}")
            try:
                line = self.queue.get(timeout=min(remain, 5.0))
            except queue.Empty:
                continue
            if line is None:                             # EOF：进程没了
                raise BrokenPipeError("插件进程已退出")
            s = line.strip()
            if not s.startswith("{"):
                noise.append(s[:80])                     # 插件打的日志，跳过（但留个底）
                del noise[:-3]
                continue
            try:
                return json.loads(s)
            except Exception:                            # noqa: BLE001
                raise PluginError(f"插件 {self.id} 的 stdout 不是合法 JSON：{s[:160]}") from None

    def kill(self) -> None:
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.kill()
        except Exception:                                # noqa: BLE001
            pass


_WORKERS: dict[str, _Worker] = {}


def _worker_for(meta: dict) -> _Worker:
    w = _WORKERS.get(meta["id"])
    if w is None or w.dir != meta["_dir"]:
        if w is not None:
            w.kill()
        w = _Worker(meta)
        _WORKERS[meta["id"]] = w
    return w


def shutdown(wait: float = 2.0) -> None:
    """收工：把所有常驻插件进程关掉（脚本结束时调，免得留孤儿）。

    `wait` 是给进程一点时间真的退出 —— 刚 `kill()` 就断言"已死"会偶发失败（实测）。
    """
    ws = list(_WORKERS.values())
    for w in ws:
        w.kill()
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and any(w.alive() for w in ws):
        time.sleep(0.05)
    _WORKERS.clear()


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

    # **交给插件的路径一律用正斜杠**：Windows API 认 `/`，而 bash 会把 `\` 当转义。
    # 实测踩过：`out` 带反斜杠时 shell 插件把文件写到了被改坏的路径上，
    # 然后报"说成功但没产出" —— 这种坑不该让每个插件作者各踩一次。
    req = {"text": text, "voice": voice, "out": out.as_posix(),
           "rate": int(rate), "volume": int(volume), "params": params or {}}

    resp = _worker_for(meta).call(req)

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
    """入口命令。支持占位符（相对路径一律用正斜杠 —— 反斜杠会被 bash 当转义）：

    - `{python}`：主程序的解释器
    - `{kokoro_python}`：Kokoro 的 Python 3.12 环境（它要求 <3.13，装不进主环境）
    - `{dir}`：插件自己的目录
    """
    raw = run.get("command")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise PluginError(f"插件 {meta['id']} 的 run.command 不合法：{raw!r}")
    kpy = os.environ.get("POLYFACE_KOKORO_PYTHON") or _default_kokoro_python()
    repl = {"{python}": Path(sys.executable).as_posix(),
            "{kokoro_python}": Path(kpy).as_posix(),
            "{dir}": Path(meta["_dir"]).as_posix()}
    out = []
    for part in raw:
        s = str(part)
        for k, v in repl.items():
            s = s.replace(k, v)
        out.append(s)
    exe = out[0]
    if not (Path(exe).is_file() or shutil.which(exe)):
        raise PluginError(f"插件 {meta['id']} 的入口不存在：{exe}")
    return out


def _default_kokoro_python() -> str:
    """Kokoro 需要 Python <3.13，所以它有自己的 venv（`python-service/.venv-kokoro`）。"""
    base = Path(__file__).resolve().parents[2] / ".venv-kokoro"
    for rel in ("Scripts/python.exe", "bin/python"):
        p = base / rel
        if p.is_file():
            return str(p)
    return str(base / "Scripts" / "python.exe")          # 不存在时给出预期路径（报错里能看见）
