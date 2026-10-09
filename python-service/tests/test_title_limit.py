"""标题字数上限的接线（2026-10-09 修）。

**修的是什么**：平台的标题上限只存在于 DNA 的 `specs.title_chars_max` 里，
而 DRAFT prompt 传给模型的是 `title_rules`（散文，如"多用数字+结果"）——
**不含长度**。于是模型压根不知道小红书标题要 ≤20 字。

而产品自己的 `_rule_qa` 又写死了 `>24 字才警告` —— 21~24 字的标题
**连产品自己都不报**，一路发出去被平台截断。

后果（T10 实测）：xhs 从 5/10 掉到 **0/10**。
"""
from __future__ import annotations

import json

import pytest

from app import dna
from app.pipeline import generate as gen
from app.pipeline import prompts
from app.schemas import Fact, StructuredMaterial
from app.schemas_gen import Brief, DraftPayload


def _mat() -> StructuredMaterial:
    return StructuredMaterial(core_message="c", tone="t", audience="a",
                              facts=[Fact(text="卡里就剩两万三。", type="data")])


def _brief() -> Brief:
    return Brief(platform_code="xhs", angle="a", hooks=["h"], structure_plan="s")


# ---------------------------------------------------------------- 取值

@pytest.mark.parametrize("code,expect", [
    ("xhs", 20), ("douyin", 55), ("gzh", 64), ("zhihu", 100), ("bilibili", 80),
])
def test_title_chars_max_comes_from_dna(code, expect):
    assert dna.title_chars_max(dna.load_dna(code)) == expect


def test_title_chars_max_returns_none_when_absent():
    """读不到就返回 None（**不猜**），调用方自己决定退回什么。"""
    assert dna.title_chars_max({}) is None
    assert dna.title_chars_max({"specs": {"title_chars_max": 0}}) is None


# ---------------------------------------------------------------- 传给模型

def test_draft_prompt_carries_title_limit():
    """**核心接线**：上限必须显式传给模型 —— 它不在 `title_rules` 的散文里。"""
    payload = json.loads(prompts.build_draft_prompt(dna.load_dna("xhs"), _mat(), _brief()))
    assert payload["platform_dna"]["title_chars_max"] == 20


def test_draft_system_mentions_title_limit():
    assert "title_chars_max" in prompts.DRAFT_SYSTEM
    assert "标题字数上限" in prompts.DRAFT_SYSTEM


# ---------------------------------------------------------------- 产品校验

def _dna_xhs() -> dict:
    return dna.load_dna("xhs")


def test_rule_qa_flags_title_over_platform_limit():
    """21 字的标题在小红书就该报 —— 原先写死 24，21~24 字**静默放过**。"""
    draft = DraftPayload(titles=["一二三四五六七八九十一二三四五六七八九十一"],
                         body="正文", tags=["t"], facts_used=[])
    _, warns = gen._rule_qa(_dna_xhs(), draft, _mat(), source_text="卡里就剩两万三。")
    assert any("标题偏长(>20字)" in w for w in warns), warns


def test_rule_qa_accepts_title_at_platform_limit():
    draft = DraftPayload(titles=["一二三四五六七八九十一二三四五六七八九十"],   # 恰好 20
                         body="正文", tags=["t"], facts_used=[])
    _, warns = gen._rule_qa(_dna_xhs(), draft, _mat(), source_text="卡里就剩两万三。")
    assert not any("标题偏长" in w for w in warns)


def test_rule_qa_falls_back_to_24_when_dna_has_no_limit():
    """DNA 里没写上限时退回 24（旧行为），而不是把所有标题都报一遍。"""
    draft = DraftPayload(titles=["一二三四五六七八九十一二三四五六七八九十"],   # 20 字
                         body="正文", tags=["t"], facts_used=[])
    _, warns = gen._rule_qa({}, draft, _mat(), source_text="卡里就剩两万三。")
    assert not any("标题偏长" in w for w in warns)
