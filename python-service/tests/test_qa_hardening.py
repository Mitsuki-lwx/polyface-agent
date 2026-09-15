"""质检加固回归测试（FR-60）+ 观测修复回归测试（FR-71）。

覆盖 `docs/36-质检加固与观测修复-spec.md` §3 的确定性场景。
全部为**离线确定性测试**，不调用真实 LLM、不接触真实观测服务。
"""
from __future__ import annotations

import pytest

from app.pipeline import generate as G
from app.schemas import Fact, StructuredMaterial
from app.schemas_gen import DraftPayload

DNA = {
    "code": "xhs", "name": "小红书",
    "limits": {"body_chars_max": 1000},
    "tags": {"count_max": 6},
}
MAT = StructuredMaterial(
    core_message="裸辞做自由职业", tone="理性", audience="自由职业者",
    facts=[Fact(text="2023年裸辞，月入从0到3万", type="data")],
)


def draft(body="这是正文，2023年裸辞做自由职业。", titles=None, tags=None) -> DraftPayload:
    return DraftPayload(
        titles=titles if titles is not None else ["裸辞做自由职业"],
        body=body,
        tags=tags if tags is not None else ["自由职业", "裸辞"],
    )


# ==================================================== 规则与结构校验（1,5,6,7,8,9,11,15）

def test_rule_qa_clean_passes():
    issues, warnings = G._rule_qa(DNA, draft(), MAT)
    assert issues == []


def test_empty_body_is_blocking():
    issues, _ = G._rule_qa(DNA, draft(body=""), MAT)
    assert any("正文为空" in i for i in issues)


def test_whitespace_body_is_blocking():
    issues, _ = G._rule_qa(DNA, draft(body="   \n\t  "), MAT)
    assert any("正文为空" in i for i in issues)


def test_all_blank_titles_is_blocking():
    issues, _ = G._rule_qa(DNA, draft(titles=["", "  "]), MAT)
    assert any("标题为空" in i for i in issues)


def test_tag_over_limit_is_blocking():
    issues, _ = G._rule_qa(DNA, draft(tags=["a", "b", "c", "d", "e", "f", "g"]), MAT)
    assert any("标签过多" in i for i in issues)


def test_tag_with_hash_is_blocking():
    issues, _ = G._rule_qa(DNA, draft(tags=["#自由职业"]), MAT)
    assert any("标签格式非法" in i for i in issues)


def test_unfounded_number_produces_warning():
    issues, warnings = G._rule_qa(DNA, draft(body="我月入999万"), MAT)
    assert any("999" in w for w in warnings), "无依据数字必须产生 warning"
    assert issues == [], "无依据数字是 warning 而非阻断"


def test_mock_mode_blocks_empty_body():
    """mock 模式下结构校验同样生效。"""
    r = G.run_qa(DNA, draft(body=""), MAT)
    assert r.passed is False
    assert any("正文为空" in i for i in r.issues)


# ==================================================== LLM 合并判定（2,3,4,10,12,13,14）

@pytest.fixture
def patch_llm(monkeypatch):
    """替换 llm.chat_json 与 is_mock，模拟真实模式下的模型返回。"""
    def _apply(payload):
        monkeypatch.setattr(G.llm, "is_mock", lambda: False)
        monkeypatch.setattr(G.llm, "chat_json", lambda *a, **kw: payload)
    return _apply


def test_string_false_is_not_passed(patch_llm):
    """修复 bool("false") == True。"""
    patch_llm({"passed": "false", "issues": [], "warnings": []})
    r = G.run_qa(DNA, draft(), MAT)
    assert r.passed is False
    assert any("类型不合约" in i for i in r.issues)


def test_numeric_passed_is_not_passed(patch_llm):
    patch_llm({"passed": 1, "issues": [], "warnings": []})
    r = G.run_qa(DNA, draft(), MAT)
    assert r.passed is False
    assert any("类型不合约" in i for i in r.issues)


def test_missing_passed_is_not_passed(patch_llm):
    patch_llm({"issues": [], "warnings": []})
    r = G.run_qa(DNA, draft(), MAT)
    assert r.passed is False


