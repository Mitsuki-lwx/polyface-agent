"""数字的**提取与归一化** —— 产品（`generate._rule_qa`）与评测断言共用一套口径。

**为什么必须只有一份**：评测集要测的是线上那条路。两边各写一份，
口径迟早分叉，测出来的东西就没意义了（`docs/spec_eval.md` §5）。

踩过的坑（每一条都是实测，不是假想）：

| # | 现象 | 处理 |
|---|---|---|
| 1 | `月入0做到3万` 里的 `0` 被 `\\b` 吃掉，素材里"隐身"、正文里"现身" | 不用 `\\b`，改「前后都不是数字」判定 |
| 2 | 分点序号 `1.` `2、` 被当数字主张 | 提取前 `_LIST_MARKER_RE` 剥离 |
| 3 | `3w` 漏检 | 单位类补 `w/W`，归一化为「万」 |
| 4 | 素材写「九十九」、稿件写「99」→ 误报 | 中文数字归一化为数值 |
| 5 | 稿件写「迈出这一步」→ 误报 | 稿件侧**严格**：只收带单位或 ≥2 位的中文数字 |
| 6 | 稿件写「2️⃣ 小标题」→ 误报 | 提取前 `_KEYCAP_RE` 剥离 emoji 键帽 |
| 7 | 稿件写「2万3」、素材写「两万三」→ 误报 | 支持「数字+单位+数字」的省略写法 |
"""
from __future__ import annotations

import re

from . import zhnum

# ⚠️ 不要用 `\b` 界定数字：汉字是 Unicode 词字符，`月入0做到3万` 在 `0` 与 `做` 之间
# 没有词边界 → `0` 被丢弃；而 `月入0，` 处有边界 → `0` 保留。结果是**同一个数字在素材里
# 隐身、在正文里现身**，必然误报。改用「前后都不是数字」判定，与邻接字符无关。
#
# 两个分支，**省略写法在前**：
#   ① `2万3`（= 两万三）—— 数字 + 单位 + 数字，必须整体吃下，
#      否则会被拆成 `2` 和 `3`，与素材的「两万三」对不上（坑 #7）
#   ② 常规：`3.5亿` / `3w` / `5.2%` / `2023`
# ⚠️ 单位字符类**只此一份**：`_NUM_RE`（提取）与 `num_value`（求值）必须用同一份，
# 分开写迟早分叉 —— 实测踩过：只给提取加了「成」，求值时又不认，于是 `3成` 变成 3。
_UNIT_CHARS = "万wWkK千亿成"
_UNIT_CLS = f"[{_UNIT_CHARS}]"
_DIGIT_UNIT = r"[%％" + _UNIT_CHARS + r"]"
_ABBREV = r"\d+(?:\.\d+)?[万wWkK千亿]\d{1,2}"
# `\s*` 是必须的：实测稿件里出现「1200 万」（数字与单位之间有空格），
# 不认这个空格就只提取到 `1200`，与素材的「1200万」对不上 → 误报。
# 空格必须是 `(?:\s*单位)?` 的一部分 —— 写成 `\s*单位?` 会把「12 个月」吃成 `12 `（实测踩到）
_NUM_RE = re.compile(rf"(?<!\d)(?:{_ABBREV}|\d+(?:\.\d+)?(?:\s*{_DIGIT_UNIT})?)(?!\d)")

# 分点/序号标记（`1.` `2、` `3）` `3．` …）不是数字主张，提取前先剥离。
# 两条防线避免"把真数字当序号吃掉"：
#   ① 只认 1~2 位（`2023` 这类年份够不到，不会被误剥）
#   ② 半角句点必须**后接空白**才算序号 —— 否则 `营收 3.5亿` 会被剥成 `5亿`
#      （中文标点 `、）．` 不存在小数点歧义，无须此约束）
_LIST_MARKER_RE = re.compile(
    r"(?m)(?:^[^\w\s]{0,4}[ \t]*|[ \t])\d{1,2}[ \t]*(?:[、)）．]|\.[ \t])(?![ \t]*\d)"
    # 补零序号（`01 收入构成` `02 副业踩坑`）：两位且以 0 开头 + 空白。
    # 限「以 0 开头」是为了不误吃真数字（`12 个月` 这类不能动）—— 实测 m02/gzh 误报。
    r"|^[^\w\s]{0,4}[ \t]*0\d[ \t]+"
)

