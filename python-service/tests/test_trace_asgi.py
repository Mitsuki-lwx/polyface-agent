"""trace 上下文**跨 ASGI 边界**（`docs/28-...-checklist.md` C 节，FR-71）。

**为什么单独测**：中间件是 `async def`（跑在事件循环里），而 `/generate` 这类端点是
普通的 `def` —— FastAPI 会把它们丢进**线程池**执行。`contextvars` **不跨线程自动继承**，
所以中间件里 `set_trace_id(...)` 设的值，端点的处理函数里**可能根本看不到** ✗。

后果不是报错，而是**静默错位**：
- 业务日志/用量记录里的 trace_id 是端点线程里**现生成**的另一个 ✗
- Langfuse 里的 trace id 也就成了那一个 ✗
- 于是「从日志里那个 id 去 Langfuse 查」**查不到** ✗（FR-71 的全部价值就没了）

实测（2026-10-31）：`POST /generate` 的响应头是 `91b6c49c…`，
而 Langfuse 上那条 trace 是 `c0cf063a…` —— 两个 id ✗。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import llm, trace, usage
from app.main import app
from app.config import get_settings

TID = "0123456789abcdef0123456789abcdef"


def test_sync_endpoint_sees_the_header_trace_id(monkeypatch, tmp_path):
    """**核心断言**：sync 端点里拿到的 trace_id 必须等于请求头那个。

    不真的调 LLM —— 只测"上下文有没有跨过 ASGI → 线程池这道墙"。
    """
    monkeypatch.setattr(llm, "is_mock", lambda: True)      # 走 mock，快且不花钱
    monkeypatch.setenv("POLYFACE_USAGE_FILE", str(tmp_path / "u.jsonl"))
    get_settings.cache_clear()

    seen: dict = {}

    @app.get("/__t_probe")
    def _probe():
        # **普通的 def** → FastAPI 会把它丢进线程池（这正是要测的那道墙）
        seen["tid"] = trace.current_trace_id()
        return {"tid": seen["tid"]}

    try:
        c = TestClient(app)
        r = c.get("/__t_probe", headers={trace.HEADER: TID})
        assert r.status_code == 200, r.text
        assert r.json()["tid"] == TID, f"端点线程里拿到的是 {r.json()['tid']}，不是请求头的 {TID}"
        assert r.headers.get(trace.HEADER) == TID, "响应头也该回同一个 id"
    finally:
        app.router.routes = [x for x in app.router.routes
                             if getattr(x, "path", "") != "/__t_probe"]
        get_settings.cache_clear()


def test_async_endpoint_sees_the_header_trace_id(monkeypatch):
    """对照组：`async def` 端点在同一条事件循环上，本来就能拿到 ✓。"""
    monkeypatch.setattr(llm, "is_mock", lambda: True)

    @app.get("/__t_probe_async")
    async def _probe():
        return {"tid": trace.current_trace_id()}

    try:
        c = TestClient(app)
        r = c.get("/__t_probe_async", headers={trace.HEADER: TID})
        assert r.json()["tid"] == TID
    finally:
        app.router.routes = [x for x in app.router.routes
                             if getattr(x, "path", "") != "/__t_probe_async"]
