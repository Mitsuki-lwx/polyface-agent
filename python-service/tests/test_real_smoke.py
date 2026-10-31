"""真实模式冒烟（`docs/28-...-checklist.md` H3/H4）。

**默认不跑**：`conftest` 把所有测试的 `LLM_MOCK` 强制成 `true`，
这里的用例自己**掀掉**这个开关。要跑：

    pytest -m real tests/test_real_smoke.py

**H4**：没有 Key 时**自动 skip**，不误报失败 —— 否则 CI/新机器上永远是红的，
红久了就没人看了（这比不测更糟）。
"""
from __future__ import annotations

import pytest

from app import llm, usage
from app.config import get_settings


@pytest.mark.real
def test_real_llm_returns_text_and_records_usage(monkeypatch, tmp_path):
    """真实调一次：拿到文本、用量里有一行、且**不含正文**。"""
    # Key 来自 `python-service/.env`（pydantic-settings 读的），**不在 `os.environ` 里** ✗ ——
    # 第一版用 `os.getenv("LLM_API_KEY")` 判，结果有 Key 也 skip（实测）。
    if not (get_settings().llm_api_key or "").strip():
        pytest.skip("没有 LLM_API_KEY（真实模式测试需要真 Key）")

    monkeypatch.setenv("LLM_MOCK", "false")
    monkeypatch.setenv("POLYFACE_USAGE_FILE", str(tmp_path / "u.jsonl"))
    get_settings.cache_clear()
    try:
        assert llm.is_mock() is False, "掀掉 LLM_MOCK 之后不该还是 mock"
        out = llm.chat("只回三个字：收到了。", scene="real-smoke")
        assert out.strip(), "真实调用返回了空文本"
        raw = (tmp_path / "u.jsonl").read_text(encoding="utf-8").strip()
        assert raw, "真实调用没有落用量记录"
        assert "只回三个字" not in raw, "用量文件里出现了 prompt 正文（脱敏红线）"
    finally:
        get_settings.cache_clear()


@pytest.mark.real
def test_is_mock_falls_back_when_key_missing(monkeypatch):
    """H4 的反面：**没 Key 时要回退到 mock**，而不是假装能调（否则每次都 401）。"""
    s = type(get_settings())(**{**get_settings().model_dump(),
                                "llm_mock": False, "llm_api_key": ""})
    monkeypatch.setattr(llm, "get_settings", lambda: s)
    assert llm.is_mock() is True
