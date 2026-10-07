"""网关响应信封兼容（`llm.unwrap_data_envelope`）的测试。

**为什么需要它**：Cline 网关（`api.cline.bot`）把成功响应包在 `{"data": {...}, "success": true}` 里，
OpenAI SDK 只认顶层 `choices` → `r.choices` 是 `None` → 调用方拿到
`TypeError: 'NoneType' object is not subscriptable`。

**这个报错极具误导性**：排查时先怀疑了额度（`insufficient_credits`）、模型名、网络中断，
都不是 —— 它看起来完全不像协议问题。所以这个兼容层必须有测试钉住，
免得哪天被"简化"掉又回来。

判据必须**收窄**：标准 OpenAI 响应不能被误动，错误响应也不能被吞掉。
"""
from __future__ import annotations

import json

from app.llm import unwrap_data_envelope

# 标准 OpenAI 响应（顶层直接是 choices）
STANDARD = {
    "id": "chatcmpl-1", "object": "chat.completion", "model": "gpt-x",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
}
# Cline 形状（实测）
CLINE = {
    "data": {
        "id": "gen_01M4BD", "object": "chat.completion", "model": "cline-pass/x",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
        "usage": {"total_tokens": 151},
    },
    "success": True,
}


def b(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------- 剥

def test_cline_envelope_is_unwrapped():
    out = json.loads(unwrap_data_envelope(b(CLINE)))
    assert "data" not in out
    assert out["choices"][0]["message"]["content"] == "ok"
    assert out["usage"]["total_tokens"] == 151


def test_success_flag_is_dropped_with_the_envelope():
    out = json.loads(unwrap_data_envelope(b(CLINE)))
    assert "success" not in out, "外层字段不该混进标准响应里"


# ---------------------------------------------------------------- 不剥

def test_standard_openai_response_is_untouched():
    """**最要紧的反例**：标准响应不能被误动，否则会把正常服务商弄坏。"""
    raw = b(STANDARD)
    assert unwrap_data_envelope(raw) == raw


def test_error_response_is_untouched():
    """错误响应（`{"error": ...}`）要原样交给 SDK，别把错误吞了。"""
    raw = b({"error": "model not found", "success": False})
    assert unwrap_data_envelope(raw) == raw


def test_data_without_choices_is_untouched():
    """`data` 里没有 choices → 不是我们要处理的形状。"""
    raw = b({"data": {"foo": 1}, "success": True})
    assert unwrap_data_envelope(raw) == raw


def test_data_not_an_object_is_untouched():
    raw = b({"data": [1, 2, 3], "success": True})
    assert unwrap_data_envelope(raw) == raw


def test_non_json_is_untouched():
    raw = b"<html>502 Bad Gateway</html>"
    assert unwrap_data_envelope(raw) == raw


def test_empty_body_is_untouched():
    assert unwrap_data_envelope(b"") == b""


def test_top_level_choices_wins_over_nested_data():
    """两种形状同时出现时**以顶层为准**（更保守：不动）。"""
    raw = b({"choices": STANDARD["choices"], "data": CLINE["data"], "success": True})
    assert unwrap_data_envelope(raw) == raw
