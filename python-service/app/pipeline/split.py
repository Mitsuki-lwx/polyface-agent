"""素材拆分：一个素材 → 多个素材源（`docs/spec_split.md`）。

**本模块的底线：拆出来的事实不许凭空造。** 做法不是"让模型别编"（那靠不住），
而是**结构上堵死**：

    模型只返回**父素材事实清单的序号**（1-based），代码再按序号取回**原文**。
    → 子素材的每一条事实，字面上就是父素材的那一条，一个字都不会变。

与既有的"事实依据声明"（`docs/spec_factguard.md`）同一条思路：
用序号而不是原文，既省 token 又精确，而且**校验变成了查表**（不需要语义比对）。

另一条：**条数与角度由用户给**（spec 关键决定二）——
模型自己拆的结果通常是"5 条都差不多"，因为它不知道你的发布节奏、也不知道你想强调什么。
"""
from __future__ import annotations

import logging

from .. import llm
from ..schemas import StructuredMaterial

logger = logging.getLogger(__name__)

MAX_ANGLES = 8          # 再多一次调用容易拆出重复的
MAX_ITEMS = 12          # 选题 × 形态 的矩阵上限 —— 别让它爆量


#: "角度太空"的判据。命中这些词/太短的角度，拆出来必然雷同。
#:
#: 为什么要有这条：用户很自然会输入「随便拆」「你看着办」——
#: 那不是角度，是**把决定权推给模型**。而 spec 的关键决定二是"不让模型自己决定拆几条"，
#: 同理也不该让它自己决定"从哪些角度拆" —— 那正是雷同的来源。
VAGUE_HINTS = ("随便", "看着办", "你决定", "任意", "都行", "都可以", "帮我拆", "拆一下",
               "拆分", "自己看着", "看情况", "来个", "弄几个")

#: 角度短于这个长度基本没有信息量（"钱的视角" 5 字是正常下限）
MIN_ANGLE_CHARS = 4


def vague_angles(angles: list[str]) -> list[str]:
    """挑出**太空**的角度（返回原样，方便直接回给用户）。纯函数，无副作用。"""
    out = []
    for a in angles or []:
        t = (a or "").strip()
        if not t:
            continue
        if len(t) < MIN_ANGLE_CHARS or any(h in t for h in VAGUE_HINTS):
            out.append(t)
    return out


class SplitError(ValueError):
    """拆分不合法。**要说清是哪一条角度**，不是一句"拆分失败"。"""


SYSTEM = """你在帮一个自媒体博主做「一次创作、多次使用」：
把一份素材拆成**几条各自能独立发布的选题**，每条有自己的角度。

硬规矩：
1. 每条只能围绕**一个**角度，条与条之间**不能是同一件事换句话**。
2. 事实只能用素材里**已有的** —— 你只能填事实清单的**序号**，不能写新的事实。
   某条角度在素材里没有足够事实支撑时，那条就少填几个序号（甚至为空），
   **绝不要为了凑数编**。
3. 每条给一个能当标题用的说法，和一句话的核心观点。"""


def _targets(angles: list[str], formats: list[str]) -> list[tuple[str, str]]:
    """两维 → 一组 `(角度, 形态)`。只给一维时另一维留空。"""
    if angles and formats:
        return [(a, f) for a in angles for f in formats]
    if angles:
        return [(a, "") for a in angles]
    return [("", f) for f in formats]


def _label(t: tuple[str, str]) -> str:
    a, f = t
    if a and f:
        return f"{a}｜{f}"
    return a or f


def _prompt(mat: StructuredMaterial, raw_text: str, targets: list[tuple[str, str]]) -> str:
    facts = "\n".join(f"{i}. [{f.type}] {f.text}" for i, f in enumerate(mat.facts, 1))
    want = "\n".join(f"{i}. {_label(t)}" for i, t in enumerate(targets, 1))
    has_fmt = any(f for _, f in targets)
    fmt_rule = ("""
形态的差别要**真的做出来**，不是换个说法：
  · 长文版 → 有论证、有层次，能独立成篇
  · 口播脚本 → 口语、短句、有停顿提示，念出来顺
  · 图文 → 一条主句 + 几句补充，适合配图
  · 金句卡片 → 只有一句话，砍到不能再砍""" if has_fmt else "")
    return f"""素材正文：
{raw_text}

素材核心观点：{mat.core_message}
受众：{mat.audience}　语气：{mat.tone}

素材事实清单（**只能引用这些的序号**）：
{facts or "（这份素材没有抽取出事实 —— 那每条就只能靠正文本身，不要编事实）"}

请按下面这 {len(targets)} 个目标各出一条：
{want}
{fmt_rule}

返回 JSON：
{{"items": [{{"angle": "对应哪个角度（原样抄；没有角度就留空）",
            "format": "对应哪个形态（原样抄；没有形态就留空）",
            "title": "能当标题的一句话",
            "core_message": "这条的核心观点（一句话）", "fact_ids": [1, 3]}}]}}

`fact_ids` 是**上面事实清单的序号**（1-based）。没有可用事实就填空数组。"""