def test_llm_issues_block_pass(patch_llm):
    """LLM 报 issues 时，即使 passed=true 也不通过。"""
    patch_llm({"passed": True, "issues": ["标题与正文不符"], "warnings": []})
    r = G.run_qa(DNA, draft(), MAT)
    assert r.passed is False
    assert any("标题与正文不符" in i for i in r.issues)


def test_rule_issues_block_even_if_llm_passed(patch_llm):
    patch_llm({"passed": True, "issues": [], "warnings": []})
    r = G.run_qa(DNA, draft(body=""), MAT)
    assert r.passed is False


def test_all_clean_with_llm_passed(patch_llm):
    patch_llm({"passed": True, "issues": [], "warnings": []})
    r = G.run_qa(DNA, draft(), MAT)
    assert r.passed is True
    assert r.issues == []


def test_rule_warnings_preserved_in_real_mode(patch_llm):
    """修复：规则 warnings 在真实模式被丢弃。"""
    patch_llm({"passed": True, "issues": [], "warnings": ["模型侧提示"]})
    r = G.run_qa(DNA, draft(body="我月入999万"), MAT)
    assert any("999" in w for w in r.warnings), "规则 warning 必须保留"
    assert any("模型侧提示" in w for w in r.warnings), "LLM warning 也要保留"


def test_llm_warnings_only_does_not_block(patch_llm):
    patch_llm({"passed": True, "issues": [], "warnings": ["轻微建议"]})
    r = G.run_qa(DNA, draft(), MAT)
    assert r.passed is True
    assert "轻微建议" in r.warnings


# ==================================================== 观测不影响业务（16,17,18,19,20,21）

class _FakeSpan:
    def __init__(self, fail_on_enter=False):
        self.fail_on_enter = fail_on_enter
    def __enter__(self):
        if self.fail_on_enter:
            raise RuntimeError("模拟 span 创建失败")
        return self
    def __exit__(self, *a):
        return False
    def update(self, **kw):
        pass


class _FakeLF:
    def __init__(self, fail_on_enter=False):
        self.fail_on_enter = fail_on_enter
    def start_as_current_observation(self, **kw):
        return _FakeSpan(self.fail_on_enter)


@pytest.fixture
def obs_enabled(monkeypatch):
    from app import observability as obs
    monkeypatch.setattr(obs, "enabled", lambda: True)
    monkeypatch.setattr(obs, "_client", _FakeLF())
    return obs


def test_trace_span_preserves_business_exception(obs_enabled):
    """核心不变式：观测不得替换业务异常类型。"""
    from app.llm import RateLimited
    with pytest.raises(RateLimited):
        with obs_enabled.trace_span("t"):
            raise RateLimited("模拟限流")


def test_trace_span_preserves_generic_exception(obs_enabled):
    class MyBizError(Exception):
        pass
    with pytest.raises(MyBizError, match="原始消息"):
        with obs_enabled.trace_span("t"):
            raise MyBizError("原始消息")


def test_trace_span_degrades_when_creation_fails(monkeypatch):
    from app import observability as obs
    monkeypatch.setattr(obs, "enabled", lambda: True)
    monkeypatch.setattr(obs, "_client", _FakeLF(fail_on_enter=True))
    ran = []
    with obs.trace_span("t"):
        ran.append(True)
    assert ran == [True], "span 创建失败应降级为 no-op，业务照常执行"


def test_trace_span_noop_when_disabled(monkeypatch):
    from app import observability as obs
    monkeypatch.setattr(obs, "enabled", lambda: False)
    called = []
    monkeypatch.setattr(obs, "_get_client", lambda: called.append(1))
    with obs.trace_span("t"):
        pass
    assert called == [], "未启用时不得初始化客户端"


def test_flush_noop_when_disabled(monkeypatch):
    from app import observability as obs
    monkeypatch.setattr(obs, "enabled", lambda: False)
    called = []
    monkeypatch.setattr(obs, "_get_client", lambda: called.append(1))
    obs.flush()
    assert called == [], "未启用时 flush 不得初始化客户端"


def test_trace_span_normal_path_creates_span(obs_enabled):
    with obs_enabled.trace_span("t", "a" * 32) as span:
        assert span is not None
