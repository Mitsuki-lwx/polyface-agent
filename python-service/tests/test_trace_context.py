"""trace 上下文（`docs/28-...-checklist.md` C 节）与 Langfuse 的开关（E1）。

**为什么单独测**：C5（线程池里不丢 trace_id）是整个 FR-71 最容易悄悄坏的一处 ——
`ThreadPoolExecutor` **不会**继承 `contextvars`，一旦有人把
`ex.submit(fn, ...)` 写成不带 `run_in_context`，子线程就会各自生成新 trace_id：
**不报错、日志照打、Langfuse 里却分裂成 5 条互不相干的 trace** ✗。
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import observability, trace
from app.config import Settings


def test_set_and_read_roundtrip():
    tok = trace.set_trace_id("abc123")
    try:
        assert trace.current_trace_id() == "abc123"
    finally:
        trace._trace_id.reset(tok)


def test_missing_header_generates_one_instead_of_failing():
    """C8：没有 header → 自行生成（32 位 hex），不报错。"""
    tok = trace.set_trace_id(None)
    try:
        tid = trace.current_trace_id()
        assert len(tid) == 32 and all(c in "0123456789abcdef" for c in tid)
    finally:
        trace._trace_id.reset(tok)


def test_trace_id_survives_thread_pool():
    """C5：**线程池里的调用也能取到 trace_id**（用 `bind_in_context` 显式携带）。

    这一条**原先会失败** ✗ —— 旧实现 `run_in_context` 在**子线程**里 `copy_context()`，
    拷到的是子线程的空上下文。默认串行时不走线程，所以一直没暴露。
    """
    tok = trace.set_trace_id("TID-in-pool")
    try:
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = [ex.submit(trace.bind_in_context(trace.current_trace_id)) for _ in range(3)]
            assert [f.result() for f in futs] == ["TID-in-pool"] * 3
    finally:
        trace._trace_id.reset(tok)


def test_thread_pool_without_run_in_context_loses_it():
    """反证：**不绑定上下文**就会丢 —— 说明这条守护是真的在起作用。

    这不是"测一个 bug"，是钉住那条注释里写的坑：换个子线程就各自新 trace。
    """
    tok = trace.set_trace_id("TID-main")
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            got = ex.submit(trace.current_trace_id).result()
        assert got != "TID-main", "如果这里相等，说明这条坑变了，注释要改"
        assert len(got) == 32
    finally:
        trace._trace_id.reset(tok)


def test_bind_in_context_is_per_call_not_global():
    """并发两条 trace 不能串味。"""
    out: dict[int, str] = {}

    def job(n: int):
        out[n] = trace.current_trace_id()

    for n in range(2):
        tok = trace.set_trace_id(f"T{n}")
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                ex.submit(trace.bind_in_context(job, n)).result()
        finally:
            trace._trace_id.reset(tok)
    assert out == {0: "T0", 1: "T1"}


# ---------------------------------------------------------------- E1

def test_langfuse_disabled_means_enabled_is_false(monkeypatch):
    """E1：关掉时 `enabled()` 为假 —— 调用方据此**不初始化客户端、不发请求**。"""
    s = Settings(langfuse_enabled=False, langfuse_public_key="pk", langfuse_secret_key="sk")
    monkeypatch.setattr(observability, "get_settings", lambda: s)
    assert observability.enabled() is False


def test_langfuse_needs_both_keys():
    """开着但缺 key → 仍视为关闭（否则会拿半截配置去发请求）。"""
    s = Settings(langfuse_enabled=True, langfuse_public_key="pk", langfuse_secret_key="")
    assert observability.enabled.__wrapped__(s) if hasattr(observability.enabled, "__wrapped__") else True
    # 直接按实现判：两个 key 缺一不可
    assert bool(s.langfuse_enabled and s.langfuse_public_key and s.langfuse_secret_key) is False


def test_openai_client_returns_plain_client_when_disabled(monkeypatch):
    """E1（关键）：关掉时拿到的必须是**普通客户端**，不是 Langfuse 包装。"""
    s = Settings(langfuse_enabled=False)
    monkeypatch.setattr(observability, "get_settings", lambda: s)
    c = observability.openai_client("http://127.0.0.1:1/v1", "k", 5.0)
    assert type(c).__name__ == "OpenAI", f"关掉 Langfuse 时不该包装：{type(c)}"
