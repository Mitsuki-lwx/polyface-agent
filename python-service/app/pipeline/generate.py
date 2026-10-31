"""多平台成稿管线：brief(策略) → draft(成稿) → [clip_sheet(剪辑单)] → qa(质量门)。

- mock 模式：规则化生成，保证无 Key 可离线演示与测试
- 真实模式：调用平台化 LLM；qa 失败可带反馈重写一轮
- 事实约束：qa 会校验正文新增数字/断言是否在素材事实清单中有依据
- 用户模板(ADR-014)：UserTemplate 注入 brief/draft/clip，优先级高于 DNA 结构部分
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import dna as dna_lib
from .. import llm, trace
from ..config import get_settings
from ..pipeline.understand import run_understand
from ..pipeline import checkpoint as ck
from ..pipeline.runlog import RunLog
from ..schemas import AnalyzeRequest, StructuredMaterial
from ..schemas_gen import (
    Brief,
    ClipScene,
    ClipSheet,
    DraftPayload,
    GenerateRequest,
    PlatformDraft,
    QaIssue,
    QaReport,
    UserTemplate,
)
from .numberish import (  # noqa: F401 — 数字口径统一在 numberish，避免两处实现分叉
    numbers_in as _numbers_in, num_value as _num_value,
    num_values as _num_values, norm_num as _norm_num,
)
from .prompts import (
    BRIEF_SYSTEM,
    CLIP_SYSTEM,
    DRAFT_SYSTEM,
    QA_SYSTEM,
    build_brief_prompt,
    build_clip_prompt,
    build_draft_skeleton_prompt,
    build_qa_prompt,
)
from .skeleton import (
    AssembleResult,
    DraftSkeleton,
    SkeletonPara,
    assemble_skeleton,
    parse_skeleton,
)

logger = logging.getLogger(__name__)

# 分镜行开头的 emoji（）—— 属于剪辑单的文本处理，与数字口径无关
_EMOJI_LEAD = re.compile(
    r"^[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]+[\s·:：]*")

def _clip(text: str, n: int) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------- brief
def run_brief(code: str, dna: dict, mat: StructuredMaterial, tone_override: str | None,
              template: UserTemplate | None = None,
              creator_profile: dict | None = None,
              retrospect_hints: list[str] | None = None) -> Brief:
    profile_note = ""
    if creator_profile:
        bits = []
        if creator_profile.get("domain"):
            bits.append(f"领域={creator_profile['domain']}")
        if creator_profile.get("brand_voice"):
            bits.append(f"声音={creator_profile['brand_voice']}")
        if creator_profile.get("audience"):
            bits.append(f"受众={creator_profile['audience']}")
        if bits:
            profile_note = "；创作者画像:" + "/".join(bits)
    retro_note = ""
    if retrospect_hints:
        retro_note = "；历史经验:" + "｜".join(retrospect_hints[:3])

    if llm.is_mock():
        structure = " → ".join(dna.get("structure_template", ["开头抛结论", "展开细节", "结尾互动"]))
        tag_dir = [t for t in (dna.get("tags") or {}).get("mock", ["干货"])]
        return Brief(
            platform_code=code,
            angle=_clip(mat.core_message, 60),
            hooks=[_clip(mat.core_message, 40)],
            structure_plan=structure,
            tag_direction=tag_dir,
            rationale=f"mock策略: 参考平台[{dna.get('name')}]的结构模板与标签方向{profile_note}{retro_note}",
        )
    tpl = template.model_dump() if template else None
    data = llm.chat_json(
        build_brief_prompt(dna, mat, tone_override, tpl, creator_profile, retrospect_hints),
        system=BRIEF_SYSTEM, temperature=0.7,
        scene="brief", platform=dna.get("code"),
    )
    return Brief(
        platform_code=code,
        angle=str(data.get("angle", "")).strip() or mat.core_message,
        hooks=[str(x).strip() for x in (data.get("hooks") or []) if str(x).strip()],
        structure_plan=str(data.get("structure_plan", "")).strip(),
        tag_direction=[str(x).strip() for x in (data.get("tag_direction") or []) if str(x).strip()],
        rationale=str(data.get("rationale", "")).strip(),
    )


# ---------------------------------------------------------------- draft（骨架式, ADR-025）
def _clip_numsafe(text: str, n: int) -> str:
    """同 `_clip`，但切点落在数字中间时退到数字串前。

    半个数字（`286块` 被切成 `28`）既读不懂，也过不了骨架的裸数字门 ——
    mock 的标题/开头用这个版本截断（mock 与真实模式共用同一道门，见下）。
    """
    t = text.strip()
    if len(t) <= n:
        return t
    cut = t[: n - 1]
    if t[n - 1].isdigit() and re.search(r"\d$", cut):
        cut = re.sub(r"\d+(?:\.\d*)?$", "", cut).rstrip()
    return cut + "…"


def _extra_evidence(template: UserTemplate | None,
                    creator_profile: dict | None) -> list[str]:
    """豁免机械门的**用户自己写**的内容：模板开头/结尾、画像避雷（ADR-025）。

    这些不是模型编造 —— 它们的数字不参与段声明核对。
    （`core_message` 是 understand 的上游产物，只在 mock 豁免：mock 里它就是素材原句拼接，
    真实模式**不豁免** —— 成稿的事实值必须落到事实清单这个闭集里。）
    """
    out = []
    if template and template.opening:
        out.append(template.opening)
    if template and template.closing:
        out.append(template.closing)
    if creator_profile and creator_profile.get("avoid"):
        out.append(str(creator_profile["avoid"]))
    return out


def _mock_skeleton(code: str, dna: dict, mat: StructuredMaterial, brief: Brief,
                   template: UserTemplate | None = None,
                   creator_profile: dict | None = None,
                   retrospect_hints: list[str] | None = None
                   ) -> tuple[DraftSkeleton, list[str]]:
    """mock 也产**骨架**，与真实模式走同一个装配器（逻辑只有一份 ——
    装配器的 bug 在 mock 的 e2e 里就能暴露，不用真 Key）。

    段内多行用 `\\n` 嵌在 text 里、段落间由装配器 `\\n\\n` 拼接 ——
    与改版前 `_mock_draft` 的行结构同构（见 test_generate 对 body 的逐字节断言）。
    """
    facts = mat.facts[:5]
    hook = (template.opening if template and template.opening
            else f"先说结论：{_clip_numsafe(mat.core_message, 80)}")
    markers = {"data": "📊", "story": "📖", "opinion": "💡"}
    fact_lines = "\n".join(f"{markers.get(f.type, '·')} {f.text}" for f in facts)
    paras = [SkeletonPara(facts=[], role="hook", text=hook)]
    if fact_lines:
        paras.append(SkeletonPara(facts=list(range(1, len(facts) + 1)), role="body",
                                  text=fact_lines))
    if creator_profile and creator_profile.get("avoid"):
        paras.append(SkeletonPara(facts=[], role="body",
                                  text=f"⚠️ {creator_profile['avoid']}"))
    closing = (template.closing if template and template.closing
               else "觉得有用就收藏，下次需要直接翻出来看～")
    paras.append(SkeletonPara(facts=[], role="close", text=closing))

    tags = (brief.tag_direction or ["干货"])[:6]
    titles = [
        _clip_numsafe(mat.core_message, 18),
        f"{_clip_numsafe(mat.core_message, 12)}｜真实经验",
    ]
    extra_rationale = ""
    if creator_profile and creator_profile.get("domain"):
        extra_rationale = f"（已对齐创作者领域:{creator_profile['domain']}）"
    if retrospect_hints:
        extra_rationale += f"（已应用{len(retrospect_hints)}条历史经验）"
    sk = DraftSkeleton(
        titles=titles,
        paras=paras,
        tags=tags,
        cover_suggestion=_clip(mat.core_message, 16),
        interaction_line="你们平时会复盘吗？评论区聊聊～",
        rationale=f"mock成稿: {dna.get('name')}风格=结论前置+清单式干货+收藏引导"
        + ("（已应用我的模板）" if template else "")
        + extra_rationale,
    )
    # mock 豁免 core_message（understand 的上游产物）；模板/避雷两类豁免同真实模式
    return sk, [mat.core_message] + _extra_evidence(template, creator_profile)


# 年份（整词匹配：`月收入在8000—12000元` 里的 `12000` 不能抠出 `2000`）
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


class SkeletonGateError(RuntimeError):
    """骨架装配机械门两轮（初次+修复）均未通过 —— 平台显式失败（FR-70 语义）。

    不静默带病产出：异常消息带全部违规明细，由 `generate()` 的单平台失败路径
    记录进 failures，其余平台成果照常交付。
    """

    def __init__(self, violations: list[str]):
        self.violations = list(violations)
        super().__init__("skeleton_gate: " + "；".join(violations))


def run_draft(
    code: str,
    dna: dict,
    mat: StructuredMaterial,
    brief: Brief,
    feedback: str | None = None,
    template: UserTemplate | None = None,
    creator_profile: dict | None = None,
    retrospect_hints: list[str] | None = None,
) -> tuple[DraftPayload, list[str]]:
    """成稿 = 骨架 → 装配 → 机械门（ADR-025）。返回 `(成稿, 软违规)`。

    - mock：`_mock_skeleton` 造骨架，走**同一个装配器**（逻辑只有一份）；
      硬违规只告警不拦截（mock 契约 = 无 Key 也能出稿；有测试钉住它恒为零）。
    - 真实：硬违规 → 带违规清单修复一轮 → 仍硬违规 → 抛 `SkeletonGateError`。
    - `facts_used` 由装配器从各段声明计算 —— 模型自报的字段不再采信
      （自报会全量声明，让 QA 的「超出声明」核对形同虚设，factguard 实测）。
    """
    if llm.is_mock():
        sk, extra = _mock_skeleton(code, dna, mat, brief, template,
                                   creator_profile, retrospect_hints)
        res = assemble_skeleton(sk, mat.facts, extra_evidence=extra)
        if res.hard:      # 不应发生（test_skeleton 用语料级样本钉住恒零）
            logger.warning("mock 骨架装配硬违规（请修 mock 或装配器）：%s", res.hard)
        return res.draft, res.soft + [f"[mock门]{v}" for v in res.hard]

    extra = _extra_evidence(template, creator_profile)
    tpl = template.model_dump() if template else None

    def _call_once(fb: str | None) -> AssembleResult:
        data = llm.chat_json(
            build_draft_skeleton_prompt(dna, mat, brief, fb, tpl,
                                        creator_profile, retrospect_hints),
            system=DRAFT_SYSTEM, temperature=0.8,
            scene="draft", platform=dna.get("code"),
        )
        return assemble_skeleton(parse_skeleton(data), mat.facts, extra_evidence=extra)

    res = _call_once(None)
    if res.hard:
        repair = ("机械校验未通过，逐条修复：\n"
                  + "\n".join(f"- {v}" for v in res.hard)
                  + "\n重新输出**完整**骨架 JSON（含全部字段）。")
        logger.info("骨架机械门未通过（%d 项），修复一轮：%s", len(res.hard), res.hard)
        res = _call_once(repair)
        if res.hard:
            raise SkeletonGateError(res.hard)
    return res.draft, res.soft


# ---------------------------------------------------------------- clip_sheet（FR-50, A阶段）
def _mock_clip_sheet(dna: dict, draft: DraftPayload) -> ClipSheet:
    """按正文行拆分成简单分镜（mock）。"""
    raw_lines = [ln.strip() for ln in draft.body.splitlines() if ln.strip()]
    scenes: list[ClipScene] = []
    visuals = ["实拍口播(近景)", "插对应图文/数据画面", "实操/清单特写", "口播+转场", "结尾画面"]
    for i, line in enumerate(raw_lines, start=1):
        script = _EMOJI_LEAD.sub("", line).strip()
        if not script:
            continue
        secs = max(2, math.ceil(len(script) / 4))
        scenes.append(ClipScene(
            seq=i,
            duration_hint=f"约{secs}s",
            script=script,
            visual=visuals[i % len(visuals)],
            subtitle=script[:40] if len(script) > 40 else script,
            sound="人声口播为主" + ("，BGM 垫底" if i == 1 else ""),
        ))
    bgm = "轻快 BGM" if dna.get("code") == "douyin" else "轻量 BGM，突出人声"
    return ClipSheet(intro_note="按口播逐句剪，保留换气停顿；前3秒务必是钩子。",
                     scenes=scenes, bgm_hint=bgm)


def run_clip_sheet(dna: dict, mat: StructuredMaterial, draft: DraftPayload,
                   template: UserTemplate | None = None) -> ClipSheet | None:
    """视频平台(video_native)成稿 → 剪辑单。非视频平台返回 None。"""
    if not dna.get("video_native"):
        return None
    if llm.is_mock():
        return _mock_clip_sheet(dna, draft)
    tpl = template.model_dump() if template else None
    data = llm.chat_json(build_clip_prompt(dna, draft, mat, tpl),
                         system=CLIP_SYSTEM, temperature=0.5,
                         scene="clip", platform=dna.get("code"))
    scenes = []
    for s in data.get("scenes") or []:
        if not isinstance(s, dict) or not str(s.get("script", "")).strip():
            continue
        scenes.append(ClipScene(
            seq=int(s.get("seq", len(scenes) + 1)),
            duration_hint=str(s.get("duration_hint", "")).strip(),
            script=str(s.get("script", "")).strip(),
            visual=str(s.get("visual", "")).strip(),
            subtitle=str(s.get("subtitle", "")).strip(),
            sound=str(s.get("sound", "")).strip(),
        ))
    return ClipSheet(
        intro_note=str(data.get("intro_note", "")).strip(),
        scenes=scenes,
        bgm_hint=str(data.get("bgm_hint", "")).strip(),
    )


# ---------------------------------------------------------------- qa
def _rule_qa(dna: dict, draft: DraftPayload, mat: StructuredMaterial,
             source_text: str = "") -> tuple[list[str], list[str]]:
    """规则与结构校验。`source_text` 是素材原文 —— **证据集合不能比素材还窄**。"""
    issues: list[str] = []
    warnings: list[str] = []
    limits = dict(dna.get("limits") or {})
    body_max = int(limits.get("body_chars_max", 1000))
    tag_max = int((dna.get("tags") or {}).get("count_max", 8))

    body_len = len(draft.body or "")
    # 结构校验（FR-60 加固）：最基本的可交付性必须由**规则**守住，不能依赖模型自评
    if not (draft.body or "").strip():
        issues.append("正文为空：不可交付")
    if not any((t or "").strip() for t in (draft.titles or [])):
        issues.append("标题为空：不可交付")
    if body_len > body_max:
        issues.append(f"正文超长: {body_len}字 > 上限{body_max}字")
    if not draft.titles:
        issues.append("缺少标题")
    # ⚠️ 这里原先写死 `>24 字` —— 而小红书的真实上限是 20。
    # 于是 21~24 字的标题**连产品自己都不报**，一路发出去被截断。
    # 改成读平台 DNA（读不到才退回 24）。
    tmax = dna_lib.title_chars_max(dna) or 24
    for t in draft.titles:
        if len(t) > tmax:
            warnings.append(f"标题偏长(>{tmax}字): {t}")
    if not draft.tags:
        issues.append("缺少话题标签")
    if len(draft.tags) > tag_max:
        issues.append(f"话题标签过多: {len(draft.tags)} > {tag_max}")
    for t in draft.tags:
        if "#" in t or not t.strip():
            issues.append(f"标签格式非法: {t!r}")

    # 事实约束：正文中出现的数字若在素材事实清单无依据 → warning(不硬拦，交由人工/LLM)
    # 合并为一条：原先每个数字刷一条，4 条噪声会把同批真告警挤出视野（docs/52 D4）。
    #
    # ⚠️ 比的是**数值**，且两边不对称（2026-10-07 修）：
    #   - 素材侧宽松：中文数字都算依据 —— 否则素材写「九十九」、稿件写「99」会误报
    #     （实测就是这样给用户报了「无依据的数字: 400、99」）
    #   - 稿件侧严格：只查像数字的中文数字（带单位或 ≥2 位）——
    #     否则「迈出这一步」里的「一」会被当成一个数字去比对，又是一类误报
    # 展示仍用**原文**（用户写的是 3w，不要给他改写成 3万）。
    # ⚠️ 证据集合 = **素材原文 ∪ 事实清单**。
    # 早先只比 `mat.facts`（3~10 条）—— 证据比被检对象还少，凡素材里有、
    # 但没被抽进事实清单的数字都会被误报（`docs/spec_factguard.md` §1）。
    cfg = get_settings()
    if not cfg.factguard_enabled:
        return issues, warnings          # 总开关关掉：只留上面的结构性检查

    # ⚠️ 证据集合 = **素材原文 ∪ 事实清单**。
    # 早先只比 `mat.facts`（3~10 条）—— 证据比被检对象还少，凡素材里有、
    # 但没被抽进事实清单的数字都会被误报（`docs/spec_factguard.md` §1）。
    evidence = " ".join([source_text or ""] + [f.text for f in mat.facts])

    if cfg.factguard_check_numbers:
        allowed = set(_num_values(evidence, strict=False))
        body_vals = _num_values(draft.body, strict=True)
        unsupported = [tok for v, tok in sorted(body_vals.items()) if v not in allowed]
        if unsupported:
            warnings.append("正文含素材中无依据的数字: "
                            + "、".join(unsupported) + "（请确认或删除）")

    if cfg.factguard_check_years:
        # 与评测集的 A2 对齐（原先只有评测集查、产品侧不查）
        known_years = {y for y in _YEAR_RE.findall(evidence)}
        foreign = sorted({y for y in _YEAR_RE.findall(draft.body) if y not in known_years})
        if foreign:
            warnings.append("正文含素材中无依据的年份: "
                            + "、".join(foreign) + "（请确认或删除）")

    if cfg.factguard_check_names:
        # 专名（T3）：正文里的专名必须能在素材里找到。
        # 白名单抽不到就**静默跳过** —— 宁可不查，不可误报（见 app/pipeline/proper.py）。
        from . import proper as _proper
        extra = {n.strip() for n in (cfg.factguard_extra_names or "").split(",") if n.strip()}
        known_names = _proper.extract(evidence, extra)                # 素材侧：宽松
        if known_names:
            # 大小写不敏感：素材写 `database`、稿件写 `Database` 是同一个词
            known_lower = {n.lower() for n in known_names}
            unknown_names = sorted(n for n in _proper.extract(draft.body, strict=True)
                                   if n.lower() not in known_lower)   # 稿件侧：严格
            if unknown_names:
                warnings.append("正文含素材中未出现的专名: "
                                + "、".join(unknown_names) + "（请确认或删除）")

    # 事实依据声明（T1）：没声明 = 违规；声明了空 = 合法（本次没用到事实）
    if draft.facts_used is None:
        warnings.append("成稿未声明依据了哪些事实（facts_used 缺失）—— 无法核对其依据范围")
    elif mat.facts:
        bad_idx = sorted({i for i in draft.facts_used if not (1 <= i <= len(mat.facts))})
        if bad_idx:
            warnings.append(f"成稿声明的事实序号越界: {bad_idx}（共 {len(mat.facts)} 条）")
    return issues, warnings


def _strict_bool(value) -> tuple[bool, str | None]:
    """严格解析 LLM 返回的 passed 字段。

    修复 `bool("false") == True` 这类类型不合约误判。**只接受真正的布尔**；
    其他类型一律按「不通过」处理并给出可读原因（fail-closed 原则）。
    """
    if isinstance(value, bool):
        return value, None
    return False, (
        f"模型返回的 passed 字段类型不合约（{type(value).__name__}={value!r}），已按未通过处理"
    )


def _llm_qa(
    dna: dict, draft: DraftPayload, mat: StructuredMaterial,
    rule_issues: list[str], rule_warnings: list[str],
    source_text: str = "",
) -> QaReport:
    """真实模式：规则 + LLM 自评**合并**判定。

    fail-closed 原则：
    - 任一来源（规则 / 结构 / LLM）存在阻断问题 → 不通过
    - LLM 的 passed=true **不能**覆盖任何阻断问题
    - 规则 warnings **必须保留**（此前被 LLM warnings 覆盖，导致「正文含无依据数字」告警丢失）
    """
    cfg = get_settings()
    verifier = (cfg.llm_qa_model or "").strip() or None
    two_stage = (cfg.factguard_two_stage and verifier
                 and verifier != (cfg.llm_model or "").strip())

    if two_stage:
        # 阶段一：用**快模型**（生成模型，实测约 50s）先判
        rep = _qa_once(dna, draft, mat, rule_issues, rule_warnings, source_text,
                       model=None, scene="qa")
        # "不确定"的判据（可观测，不靠模型自报置信度）：
        #   **判决与它自己报的证据不一致** —— 判"过"却报出有没依据的断言。
        if rep.passed and rep.claims_unsupported > 0:
            logger.info("QA 不确定（passed 但有 %d 条无依据）→ 升级到 verifier %s",
                        rep.claims_unsupported, verifier)
            return _qa_once(dna, draft, mat, rule_issues, rule_warnings, source_text,
                            model=verifier, scene="qa_verify")
        return rep

    # 单阶段：直接用 verifier（跨模型校验，实测 A7 支持率 +4 点但慢近 3 倍）
    return _qa_once(dna, draft, mat, rule_issues, rule_warnings, source_text,
                    model=verifier, scene="qa")


def _qa_once(dna: dict, draft: DraftPayload, mat: StructuredMaterial,
             rule_issues: list[str], rule_warnings: list[str],
             source_text: str, *, model: str | None, scene: str) -> QaReport:
    """跑一次 LLM 质检并把结果与规则结论合并（fail-closed）。"""
    data = llm.chat_json(
        build_qa_prompt(dna, draft, mat, source_text), system=QA_SYSTEM, temperature=0.2,
        model=model, scene=scene, platform=dna.get("code"),
    )
    llm_issues = [str(x).strip() for x in (data.get("issues") or []) if str(x).strip()]
    llm_warns = [str(x).strip() for x in (data.get("warnings") or []) if str(x).strip()]
    llm_passed, type_error = _strict_bool(data.get("passed"))

    blocking = list(rule_issues) + llm_issues[:3]
    if type_error:
        blocking.append(type_error)

    return QaReport(
        passed=(not blocking) and llm_passed,
        issues=blocking,
        warnings=list(rule_warnings) + llm_warns,
        issue_items=_parse_issue_items(data.get("issue_items")),
        claims_checked=_nonneg_int(data.get("claims_checked")),
        claims_unsupported=_nonneg_int(data.get("claims_unsupported")),
    )


def _nonneg_int(raw) -> int:
    """宽容解析非负整数。

    模型有时回字符串（"12"）、有时回浮点（3.9）—— 都截断成整数。
    解析不出来返回 0（**不猜**）。
    """
    try:
        return max(0, int(float(str(raw).strip())))
    except (TypeError, ValueError):
        return 0


# 每类的**定向修复指令**（T6）。分类是封闭的，所以指令也是封闭的 ——
# 不给开放式建议，否则没法验收。
_FIX_HINT = {
    "数字": "把该处数字改成素材原文里的写法；素材里没有这个数字，就删掉这一句。",
    "年份": "把该处年份改成素材原文里的；素材里没有这个年份，就删掉。",
    "专名": "把该处的品牌/工具/机构名改成素材里的；素材里没有就删掉。",
    "新增断言": "删掉该句，或改成素材原话的复述。**不要**给它补素材没有的动机、程度、"
                "时间跨度或因果 —— 那是本次要修的问题本身。",
    "其它": "按说明修改。",
}


def _parse_issue_items(raw) -> list[QaIssue]:
    """把模型回的 issue_items 规整成 `QaIssue`。形状不对就丢弃（不猜）。"""
    out: list[QaIssue] = []
    for x in (raw or []):
        if not isinstance(x, dict):
            continue
        cat = str(x.get("category") or "").strip()
        if cat not in ("数字", "年份", "专名", "新增断言", "其它"):
            cat = "其它"
        detail = str(x.get("detail") or "").strip()
        quote = str(x.get("quote") or "").strip()
        if not detail and not quote:
            continue
        out.append(QaIssue(category=cat, quote=quote[:120], detail=detail[:300]))
    return out[:8]


def _build_targeted_feedback(qa: QaReport) -> str:
    """按类给定向指令；拿不到结构化问题时退回旧的笼统 feedback（不假装有分类）。"""
    items = list(qa.issue_items or [])
    if not items:
        return "；".join(qa.issues)
    lines = []
    for it in items:
        hint = _FIX_HINT.get(it.category, _FIX_HINT["其它"])
        where = f"「{it.quote}」" if it.quote else ""
        lines.append(f"- [{it.category}] {where}{it.detail} → {hint}")
    return "只改下面这些，别的地方不要动：\n" + "\n".join(lines)


def run_qa(dna: dict, draft: DraftPayload, mat: StructuredMaterial,
           source_text: str = "") -> QaReport:
    """质检门：规则与结构校验恒生效；真实模式再叠加 LLM 自评。

    **严格程度**（`FACTGUARD_STRICTNESS`，见 `docs/spec_factguard.md`）：
      · `loose`    —— 只报不拦：问题进 warnings，`passed` 仍为 true
      · `standard` —— 不过则重写（默认，重写由 `_generate_one` 做）
      · `strict`   —— 不过即判失败，**不进入重写**
    """
    issues, warnings = _rule_qa(dna, draft, mat, source_text)
    if llm.is_mock():
        rep = QaReport(passed=not issues, issues=issues, warnings=warnings)
    else:
        rep = _llm_qa(dna, draft, mat, issues, warnings, source_text)

    if (get_settings().factguard_strictness or "").strip().lower() == "loose":
        # 只报不拦：把阻断项降级成提醒，`passed` 强制为真
        return rep.model_copy(update={
            "passed": True,
            "warnings": list(rep.warnings) + [f"[仅提醒]{x}" for x in rep.issues],
            "issues": [],
        })
    return rep


# ---------------------------------------------------------------- orchestrator
def _open_log(events_path: str | None):
    """按需打开事件出口。

    - 没给路径 → 返回 None（**不创建任何文件**，向后兼容既有调用）
    - 给了路径 → 用 `level="quiet"`：只落事件、**不往服务端 stdout 打**（那是服务日志，不是给人看的进度）

    阶段事件底座在 `runlog.py`（带 version 的 JSONL）；这里只是把它接进生成流程。
    """
    if not events_path:
        return None
    try:
        return RunLog(sink=Path(events_path), level="quiet")
    except Exception as e:                      # noqa: BLE001 —— 观测绝不影响业务
        logger.warning("事件出口初始化失败（生成继续）：%s", e)
        return None


class _NoopReporter:
    """没有事件出口时的空实现 —— 让调用点不必到处写 if。"""

    summary = ""
    fields: dict = {}

    def progress(self, pct: float) -> None:      # noqa: D102
        pass

    def done(self, summary: str = "", **fields) -> None:  # noqa: D102
        pass


@contextlib.contextmanager
def _stage(log, name: str):
    """有出口就发阶段事件，没有就什么都不做。"""
    if log is None:
        yield _NoopReporter()
    else:
        with log.stage(name) as r:
            yield r


def _generate_one(code: str, mat: StructuredMaterial, tone_override: str | None,
                  template: UserTemplate | None = None,
                  creator_profile: dict | None = None,
                  retrospect_hints: list[str] | None = None,
                  log=None, source_text: str = "") -> PlatformDraft:
    dna = dna_lib.load_dna(code)
    with _stage(log, f"平台:{code}:策略") as r:
        brief = run_brief(code, dna, mat, tone_override, template, creator_profile, retrospect_hints)
        r.done("策略完成")
    gate_soft: list[str] = []
    with _stage(log, f"平台:{code}:成稿") as r:
        draft, gate_soft = run_draft(code, dna, mat, brief, template=template,
                                     creator_profile=creator_profile,
                                     retrospect_hints=retrospect_hints)
        r.done("成稿完成" if not gate_soft else f"成稿完成（{len(gate_soft)} 项软提醒）")
    with _stage(log, f"平台:{code}:质检") as r:
        qa = run_qa(dna, draft, mat, source_text)
        # 骨架机械门的软违规（如疑似未声明专名）并进 QA warnings —— 不阻断，但要可见
        if gate_soft:
            qa = qa.model_copy(update={"warnings": list(qa.warnings) + list(gate_soft)})
        r.done("通过" if qa.passed else f"未通过（{len(qa.issues)} 项）")

    # 真实模式下 QA 未通过 → **定向**重写（T6），轮数可配（T7）
    cfg = get_settings()
    rounds = max(0, int(cfg.factguard_rewrite_rounds)) if cfg.factguard_enabled else 1
    strictness = (cfg.factguard_strictness or "standard").strip().lower()
    if not qa.passed and not llm.is_mock() and strictness != "strict" and rounds > 0:
        for attempt in range(1, rounds + 1):
            feedback = _build_targeted_feedback(qa)
            with _stage(log, f"平台:{code}:改写{attempt}" if rounds > 1 else f"平台:{code}:改写") as r:
                draft, gate_soft = run_draft(code, dna, mat, brief, feedback=feedback, template=template,
                                             creator_profile=creator_profile, retrospect_hints=retrospect_hints)
                qa = run_qa(dna, draft, mat, source_text)
                if gate_soft:
                    qa = qa.model_copy(update={"warnings": list(qa.warnings) + list(gate_soft)})
                r.done("改写后通过" if qa.passed else f"改写{attempt}后仍未通过")
            if qa.passed:
                break

    # 视频平台：成稿后附剪辑单（A 阶段）
    with _stage(log, f"平台:{code}:剪辑单"):
        clip = run_clip_sheet(dna, mat, draft, template)
        if clip is not None:
            draft = draft.model_copy(update={"clip_sheet": clip})

    return PlatformDraft(
        platform_code=code,
        platform_name=dna.get("name", code),
        brief=brief,
        draft=draft,
        qa=qa,
    )


def generate(req: GenerateRequest) -> tuple[dict, list[PlatformDraft], bool]:
    """全链路：素材理解 → 每平台 brief/draft/clip/qa。

    若请求带 `confirmed_facts`（用户已确认的事实），则**跳过理解阶段**直接使用，
    省一次 LLM 调用，并保证「解析时看到的事实」== 「生成时用的事实」。
    """
    used_mock = llm.is_mock()
    log = _open_log(getattr(req, "events_path", None))
    if req.confirmed_facts is not None:
        structured = req.confirmed_facts
        logger.info("using user-confirmed facts; skipping understand step (facts=%d)",
                    len(structured.facts or []))
    else:
        a_req = AnalyzeRequest(raw_text=req.raw_text, source_kind=req.source_kind, title=req.title)
        with _stage(log, "理解") as r:
            structured, _ = run_understand(a_req)
            r.done(f"{len(structured.facts or [])} 条事实")

    # 校验平台合法性（统一提前报错）
    for code in req.platforms:
        try:
            dna_lib.load_dna(code)
        except ValueError as e:
            raise ValueError(f"不支持的平台 [{code}]；可用: {[p['code'] for p in dna_lib.list_platforms()]}") from e

    results: list[PlatformDraft] = []
    failures: list[dict] = []
    cfg = get_settings()

    # ---- 断点续跑（T2）----
    # **落盘粒度 = 续跑粒度**（见 docs/spec_resume.md §5）：一个平台一完成就落盘，
    # 续跑时只跑缺的那些。没有 events_path（CLI 直跑）时不启用 —— 保持两种用法行为一致。
    workdir = ck.workdir_of(getattr(req, "events_path", None))
    done_by_code: dict[str, PlatformDraft] = {}
    todo: list[str] = []
    for code in req.platforms:
        payload = ck.read_unit(workdir, "platform", code) if workdir else None
        if payload is not None:
            try:
                done_by_code[code] = PlatformDraft.model_validate(payload)
                continue
            except Exception:  # noqa: BLE001 —— 落盘读不出就当没完成，重跑
                logger.warning("已完成平台 %s 的落盘读不出，重跑该平台", code)
        todo.append(code)
    if workdir and len(todo) < len(req.platforms):
        skipped = [c for c in req.platforms if c not in todo]
        logger.info("断点续跑：跳过已完成平台 %s，只跑 %s", skipped, todo)
        with _stage(log, "续跑") as r:
            r.done(f"跳过 {len(skipped)} 个已完成平台 {skipped}；只跑 {len(todo)} 个 {todo}")

    def _remember(code: str, draft: PlatformDraft) -> None:
        """一个平台一完成就落盘 —— 不等整批返回，否则中断即全丢。"""
        done_by_code[code] = draft
        if workdir:
            try:
                ck.write_unit(workdir, "platform", code,
                              json.loads(draft.model_dump_json()))
            except Exception:  # noqa: BLE001 —— 落盘失败不该毁掉本次生成
                logger.exception("平台 %s 落盘失败（本次结果仍会返回）", code)

    def _call(code: str):
        """**串行**调用（同一线程，上下文天然继承，不需要绑定）。"""
        return _generate_one(code, structured, req.tone_override, req.template,
                             req.creator_profile, req.retrospect_hints, log, req.raw_text)

    def _bound(code: str):
        """**并行**用：在调用方线程把上下文绑好，返回可在子线程执行的可调用。

        ⚠️ `ThreadPoolExecutor` 不继承 `contextvars`。而且**绑定必须发生在调用方线程** ——
        早先写成 `submit(trace.run_in_context, fn, …)` 是在**子线程**里 `copy_context()`，
        拷到的是子线程的空上下文，trace_id 照样丢 ✗（实测 2026-10-30）。
        """
        return trace.bind_in_context(_generate_one, code, structured,
                                     req.tone_override, req.template,
                                     req.creator_profile, req.retrospect_hints, log,
                                     req.raw_text)

    def _record_failure(code: str, exc: Exception) -> None:
        """单平台失败不致命：记录原因后继续，已完成平台的产物必须交付。"""
        logger.exception("platform %s failed", code)
        failures.append({
            "platform": code,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        })

    if cfg.llm_parallel and len(todo) > 1:
        # 并行：快，但需上游额度宽裕（默认关闭，理由见 config）
        with ThreadPoolExecutor(max_workers=min(4, len(todo))) as ex:
            # `bind_in_context` **返回可调用** —— 必须在**调用方线程**里先绑好再交出去
            futures = {ex.submit(_bound(code)): code for code in todo}
            for f, code in futures.items():
                try:
                    _remember(code, f.result())
                except Exception as e:  # noqa: BLE001 — 部分失败可容忍
                    _record_failure(code, e)
    else:
        # 串行 + 最小间隔：实测限流策略下的可靠路径
        interval = max(0, int(cfg.llm_min_interval_ms)) / 1000.0
        for i, code in enumerate(todo):
            if i > 0 and interval > 0:
                time.sleep(interval)
            try:
                _remember(code, _call(code))
            except Exception as e:  # noqa: BLE001 — 部分失败可容忍
                _record_failure(code, e)

    # 按请求顺序返回**完整**集合（已完成 + 本次新跑）——调用方分不出续跑与一次跑完
    results = [done_by_code[c] for c in req.platforms if c in done_by_code]
    structured_dict = json.loads(structured.model_dump_json())
    return structured_dict, results, used_mock, failures
