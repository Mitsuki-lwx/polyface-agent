"""`llm.chat_json` 的 **JSON 解析失败重试**（2026-10-07 修）。

**为什么要测**：`chat()` 的重试只管网络/限流/额度，而 JSON 解析发生在它返回**之后**。
修前不重试 —— 模型偶发返回一次坏 JSON，整个平台生成直接失败。

实测（冒烟两轮）：4 次草稿调用里 2 次中招，报
`LLMParseError: Expecting ',' delimiter: line 7 column 206`。

**不变式**：
1. 第一次坏、第二次好 → 必须**返回成功**，且真的调了两次
2. 一直坏 → 抛 `LLMParseError`，且调用次数**恰好**等于 `llm_json_retries`（不多不少）
3. 第一次就好 → **只调一次**（重试不能变成"每次都多打一次"）
4. `llm_json_retries=1` → 等于不重试（可配项必须真的起作用）
5. 每次解析失败都**进用量日志** —— 否则「这件事多常发生」只能靠翻日志猜
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import llm, usage
from app.config import Settings


def _settings(monkeypatch, **overrides) -> Settings:
    """替换 `llm` 模块看到的 Settings（`get_settings()` 带 lru_cache，不能用环境变量控）。"""
    s = Settings(**overrides)
    monkeypatch.setattr(llm, "get_settings", lambda: s)
    return s


def _stub_chat(monkeypatch, replies: list[str]) -> list[int]:
    """把 `llm.chat` 换成按序返回预设文本的桩。返回"调用次数"的可变计数器。"""
    calls = [0]

    def fake_chat(*_a, **_k) -> str:
        i = min(calls[0], len(replies) - 1)
        calls[0] += 1
        return replies[i]

    monkeypatch.setattr(llm, "chat", fake_chat)
    # 退避真的睡会拖慢测试；这里只验证"会不会重试"，不验证睡多久
    monkeypatch.setattr(llm.time, "sleep", lambda *_a, **_k: None)
    return calls


def _usage_lines() -> list[dict]:
    p = usage.usage_path()
    if not Path(p).exists():
        return []
    return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]


BAD = '{"titles": ["a" "b"]}'          # 缺逗号 —— 与实测报错同形
GOOD = '{"titles": ["a", "b"]}'


# ---------------------------------------------------------------- 重试生效

def test_bad_then_good_returns_success(monkeypatch):
    """**修的核心**：第一次坏、第二次好 → 必须成功，而不是把 LLMParseError 抛给调用方。"""
    _settings(monkeypatch, llm_json_retries=2)
    calls = _stub_chat(monkeypatch, [BAD, GOOD])
    out = llm.chat_json("写点什么", scene="draft")
    assert out == {"titles": ["a", "b"]}
    assert calls[0] == 2, "应当恰好重试一次"


def test_first_try_good_calls_chat_once(monkeypatch):
    """反例守卫：重试不能变成"每次都多打一次"（那会白白翻倍成本）。"""
    _settings(monkeypatch, llm_json_retries=2)
    calls = _stub_chat(monkeypatch, [GOOD])
    assert llm.chat_json("x") == {"titles": ["a", "b"]}
    assert calls[0] == 1


def test_markdown_fence_still_parsed(monkeypatch):
    """容错剥离围栏的行为不能被重试改坏。"""
    _settings(monkeypatch, llm_json_retries=2)
    calls = _stub_chat(monkeypatch, ["```json\n" + GOOD + "\n```"])
    assert llm.chat_json("x") == {"titles": ["a", "b"]}
    assert calls[0] == 1


# ---------------------------------------------------------------- 上限与可配

def test_always_bad_raises_after_exactly_n_attempts(monkeypatch):
    _settings(monkeypatch, llm_json_retries=3)
    calls = _stub_chat(monkeypatch, [BAD])
    with pytest.raises(llm.LLMParseError):
        llm.chat_json("x")
    assert calls[0] == 3, "调用次数必须恰好等于 llm_json_retries"


def test_retries_is_configurable(monkeypatch):
    """可调项必须真的起作用：1 = 不重试，4 = 试四次。"""
    _settings(monkeypatch, llm_json_retries=1)
    calls = _stub_chat(monkeypatch, [BAD])
    with pytest.raises(llm.LLMParseError):
        llm.chat_json("x")
    assert calls[0] == 1

    _settings(monkeypatch, llm_json_retries=4)
    calls = _stub_chat(monkeypatch, [BAD])
    with pytest.raises(llm.LLMParseError):
        llm.chat_json("x")
    assert calls[0] == 4


def test_zero_or_negative_falls_back_to_one(monkeypatch):
    """防御：配成 0 或负数时至少试一次，不能一次都不试就抛。"""
    _settings(monkeypatch, llm_json_retries=0)
    calls = _stub_chat(monkeypatch, [BAD])
    with pytest.raises(llm.LLMParseError):
        llm.chat_json("x")
    assert calls[0] == 1


# ---------------------------------------------------------------- 可观测

def test_parse_failures_are_recorded_in_usage(monkeypatch):
    """**每次**解析失败都要进用量日志 —— 否则"多常发生"无法回答。"""
    _settings(monkeypatch, llm_json_retries=2)
    _stub_chat(monkeypatch, [BAD, GOOD])
    before = len(_usage_lines())
    llm.chat_json("x", scene="draft", platform="xhs")
    new = _usage_lines()[before:]
    assert len(new) == 1, f"应记 1 条解析失败，实际 {len(new)}"
    e = new[0]
    assert e["ok"] is False
    assert e["error_type"] == "LLMParseError"
    assert e["scene"] == "draft"
    assert e["platform"] == "xhs"


def test_no_usage_record_when_parsing_succeeds(monkeypatch):
    _settings(monkeypatch, llm_json_retries=2)
    _stub_chat(monkeypatch, [GOOD])
    before = len(_usage_lines())
    llm.chat_json("x")
    assert len(_usage_lines()) == before


# ---------------------------------------------------------------- 边界

def test_empty_response_is_retried_then_raises(monkeypatch):
    """空响应也是解析失败，同样要走重试路径。"""
    _settings(monkeypatch, llm_json_retries=2)
    calls = _stub_chat(monkeypatch, [""])
    with pytest.raises(llm.LLMParseError):
        llm.chat_json("x")
    assert calls[0] == 2


def test_non_object_json_is_rejected(monkeypatch):
    """顶层不是对象（如数组）不算合法产物。"""
    _settings(monkeypatch, llm_json_retries=2)
    _stub_chat(monkeypatch, ["[1, 2, 3]"])
    with pytest.raises(llm.LLMParseError):
        llm.chat_json("x")
