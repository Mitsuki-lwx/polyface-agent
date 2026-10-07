"""数字依据校验（FR-60）回归测试 —— 对应 docs/52 spec §6 的 10 个场景。

起因：本项目第一次真实 LLM 端到端暴露规则质检缺陷 —— 一份完全干净的小红书成稿
刷出 4 条假告警（`1. 2. 3.` 分点序号 + 素材里明明有的 `0`）。
mock 模式测不出来：mock 成稿是固定文本，不含分点结构。

本文件锁住三件事：
  1. 中文邻接数字必须能被稳定提取（旧版 `\\b` 会让同一数字时隐时现）
  2. 分点序号不得被当成事实数字（分点是 DNA 推荐的标准结构，否则越规范越吵）
  3. `3w` 这类单位简写不得漏检，且与 `3万` 可比
"""
from __future__ import annotations

import pytest

from app.pipeline.generate import _numbers_in, _norm_num, _rule_qa
from app.schemas import Fact, StructuredMaterial
from app.schemas_gen import DraftPayload

DNA = {"code": "xhs", "limits": {"body_chars_max": 5000}, "tags": {"count_max": 8}}


def mat(*facts: str) -> StructuredMaterial:
    return StructuredMaterial(core_message="c", tone="t", audience="a",
                              facts=[Fact(text=f, type="story") for f in facts])


def num_warnings(body: str, *facts: str) -> list[str]:
    _, warns = _rule_qa(DNA, DraftPayload(titles=["t"], body=body, tags=["a"]), mat(*facts))
    return [w for w in warns if "无依据的数字" in w]


# ============================================================ 正向：必须抓到

def test_flags_number_absent_from_facts():
    """1. 正文有素材没有的数字 → 必须报。"""
    w = num_warnings("我月入999万。", "月入3万")
    assert len(w) == 1 and "999万" in w[0]


def test_flags_shorthand_w_not_in_facts():
    """2. `5w` 与素材的 `3万` 不同 → 必须报（旧版完全漏检）。"""
    w = num_warnings("现在月入5w。", "从月入0做到3万")
    assert len(w) == 1 and "5w" in w[0]


def test_flags_decimal_amount():
    """3. `3.5亿` 素材里没有 → 必须报，且不得被序号剥离误伤。"""
    w = num_warnings("我们营收 3.5亿。", "去年营收 1亿")
    assert len(w) == 1 and "3.5亿" in w[0]


# ============================================================ 反向：必须不报

def test_no_warning_when_chinese_adjacent():
    """4. D1 回归：同一个 `0` 在素材里是 `月入0做到`，在正文里不得因邻接汉字而误报。"""
    assert num_warnings("靠写作从月入0做到3万。", "靠写作从月入0做到3万") == []


def test_same_number_extracted_regardless_of_neighbour():
    """4b. 邻接字符不该改变提取结果（旧版 `\\b` 的核心病灶）。"""
    assert _numbers_in("月入0做到3万") == _numbers_in("月入0，坚持3万")


def test_no_warning_for_list_markers():
    """5. D2 回归：分点序号不是事实数字。"""
    assert num_warnings("1. 甲\n2. 乙\n3. 丙", "素材里一个数字都没有") == []


def test_no_warning_for_emoji_prefixed_list():
    """6. emoji 前缀的序号同样不得误报。"""
    assert num_warnings("📊 1. 复盘数据\n💬 2. 分析反馈\n📝 3. 建立选题库", "无数字素材") == []


def test_no_warning_for_shorthand_matching_full_form():
    """7. D3 回归：`3w` 与素材的 `3万` 是同一个数 → 不报。"""
    assert num_warnings("靠写作从0做到月入3w。", "靠写作从月入0做到3万") == []


def test_year_survives_list_marker_stripping():
    """8. 年份不得被当序号剥掉（`2023年` 仍参与比对）。"""
    assert "2023" in _numbers_in("2023年刚裸辞")
    assert "2023" in _numbers_in("2023. 年")


# ============================================================ 边界

def test_multiple_unsupported_numbers_merged_into_one_warning():
    """9. 多条无依据数字合并为一条，且列出全部。"""
    w = num_warnings("去年 888 单，今年 999 单。", "无数字素材")
    assert len(w) == 1, f"应合并为一条，实际 {len(w)} 条: {w}"
    assert "888" in w[0] and "999" in w[0]


def test_single_number_warning_text_unchanged():
    """9b. 单条时文本与原先逐字一致（既有测试与 UI 文案不破）。"""
    assert num_warnings("我月入999万。", "月入3万") == [
        "正文含素材中无依据的数字: 999万（请确认或删除）"]


def test_marker_and_real_number_coexist():
    """10. 序号与真无依据数字并存时，只报真的有依据问题的那个。"""
    w = num_warnings("1. 第一点\n2. 第二点\n去年赚了 777 万。", "无数字素材")
    assert len(w) == 1
    assert "777" in w[0]
    assert "1" not in w[0].split(":")[1] and "2" not in w[0].split(":")[1]


# ============================================================ 单元：token 层

@pytest.mark.parametrize("tok,expect", [("3w", "3万"), ("3W", "3万"), ("3万", "3万"),
                                        ("3k", "3k"), ("5亿", "5亿")])
def test_norm_num(tok, expect):
    """C. 单位归一化：只把「万」的简写归一。"""
    assert _norm_num(tok) == expect


def test_norm_num_keeps_original_for_display():
    """C4/F3. 比较用归一化，展示用原文 —— 不该把用户写的 3w 改写成 3万。"""
    assert _numbers_in("月入3w") == {"3万": "3w"}


def test_decimal_and_int_are_distinct():
    """3b. `3.5亿` 与 `35亿` 不是同一个数。"""
    assert _numbers_in("3.5亿") != _numbers_in("35亿")


# ============================================================ 中文数字（2026-10-07 修）

def test_no_warning_when_material_uses_chinese_numeral():
    """**实测误报的回归**：素材事实写「定价九十九」，稿件写「99」—— 那是有依据的。

    修前会给用户报「正文含素材中无依据的数字: 400、99（请确认或删除）」，
    让他去删一个明明有依据的数。
    """
    assert num_warnings("我搭了付费专栏，定价99，卖了400多份。", "付费专栏定价九十九，卖了四百多份") == []


def test_chinese_numeral_fabrication_still_caught():
    """归一化之后**真幻觉照样要抓** —— 别把校验修成永远不报。"""
    w = num_warnings("我搭了付费专栏，定价199，卖了400多份。", "付费专栏定价九十九，卖了四百多份")
    assert len(w) == 1 and "199" in w[0]


def test_no_warning_for_idiomatic_chinese_numeral():
    """「迈出这一步」「两下搞定」不是事实数字 —— 别把它当数字去比对。"""
    assert num_warnings("迈出这一步并不难，两下就想明白了。", "素材里一个数字都没有") == []


def test_bare_unit_char_is_not_a_number():
    """「去年赚了 777 万」里的那个「万」单独出现时不是数字，不能解析成 10000。"""
    w = num_warnings("去年赚了 777 万。", "无数字素材")
    assert len(w) == 1
    assert "777" in w[0]
    assert "10000" not in w[0]
