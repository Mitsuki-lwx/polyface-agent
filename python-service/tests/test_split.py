"""素材拆分（`docs/checklist_split.md` §3 §4）。

最要紧的一条：**拆出来的事实不许凭空造**。
做法是结构性的 —— 模型只返回父素材事实的**序号**，代码按序号取原文，
所以"编一条新事实"在结构上就不可能，越界序号会被当场拦下。
"""
from __future__ import annotations

import pytest

from app.pipeline import split as sp
from app.schemas import Fact, StructuredMaterial

MAT = StructuredMaterial(
    core_message="裸辞11个月，从月入0做到3万",
    tone="理性干货",
    audience="想转自由职业的人",
    facts=[Fact(text="2023年3月裸辞", type="data"),
           Fact(text="存款8万，够撑14个月", type="data"),
           Fact(text="单价从800涨到3000", type="data")],
)
RAW = "我2023年3月裸辞……（略）"


@pytest.fixture
def fake_llm(monkeypatch):
    """替换 chat_json，让测试只考拆分逻辑本身。"""
    def _apply(payload):
        monkeypatch.setattr(sp.llm, "chat_json", lambda *a, **kw: payload)
    return _apply


def _item(angle, ids, title="标题", core="核心"):
    return {"angle": angle, "title": title, "core_message": core, "fact_ids": ids}


# ---------------- 输入校验 ----------------

def test_rejects_empty_angles(fake_llm):
    fake_llm({"items": []})
    with pytest.raises(sp.SplitError, match="至少要给角度或形态"):
        sp.split_material(MAT, RAW, [])


def test_rejects_too_many_angles(fake_llm):
    fake_llm({"items": []})
    with pytest.raises(sp.SplitError, match=f"最多 {sp.MAX_ANGLES} 个角度"):
        sp.split_material(MAT, RAW, [f"角度{i}" for i in range(sp.MAX_ANGLES + 1)])


def test_rejects_empty_model_output(fake_llm):
    fake_llm({"items": []})
    with pytest.raises(sp.SplitError, match="没有返回任何"):
        sp.split_material(MAT, RAW, ["钱"])


# ---------------- 条数对不上：要说清是哪个角度 ----------------

def test_missing_angle_names_the_angle(fake_llm):
    """少一条时**必须点名是哪个角度** —— "少了一条"没法让人改。"""
    fake_llm({"items": [_item("钱的视角", [1]), _item("方法的视角", [2])]})
    with pytest.raises(sp.SplitError) as e:
        sp.split_material(MAT, RAW, ["钱的视角", "心理的视角", "方法的视角"])
    msg = str(e.value)
    assert "第 2 个目标" in msg and "心理的视角" in msg, f"要点名：{msg}"


def test_angle_matching_tolerates_rewording(fake_llm):
    """模型常把角度**增减几个字**（"从钱的视角" → "钱的视角"）—— 要能配回去。

    匹配规则是**子串包含**，可预期；不做"语义相近"的猜测 ——
    `金钱视角` 和 `从钱的视角` 人看得出是一回事，但静默配错角度比报错让你改更糟。
    """
    fake_llm({"items": [_item("钱的视角", [1]), _item("从心理的视角出发", [2])]})
    out = sp.split_material(MAT, RAW, ["从钱的视角", "从心理的视角"])
    assert [x["angle"] for x in out] == ["从钱的视角", "从心理的视角"]
    assert [x["title"] for x in out] == ["标题", "标题"]


def test_angle_that_cannot_be_matched_is_named_not_guessed(fake_llm):
    """配不上就**点名**，不猜 —— 猜错角度会产出跑偏的子素材，比报错难查。"""
    fake_llm({"items": [_item("金钱视角", [1])]})
    with pytest.raises(sp.SplitError, match="从钱的视角"):
        sp.split_material(MAT, RAW, ["从钱的视角"])


# ---------------- 事实不许凭空造（本模块的底线）----------------

def test_facts_are_the_parent_texts_verbatim(fake_llm):
    """**核心断言**：子素材的事实就是父素材那几条**原文**，一个字都不变。"""
    fake_llm({"items": [_item("钱的视角", [2, 1])]})
    out = sp.split_material(MAT, RAW, ["钱的视角"])
    assert [f["text"] for f in out[0]["facts"]] == ["存款8万，够撑14个月", "2023年3月裸辞"]
    assert [f["from_fact"] for f in out[0]["facts"]] == [2, 1], "要能指出来自父素材第几条"
    assert [f["type"] for f in out[0]["facts"]] == ["data", "data"]


def test_out_of_range_fact_id_is_blocked(fake_llm):
    """**拦截点**：引用了不存在的事实序号 = 在编 —— 必须当场拦下并点名。"""
    fake_llm({"items": [_item("钱的视角", [1, 99])]})
    with pytest.raises(sp.SplitError) as e:
        sp.split_material(MAT, RAW, ["钱的视角"])
    msg = str(e.value)
    assert "凭空造事实" in msg and "99" in msg and "钱的视角" in msg, msg


