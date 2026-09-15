"""多平台成稿管线：brief(策略) → draft(成稿) → [clip_sheet(剪辑单)] → qa(质量门)。

- mock 模式：规则化生成，保证无 Key 可离线演示与测试
- 真实模式：调用平台化 LLM；qa 失败可带反馈重写一轮
- 事实约束：qa 会校验正文新增数字/断言是否在素材事实清单中有依据
- 用户模板(ADR-014)：UserTemplate 注入 brief/draft/clip，优先级高于 DNA 结构部分
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor

from .. import dna as dna_lib
from .. import llm, trace
from ..config import get_settings
from ..pipeline.understand import run_understand
from ..schemas import AnalyzeRequest, StructuredMaterial
from ..schemas_gen import (
    Brief,
    ClipScene,
    ClipSheet,
    DraftPayload,
    GenerateRequest,
    PlatformDraft,
    QaReport,
    UserTemplate,
)
from .prompts import (
    BRIEF_SYSTEM,
    CLIP_SYSTEM,
    DRAFT_SYSTEM,
    QA_SYSTEM,
    build_brief_prompt,
    build_clip_prompt,
    build_draft_prompt,
    build_qa_prompt,
)

logger = logging.getLogger(__name__)

_NUM_RE = re.compile(r"\d+(?:\.\d+)?(?:[%％万kK亿])?\b|\d{2,}")
_EMOJI_LEAD = re.compile(r"^[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]+[\s·:：]*")


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


# ---------------------------------------------------------------- draft
def _mock_draft(code: str, dna: dict, mat: StructuredMaterial, brief: Brief,
                template: UserTemplate | None = None,
                creator_profile: dict | None = None,
                retrospect_hints: list[str] | None = None) -> DraftPayload:
    facts = mat.facts[:5]
    body_lines = []
    # 用户模板优先级：opening 覆盖默认开头（ADR-014）
    if template and template.opening:
        body_lines.append(template.opening)
    else:
        body_lines.append(f"先说结论：{_clip(mat.core_message, 80)}")
    body_lines.append("")
    for f in facts:
        marker = {"data": "📊", "story": "📖", "opinion": "💡"}.get(f.type, "·")
        body_lines.append(f"{marker} {f.text}")
    # FR-32 创作者画像在 mock 中以"避雷"段落呈现
    if creator_profile and creator_profile.get("avoid"):
        body_lines.append("")
        body_lines.append(f"⚠️ {creator_profile['avoid']}")
    if template and template.closing:
        body_lines.append("")
        body_lines.append(template.closing)
    else:
        body_lines += ["", "觉得有用就收藏，下次需要直接翻出来看～"]
    tags = (brief.tag_direction or ["干货"])[:6]
    titles = [
        _clip(mat.core_message, 18),
        f"{_clip(mat.core_message, 12)}｜真实经验",
    ]
    extra_rationale = ""
    if creator_profile and creator_profile.get("domain"):
        extra_rationale = f"（已对齐创作者领域:{creator_profile['domain']}）"
    if retrospect_hints:
        extra_rationale += f"（已应用{len(retrospect_hints)}条历史经验）"
    return DraftPayload(
        titles=titles,
        body="\n".join(body_lines),
        tags=tags,
        cover_suggestion=_clip(mat.core_message, 16),
        interaction_line="你们平时会复盘吗？评论区聊聊～",
        rationale=f"mock成稿: {dna.get('name')}风格=结论前置+清单式干货+收藏引导"
        + ("（已应用我的模板）" if template else "")
        + extra_rationale,
    )


def run_draft(
    code: str,
    dna: dict,
    mat: StructuredMaterial,
    brief: Brief,
    feedback: str | None = None,
    template: UserTemplate | None = None,
    creator_profile: dict | None = None,
    retrospect_hints: list[str] | None = None,
) -> DraftPayload:
    if llm.is_mock():
        return _mock_draft(code, dna, mat, brief, template, creator_profile, retrospect_hints)
    tpl = template.model_dump() if template else None
    data = llm.chat_json(
        build_draft_prompt(dna, mat, brief, feedback, tpl, creator_profile, retrospect_hints),
        system=DRAFT_SYSTEM, temperature=0.8,
        scene="draft", platform=dna.get("code"),
    )
    return DraftPayload(
        titles=[str(t).strip() for t in (data.get("titles") or []) if str(t).strip()][:3],
        body=str(data.get("body", "")).strip(),
        tags=[str(t).strip().lstrip("#") for t in (data.get("tags") or []) if str(t).strip()][:8],
        cover_suggestion=str(data.get("cover_suggestion", "")).strip(),
        interaction_line=str(data.get("interaction_line", "")).strip(),
        rationale=str(data.get("rationale", "")).strip(),
    )


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
def _rule_qa(dna: dict, draft: DraftPayload, mat: StructuredMaterial) -> tuple[list[str], list[str]]:
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
    for t in draft.titles:
        if len(t) > 24:
            warnings.append(f"标题偏长(>{24}字): {t}")
    if not draft.tags:
        issues.append("缺少话题标签")
    if len(draft.tags) > tag_max:
        issues.append(f"话题标签过多: {len(draft.tags)} > {tag_max}")
    for t in draft.tags:
        if "#" in t or not t.strip():
            issues.append(f"标签格式非法: {t!r}")

    # 事实约束：正文中出现的数字若在素材事实清单无依据 → warning(不硬拦，交由人工/LLM)
    fact_text = " ".join(f.text for f in mat.facts)
    nums_in_body = set(_NUM_RE.findall(draft.body))
    nums_in_facts = set(_NUM_RE.findall(fact_text))
    for n in sorted(nums_in_body):
        if n not in nums_in_facts:
            warnings.append(f"正文含素材中无依据的数字: {n}（请确认或删除）")
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
) -> QaReport:
    """真实模式：规则 + LLM 自评**合并**判定。

    fail-closed 原则：
    - 任一来源（规则 / 结构 / LLM）存在阻断问题 → 不通过
    - LLM 的 passed=true **不能**覆盖任何阻断问题
    - 规则 warnings **必须保留**（此前被 LLM warnings 覆盖，导致「正文含无依据数字」告警丢失）
    """
    data = llm.chat_json(
        build_qa_prompt(dna, draft, mat), system=QA_SYSTEM, temperature=0.2,
        scene="qa", platform=dna.get("code"),
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
    )


def run_qa(dna: dict, draft: DraftPayload, mat: StructuredMaterial) -> QaReport:
    """质检门：规则与结构校验恒生效；真实模式再叠加 LLM 自评。"""
    issues, warnings = _rule_qa(dna, draft, mat)
    if llm.is_mock():
        return QaReport(passed=not issues, issues=issues, warnings=warnings)
    return _llm_qa(dna, draft, mat, issues, warnings)


# ---------------------------------------------------------------- orchestrator
def _generate_one(code: str, mat: StructuredMaterial, tone_override: str | None,
                  template: UserTemplate | None = None,
                  creator_profile: dict | None = None,
                  retrospect_hints: list[str] | None = None) -> PlatformDraft:
    dna = dna_lib.load_dna(code)
    brief = run_brief(code, dna, mat, tone_override, template, creator_profile, retrospect_hints)
    draft = run_draft(code, dna, mat, brief, template=template,
                      creator_profile=creator_profile, retrospect_hints=retrospect_hints)
    qa = run_qa(dna, draft, mat)

    # 真实模式下 QA 未通过 → 带反馈重写一轮
    if not qa.passed and not llm.is_mock():
        feedback = "；".join(qa.issues)
        draft = run_draft(code, dna, mat, brief, feedback=feedback, template=template,
                          creator_profile=creator_profile, retrospect_hints=retrospect_hints)
        qa = run_qa(dna, draft, mat)

    # 视频平台：成稿后附剪辑单（A 阶段）
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
    """全链路：素材理解 → 每平台 brief/draft/clip/qa（平台并行）。"""
    used_mock = llm.is_mock()
    a_req = AnalyzeRequest(raw_text=req.raw_text, source_kind=req.source_kind, title=req.title)
    structured, _ = run_understand(a_req)

    # 校验平台合法性（统一提前报错）
    for code in req.platforms:
        try:
            dna_lib.load_dna(code)
        except ValueError as e:
            raise ValueError(f"不支持的平台 [{code}]；可用: {[p['code'] for p in dna_lib.list_platforms()]}") from e

    results: list[PlatformDraft] = []
    cfg = get_settings()

    def _submit(code: str):
        # 显式携带上下文：ThreadPoolExecutor **不会**继承 contextvars，
        # 不包装则子线程内 trace_id 丢失（各平台各自生成新 trace）
        return trace.run_in_context(_generate_one, code, structured,
                                    req.tone_override, req.template,
                                    req.creator_profile, req.retrospect_hints)

    if cfg.llm_parallel and len(req.platforms) > 1:
        # 并行：快，但需上游额度宽裕（默认关闭，理由见 config）
        with ThreadPoolExecutor(max_workers=min(4, len(req.platforms))) as ex:
            futures = [ex.submit(_submit, code) for code in req.platforms]
            for f in futures:
                results.append(f.result())
    else:
        # 串行 + 最小间隔：实测限流策略下的可靠路径
        interval = max(0, int(cfg.llm_min_interval_ms)) / 1000.0
        for i, code in enumerate(req.platforms):
            if i > 0 and interval > 0:
                time.sleep(interval)
            results.append(_submit(code))

    structured_dict = json.loads(structured.model_dump_json())
    return structured_dict, results, used_mock
