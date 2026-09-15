"""事实确认闭环测试（FR-34）：已确认事实应跳过重复理解。

覆盖 `docs/40-事实确认与稿件编辑闭环-spec.md` §7.2。
"""
from __future__ import annotations

import pytest

from app.pipeline import generate as G
from app.schemas import Fact, StructuredMaterial
from app.schemas_gen import GenerateRequest

SAMPLE = "我2023年裸辞做自由职业，靠写作从月入0做到3万。"
CONFIRMED = StructuredMaterial(
    core_message="裸辞做自由职业，写作月入0到3万",
    tone="理性干货", audience="自由职业者",
    facts=[Fact(text="2023年裸辞，写作月入从0到3万", type="data")],
)


@pytest.fixture
def spy_understand(monkeypatch):
    """记录 understand 是否被调用。"""
    calls = []

    def fake_understand(_req):
        calls.append(1)
        return CONFIRMED, True

    monkeypatch.setattr(G, "run_understand", fake_understand)
    return calls


def test_confirmed_facts_skips_understand(monkeypatch, spy_understand):
    """传入 confirmed_facts → 不调用 understand（省一次 LLM 调用）。"""
    req = GenerateRequest(raw_text=SAMPLE, platforms=["xhs"], confirmed_facts=CONFIRMED)
    _, drafts, _ = G.generate(req)
    assert spy_understand == [], "不应调用理解阶段"
    assert drafts and drafts[0].platform_code == "xhs"


def test_without_confirmed_facts_runs_understand(spy_understand):
    """不传 → 走原路径（向后兼容）。"""
    req = GenerateRequest(raw_text=SAMPLE, platforms=["xhs"])
    G.generate(req)
    assert spy_understand == [1], "应调用一次理解阶段"


def test_confirmed_facts_used_as_source(monkeypatch, spy_understand):
    """生成使用的事实必须是用户确认的那份（所见即所用）。"""
    req = GenerateRequest(raw_text=SAMPLE, platforms=["xhs"], confirmed_facts=CONFIRMED)
    structured_dict, _, _ = G.generate(req)
    texts = [f["text"] for f in structured_dict.get("facts", [])]
    assert "2023年裸辞，写作月入从0到3万" in texts


def test_qa_still_checks_facts_when_confirmed(monkeypatch, spy_understand):
    """用已确认事实时，QA 的事实校验照常执行（无依据数字仍产生 warning）。"""
    from app.pipeline.generate import _rule_qa
    from app.schemas_gen import DraftPayload
    draft = DraftPayload(titles=["标题"], body="我月入999万", tags=["a"])
    dna = {"code": "xhs", "name": "小红书", "limits": {}, "tags": {}}
    issues, warnings = _rule_qa(dna, draft, CONFIRMED)
    assert any("999" in w for w in warnings), "事实校验必须照常"
