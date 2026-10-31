"""LLM 运行时加固：错误分类 / 退避 / 重试上限 / 模型降级链（`docs/28-...-checklist.md` A、B 两节）。

**为什么单独建这个文件**：这套加固原先**一条专门的测试都没有** ✗ ——
只在 `test_llm_json_retry` / `test_qa_hardening` 里被间接碰到。
而它出错的形态是**静默**的：分类错一个分支，重试策略就整个变形（额度耗尽也去重试、限流却直接放弃）。

判据都取"**可观测的外部行为**"（调用次数、抛出的异常类型、用量日志里的 model 序列），
不去断言源码写了什么。
"""
from __future__ import annotations

import types

import pytest

from app import llm, observability, usage
from app.config import Settings


# ---------------------------------------------------------------- 工具

def _settings(monkeypatch, **overrides) -> Settings:
    s = Settings(**overrides)
    monkeypatch.setattr(llm, "get_settings", lambda: s)
    return s


class _Resp:
    def __init__(self, text: str, pt: int | None = None, ct: int | None = None):
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=text))]
        self.usage = None if pt is None else types.SimpleNamespace(prompt_tokens=pt, completion_tokens=ct)


class _Completions:
    def __init__(self, behaviour, calls: list):
        self._behaviour = behaviour
        self._calls = calls

    def create(self, **kw):
        self._calls.append(kw.get("model"))
        return self._behaviour(kw.get("model"), len(self._calls))


def _fake_client(monkeypatch, behaviour, calls: list) -> None:
    """把 `observability.openai_client` 换成一个假客户端。

    **必须换 `openai_client` 而不是 `chat`** —— 要测的正是 `chat` 自己的重试/降级循环。
    """
    class _C:
        def __init__(self):
            self.chat = types.SimpleNamespace(completions=_Completions(behaviour, calls))

    monkeypatch.setattr(observability, "openai_client", lambda *a, **k: _C())
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)     # 别真等


def _record_calls(monkeypatch) -> list:
    """把用量记录换成内存收集 —— 判"哪个模型被试过、第几次"。"""
    recs: list = []
    monkeypatch.setattr(usage, "record_call", lambda **kw: recs.append(kw))
    return recs


def _http_err(msg: str, status: int | None = None) -> Exception:
    e = Exception(msg)
    if status is not None:
        e.status_code = status   # type: ignore[attr-defined]
    return e


# ---------------------------------------------------------------- A. 错误分类

@pytest.mark.parametrize("exc,expect", [
    (_http_err("Request timed out"), llm.LLMTimeout),
    (_http_err("connection error"), llm.LLMNetworkError),
    (_http_err("429 Too Many Requests: tpm/rpm limit"), llm.RateLimited),
    (_http_err("429 insufficient_quota"), llm.QuotaExhausted),
    (_http_err("model route not found"), llm.QuotaExhausted),
    (_http_err("model is not found"), llm.QuotaExhausted),
    (_http_err("upstream exploded", status=503), llm.LLMNetworkError),
])
def test_classify_buckets(exc, expect):
    """A1~A5：每条上游失败都归到对的桶里（桶决定"重试"还是"换模型"）。"""
    assert isinstance(llm.classify(exc), expect)


def test_quota_beats_rate_limit_when_both_features_present():
    """两条特征同时出现时，**额度优先** —— 否则会把"没额度"当成"限流"，白等三轮。"""
    e = _http_err("429 insufficient_quota / tpm limit")
    assert isinstance(llm.classify(e), llm.QuotaExhausted)


def test_retryable_flags_match_the_policy():
    assert llm.RateLimited.retryable is True
    assert llm.LLMTimeout.retryable is True
    assert llm.LLMNetworkError.retryable is True
    assert llm.QuotaExhausted.retryable is False
    assert llm.LLMParseError.retryable is False


# ---------------------------------------------------------------- A. 退避

def test_backoff_series_is_capped_at_8s_plus_jitter():
    """A6：`min(2^n, 8) + jitter`。抖动在 [0, 0.5)，所以每一档是一个区间。"""
    for n in range(0, 6):
        base = min(2 ** n, 8)
        for _ in range(20):
            v = llm.backoff_seconds(n)
            assert base <= v < base + 0.5, f"第 {n} 档应为 [{base}, {base + 0.5})，实际 {v}"


# ---------------------------------------------------------------- A. 重试上限 / 换模型

def test_retries_up_to_max_attempts_then_moves_on(monkeypatch):
    """A7：`llm_max_attempts=3` → 同一个模型**恰好试 3 次**，然后放弃。"""
    _settings(monkeypatch, llm_mock=False, llm_api_key="k",
              llm_model="m1", llm_fallback_models="", llm_max_attempts=3)
    calls: list = []
    _fake_client(monkeypatch, lambda mdl, n: (_ for _ in ()).throw(_http_err("429 tpm limit")), calls)
    _record_calls(monkeypatch)
    with pytest.raises(llm.RateLimited, match="已尝试"):
        llm.chat("hi", scene="t")
    assert calls == ["m1"] * 3


