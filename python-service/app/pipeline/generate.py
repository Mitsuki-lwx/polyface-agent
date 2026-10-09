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
from ..pipeline.runlog import RunLog
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
    build_draft_prompt,
    build_qa_prompt,
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


def _coerce_facts_used(raw) -> list[int] | None:
    """把模型回的「依据了哪几条事实」规整成 `list[int]`。

    ⚠️ **None 与 [] 必须区分**（`docs/spec_factguard.md` 关键决定三）：
      None = 没声明（违规）；[] = 声明了"没用到事实"（合法）。
    模型有时会把 `[1,3]` 回成字符串 `"1,3"` 或 `"[1, 3]"`，所以这里宽容解析，
    但**解析不出来时返回 None**（宁可按"没声明"报，也不假装声明过）。
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        # 显式的"空声明"（`[]` / `空` / `无`）→ `[]`；**其它非空但无数字的串是垃圾** → None。
        # 不能把垃圾当成"声明了空" —— 那会把违规伪装成合法（三态语义的关键）。
        bare = raw.strip().strip("[]").strip()
        if not bare or bare.lower() in ("空", "无", "none", "null"):
            return []
        raw = re.findall(r"\d+", raw)
        if not raw:
            return None
    if not isinstance(raw, (list, tuple)):
        return None
    out: list[int] = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


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
        # ⚠️ 这一行不能漏：`run_draft` 是**逐字段构造**的，
        # 漏掉字段不会报错，只会让 `facts_used` 永远是 None —— 整个声明机制静默失效
        # （实测踩到：smoke 跑出来 facts_used=None，而单独测 prompt 时模型明明回了 [1,2,3]）
        facts_used=_coerce_facts_used(data.get("facts_used")),
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
    evidence = " ".join([source_text or ""] + [f.text for f in mat.facts])
    allowed = set(_num_values(evidence, strict=False))
    body_vals = _num_values(draft.body, strict=True)
    unsupported = [tok for v, tok in sorted(body_vals.items()) if v not in allowed]
    if unsupported:
        warnings.append("正文含素材中无依据的数字: "
                        + "、".join(unsupported) + "（请确认或删除）")

    # 专名检查（T3）：正文里的专名必须能在素材里找到。
    # 白名单抽不到就**静默跳过** —— 宁可不查，不可误报（见 app/pipeline/proper.py）。
    from . import proper as _proper
    known_names = _proper.extract(evidence)                       # 素材侧：宽松
    if known_names:
        # 大小写不敏感：素材写 `database`、稿件写 `Database` 是同一个词
        # （实测 2026-10-09：m03/xhs 因首字母大写被误报）
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
    data = llm.chat_json(
        build_qa_prompt(dna, draft, mat, source_text), system=QA_SYSTEM, temperature=0.2,
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


def run_qa(dna: dict, draft: DraftPayload, mat: StructuredMaterial,
           source_text: str = "") -> QaReport:
    """质检门：规则与结构校验恒生效；真实模式再叠加 LLM 自评。"""
    issues, warnings = _rule_qa(dna, draft, mat, source_text)
    if llm.is_mock():
        return QaReport(passed=not issues, issues=issues, warnings=warnings)
    return _llm_qa(dna, draft, mat, issues, warnings, source_text)


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
    with _stage(log, f"平台:{code}:成稿") as r:
        draft = run_draft(code, dna, mat, brief, template=template,
                          creator_profile=creator_profile, retrospect_hints=retrospect_hints)
        r.done("成稿完成")
    with _stage(log, f"平台:{code}:质检") as r:
        qa = run_qa(dna, draft, mat, source_text)
        r.done("通过" if qa.passed else f"未通过（{len(qa.issues)} 项）")

    # 真实模式下 QA 未通过 → 带反馈重写一轮
    if not qa.passed and not llm.is_mock():
        feedback = "；".join(qa.issues)
        with _stage(log, f"平台:{code}:改写") as r:
            draft = run_draft(code, dna, mat, brief, feedback=feedback, template=template,
                              creator_profile=creator_profile, retrospect_hints=retrospect_hints)
            qa = run_qa(dna, draft, mat, source_text)
            r.done("改写后通过" if qa.passed else "改写后仍未通过")

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

    def _submit(code: str):
        # 显式携带上下文：ThreadPoolExecutor **不会**继承 contextvars，
        # 不包装则子线程内 trace_id 丢失（各平台各自生成新 trace）
        return trace.run_in_context(_generate_one, code, structured,
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

    if cfg.llm_parallel and len(req.platforms) > 1:
        # 并行：快，但需上游额度宽裕（默认关闭，理由见 config）
        with ThreadPoolExecutor(max_workers=min(4, len(req.platforms))) as ex:
            futures = {ex.submit(_submit, code): code for code in req.platforms}
            for f, code in futures.items():
                try:
                    results.append(f.result())
                except Exception as e:  # noqa: BLE001 — 部分失败可容忍
                    _record_failure(code, e)
    else:
        # 串行 + 最小间隔：实测限流策略下的可靠路径
        interval = max(0, int(cfg.llm_min_interval_ms)) / 1000.0
        for i, code in enumerate(req.platforms):
            if i > 0 and interval > 0:
                time.sleep(interval)
            try:
                results.append(_submit(code))
            except Exception as e:  # noqa: BLE001 — 部分失败可容忍
                _record_failure(code, e)

    structured_dict = json.loads(structured.model_dump_json())
    return structured_dict, results, used_mock, failures
