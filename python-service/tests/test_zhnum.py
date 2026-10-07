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
