"""跨平台去重自动改写闭环（`openspec: diffcheck-auto-rewrite`）测试。

验证重点：
1. 重合超标时，系统自动识别冲突后发平台并发起定向改写；
2. 改写成功收敛时，采纳新稿件并更新重合率；
3. 配置关闭或未超标时，不产生额外调用；
4. 改写未改善或 QA 不通过时，安全降级保留原稿。
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.pipeline import diffcheck, generate as gen
from app.schemas import Fact, StructuredMaterial
from app.schemas_gen import Brief, DraftPayload, GenerateRequest, PlatformDraft, QaReport


def _mat() -> StructuredMaterial:
    return StructuredMaterial(
        core_message="内容复用需要换形态",
        tone="理性干货",
        audience="自媒体作者",
        facts=[
            Fact(text="长文可以拆成10条内容，熟练后2小时跑完一轮", type="data"),
            Fact(text="同一段话发5个平台用户只会觉得敷衍", type="opinion"),
            Fact(text="真正的复用是换场景换形态换钩子", type="opinion"),
        ],
    )


def _draft_with_body(body: str, tags: list[str] | None = None) -> DraftPayload:
    return DraftPayload(
        titles=["标题1", "备选2", "备选3"],
        body=body,
        tags=tags or ["标签1", "标签2"],
        cover_suggestion="封面建议",
        interaction_line="评论区见",
        rationale="机制说明",
        facts_used=[1],
    )


def test_auto_rewrite_triggers_and_adopts_improved_draft(monkeypatch):
    """重合超标时触发改写，改写后重合下降则采纳新成稿。"""
    shared_sent = "同一段话发5个平台用户只会觉得敷衍，内容复用不是偷懒是让内容多活几次。"
    gzh_body = f"公众号深度解析。{shared_sent} 这是长文的展开。"
    zhihu_body_old = f"知乎结论先行。{shared_sent} 这是知乎的论述。"
    zhihu_body_new = "知乎结论先行。把相同的话到处复制会被读者鄙视，核心是围绕本质逻辑重新推导。"

    rewrite_calls = []

    def mock_generate_one(code, mat, tone, template, profile, hints, log, text,
                          avoid=None, library_context=None, image_notes=None):
        if code == "gzh":
            d = _draft_with_body(gzh_body)
        else:
            d = _draft_with_body(zhihu_body_old)
        return PlatformDraft(
            platform_code=code,
            platform_name=code,
            brief=Brief(platform_code=code, angle="a", hooks=["h"], structure_plan="s", tag_direction=["t"]),
            draft=d,
            qa=QaReport(passed=True, issues=[], warnings=[]),
        )

    def mock_run_draft(code, dna, mat, brief, feedback=None, template=None, creator_profile=None, retrospect_hints=None, avoid=None):
        rewrite_calls.append({"code": code, "feedback": feedback, "avoid": avoid})
        return _draft_with_body(zhihu_body_new), []

    monkeypatch.setattr(gen, "_generate_one", mock_generate_one)
    monkeypatch.setattr(gen, "run_draft", mock_run_draft)
    monkeypatch.setattr(gen, "run_qa", lambda *a, **k: QaReport(passed=True, issues=[], warnings=[]))
    monkeypatch.setattr(gen.llm, "is_mock", lambda: False)

    monkeypatch.setenv("DIFF_AUTO_REWRITE_ENABLED", "true")
    monkeypatch.setenv("DIFF_MAX_REWRITE_ROUNDS", "1")
    monkeypatch.setenv("DIFF_MAX_OVERLAP", "0.10")
    get_settings.cache_clear()

    req = GenerateRequest(
        raw_text="素材正文",
        source_kind="长文",
        title="复用测试",
        platforms=["gzh", "zhihu"],
        confirmed_facts=_mat(),
    )

    _, results, _, failures = gen.generate(req)
    assert not failures
    assert len(results) == 2

    # 验证知乎触发了改写，且反馈中包含冲突句
    assert len(rewrite_calls) == 1
    assert rewrite_calls[0]["code"] == "zhihu"
    assert "跨平台去重定向改写" in rewrite_calls[0]["feedback"]
    assert "同一段话发5个平台" in rewrite_calls[0]["feedback"]

    # 验证最终采纳了新成稿
    zhihu_draft = next(d for d in results if d.platform_code == "zhihu")
    assert zhihu_draft.draft.body == zhihu_body_new


def test_auto_rewrite_disabled_by_config(monkeypatch):
    """配置禁用时，即使重合超标也不触发改写。"""
    shared_sent = "同一段话发5个平台用户只会觉得敷衍，内容复用不是偷懒是让内容多活几次。"
    gzh_body = f"公众号深度解析。{shared_sent} 这是长文展开。"
    zhihu_body = f"知乎结论先行。{shared_sent} 这是知乎论述。"

    rewrite_calls = []

    def mock_generate_one(code, *a, **k):
        b = gzh_body if code == "gzh" else zhihu_body
        return PlatformDraft(
            platform_code=code,
            platform_name=code,
            brief=Brief(platform_code=code, angle="a", hooks=["h"], structure_plan="s", tag_direction=["t"]),
            draft=_draft_with_body(b),
            qa=QaReport(passed=True, issues=[], warnings=[]),
        )

    monkeypatch.setattr(gen, "_generate_one", mock_generate_one)
    monkeypatch.setattr(gen, "run_draft", lambda *a, **k: (rewrite_calls.append(1), _draft_with_body("x"), [])[1:])
    monkeypatch.setattr(gen.llm, "is_mock", lambda: False)

    monkeypatch.setenv("DIFF_AUTO_REWRITE_ENABLED", "false")
    get_settings.cache_clear()

    req = GenerateRequest(
        raw_text="素材正文",
        source_kind="长文",
        platforms=["gzh", "zhihu"],
        confirmed_facts=_mat(),
    )

    _, results, _, _ = gen.generate(req)
    assert len(rewrite_calls) == 0, "禁用配置后不应发起改写"
    # 但警告仍应如实存在
    zhihu_draft = next(d for d in results if d.platform_code == "zhihu")
    assert any("逐字重合过高" in w for w in zhihu_draft.qa.warnings)


def test_auto_rewrite_failsafe_keeps_original_if_qa_fails(monkeypatch):
    """若改写后的成稿未能通过质检，系统平滑降级保留初稿版本。"""
    shared_sent = "同一段话发5个平台用户只会觉得敷衍，内容复用不是偷懒是让内容多活几次。"
    gzh_body = f"公众号深度解析。{shared_sent} 这是长文展开。"
    zhihu_body_old = f"知乎结论先行。{shared_sent} 这是知乎论述。"
    zhihu_body_broken = "完全重写但事实全崩了的内容。"

    def mock_generate_one(code, *a, **k):
        b = gzh_body if code == "gzh" else zhihu_body_old
        return PlatformDraft(
            platform_code=code,
            platform_name=code,
            brief=Brief(platform_code=code, angle="a", hooks=["h"], structure_plan="s", tag_direction=["t"]),
            draft=_draft_with_body(b),
            qa=QaReport(passed=True, issues=[], warnings=[]),
        )

    monkeypatch.setattr(gen, "_generate_one", mock_generate_one)
    monkeypatch.setattr(gen, "run_draft", lambda *a, **k: (_draft_with_body(zhihu_body_broken), []))
    monkeypatch.setattr(gen, "run_qa", lambda *a, **k: QaReport(passed=False, issues=["事实虚构"], warnings=[]))
    monkeypatch.setattr(gen.llm, "is_mock", lambda: False)

    monkeypatch.setenv("DIFF_AUTO_REWRITE_ENABLED", "true")
    monkeypatch.setenv("DIFF_MAX_REWRITE_ROUNDS", "1")
    get_settings.cache_clear()

    req = GenerateRequest(
        raw_text="素材正文",
        source_kind="长文",
        platforms=["gzh", "zhihu"],
        confirmed_facts=_mat(),
    )

    _, results, _, _ = gen.generate(req)
    zhihu_draft = next(d for d in results if d.platform_code == "zhihu")
    # 依然保留合规的初稿
    assert zhihu_draft.draft.body == zhihu_body_old