def test_quota_does_not_retry_same_model(monkeypatch):
    """A8：额度耗尽**不重试**，立刻换下一个模型。"""
    _settings(monkeypatch, llm_mock=False, llm_api_key="k",
              llm_model="m1", llm_fallback_models="m2", llm_max_attempts=3)

    def behaviour(mdl, _n):
        if mdl == "m1":
            raise _http_err("insufficient_quota")
        return _Resp("ok from m2")

    calls: list = []
    _fake_client(monkeypatch, behaviour, calls)
    _record_calls(monkeypatch)
    assert llm.chat("hi", scene="t") == "ok from m2"
    assert calls == ["m1", "m2"], "m1 只该试一次"


def test_all_models_exhausted_error_lists_what_was_tried(monkeypatch):
    """A9：全耗尽 → 可读错误，且**含已尝试的模型列表**。"""
    _settings(monkeypatch, llm_mock=False, llm_api_key="k",
              llm_model="m1", llm_fallback_models="m2,m3", llm_max_attempts=1)
    calls: list = []
    _fake_client(monkeypatch, lambda mdl, n: (_ for _ in ()).throw(_http_err("insufficient_quota")), calls)
    _record_calls(monkeypatch)
    with pytest.raises(llm.QuotaExhausted) as e:
        llm.chat("hi", scene="t")
    msg = str(e.value)
    assert "m1" in msg and "m2" in msg and "m3" in msg, msg
    assert calls == ["m1", "m2", "m3"]


def test_mock_mode_never_touches_the_hardening(monkeypatch):
    """A10：mock 模式下**根本不发请求**。

    ⚠️ 分流在**调用方**（`generate._generate_one` 等处的 `if llm.is_mock():`），
    不在 `chat()` 里 —— 所以这条要测调用方，测 `chat()` 会测错层 ✗（第一版就是这么错的）。
    """
    _settings(monkeypatch, llm_mock=True)
    calls: list = []
    _fake_client(monkeypatch, lambda mdl, n: _Resp("不该被调用"), calls)
    monkeypatch.setattr(llm, "is_mock", lambda: True)
    from app.pipeline import generate as gen

    gen.run_brief("xhs", {"name": "小红书"}, _fg_mat(), None)   # mock 下直接走模板，不该碰 LLM
    assert calls == [], f"mock 模式不该发请求，实际打了：{calls}"


def _fg_mat():
    from app.schemas import Fact, StructuredMaterial
    return StructuredMaterial(core_message="c", tone="t", audience="a",
                              facts=[Fact(text="卡里就剩两万三。", type="data")])


# ---------------------------------------------------------------- B. 模型降级链

def test_chain_is_primary_then_fallbacks_deduped(monkeypatch):
    """B1/B2：链 = 主模型 + 追加项（去重保序），且能被配置覆盖。"""
    _settings(monkeypatch, llm_model="a", llm_fallback_models="b, c ,a")
    assert llm.model_chain() == ["a", "b", "c"]


def test_explicit_model_bypasses_the_chain(monkeypatch):
    """B5：显式指定 `model=` → 只用它（尊重调用方）。"""
    _settings(monkeypatch, llm_model="a", llm_fallback_models="b,c")
    assert llm.model_chain("only-this") == ["only-this"]


def test_fallback_records_the_model_that_actually_worked(monkeypatch):
    """B3/B4：主模型失败 → 切备用并成功；**用量里能看到 model 变化**。"""
    _settings(monkeypatch, llm_mock=False, llm_api_key="k",
              llm_model="main", llm_fallback_models="backup", llm_max_attempts=1)

    def behaviour(mdl, _n):
        if mdl == "main":
            raise _http_err("insufficient_quota")
        return _Resp("ok", pt=11, ct=22)

    calls: list = []
    _fake_client(monkeypatch, behaviour, calls)
    recs = _record_calls(monkeypatch)
    assert llm.chat("hi", scene="t") == "ok"
    models = [r["model"] for r in recs]
    assert models == ["main", "backup"], models
    assert recs[0]["ok"] is False and recs[1]["ok"] is True
    assert recs[1]["prompt_tokens"] == 11 and recs[1]["completion_tokens"] == 22


# ---------------------------------------------------------------- F. 串行化

def test_parallel_is_off_by_default_and_interval_is_configurable(monkeypatch):
    """F3/F5（以及 F1 的**现状**）：默认串行；最小间隔可配。

    ⚠️ checklist 的 F1 写的是"默认多平台并行" —— **现状相反**（默认串行），
    理由写在 `config.py`：实测上游 tpm/rpm 极紧，4 并发会被大面积拒绝。
    """
    s = Settings()
    assert s.llm_parallel is False, "默认必须是串行（与 checklist F1 不同，已如实记录）"
    assert s.llm_min_interval_ms == 1500
    assert Settings(llm_min_interval_ms=4000).llm_min_interval_ms == 4000