def test_non_integer_fact_id_is_blocked(fake_llm):
    fake_llm({"items": [_item("钱的视角", ["第一条"])]})
    with pytest.raises(sp.SplitError, match="非法的事实序号"):
        sp.split_material(MAT, RAW, ["钱的视角"])


def test_facts_are_always_a_subset(fake_llm):
    """子素材的事实永远是父素材的**子集**（数量不会变多）。"""
    fake_llm({"items": [_item("钱的视角", [1, 2, 3])]})
    out = sp.split_material(MAT, RAW, ["钱的视角"])
    assert len(out[0]["facts"]) <= len(MAT.facts)


def test_no_facts_is_allowed(fake_llm):
    """素材里撑不起这个角度时，允许空事实 —— **而不是编几条凑数**。"""
    fake_llm({"items": [_item("一个素材里没有的角度", [])]})
    out = sp.split_material(MAT, RAW, ["一个素材里没有的角度"])
    assert out[0]["facts"] == []


def test_parent_material_is_not_mutated(fake_llm):
    fake_llm({"items": [_item("钱的视角", [1])]})
    before = [f.text for f in MAT.facts]
    sp.split_material(MAT, RAW, ["钱的视角"])
    assert [f.text for f in MAT.facts] == before


def test_prompt_carries_the_fact_numbers(fake_llm, monkeypatch):
    """提示词里必须把事实**编号**列出来 —— 不然模型没法按序号引用。"""
    seen = {}

    def spy(prompt, **kw):
        seen["p"] = prompt
        return {"items": [_item("钱的视角", [1])]}

    monkeypatch.setattr(sp.llm, "chat_json", spy)
    sp.split_material(MAT, RAW, ["钱的视角"])
    p = seen["p"]
    assert "1. [data] 2023年3月裸辞" in p
    assert "3. [data] 单价从800涨到3000" in p
    assert "只能引用这些的序号" in p


# ---------------- 形态这一维（黑客松原句：channels **and formats**）----------------

def test_formats_only(fake_llm):
    """**只给形态** → 同一内容出多种形态（不按选题拆）。"""
    fake_llm({"items": [{"angle": "", "format": "口播脚本", "title": "口播", "core_message": "c",
                         "fact_ids": [1]},
                        {"angle": "", "format": "金句卡片", "title": "金句", "core_message": "c",
                         "fact_ids": []}]})
    out = sp.split_material(MAT, RAW, formats=["口播脚本", "金句卡片"])
    assert [(x["angle"], x["format"]) for x in out] == [("", "口播脚本"), ("", "金句卡片")]
    assert [f["text"] for f in out[0]["facts"]] == ["2023年3月裸辞"]


def test_angle_x_format_is_a_matrix(fake_llm):
    """**两维都给** → 选题 × 形态 的矩阵，每条都带齐两维。"""
    fake_llm({"items": [
        {"angle": "钱的视角", "format": "口播脚本", "title": "a", "core_message": "c", "fact_ids": [1]},
        {"angle": "钱的视角", "format": "金句卡片", "title": "b", "core_message": "c", "fact_ids": []},
        {"angle": "心理的视角", "format": "口播脚本", "title": "c", "core_message": "c", "fact_ids": [2]},
        {"angle": "心理的视角", "format": "金句卡片", "title": "d", "core_message": "c", "fact_ids": []},
    ]})
    out = sp.split_material(MAT, RAW, angles=["钱的视角", "心理的视角"],
                            formats=["口播脚本", "金句卡片"])
    assert len(out) == 4
    assert [(x["angle"], x["format"]) for x in out] == [
        ("钱的视角", "口播脚本"), ("钱的视角", "金句卡片"),
        ("心理的视角", "口播脚本"), ("心理的视角", "金句卡片")]


def test_matrix_has_a_cap(fake_llm):
    """两维相乘会爆量 —— 要有上限，且**明确报出乘出来多少**。"""
    fake_llm({"items": []})
    with pytest.raises(sp.SplitError, match=f"超过上限 {sp.MAX_ITEMS}"):
        sp.split_material(MAT, RAW, angles=[f"角度{i}" for i in range(sp.MAX_ANGLES)],
                          formats=["a", "b", "c"])


def test_formats_prompt_says_what_each_format_means(fake_llm, monkeypatch):
    """提示词要说清每个形态**具体长什么样** —— 不然四条都是同一段话换开头。"""
    seen = {}

    def spy(prompt, **kw):
        seen["p"] = prompt
        return {"items": [{"angle": "", "format": "口播脚本", "title": "t", "core_message": "c",
                           "fact_ids": []}]}

    monkeypatch.setattr(sp.llm, "chat_json", spy)
    sp.split_material(MAT, RAW, formats=["口播脚本"])
    assert "口播脚本 → 口语、短句、有停顿提示" in seen["p"]
    assert "金句卡片 → 只有一句话" in seen["p"]
