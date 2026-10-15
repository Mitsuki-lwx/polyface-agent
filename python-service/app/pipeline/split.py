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


def _prompt(mat: StructuredMaterial, raw_text: str, angles: list[str]) -> str:
    facts = "\n".join(f"{i}. [{f.type}] {f.text}" for i, f in enumerate(mat.facts, 1))
    want = "\n".join(f"{i}. {a}" for i, a in enumerate(angles, 1))
    return f"""素材正文：
{raw_text}

素材核心观点：{mat.core_message}
受众：{mat.audience}　语气：{mat.tone}

素材事实清单（**只能引用这些的序号**）：
{facts or "（这份素材没有抽取出事实 —— 那每条就只能靠正文本身，不要编事实）"}

请按下面这 {len(angles)} 个角度各拆一条：
{want}

返回 JSON：
{{"items": [{{"angle": "对应上面哪个角度（原样抄）", "title": "能当标题的一句话",
            "core_message": "这条的核心观点（一句话）", "fact_ids": [1, 3]}}]}}

`fact_ids` 是**上面事实清单的序号**（1-based）。没有可用事实就填空数组。"""


def split_material(mat: StructuredMaterial, raw_text: str, angles: list[str], *,
                   temperature: float = 0.6) -> list[dict]:
    """拆一次。返回 `[{angle, title, core_message, facts:[Fact…]}]`。

    :raises SplitError: 角度为空/超上限/产出条数对不上/引用了不存在的事实序号
    """
    angles = [a.strip() for a in (angles or []) if a and a.strip()]
    if not angles:
        raise SplitError("至少要给一个角度（模型不替你决定拆几条 —— 见 spec 关键决定二）")
    if len(angles) > MAX_ANGLES:
        raise SplitError(f"一次最多拆 {MAX_ANGLES} 条，收到 {len(angles)} 条"
                         f"（再多容易拆出重复的，建议分批）")

    data = llm.chat_json(_prompt(mat, raw_text, angles), system=SYSTEM,
                         temperature=temperature, scene="split")
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise SplitError("模型没有返回任何拆出来的条目")

    by_angle = _match_angles(raw_items, angles)
    out: list[dict] = []
    for i, angle in enumerate(angles, 1):
        item = by_angle.get(angle)
        if item is None:
            # **说清是哪个角度**，不是"少了一条"
            raise SplitError(f"第 {i} 个角度「{angle}」没有拆出内容 —— "
                             f"可能是素材里支撑不了这个角度，换个角度或删掉它")
        ids = item.get("fact_ids") or []
        if not isinstance(ids, list):
            raise SplitError(f"角度「{angle}」的 fact_ids 不是数组：{ids!r}")
        facts = []
        for fid in ids:
            try:
                n = int(fid)
            except (TypeError, ValueError):
                raise SplitError(f"角度「{angle}」引用了非法的事实序号：{fid!r}") from None
            if not 1 <= n <= len(mat.facts):
                # **这里就是"凭空造事实"的拦截点** —— 序号越界说明它在编
                raise SplitError(f"角度「{angle}」引用了不存在的事实序号 {n}"
                                 f"（父素材只有 {len(mat.facts)} 条）—— 这是凭空造事实")
            f = mat.facts[n - 1]
            facts.append({"text": f.text, "type": f.type, "from_fact": n})
        out.append({
            "angle": angle,
            "title": str(item.get("title") or "").strip(),
            "core_message": str(item.get("core_message") or "").strip(),
            "facts": facts,
        })
    logger.info("拆分完成：%d 个角度 → %d 条", len(angles), len(out))
    return out


def _match_angles(raw_items: list, angles: list[str]) -> dict:
    """把模型返回的条目按角度对回去。

    模型有时会把角度**改写**（"从钱的视角" → "金钱视角"）—— 所以先精确匹配，
    匹配不上再按"互相包含"配一次。仍然配不上就当那条没拆出来（上层会点名）。
    """
    by_angle: dict[str, dict] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        a = str(item.get("angle") or "").strip()
        for want in angles:
            if a == want or (a and (a in want or want in a)):
                by_angle.setdefault(want, item)
                break
    return by_angle
