"""中文数字解析（`app/pipeline/zhnum.py`）的测试。

为什么值得单独测：A1 数字依据断言的正确性**完全依赖它**。
它错了，评测集就会产生系统性误报 —— 而误报会让人不再信这份报告
（比不检查更糟，见 test_check_scripts.py 的同款理由）。
"""
from __future__ import annotations

import pytest

from app.pipeline import zhnum as Z


@pytest.mark.parametrize("text,expect", [
    ("九十九", 99),          # 实测素材里的写法
    ("两万三", 23000),        # 口语省略末位单位
    ("三千二", 3200),
    ("五十", 50),
    ("四百", 400),
    ("六百", 600),
    ("二十", 20),
    ("七", 7),
    ("一万", 10000),
    ("十万", 100000),        # 全是单位，没有数字字符
    ("一百二十三", 123),
    ("一千零五", 1005),       # 有「零」占位 → 末尾是个位，不是省略单位
    ("一万零五十", 10050),
    ("两万三千", 23000),
    ("十万八千", 108000),
    ("一亿", 100000000),
    ("十二", 12),
    ("两百", 200),
    ("两千", 2000),
    ("三十五", 35),
])
def test_parse_known_forms(text, expect):
    assert Z.parse(text) == expect


@pytest.mark.parametrize("text", ["三点五", "多", "", "abc", "第一", "一、"])
def test_parse_refuses_what_it_cannot_read(text):
    """读不出来就返回 None —— **不猜**。"""
    assert Z.parse(text) is None


# ---------------------------------------------------------------- strict

def test_strict_drops_idiomatic_single_digits():
    """「迈出这一步」里的「一」不是事实数字 —— 严格模式必须丢掉它。

    这是实测踩到的第二类误报（第一类是中文数字没归一化）。
    """
    text = "我决定迈出这一步，两下就搞定了，一定有办法"
    assert 1 not in Z.numbers_in(text, strict=True)
    assert 2 not in Z.numbers_in(text, strict=True)
    # 宽松模式仍然收（素材侧要用宽松，宁可放过）
    assert 1 in Z.numbers_in(text, strict=False)


def test_strict_keeps_number_like_tokens():
    """带单位或 ≥2 位的照收 —— 那些才像事实。"""
    text = "定价九十九，房租三千二，卖了四百多份"
    got = Z.numbers_in(text, strict=True)
    assert {99, 3200, 400} <= got


def test_strict_keeps_two_digit_bare_number():
    """「三十五」没有单独的单位字符以外的判断 —— 两位以上照收。"""
    assert 35 in Z.numbers_in("三十五", strict=True)


def test_numbers_in_on_real_material_sentence():
    """回归：实测素材 m01 里那句。"""
    text = "靠着一个叫小鹅通的工具，我搭了自己的付费专栏，定价九十九。三个月下来，卖了四百多份。"
    got = Z.numbers_in(text, strict=True)
    assert 99 in got and 400 in got


# ------------------------------------------------ 全量跑分实测的两处（2026-10-07）

@pytest.mark.parametrize("text,expect", [
    ("三成", 30),      # 「成」= 10%。素材写「闲鱼占三成」、稿件写「闲鱼30%」
    ("五成", 50),
    ("两成", 20),
    ("一成", 10),
    ("十成", 100),     # 单独「十」也是合法数字
    ("一个亿", 100000000),   # 量词「个」夹在数字与单位之间
    ("三个", 3),
])
def test_measured_forms(text, expect):
    assert Z.parse(text) == expect


def test_cheng_is_a_fraction_unit():
    """**实测误报的回归**：素材「闲鱼占三成，小红书接单占五成」，
    稿件「闲鱼30%、小红书50%」—— 同一个意思，必须对得上。"""
    from app.pipeline.numberish import num_values
    mat = set(num_values("闲鱼占三成，小红书接单占五成，知识付费占两成"))
    body = set(num_values("闲鱼30%、小红书50%、知识付费20%"))
    assert body <= mat, f"稿件里素材找不到的：{sorted(body - mat)}"


def test_lone_wan_is_still_not_a_number():
    """反例守卫：放宽「十」的时候不能把「万」也放进来
    （「去年赚了 777 万」里的那个「万」不是数字）。"""
    assert Z.parse("万") is None
    assert Z.parse("亿") is None
    assert Z.parse("百") is None


def test_lone_shi_is_a_number():
    assert Z.parse("十") == 10


def test_cheng_in_the_middle_does_not_crash():
    """**实测炸过**：把「成」加进 `_CN_RE` 后，`parse` 的循环遇到它就 `UNITS[ch]` → KeyError。

    调用链是 `_rule_qa → _num_values → zhnum.tokens_in → parse`，
    所以**生产里任何一句含「成」的稿件都会让整个平台生成失败**。
    """
    assert Z.parse("五成三") is None
    assert Z.parse("成") is None
    assert Z.parse("三成五") is None
    # 整句也不能炸
    assert Z.numbers_in("闲鱼占三成，小红书占五成") == {30, 50}


def test_every_cn_char_is_handled_by_parse():
    """**穷举守卫**：`_CN_RE` 能匹配到的每个字符，`parse` 都必须能处理。

    这条比逐个举例强 —— 以后谁再往正则里加字符而忘了改 `parse`，这里立刻红。
    """
    import re
    for ch in Z._CN_RE.pattern:
        if not ("\u4e00" <= ch <= "\u9fff"):
            continue
        # 不该抛异常（返回 None 或数字都算通过）
        try:
            Z.parse(ch)
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"parse({ch!r}) 抛了 {type(e).__name__}: {e}") from e
