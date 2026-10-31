"""本地用量落库（`docs/28-...-checklist.md` D 节）。

**为什么单独测**：这一层原先**一条测试都没有** ✗。
它出错的形态同样是静默的 —— 字段少一个，聚合永远是 0；
或者把 prompt 正文写进去（**脱敏红线** ✗），而没人会发现。

判据取"读回来的文件内容"与"聚合出的数字"，不去断言源码。
"""
from __future__ import annotations

import json

import pytest

from app import usage


@pytest.fixture()
def ufile(tmp_path, monkeypatch):
    p = tmp_path / "llm-usage.jsonl"
    monkeypatch.setenv("POLYFACE_USAGE_FILE", str(p))
    return p


# ---------------------------------------------------------------- D1~D4

def test_every_call_appends_one_jsonl_line(ufile):
    """D1：每次调用追加**一行**。"""
    for i in range(3):
        usage.record_call(trace_id="t", scene=f"s{i}", model="m", attempt=1, ok=True)
    lines = ufile.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert all(json.loads(l) for l in lines)


def test_record_has_the_required_fields(ufile):
    """D2/D3：必需字段齐全；有 token 就记 token，没有就留 null。"""
    usage.record_call(trace_id="tid", scene="draft", platform="xhs", model="m1",
                      attempt=2, ok=True, duration_ms=1234,
                      prompt_chars=100, completion_chars=200,
                      prompt_tokens=11, completion_tokens=22)
    e = json.loads(ufile.read_text(encoding="utf-8").strip().splitlines()[0])
    for k in ("ts", "trace_id", "scene", "platform", "model",
              "attempt", "ok", "error_type", "duration_ms"):
        assert k in e, f"缺字段 {k}"
    assert (e["prompt_chars"], e["completion_chars"]) == (100, 200)
    assert (e["prompt_tokens"], e["completion_tokens"]) == (11, 22)

    usage.record_call(scene="qa", model="m1", attempt=1, ok=True, prompt_chars=1)
    e2 = json.loads(ufile.read_text(encoding="utf-8").strip().splitlines()[1])
    assert e2["prompt_tokens"] is None, "拿不到 token 时留 null，不要编一个数"


def test_jsonl_never_contains_prompt_or_response_text(ufile):
    """D4：**脱敏红线** —— 只记长度，绝不记正文。"""
    secret = "这是一段绝不该落盘的正文，里面还有手机号 13900001111"
    usage.record_call(trace_id="t", scene="draft", model="m", prompt_chars=len(secret),
                      completion_chars=len(secret))
    raw = ufile.read_text(encoding="utf-8")
    assert secret[:12] not in raw, "正文泄漏到用量文件里了"
    assert "13900001111" not in raw


def test_mock_calls_are_marked(ufile):
    """D9：mock 调用要么不落盘，要么**标出来**（不能和真实调用混在一起）。"""
    usage.record_call(scene="draft", model="", mock=True, ok=True)
    e = json.loads(ufile.read_text(encoding="utf-8").strip().splitlines()[0])
    assert e["mock"] is True
    assert e["model"] == "mock", f"mock 不该伪装成真模型名：{e['model']!r}"


# ---------------------------------------------------------------- D5

def test_summary_aggregates(ufile):
    """D5：聚合里有 total / failures / retries / avg / by_model / by_scene / recent。"""
    usage.record_call(scene="draft", model="m1", attempt=1, ok=True, duration_ms=100)
    usage.record_call(scene="draft", model="m1", attempt=3, ok=False, duration_ms=300,
                      error_type="RateLimited")
    usage.record_call(scene="qa", model="m2", attempt=1, ok=True, duration_ms=200)
    s = usage.summary()
    assert s["total_calls"] == 3
    assert s["failures"] == 1
    assert s["retries"] == 2, "attempt=3 记 2 次重试"
    assert s["avg_duration_ms"] == 200
    assert s["by_model"] == {"m1": 2, "m2": 1}
    assert s["by_scene"] == {"draft": 2, "qa": 1}
    assert len(s["recent"]) == 3
    assert s["recent"][0]["scene"] == "qa", "recent 应当是倒序（最新在前）"


# ---------------------------------------------------------------- D8

def test_corrupt_lines_do_not_break_aggregation(ufile):
    """D8：半行/坏行**跳过**，不影响聚合（进程被杀时最常见的现场）。"""
    usage.record_call(scene="draft", model="m1", attempt=1, ok=True)
    with ufile.open("a", encoding="utf-8") as f:
        f.write('{"half": ')                      # 写到一半就死了
        f.write("\n")
        f.write("这不是 json\n")
    usage.record_call(scene="qa", model="m2", attempt=1, ok=True)
    s = usage.summary()
    assert s["total_calls"] == 2, f"坏行应被跳过：{s}"
    assert s["by_scene"] == {"draft": 1, "qa": 1}


def test_missing_file_is_empty_not_an_error(ufile, monkeypatch, tmp_path):
    monkeypatch.setenv("POLYFACE_USAGE_FILE", str(tmp_path / "nope.jsonl"))
    s = usage.summary()
    assert s["total_calls"] == 0
    assert s["by_model"] == {}