def split_material(mat: StructuredMaterial, raw_text: str,
                   angles: list[str] | None = None,
                   formats: list[str] | None = None, *,
                   temperature: float = 0.6) -> list[dict]:
    """拆一次。返回 `[{angle, format, title, core_message, facts:[Fact…]}]`。

    **选题与形态是两个维度**（黑客松原句写的是 "channels and **formats**"）：
    - 只给 `angles`  → 按选题拆（每条一个角度）
    - 只给 `formats` → 同一内容出多种形态（长文版 / 口播脚本 / 图文 / 金句卡片）
    - 两个都给       → 选题 × 形态 的矩阵（有上限，别爆量）

    :raises SplitError: 两维都没给/超上限/产出对不上/引用了不存在的事实序号
    """
    angles = [a.strip() for a in (angles or []) if a and a.strip()]
    formats = [f.strip() for f in (formats or []) if f and f.strip()]
    if not angles and not formats:
        raise SplitError("至少要给角度或形态之一"
                         "（模型不替你决定拆几条 —— 见 spec 关键决定二）")
    # **角度全是空的 → 明确拒绝**（spec §3 第 4 条）：
    # 与其拆出一堆雷同的东西让用户自己发现，不如当场说清"这不算角度"。
    # 只**部分**空则放行 —— 模型对具体的那几个角度仍然能拆好。
    vague = vague_angles(angles)
    if angles and len(vague) == len(angles):
        raise SplitError(
            f"这些角度太空，拆出来会雷同：{('、'.join(vague))}。"
            f"换成能区分开的角度再试，例如「从钱的视角」「从时间的视角」「从审美的视角」")
    if len(angles) > MAX_ANGLES:
        raise SplitError(f"一次最多 {MAX_ANGLES} 个角度，收到 {len(angles)} 个"
                         f"（再多容易拆出重复的，建议分批）")
    targets = _targets(angles, formats)
    if len(targets) > MAX_ITEMS:
        raise SplitError(f"选题 × 形态 = {len(targets)} 条，超过上限 {MAX_ITEMS}"
                         f"（别一次全铺开，分批做）")

    data = llm.chat_json(_prompt(mat, raw_text, targets), system=SYSTEM,
                         temperature=temperature, scene="split")
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise SplitError("模型没有返回任何拆出来的条目")

    by_label = _match_targets(raw_items, targets)
    out: list[dict] = []
    for idx, (angle, fmt) in enumerate(targets, 1):
        label = _label((angle, fmt))
        item = by_label.get(label)
        if item is None:
            # **说清是哪个目标**，不是"少了一条"
            raise SplitError(f"第 {idx} 个目标「{label}」没有拆出内容 —— "
                             f"可能是素材里支撑不了这个角度/形态，换一个或删掉它")
        ids = item.get("fact_ids") or []
        if not isinstance(ids, list):
            raise SplitError(f"目标「{label}」的 fact_ids 不是数组：{ids!r}")
        facts = []
        for fid in ids:
            try:
                n = int(fid)
            except (TypeError, ValueError):
                raise SplitError(f"目标「{label}」引用了非法的事实序号：{fid!r}") from None
            if not 1 <= n <= len(mat.facts):
                # **这里就是"凭空造事实"的拦截点** —— 序号越界说明它在编
                raise SplitError(f"目标「{label}」引用了不存在的事实序号 {n}"
                                 f"（父素材只有 {len(mat.facts)} 条）—— 这是凭空造事实")
            f = mat.facts[n - 1]
            facts.append({"text": f.text, "type": f.type, "from_fact": n})
        out.append({
            "angle": angle,
            "format": fmt,
            "title": str(item.get("title") or "").strip(),
            "core_message": str(item.get("core_message") or "").strip(),
            "facts": facts,
        })
    logger.info("拆分完成：%d 个目标 → %d 条", len(targets), len(out))
    return out


def _match_targets(raw_items: list, targets: list[tuple[str, str]]) -> dict:
    """把模型返回的条目按目标对回去。

    匹配规则是**子串包含**（模型常增减几个字："从钱的视角" → "钱的视角"），
    **不做"语义相近"的猜测** —— `金钱视角` 和 `从钱的视角` 人看得出是一回事，
    但静默配错目标会产出跑偏的子素材，比报错难查。

    两维都给时用"角度｜形态"整体比；只给一维时比那一维。
    """
    by_label: dict[str, dict] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        a = str(item.get("angle") or "").strip()
        f = str(item.get("format") or "").strip()
        for angle, fmt in targets:
            label = _label((angle, fmt))
            ok = ((a and (a in angle or angle in a)) if angle else not a) and \
                 ((f and (f in fmt or fmt in f)) if fmt else not f)
            if ok:
                by_label.setdefault(label, item)
                break
    return by_label
