"""阶段事件底座（T1–T3）：长任务"现在在干什么"的可见性。

设计（见 `docs/spec_observability.md` §5）：**一份事件流，两个消费者**。

    业务阶段 ──► 事件流 ──┬──► 终端渲染（给人看：阶段 + 耗时 + 进度 + 重要行）
                          └──► 事件文件（给机器/事后看：JSONL，稳定 schema）

三条硬不变量（都来自项目自己踩过的坑）：

1. **观测不得改变业务**。`docs/29` 记过"观测破坏业务语义"（上下文管理器重复 yield，
   把限流异常替换成了 RuntimeError）。所以这里所有上报都包在 `_safe()` 里：
   **上报失败只告警，绝不向上抛**。
2. **格式稳定**。事件是给机器读的（用户明确说"以后让 agent 自己读"），
   所以带 `version`，字段名不随手改。
3. **长阶段不能看起来卡死**。每个阶段起一个心跳线程，每 N 秒发一条"还活着"。
"""
from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1

START, PROGRESS, END, FAIL = "start", "progress", "end", "fail"

# 输出级别：安静（脚本）/ 正常（人，默认）/ 详细（调试）
LEVELS = ("quiet", "normal", "verbose")

# 进度打印的最小间隔：百分比跨过 10 的倍数才打一行，避免刷屏
_PROGRESS_STEP = 10


@dataclass
class StageEvent:
    """一条阶段事件。字段即 schema —— 改名等于破坏给机器读的契约。"""

    run_id: str
    stage: str
    kind: str
    ts: str
    elapsed_ms: int = 0
    message: str = ""
    fields: dict = field(default_factory=dict)
    version: int = SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, sort_keys=True)


class RunLog:
    """事件流的发布者。业务只管 `stage()` 与 `progress()`，两个消费者由这里管。"""

    def __init__(self, *, run_id: str | None = None, sink: Path | None = None,
                 level: str = "normal", heartbeat_sec: float = 5.0,
                 stream=None) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self.sink = sink
        self.level = level if level in LEVELS else "normal"
        self.heartbeat_sec = heartbeat_sec
        self.stream = stream if stream is not None else sys.stdout
        self._lock = threading.Lock()
        self._sink_broken = False
        self._events: list[dict] = []          # 供报告与测试读取

    # ---------------- 消费者 1：事件文件 ----------------

    def _write(self, ev: StageEvent) -> None:
        self._events.append(json.loads(ev.to_json()))
        if self.sink is None or self._sink_broken:
            return
        try:
            self.sink.parent.mkdir(parents=True, exist_ok=True)
            with self.sink.open("a", encoding="utf-8") as f:
                f.write(ev.to_json() + "\n")
        except Exception as e:                  # noqa: BLE001 —— 观测失败绝不影响业务
            self._sink_broken = True
            self._warn(f"事件文件写入失败，后续事件只走终端：{e}")

    # ---------------- 消费者 2：终端渲染 ----------------

    def _warn(self, msg: str) -> None:
        try:
            with self._lock:
                print(f"[warn] {msg}", file=self.stream, flush=True)
        except Exception:                       # noqa: BLE001
            pass

    def _print(self, text: str) -> None:
        if self.level == "quiet":
            return
        try:
            with self._lock:
                print(text, file=self.stream, flush=True)
        except Exception:                       # noqa: BLE001
            pass

    def important(self, msg: str) -> None:
        """重要行：正常档也显示（安静档仍然隐藏）。"""
        self._print(f"  {msg}")

    def raw(self, line: str) -> None:
        """底层工具的原样输出：**只有详细档**才流出来。"""
        if self.level == "verbose":
            self._print(f"    | {line.rstrip()}")

    # ---------------- 发事件 ----------------

    def _emit(self, stage: str, kind: str, *, elapsed_ms: int = 0,
              message: str = "", **fields) -> None:
        ev = StageEvent(run_id=self.run_id, stage=stage, kind=kind,
                        ts=datetime.now().astimezone().isoformat(timespec="seconds"),
                        elapsed_ms=elapsed_ms, message=message, fields=fields)
        self._safe(self._write, ev)

    def _safe(self, fn, *a) -> None:
        """**唯一**的上报入口：任何异常都被吞掉并告警（观测不得影响业务）。"""
        try:
            fn(*a)
        except Exception as e:                  # noqa: BLE001
            self._warn(f"事件上报失败（已忽略）：{e}")

    @contextmanager
    def stage(self, name: str, **fields):
        """阶段上下文：自动发 start / end（或 fail），并起心跳。

        `yield` 出来的是一个 `reporter`，阶段内部可以调 `reporter.progress(pct)`。
        """
        t0 = time.monotonic()
        self._emit(name, START, **fields)
        self._print(f"▶ {name}")

        stop = threading.Event()
        hb = None
        if self.level != "quiet" and self.heartbeat_sec > 0:
            hb = threading.Thread(target=self._heartbeat_loop, args=(name, stop, t0),
                                  daemon=True, name=f"hb-{name}")
            hb.start()
        reporter = _Reporter(self, name)
        try:
            yield reporter
        except BaseException as e:
            stop.set()
            ms = int((time.monotonic() - t0) * 1000)
            self._emit(name, FAIL, elapsed_ms=ms, message=str(e)[:200])
            self._print(f"✗ {name} 失败（{ms / 1000:.1f}s）：{str(e)[:120]}")
            raise
        else:
            stop.set()
            ms = int((time.monotonic() - t0) * 1000)
            msg = reporter.summary
            self._emit(name, END, elapsed_ms=ms, message=msg, **reporter.fields)
            self._print(f"✓ {name}  {ms / 1000:.1f}s" + (f"  {msg}" if msg else ""))
        finally:
            stop.set()
            if hb is not None:
                hb.join(timeout=1.0)

    def _heartbeat_loop(self, name: str, stop: threading.Event, t0: float) -> None:
        while not stop.wait(self.heartbeat_sec):
            ms = int((time.monotonic() - t0) * 1000)
            self._emit(name, PROGRESS, elapsed_ms=ms, message="心跳")
            self._print(f"· {name} 还在跑… {ms / 1000:.0f}s")

    # ---------------- 供报告使用 ----------------

    def events(self) -> list[dict]:
        return list(self._events)

    def stage_durations(self) -> dict[str, int]:
        """阶段名 → 耗时(ms)。给运行报告用。"""
        out: dict[str, int] = {}
        for ev in self._events:
            if ev["kind"] == END:
                out[ev["stage"]] = ev["elapsed_ms"]
        return out


class _Reporter:
    """阶段内部的把手：报进度、记摘要。**不暴露 RunLog 的内部**。"""

    def __init__(self, log: RunLog, stage: str) -> None:
        self._log = log
        self._stage = stage
        self.summary = ""
        self.fields: dict = {}
        self._last_pct = -_PROGRESS_STEP

    def progress(self, pct: float) -> None:
        """报进度（0–100）。只在跨过 10 的倍数时打一行，避免刷屏。"""
        pct = max(0.0, min(100.0, float(pct)))
        if pct < self._last_pct:            # 单调不减：回退就不报
            return
        self._log._emit(self._stage, PROGRESS, message=f"{pct:.0f}%", pct=round(pct, 1))
        if pct - self._last_pct >= _PROGRESS_STEP or pct >= 100:
            self._last_pct = pct
            self._log._print(f"  {self._stage} {pct:.0f}%")

    def done(self, summary: str = "", **fields) -> None:
        """阶段结束时想说的话（会出现在 end 事件与终端那行里）。"""
        self.summary = summary
        self.fields.update(fields)
