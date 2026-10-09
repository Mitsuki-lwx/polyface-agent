"""事实约束加固的**接线**测试（`docs/spec_factguard.md` / `tasks_factguard.md` T1~T4）。

**为什么单独测接线**：这一层出错的典型形态是**静默失效** ——
加了字段但没在 `run_draft` 的逐字段构造里传进去，不报错、测试全绿，
只是那个功能永远是 `None`。

实测踩到过：smoke 跑出来 `facts_used=None`，而单独测 prompt 时模型明明回了 `[1,2,3]`。
"""
from __future__ import annotations

import pytest

from app import llm
from app.pipeline import generate as gen
from app.pipeline import prompts
from app.schemas import Fact, StructuredMaterial
from app.schemas_gen import Brief, DraftPayload


def _mat() -> StructuredMaterial:
    return StructuredMaterial(
        core_message="c", tone="t", audience="a",
        facts=[Fact(text="卡里就剩两万三。", type="data"),
               Fact(text="投了不下五十份简历。", type="story")])


def _brief() -> Brief:
    return Brief(platform_code="xhs", angle="a", hooks=["h"], structure_plan="s")


def _dna() -> dict:
    return {"code": "xhs", "name": "小红书", "content_forms": [], "style": [],
            "structure_template": [], "title_rules": [], "tags": {},
            "limits": {"body_chars_max": 1000}, "viral_logic": []}


# ---------------------------------------------------------------- 接线

def test_run_draft_carries_facts_used(monkeypatch):
    """**核心接线守卫**：模型回了 facts_used，`run_draft` 必须把它带进产物。

    漏掉这一行不会报错 —— 只会让整个声明机制静默失效。
    """
    monkeypatch.setattr(llm, "is_mock", lambda: False)
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {
        "titles": ["标题"], "body": "正文", "tags": ["t"],
        "facts_used": [1, 2], "rationale": "r"})
    d = gen.run_draft("xhs", _dna(), _mat(), _brief())
    assert d.facts_used == [1, 2]


def test_run_draft_keeps_none_when_model_omits(monkeypatch):
    """模型没回这个字段 → 必须是 `None`（= 没声明，违规），**不能**被填成 `[]`。"""
    monkeypatch.setattr(llm, "is_mock", lambda: False)
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {
        "titles": ["标题"], "body": "正文", "tags": ["t"]})
    d = gen.run_draft("xhs", _dna(), _mat(), _brief())
    assert d.facts_used is None


# ---------------------------------------------------------------- 宽容解析

@pytest.mark.parametrize("raw,expect", [
    ([1, 2], [1, 2]),
    (["1", "3"], [1, 3]),
    ("[1, 3]", [1, 3]),          # 模型有时把列表回成字符串
    ("1、3", [1, 3]),
    ([], []),                    # 声明了"没用到事实" —— 合法，且与 None 不同
    (None, None),                # 没声明
    ("说不清", None),             # 解析不出来 → 按"没声明"处理，不假装声明过
])
def test_coerce_facts_used(raw, expect):
    assert gen._coerce_facts_used(raw) == expect


def test_none_and_empty_are_different():
    """**三态语义**：None（没声明）与 []（声明了空）必须可区分。"""
    assert gen._coerce_facts_used(None) is None
    assert gen._coerce_facts_used([]) == []
    assert gen._coerce_facts_used(None) != gen._coerce_facts_used([])


# ---------------------------------------------------------------- 证据集合

def test_rule_qa_uses_source_text_not_only_facts():
    """**T3 的核心修复**：证据集合 = 素材原文 ∪ 事实清单。

    素材里有、但没被抽进事实清单的数字，**不该**被误报。
    """
    mat = StructuredMaterial(core_message="c", tone="t", audience="a",
                             facts=[Fact(text="卡里就剩两万三。", type="data")])
    # 正文里的 4500 只出现在素材原文里，不在事实清单里
    draft = DraftPayload(titles=["标题"], body="房租每月4500，卡里就剩两万三。",
                         tags=["t"], facts_used=[1])
    _, warns_only_facts = gen._rule_qa(_dna(), draft, mat, source_text="")
    _, warns_with_source = gen._rule_qa(
        _dna(), draft, mat, source_text="房租每月4500，卡里就剩两万三。")
    assert any("4500" in w for w in warns_only_facts), "只给事实清单时应报（证据不足）"
    assert not any("4500" in w for w in warns_with_source), "给了素材原文后不该报"


def test_rule_qa_flags_missing_declaration():
    """没声明依据 = 违规（T1 三态语义）。"""
    mat = _mat()
    draft = DraftPayload(titles=["标题"], body="卡里就剩两万三。", tags=["t"])
    _, warns = gen._rule_qa(_dna(), draft, mat, source_text="卡里就剩两万三。")
    assert any("未声明" in w for w in warns)


def test_rule_qa_accepts_empty_declaration():
    """声明了空 = 合法（本次没用到事实）。"""
    mat = _mat()
    draft = DraftPayload(titles=["标题"], body="随便写点不涉及事实的话，就聊聊天。",
                         tags=["t"], facts_used=[])
    _, warns = gen._rule_qa(_dna(), draft, mat, source_text="卡里就剩两万三。")
    assert not any("未声明" in w for w in warns)


def test_rule_qa_flags_out_of_range_fact_index():
    mat = _mat()          # 只有 2 条
    draft = DraftPayload(titles=["标题"], body="卡里就剩两万三。", tags=["t"],
                         facts_used=[1, 99])
    _, warns = gen._rule_qa(_dna(), draft, mat, source_text="卡里就剩两万三。")
    assert any("越界" in w for w in warns)


# ---------------------------------------------------------------- QA 输入

def test_qa_prompt_carries_source_text():
    """QA 的输入里必须有**素材原文** —— 判定证据不能比被检对象还少（T4）。"""
    mat = _mat()
    draft = DraftPayload(titles=["标题"], body="正文", tags=["t"], facts_used=[1])
    payload = prompts.build_qa_prompt(_dna(), draft, mat, source_text="这是素材原文的独特标记XYZ")
    assert "XYZ" in payload
    assert "facts_used" in payload
