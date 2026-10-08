"""中文数字 → 整数。给「数字依据」校验用（产品与评测集共用同一套口径）。

**为什么需要它**：`generate._numbers_in` 只提取**阿拉伯数字**。
素材里写「定价九十九」、稿件里写成「99」时，数字依据校验会**误判为"素材里没有的数字"**。

实测（2026-10-07）两处都踩到了：
1. **评测集**首次跑分，A1 报了 `['99']` —— 素材 `m01` 用的正是中文数字；
2. **产品** `_rule_qa` 同样误报，给用户的提示是
   「正文含素材中无依据的数字: 400、99（请确认或删除）」—— 让用户去删一个明明有依据的数。

两处必须是**同一套口径**，否则评测集测的不是线上那条路。

覆盖的口语形式（实测素材里都出现了）：
    九十九 → 99      两万三 → 23000    三千二 → 3200
    五十   → 50      四百多份 → 400（"多"不参与）
"""
from __future__ import annotations

import re

DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
          "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
# 「个」是量词，会夹在数字与单位之间（「一个亿」= 1亿）。当无意义字符跳过。
FILLERS = {"个": 0}
UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}

# 连续的中文数字串（含单位）。刻意不含"多/几/数"这类模糊量词。
# 「个」只在**数字与单位之间**才算数（「一个亿」= 1亿）。
# 不能直接把它并进字符类 —— 那样「三个月」会整体变成一个"数字"（len≥2），
# 绕过严格模式的单字过滤，又变成一类误报（实测踩到）。
_CN_RE = re.compile(r"[零〇一二两三四五六七八九十百千万亿成]+(?:个[十百千万亿]+)?")


def parse(text: str) -> int | None:
    """解析一个纯中文数字串。解析不了返回 None（**不猜**）。

    「成」是分数单位（一成 = 10%）。本项目的百分比口径把 `50%` 记作数值 `50`，
    所以「五成」也换算成 `50` —— 这样「素材写五成、稿件写 50%」才能对上
    （实测 m02 的误报就是这么来的）。
    """
    if text.endswith("成"):
        head = parse(text[:-1])
        return None if head is None else head * 10

    if not text or any(c not in DIGITS and c not in UNITS and c not in FILLERS
                       and c != "成" for c in text):
        return None

    total = 0          # 已结算部分（万/亿 之上）
    section = 0        # 当前段
    last_unit = 0      # 最近一个单位（用于口语省略）
    number = 0         # 当前累积的阿拉伯位
    zero_after_unit = False   # 见过「零」→ 末尾那位是**个位**，不是省略单位
    seen_digit = False        # 见过数字字符（「十万」没有，但它是合法的）

    for ch in text:
        if ch in FILLERS:
            continue
        if ch in DIGITS:
            number = DIGITS[ch]
            seen_digit = True
            if ch in ("零", "〇") and last_unit:
                zero_after_unit = True
        else:
            unit = UNITS[ch]
            if unit >= 10000:                      # 万 / 亿
                base = section + number
                section = (base if base else 1) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            last_unit = unit
            zero_after_unit = False
            number = 0

    if number:
        # 口语省略末位单位：「两万三」= 2万 + 3千，「三千二」= 3千 + 2百
        # 但「一千零五」里的 5 是**个位**（有「零」占位），不能按省略算
        if last_unit >= 100 and not zero_after_unit and (total + section):
            section += number * (last_unit // 10)
        else:
            section += number

    # 单个单位字符不是数字：「万」/「十」单独出现时（如「去年赚了 777 万」里的那个「万」）
    # 不该被解析成 10000。至少要有数字字符，或长度 ≥2（「十万」合法）。
    # 单个单位字符一般不是数字（「去年赚了 777 万」里的那个「万」），
    # 但「十」是例外 —— 它单独出现就是 10（「十成」= 100%）。
    if not seen_digit and len(text) < 2 and text != "十":
        return None
    return total + section


def tokens_in(text: str, *, strict: bool = False) -> dict[int, str]:
    """文本里中文数字的 **值 → 原文**。展示告警时要用原文（别把「九十九」显示成 99）。"""
    out: dict[int, str] = {}
    for m in _CN_RE.finditer(text or ""):
        token = m.group(0)
        if strict and not (len(token) >= 2 or any(c in UNITS for c in token)):
            continue
        v = parse(token)
        if v is not None:
            out.setdefault(v, token)
    return out


def numbers_in(text: str, *, strict: bool = False) -> set[int]:
    """文本里所有中文数字的**数值**集合。

    `strict=True` 只收**像数字的**：带单位（十百千万亿）或 ≥2 个数字字符。
    这是为了避开成语化用法 —— 「迈出**这一步**」「**两**下就搞定」里的 一/两
    不是事实，不该被当成"稿件里的数字"去比对（实测踩到过）。

    **用法是不对称的**：素材侧用宽松（都算"有依据"，宁可放过），
    稿件侧用严格（只查真正像数字的，宁可少报）。
    """
    out: set[int] = set()
    for m in _CN_RE.finditer(text or ""):
        token = m.group(0)
        if strict and not (len(token) >= 2 or any(c in UNITS for c in token)):
            continue
        v = parse(token)
        if v is not None:
            out.add(v)
    return out
