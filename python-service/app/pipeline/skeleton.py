"""段落级骨架装配器 —— Extract-then-select（`docs/spec_skeleton.md` · ADR-025）。

draft 阶段不再让模型自由写正文，而是产出**骨架**：每段声明依据的事实序号 + 段内文本；
段内/标题里的数字、年份、专名写成槽位 `{{n:序号|表层}}`，由本模块：

  1. 校验槽位引用合法（G1）与表层有依据（G2）—— 数值等价复用 `numberish`/`zhnum`
     一份口径（素材侧宽松、表层侧同宽：这是**放行**判定，宽松只会少误拦，不会漏放）；
  2. 回填表层 → 拼接正文；
  3. 机械门（G3 裸数字/年份拦截、G4 结构）—— 硬违规由 `run_draft` 修复一轮，
     仍违规则平台显式失败（FR-70：不静默带病产出）。

**为什么是纯函数**：`(骨架, 事实清单, 豁免证据) → (产物, 硬违规, 软违规)` 不读网、
不写文件、不调模型 —— 与评测断言同一约束，可单测、可复跑。

**豁免证据（extra_evidence）**：用户模板开头/结尾、画像避雷这类**用户自己写的**内容
不是模型编造，其数字不参与段声明核对（ADR-014：模板原样入稿）。mock 模式额外豁免
`core_message`（understand 的上游产物，mock 里它就是素材原句拼接）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from pydantic import BaseModel, Field

from ..schemas import Fact
from ..schemas_gen import DraftPayload
from . import proper
from .numberish import num_values

# 全角花括号归一（模型偶发输出 ｛｛3|286块｝｝）—— 归一后的文本也是回填后的成稿文本。
_FW_TRANSLATE = str.maketrans("｛｝", "{}")

# 槽位：`{{n:3|286块}}` / `{{3|286块}}` / `{{n：3｜286块}}`。
# 坏形（`{{n:}}`、`{{x|y}}`、未闭合）**故意不匹配** —— 留在文本里，
# 由「残留 {{」检查按硬违规接住（宁拦不漏，修复轮会给出可读原因）。
_SLOT_RE = re.compile(r"\{\{\s*(?:n\s*[:：]\s*)?(\d{1,3})\s*[,|｜]\s*([^{}|｜]+?)\s*\}\}")

# 年份口径与 `generate._YEAR_RE` / `eval/assertions.py` 同一份：整词匹配，
# `月收入在8000—12000元` 里的 `12000` 不能抠出 `2000` 当年份。
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


class SkeletonPara(BaseModel):
    """骨架的一段：声明依据的事实序号 + 段内文本（可含槽位）。"""
    facts: list[int] = Field(default_factory=list)
    role: str = "body"          # hook|body|evidence|close —— 仅 advisory，不参与装配
    text: str = ""


class DraftSkeleton(BaseModel):
    """draft 阶段的新产物契约（`docs/spec_skeleton.md` §3）。"""
    titles: list[str] = Field(default_factory=list)
    paras: list[SkeletonPara] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    cover_suggestion: str = ""
    interaction_line: str = ""
    rationale: str = ""


@dataclass
class AssembleResult:
    draft: DraftPayload
    hard: list[str] = field(default_factory=list)   # 硬违规：修复一轮后仍存在 → 平台失败
    soft: list[str] = field(default_factory=list)   # 软违规：进 QA warnings


# ---------------------------------------------------------------- 宽容解析
def parse_skeleton(data: dict[str, Any] | None) -> DraftSkeleton:
    """把 LLM 回的 dict 规整成骨架。形状不对不抛错 —— 结构问题由 G4 报，
    这里只做「能救的救」（字段转字符串、丢空值），不猜内容。"""
    data = data if isinstance(data, dict) else {}
    paras: list[SkeletonPara] = []
    for p in data.get("paras") or []:
        if not isinstance(p, dict):
            continue
        facts: list[int] = []
        for x in p.get("facts") or []:
            try:
                n = int(x)
            except (TypeError, ValueError):
                continue
            if n > 0:
                facts.append(n)
        text = str(p.get("text", "")).strip()
        if not text:
            continue          # 空段直接丢弃，G4 只对「全军覆没」报硬违规
        paras.append(SkeletonPara(facts=facts, role=str(p.get("role", "body")).strip() or "body",
                                  text=text))
    return DraftSkeleton(
        titles=[str(t).strip() for t in (data.get("titles") or []) if str(t).strip()],
        paras=paras,
        tags=[str(t).strip().lstrip("#") for t in (data.get("tags") or []) if str(t).strip()],
        cover_suggestion=str(data.get("cover_suggestion", "")).strip(),
        interaction_line=str(data.get("interaction_line", "")).strip(),
        rationale=str(data.get("rationale", "")).strip(),
    )


# ---------------------------------------------------------------- 接地（G2）
def surface_grounded(surface: str, fact_text: str) -> bool:
    """槽位表层在**这一条**事实里有依据吗。

    三条路（任一命中即过）：
    1. 子串：表层逐字出现在事实原文里（「286块」∈「要交286块」）；
    2. 数值等价：表层的数值集合 ⊆ 事实的数值集合（素材「两百八十六」表层「286」；
       素材「第1步」表层「第一步」）—— 两侧都用宽松口径：这里是**放行**判定，
       宽松只会少误拦；真正的拦截在 G3 的裸数字门（稿件侧严格）；
    3. 专名：表层是这条事实里抽得到的专名（大小写不敏感）。
    """
    s = (surface or "").strip()
    if not s:
        return False
    if s in (fact_text or ""):
        return True
    allowed = set(num_values(fact_text or "", strict=False))
    surf_vals = set(num_values(s, strict=False))
    if surf_vals and surf_vals <= allowed:
        return True
    known_names = proper.extract(fact_text or "")
    if known_names and s.lower() in {n.lower() for n in known_names}:
        return True
    return False


# ---------------------------------------------------------------- 单元装配（段/标题共用）
def _assemble_unit(kind: str, text: str, allowed_idx: set[int], facts: list[Fact],
                   extra: list[str], hard: list[str]) -> str:
    """处理一个文本单元（一段 / 一个标题）：槽位校验+回填，返回回填后的文本。

    `allowed_idx`：该单元槽位可引用的事实序号集合（段=其声明；标题=全部）。
    裸数字/年份检查（G3）在 `_gate_numbers` 里对所有单元统一做 —— 这里只管槽位。
    """
    text = (text or "").strip().translate(_FW_TRANSLATE)
    out: list[str] = []
    pos = 0
    for m in _SLOT_RE.finditer(text):
        n = int(m.group(1))
        surface = m.group(2).strip()
        where = f"{kind}"
        if not (1 <= n <= len(facts)):
            hard.append(f"{where} 槽位引用了不存在的事实序号 n={n}（共 {len(facts)} 条）")
        elif n not in allowed_idx:
            hard.append(f"{where} 槽位引用 n={n}，但它不在该段声明的事实 {sorted(allowed_idx)} 内")
        elif not surface:
            hard.append(f"{where} 槽位表层为空：{m.group(0)}")
        elif not surface_grounded(surface, facts[n - 1].text):
            hard.append(f"{where} 槽位 {m.group(0)} 的表层「{surface}」"
                        f"在第 {n} 条事实里找不到依据")
        else:
            out.append(text[pos:m.start()])
            out.append(surface)          # 表层原样回填 —— 表述归模型，依据归代码
            pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def _gate_numbers(kind: str, text: str, evidence: list[str], hard: list[str],
                  soft: list[str]) -> None:
    """G3：单元文本里的数字/年份必须落在证据集合里；专名疑似越界只报软违规。

    证据集合 = 该单元声明的事实 ∪ 已回填表层 ∪ 豁免证据（用户模板等）。
    口径与 `_rule_qa` / 评测 A1/A2 相同：证据侧宽松、稿件侧严格 ——
    「迈出这一步」不会误报，真幻觉 `199` 一定拦。

    ⚠️ 证据为空**不是**跳过检查 —— 一段没声明任何事实，里面的数字就全是
    无依据的（「没有可对照的证据」≠「数字合规」）。
    """
    ev = " ".join(x for x in evidence if x)
    allowed_vals = set(num_values(ev, strict=False)) if ev else set()
    for v, tok in num_values(text, strict=True).items():
        if v not in allowed_vals:
            hard.append(f"{kind} 出现声明依据之外的数字「{tok}」")
    known_years = set(_YEAR_RE.findall(ev))
    for y in _YEAR_RE.findall(text):
        if y not in known_years:
            hard.append(f"{kind} 出现声明依据之外的年份「{y}」")
    # 专名疑似越界：软违规（专名抽取有漏，硬拦会误伤 —— 同 _rule_qa 的宁可不查）
    known_names = proper.extract(ev, strict=False) if ev else set()
    if known_names:
        known_lower = {n.lower() for n in known_names}
        unknown = sorted(n for n in proper.extract(text, strict=True)
                         if n.lower() not in known_lower)
        if unknown:
            soft.append(f"{kind} 疑似使用了未声明依据的专名: {'、'.join(unknown)}（请确认）")


# ---------------------------------------------------------------- 装配
def assemble_skeleton(sk: DraftSkeleton, facts: list[Fact],
                      extra_evidence: Iterable[str] = ()) -> AssembleResult:
    """骨架 → 成稿。纯函数：不抛业务异常，所有问题进 hard/soft（G4 结构除外——
    没有可交付物时也返回一个带占位正文的 DraftPayload，让调用方按 hard 处置）。"""
    hard: list[str] = []
    soft: list[str] = []
    extra = [x for x in extra_evidence if x]

    # ---- G4：结构（可交付性必须由规则守住，同 FR-60 的原则）
    if not sk.paras:
        hard.append("骨架没有任何非空段落")
    if not sk.titles:
        hard.append("骨架没有标题")

    facts_used: set[int] = set()
    para_texts: list[str] = []
    for i, p in enumerate(sk.paras, 1):
        # 段声明里引用不存在的事实序号 → 硬违规（声明本身不合法）
        legal = {n for n in p.facts if 1 <= n <= len(facts)}
        bad = sorted(set(p.facts) - legal)
        if bad:
            hard.append(f"段{i} 声明了不存在的事实序号 {bad}（共 {len(facts)} 条）")
        facts_used |= legal

        filled = _assemble_unit(f"段{i}", p.text, legal, facts, extra, hard)
        if "{{" in filled:
            hard.append(f"段{i} 残留未回填的槽位写法（格式不对或未通过校验：检查 {{{{n:序号|表层}}}}）")
        evidence = [facts[n - 1].text for n in sorted(legal)] + extra
        _gate_numbers(f"段{i}", filled, evidence, hard, soft)
        para_texts.append(filled)

    # 标题的证据集合 = 全部事实 ∪ 豁免证据（标题是全篇层面，不绑定段 —— spec §3）
    titles: list[str] = []
    title_evidence = [f.text for f in facts] + extra
    for i, t in enumerate(sk.titles[:3], 1):
        filled = _assemble_unit(f"标题{i}", t, set(range(1, len(facts) + 1)), facts, extra, hard)
        if "{{" in filled:
            hard.append(f"标题{i} 残留未回填的槽位写法（格式不对或未通过校验）")
        _gate_numbers(f"标题{i}", filled, title_evidence, hard, soft)
        titles.append(filled)
    titles = [t for t in titles if t.strip()]
    if not titles:
        hard.append("标题处理后为空")

    body = "\n\n".join(para_texts)
    if not body.strip():
        hard.append("正文处理后为空")

    # 免检字段（spec §3）：封面建议/互动语不进门、不硬拦，但**槽位必须尽力回填** ——
    # 它们是用户可见文本，发布裸 {{n:…}} 占位符等于坏稿（真模型实测踩到：封面建议带槽位）。
    def _backfill_free(kind: str, text: str) -> str:
        if not text or "{{" not in text:
            return text
        def _sub(m: re.Match) -> str:
            n, surface = int(m.group(1)), m.group(2).strip()
            if not (1 <= n <= len(facts)) or not surface_grounded(surface, facts[n - 1].text):
                soft.append(f"{kind} 的槽位 {m.group(0)} 未通过接地校验，仍按表层回填（请人工确认）")
            return surface
        filled = _SLOT_RE.sub(_sub, text.translate(_FW_TRANSLATE))
        if "{{" in filled:
            soft.append(f"{kind} 残留未识别的槽位写法，请人工处理")
        return filled

    draft = DraftPayload(
        titles=titles or ["(装配失败)"],
        body=body,
        tags=sk.tags[:8],
        cover_suggestion=_backfill_free("封面建议", sk.cover_suggestion),
        interaction_line=_backfill_free("互动语", sk.interaction_line),
        rationale=sk.rationale,
        # facts_used 由**代码**计算（各段声明的并集）—— 模型自报会全量声明，
        # 让 QA 的「超出声明」核对形同虚设（factguard 实测，见 ADR-025）。
        facts_used=sorted(facts_used),
    )
    return AssembleResult(draft=draft, hard=hard, soft=soft)
