"""trace 上下文（FR-71）：跨 SPA → Java → Python → LLM 的链路标识。

- Java 通过 `X-Trace-Id` header 传入；无则自动生成（不报错）
- 用 `contextvars` 存储，兼容 asyncio
- ⚠️ **ThreadPoolExecutor 不会自动继承 contextvars** —— 提交任务时必须用
  `run_in_context()` 显式携带上下文，否则子线程内 trace_id 丢失（会各自新生成）
"""
from __future__ import annotations

import contextvars
import uuid

HEADER = "X-Trace-Id"

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("polyface_trace_id", default="")


def new_trace_id() -> str:
    """32 位 hex —— 遵循 W3C Trace Context（Langfuse v4 的 trace_id 规范）。"""
    return uuid.uuid4().hex


def set_trace_id(tid: str | None) -> contextvars.Token:
    """设置当前上下文 trace_id；为空则生成。"""
    return _trace_id.set((tid or "").strip() or new_trace_id())


def current_trace_id() -> str:
    """读取当前 trace_id；若未设置则生成并写回当前上下文。"""
    tid = _trace_id.get()
    if not tid:
        tid = new_trace_id()
        _trace_id.set(tid)
    return tid


def run_in_context(fn, *args, **kwargs):
    """在**当前上下文快照**中执行 fn —— 供 ThreadPoolExecutor 提交使用。

    用法：
        pool.submit(trace.run_in_context, _gen_one, platform)
    """
    return contextvars.copy_context().run(fn, *args, **kwargs)