# emoji 数字键帽（`1️⃣` `2️⃣` `#️⃣`）同样是分点标记，不是数字主张。
# 形态是 `数字 + (变体选择符) + U+20E3`，`_LIST_MARKER_RE` 抓不到（坑 #6）。
_KEYCAP_RE = re.compile(r"[0-9#*]\ufe0f?\u20e3")

_UNIT_ALIAS_RE = re.compile(r"[wW]$")
_UNITS = {"万": 10000.0, "w": 10000.0, "W": 10000.0,
          "k": 1000.0, "K": 1000.0, "千": 1000.0, "亿": 100000000.0,
          # 「成」是分数单位（一成 = 10%）。本项目百分比口径把 `50%` 记作 50，
          # 所以 `3成` 也要记作 30 —— 否则稿件写「3成」、素材写「三成」会对不上（实测误报）。
          "成": 10.0}


def norm_num(tok: str) -> str:
    """把「万」的简写归一，使 `3w` 与 `3万` 可比（只作用于**紧跟数字**的单位）。

    同时去掉数字与单位之间的空白 —— `777 万` 与 `777万` 是同一个数，键也该一样。
    """
    return _UNIT_ALIAS_RE.sub("万", tok.replace(" ", "").replace("\u3000", ""))


def numbers_in(text: str) -> dict[str, str]:
    """提取文本中的数字 token。

    返回 `{归一化形式: 原文}` —— 归一化只用于**比较**（`3w` 与 `3万` 视为同一个数），
    展示时必须用原文，否则会把用户写的 `3w` 改写成 `3万`，看着像被篡改。
    """
    out: dict[str, str] = {}
    cleaned = _KEYCAP_RE.sub(" ", _LIST_MARKER_RE.sub(" ", text or ""))
    for tok in _NUM_RE.findall(cleaned):
        out.setdefault(norm_num(tok), tok)
    return out


def num_value(tok: str) -> float | None:
    """数字 token → 数值。

    `99`→99.0　`3.5亿`→3.5e8　`5.2%`→5.2　`2万3`→23000.0（省略写法）
    """
    t = (tok or "").strip().rstrip("%").replace(" ", "").replace("\u3000", "")
    if not t:
        return None
    m = re.fullmatch(rf"(\d+(?:\.\d+)?)({_UNIT_CLS})?(\d{{1,2}})?", t)
    if not m:
        return None
    head, unit, tail = m.group(1), m.group(2), m.group(3)
    try:
        base = float(head)
    except ValueError:
        return None
    if not unit:
        return base
    mult = _UNITS[unit]
    if tail is None:
        return base * mult
    # 省略写法：`2万3` = 2万 + 3千；`3亿5` = 3亿 + 5千万
    return base * mult + float(tail) * (mult / 10.0)


def num_values(text: str, *, strict: bool = False) -> dict[float, str]:
    """文本里数字的**数值** → 原始写法（阿拉伯 + 中文数字统一归一化）。

    `strict` 的用法**不对称**（坑 #4/#5）：
    - 素材侧 `strict=False`：中文数字**都算依据**，宁可放过 ——
      否则素材写「三」、稿件写「3」会误报。
    - 稿件侧 `strict=True`：只查**像数字的**中文数字（带单位或 ≥2 位），
      否则「迈出这一步」里的「一」会被当成一个数字去比对。
    """
    out: dict[float, str] = {}
    for n, original in numbers_in(text).items():
        v = num_value(n)
        if v is not None:
            out.setdefault(v, original)      # 展示用原文，别改写成归一化形式
    for v, original in zhnum.tokens_in(text, strict=strict).items():
        out.setdefault(float(v), original)   # 展示用原文：「九十九」别显示成 99
    return out
