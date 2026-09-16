"""LLM 模式判定与「未配 Key 回退 mock」的回归测试。

**为什么要测这个**：`llm.is_mock()` 在 `LLM_MOCK=false` 但**未配置 `LLM_API_KEY`** 时
会回退到 mock —— 用户以为在跑真实模型，实际拿到的是**离线演示结果**。
这条降级路径此前没有任何测试（见 `docs/08` ADR-017 备注、09-14 评估报告）。

**不变式**：
1. 回退**必须发生** —— 绝不能带着空 Key 去请求真实接口
2. 回退**必须不静默** —— 有 warning，且文案可操作（指出缺哪个变量）
3. `LLM_MOCK=true` 时**不得**出现"回退"告警 —— 否则日志误导
4. 回退不能只是标志位 —— 下游管线必须真的走 mock 分支
"""
from __future__ import annotations

import logging

from app import llm
from app.config import Settings

FALLBACK_MARK = "回退到 mock"


def _settings_seen_by_llm(monkeypatch, **overrides) -> Settings:
    """把 `llm` 模块看到的 Settings 换成受控实例。

    不能用环境变量控制：`get_settings()` 带 `lru_cache`，且 conftest 已把
    `LLM_MOCK=true` 写进环境。这里直接替换 `llm` 模块里的 `get_settings` 引用。
    """
    s = Settings(**overrides)
    monkeypatch.setattr(llm, "get_settings", lambda: s)
    return s


def _fallback_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if FALLBACK_MARK in r.getMessage()]


# ---------------------------------------------------------------- 模式判定

def test_explicit_mock_returns_mock(monkeypatch):
    _settings_seen_by_llm(monkeypatch, llm_mock=True, llm_api_key="")
    assert llm.is_mock() is True


def test_real_mode_when_key_present(monkeypatch):
    """有 Key + LLM_MOCK=false → 走真实模型。"""
    _settings_seen_by_llm(monkeypatch, llm_mock=False, llm_api_key="sk-test-not-a-real-key")
    assert llm.is_mock() is False


def test_explicit_mock_wins_even_with_key(monkeypatch):
    """显式 mock 优先于 Key：不能因为配了 Key 就偷偷跑真实调用。"""
    _settings_seen_by_llm(monkeypatch, llm_mock=True, llm_api_key="sk-test-not-a-real-key")
    assert llm.is_mock() is True


# ---------------------------------------------------------------- 回退路径

def test_missing_key_falls_back_to_mock(monkeypatch):
    """不变式 1：LLM_MOCK=false 但无 Key → 回退 mock（不空 Key 请求）。"""
    _settings_seen_by_llm(monkeypatch, llm_mock=False, llm_api_key="")
    assert llm.is_mock() is True


def test_missing_key_fallback_is_not_silent(monkeypatch, caplog):
    """不变式 2：回退必须留痕，不能静默降级。"""
    _settings_seen_by_llm(monkeypatch, llm_mock=False, llm_api_key="")
    with caplog.at_level(logging.WARNING):
        llm.is_mock()
    assert _fallback_warnings(caplog), "回退到 mock 时必须有 warning"


def test_fallback_warning_names_the_missing_variable(monkeypatch, caplog):
    """不变式 2（续）：告警文案要可操作 —— 得说清缺的是哪个变量。"""
    _settings_seen_by_llm(monkeypatch, llm_mock=False, llm_api_key="")
    with caplog.at_level(logging.WARNING):
        llm.is_mock()
    msgs = _fallback_warnings(caplog)
    assert msgs, "回退到 mock 时必须有 warning"
    assert "LLM_API_KEY" in msgs[0], f"告警应指出缺失的变量名，实际：{msgs[0]}"


def test_explicit_mock_does_not_warn_about_fallback(monkeypatch, caplog):
    """不变式 3：显式 mock 不是"回退"，不应产生回退告警（避免日志误导）。"""
    _settings_seen_by_llm(monkeypatch, llm_mock=True, llm_api_key="")
    with caplog.at_level(logging.WARNING):
        llm.is_mock()
    assert not _fallback_warnings(caplog), "显式 mock 不应报「回退到 mock」"


# ---------------------------------------------------------------- 下游行为

def test_understand_reports_used_mock_on_fallback(monkeypatch):
    """不变式 4：回退不能只是标志位 —— 管线必须真的走 mock 分支并回报 used_mock。

    这是最容易被忽略的一点：如果下游忘了读 `is_mock()`，
    标志位改了也没用，用户仍会拿到（失败的）真实调用。
    """
    _settings_seen_by_llm(monkeypatch, llm_mock=False, llm_api_key="")
    from app.pipeline.understand import run_understand
    from app.schemas import AnalyzeRequest

    mat, used_mock = run_understand(AnalyzeRequest(
        raw_text="我2023年裸辞做自由职业，靠写作从月入0做到3万。", source_kind="长文"))

    assert used_mock is True, "回退后管线必须回报 used_mock=True（UI 据此显示演示模式徽章）"
    assert mat.facts, "mock 分支也应产出结构（否则用户看到的是空结果而非演示结果）"
