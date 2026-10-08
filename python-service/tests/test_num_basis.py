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
from pathlib import Path

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


def test_no_warning_for_emoji_keycap_markers():
    """**全量跑分实测的误报**：稿件用小标题 `2️⃣ 再做复利：…`。

    `_numbers_in` 会把键帽里的 `2` 当成数字主张，报「素材里找不到依据的数字: ['2']」。
    键帽形态是 `数字 + (变体选择符) + U+20E3`，`_LIST_MARKER_RE` 抓不到。
    """
    body = "2️⃣ 再做复利：99元专栏，三个月卖出400多份\n3️⃣ 换个思路：不挤独木桥"
    assert num_warnings(body, "付费专栏定价99，卖了400多份") == []


def test_keycap_stripped_but_real_numbers_kept():
    got = _numbers_in("2️⃣ 再做复利：99元专栏，三个月卖出400多份")
    assert "2" not in got
    assert "99" in got and "400" in got


def test_keycap_variants_are_stripped():
    """`1️⃣` `#️⃣` `*️⃣` 都不该留下数字。"""
    assert _numbers_in("1️⃣ 甲\n#️⃣ 乙") == {}


# ============================================================ 全量跑分实测的三处 bug（2026-10-07）

def test_thousand_unit_is_recognised():
    """`8千` 曾被拆成 `8` —— 单位类漏了「千」，与素材的「八千」对不上。"""
    assert _numbers_in("月入稳定在8千-1万2") == {"8千": "8千", "1万2": "1万2"}
    assert num_warnings("月入稳定在8千。", "月入稳定在八千") == []


def test_abbreviated_form_matches_chinese_numeral():
    """`2万3` 与「两万三」是同一个数。"""
    assert num_warnings("卡里2万3。", "卡里就剩两万三") == []


def test_zero_padded_section_marker_is_stripped():
    """`**01 收入构成**` 是补零序号，不是数字主张。"""
    assert _numbers_in("**01 收入构成：不是暴富**") == {}


def test_plain_two_digit_number_is_not_eaten():
    """补零序号规则**不能**误吃真数字。"""
    assert _numbers_in("12 个月") == {"12": "12"}
    assert _numbers_in("2023年全国") == {"2023": "2023"}


# ------------------------------------------------ 全量跑分实测：又两处误报（2026-10-08）

def test_arabic_digit_with_cheng():
    """稿件写「闲鱼3成」，素材写「闲鱼占三成」—— 同一个意思。

    中文的「成」由 `zhnum` 处理，阿拉伯的 `3成` 由 `numberish` 处理，两边都是 ×10。
    """
    assert _numbers_in("小红书接单5成｜闲鱼3成") == {"5成": "5成", "3成": "3成"}
    assert num_warnings("小红书接单5成｜闲鱼3成。", "闲鱼占三成，小红书接单占五成") == []


def test_space_between_number_and_unit():
    """**实测误报**：稿件写「1200 万」（数字与单位之间有空格），素材写「1200万」。

    不认这个空格就只提取到 `1200`，与素材的 1200万 对不上。
    """
    assert _numbers_in("拿了 1200 万美元") == {"1200万": "1200 万"}
    assert num_warnings("它拿了 1200 万美元融资。", "它拿了1200万美元融资") == []


def test_space_fix_does_not_resurrect_bare_unit():
    """反例守卫：允许空格之后，单独的「万」仍不能被当成数字。"""
    assert _numbers_in("去年赚了 777 万") == {"777万": "777 万"}
    assert "10000" not in "".join(num_warnings("去年赚了 777 万。", "无数字素材"))


# ============================================================ 真实输入不崩（性质守卫）

def test_number_pipeline_never_crashes_on_real_material():
    """拿**真实的冻结素材**跑一遍数字管线，只要求「不抛异常」。

    为什么值得单列：这几轮 bug 全是"某个字符出现在某个位置"炸的 ——
    `成` 在中间（KeyError）、空格在数字与单位之间、单位类分两处写导致分叉。
    逐个举例永远漏，不如拿真语料整段过一遍。
    """
    from app.pipeline.numberish import num_values
    corpus = Path(__file__).resolve().parents[2] / "eval" / "corpus"
    files = sorted(corpus.glob("m[0-9][0-9].json"))
    if not files:
        pytest.skip("素材集不在（eval/corpus/）")
    import json
    for f in files:
        text = json.loads(f.read_text(encoding="utf-8"))["raw_text"]
        # 素材侧（宽松）与稿件侧（严格）两种口径都要过
        num_values(text, strict=False)
        num_values(text, strict=True)


@pytest.mark.parametrize("text", [
    "五成三", "成", "三成五", "去年赚了 777 万", "12 个月", "1200 万",
    "5成3", "3成", "一个亿", "两万三", "2023. 年", "3.5亿", "5.2%",
    "1. 甲\n2. 乙", "2️⃣ 小标题", "从0到1", "迈出这一步", "第1、2、3点",
])
def test_number_pipeline_never_crashes_on_tricky_snippets(text):
    """边角串也不能抛 —— 它们都是从实测误报里攒下来的。"""
    from app.pipeline.numberish import num_values
    num_values(text, strict=False)
    num_values(text, strict=True)
