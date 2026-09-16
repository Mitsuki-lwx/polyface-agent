"""部分失败保留测试：单平台失败不应丢弃其他平台的成果。

覆盖 `docs/44-长任务预算与部分失败保留-spec.md` §5.1。
"""
from __future__ import annotations

import pytest

from app.pipeline import generate as G
from app.schemas_gen import GenerateRequest

SAMPLE = "我2023年裸辞做自由职业，靠写作从月入0做到3万。"


@pytest.fixture
def patch_one_platform(monkeypatch):
    """让指定平台在生成时抛异常，其余正常（mock 生成）。"""
    def _apply(fail_codes):
        real = G._generate_one

        def fake(code, *args, **kwargs):
            if code in fail_codes:
                raise RuntimeError(f"模拟 {code} 生成失败")
            return real(code, *args, **kwargs)

        monkeypatch.setattr(G, "_generate_one", fake)
    return _apply


def test_partial_failure_keeps_successful(patch_one_platform):
    """2 平台，第 2 个失败 → 第 1 个仍返回，失败记入 failures。"""
    patch_one_platform({"douyin"})
    req = GenerateRequest(raw_text=SAMPLE, platforms=["xhs", "douyin"])
    _, drafts, _, failures = G.generate(req)

    assert [d.platform_code for d in drafts] == ["xhs"], "成功平台必须保留"
    assert len(failures) == 1
    assert failures[0]["platform"] == "douyin"
    assert "RuntimeError" in failures[0]["error"], "失败信息应含异常类型"


def test_all_failures_do_not_raise(patch_one_platform):
    """全部失败也不抛异常（由调用方决定呈现）。"""
    patch_one_platform({"xhs", "douyin"})
    req = GenerateRequest(raw_text=SAMPLE, platforms=["xhs", "douyin"])
    _, drafts, _, failures = G.generate(req)

    assert drafts == []
    assert len(failures) == 2
    assert {f["platform"] for f in failures} == {"xhs", "douyin"}


def test_single_success_has_no_failures(patch_one_platform):
    patch_one_platform(set())
    req = GenerateRequest(raw_text=SAMPLE, platforms=["xhs"])
    _, drafts, _, failures = G.generate(req)

    assert len(drafts) == 1
    assert failures == []


def test_failure_error_truncated_and_typed(patch_one_platform):
    patch_one_platform({"douyin"})
    req = GenerateRequest(raw_text=SAMPLE, platforms=["douyin"])
    _, _, _, failures = G.generate(req)

    assert len(failures) == 1
    err = failures[0]["error"]
    assert err.startswith("RuntimeError:"), f"应以异常类型开头，实际：{err[:40]}"
    assert len(err) <= 320, "失败信息应截断，避免响应体膨胀"
