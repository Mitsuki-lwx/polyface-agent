"""事实约束加固的**接线**测试（`docs/spec_factguard.md` / `tasks_factguard.md` T1~T4）。

**为什么单独测接线**：这一层出错的典型形态是**静默失效** ——
加了字段但没在 `run_draft` 的逐字段构造里传进去，不报错、测试全绿，
只是那个功能永远是 `None`。

实测踩到过：smoke 跑出来 `facts_used=None`，而单独测 prompt 时模型明明回了 `[1,2,3]`。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app import llm
from app.pipeline import generate as gen
from app.pipeline import prompts
from app.schemas import Fact, StructuredMaterial
from app.schemas_gen import Brief, DraftPayload, QaIssue, QaReport

# `scripts/eval/` 不是包，按文件路径加载（与 test_eval_assertions.py 同一套做法）
import importlib.util  # noqa: E402

_EVAL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "eval"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _EVAL_DIR / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


A = _load("polyface_eval_assertions_fg", "assertions.py")


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


# ============================================================ T6 定向重写

def test_targeted_feedback_uses_category_and_hint():
    """按类给**定向**指令（T6）：指出哪一类、哪一句、怎么改。"""
    qa = QaReport(passed=False, issues=["x"], issue_items=[
        QaIssue(category="数字", quote="卡里2万3", detail="素材写两万三"),
        QaIssue(category="新增断言", quote="漏交了286块", detail="素材只说\"要交\""),
    ])
    fb = gen._build_targeted_feedback(qa)
    assert "[数字]" in fb and "卡里2万3" in fb
    assert "[新增断言]" in fb and "漏交了286块" in fb
    assert "别的地方不要动" in fb


def test_targeted_feedback_falls_back_when_no_items():
    """拿不到结构化问题时**退回旧的笼统 feedback** —— 不假装有分类。"""
    fb = gen._build_targeted_feedback(QaReport(passed=False, issues=["甲", "乙"]))
    assert fb == "甲；乙"


@pytest.mark.parametrize("raw,expect_cat", [
    ([{"category": "数字", "detail": "d"}], "数字"),
    ([{"category": "不存在的类", "detail": "d"}], "其它"),   # 未知分类归"其它"，不猜
    ([{"detail": "没有分类"}], "其它"),
])
def test_parse_issue_items_normalises_category(raw, expect_cat):
    items = gen._parse_issue_items(raw)
    assert len(items) == 1 and items[0].category == expect_cat


def test_parse_issue_items_drops_garbage():
    """形状不对的条目直接丢 —— 不猜、不报错。"""
    assert gen._parse_issue_items(["bad", 123, {}, {"category": "数字"}]) == []
    assert gen._parse_issue_items(None) == []


def test_fix_hints_cover_all_five_categories():
    """五类**封闭分类**必须都有对应指令，否则会出现"没话可说"的类。"""
    from app.schemas_gen import QaIssueCategory
    import typing
    cats = set(typing.get_args(QaIssueCategory))
    assert cats <= set(gen._FIX_HINT), f"缺指令的类：{cats - set(gen._FIX_HINT)}"


# ============================================================ T7 配置

def test_rule_qa_obeys_number_switch(monkeypatch):
    """关掉数字检查 → 数字问题不再报，其余照旧。"""
    from app.config import Settings
    monkeypatch.setattr(gen, "get_settings", lambda: Settings(factguard_check_numbers=False))
    mat = _mat()
    draft = DraftPayload(titles=["标题"], body="卡里就剩99999。", tags=["t"], facts_used=[1])
    _, warns = gen._rule_qa(_dna(), draft, mat, source_text="卡里就剩两万三。")
    assert not any("99999" in w for w in warns)


def test_rule_qa_master_switch_off(monkeypatch):
    """总开关关掉 → 退回加固前行为（只剩结构性检查）。"""
    from app.config import Settings
    monkeypatch.setattr(gen, "get_settings", lambda: Settings(factguard_enabled=False))
    mat = _mat()
    draft = DraftPayload(titles=["标题"], body="卡里就剩99999，还提到了飞书。", tags=["t"])
    _, warns = gen._rule_qa(_dna(), draft, mat, source_text="卡里就剩两万三。")
    assert not any("99999" in w for w in warns)
    assert not any("飞书" in w for w in warns)
    assert not any("未声明" in w for w in warns)


def test_extra_names_wordlist_is_used(monkeypatch):
    """用户补的行业词表（T7）要能生效。"""
    from app.config import Settings
    monkeypatch.setattr(gen, "get_settings",
                        lambda: Settings(factguard_extra_names="某某工具"))
    mat = _mat()
    draft = DraftPayload(titles=["标题"], body="我用了某某工具。", tags=["t"], facts_used=[])
    _, warns = gen._rule_qa(_dna(), draft, mat, source_text="卡里就剩两万三。")
    assert not any("某某工具" in w for w in warns)


def test_year_check_exists_and_is_gated(monkeypatch):
    """年份检查（与评测集 A2 对齐）—— 原先只有评测集查、产品侧不查。

    ⚠️ 注意：**年份本身就是数字**，所以关掉年份检查后数字检查仍会报它。
    年份检查的价值是**给出更准的措辞**（"年份"而不是"数字"），便于 T6 按类定向重写。
    所以这里断言的是**措辞**，不是"报不报"。
    """
    from app.config import Settings
    mat = _mat()
    draft = DraftPayload(titles=["标题"], body="2021 年我辞职了。", tags=["t"], facts_used=[])

    monkeypatch.setattr(gen, "get_settings", lambda: Settings())
    _, warns = gen._rule_qa(_dna(), draft, mat, source_text="卡里就剩两万三。")
    assert any("2021" in w and "年份" in w for w in warns), warns

    monkeypatch.setattr(gen, "get_settings", lambda: Settings(factguard_check_years=False))
    _, warns = gen._rule_qa(_dna(), draft, mat, source_text="卡里就剩两万三。")
    assert not any("年份" in w for w in warns), warns      # 年份措辞消失（数字检查仍会报）


# ============================================================ A7 原子化

@pytest.mark.parametrize("raw,expect", [
    (12, 12), ("12", 12), (" 7 ", 7), (None, 0), ("说不清", 0), (-3, 0), (3.9, 3),
])
def test_nonneg_int_parsing(raw, expect):
    """宽容解析非负整数；解析不出来按 0（**不猜**）。"""
    assert gen._nonneg_int(raw) == expect


def test_a7_records_atomic_counts():
    """**核心**：A7 要带上"核对了多少条断言、多少条没依据"。

    为什么：二值通过率噪声极大（实测同一代码多轮 38%~78%），而机械检查
    （数字/年份/专名）几乎总是干净的 —— 真正在失败的是语义级断言。
    不把 A7 原子化，支持率就既稳又测不到主要矛盾。
    """
    res = A.check_all({"material": {"raw_text": "x"}, "structured": None, "platform": "xhs",
                       "draft": {"titles": ["标题"], "body": "正文", "tags": ["t"],
                                 "qa": {"passed": False, "issues": ["有编造"],
                                        "claims_checked": 6, "claims_unsupported": 2}},
                       "rules": {}})
    a7 = next(r for r in res if r.code.startswith("A7"))
    assert a7.checked == 6 and a7.failed == 2


def test_a7_clamps_impossible_counts():
    """防御：模型偶尔报出 `unsupported > checked` 这种不合逻辑的数，要夹住。"""
    res = A.check_all({"material": {"raw_text": "x"}, "structured": None, "platform": "xhs",
                       "draft": {"titles": ["标题"], "body": "正文", "tags": ["t"],
                                 "qa": {"passed": True, "issues": [],
                                        "claims_checked": 3, "claims_unsupported": 99}},
                       "rules": {}})
    a7 = next(r for r in res if r.code.startswith("A7"))
    assert a7.checked == 3 and a7.failed == 3


def test_a7_without_counts_is_zero_not_guessed():
    """老产物没有这两个字段 → 0/0（不猜），而不是编一个数。"""
    res = A.check_all({"material": {"raw_text": "x"}, "structured": None, "platform": "xhs",
                       "draft": {"titles": ["标题"], "body": "正文", "tags": ["t"],
                                 "qa": {"passed": True, "issues": []}},
                       "rules": {}})
    a7 = next(r for r in res if r.code.startswith("A7"))
    assert a7.checked == 0 and a7.failed == 0
