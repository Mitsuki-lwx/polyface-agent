"""多平台成稿管线：brief(策略) → draft(成稿) → qa(质量门)。

- mock 模式：规则化生成，保证无 Key 可离线演示与测试
- 真实模式：调用平台化 LLM；qa 失败可带反馈重写一轮
- 事实约束：qa 会校验正文新增数字/断言是否在素材事实清单中有依据
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from .. import dna as dna_lib
from .. import llm
from ..pipeline.understand import run_understand
from ..schemas import AnalyzeRequest, StructuredMaterial
from ..schemas_gen import Brief, DraftPayload, GenerateRequest, PlatformDraft, QaReport
from .prompts import (
    BRIEF_SYSTEM,
    DRAFT_SYSTEM,
    QA_SYSTEM,
    build_brief_prompt,
    build_draft_prompt,
    build_qa_prompt,
)

logger = logging.getLogger(__name__)

_NUM_RE = re.compile(r"\d+(?:\.\d+)?(?:[%％万kK亿])?\b|\d{2,}")


def _clip(text: str, n: int) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------- brief
def run_brief(code: str, dna: dict, mat: StructuredMaterial, tone_override: str | None) -> Brief:
    if llm.is_mock():
        structure = " → ".join(dna.get("structure_template", ["开头抛结论", "展开细节", "结尾互动"]))
        tag_dir = [t for t in (dna.get("tags") or {}).get("mock", ["干货"])]
        return Brief(
            platform_code=code,
            angle=_clip(mat.core_message, 60),
            hooks=[_clip(mat.core_message, 40)],
            structure_plan=structure,
            tag_direction=tag_dir,
            rationale=f"mock策略: 参考平台[{dna.get('name')}]的结构模板与标签方向",
        )
    data = llm.chat_json(
        build_brief_prompt(dna, mat, tone_override), system=BRIEF_SYSTEM, temperature=0.7
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
def _mock_draft(code: str, dna: dict, mat: StructuredMaterial, brief: Brief) -> DraftPayload:
    facts = mat.facts[:5]
    body_lines = [
        f"先说结论：{_clip(mat.core_message, 80)}",
        "",
    ]
    for i, f in enumerate(facts, 1):
        marker = {"data": "📊", "story": "📖", "opinion": "💡"}.get(f.type, "·")
        body_lines.append(f"{marker} {f.text}")
    body_lines += ["", "觉得有用就收藏，下次需要直接翻出来看～"]
    tags = (brief.tag_direction or ["干货"])[:6]
    titles = [
        _clip(mat.core_message, 18),
        f"{_clip(mat.core_message, 12)}｜真实经验",
    ]
    return DraftPayload(
        titles=titles,
        body="\n".join(body_lines),
        tags=tags,
        cover_suggestion=_clip(mat.core_message, 16),
        interaction_line="你们平时会复盘吗？评论区聊聊～",
        rationale=f"mock成稿: {dna.get('name')}风格=结论前置+清单式干货+收藏引导",
    )


def run_draft(
    code: str,
    dna: dict,
    mat: StructuredMaterial,
    brief: Brief,
    feedback: str | None = None,
) -> DraftPayload:
    if llm.is_mock():
        return _mock_draft(code, dna, mat, brief)
    data = llm.chat_json(
        build_draft_prompt(dna, mat, brief, feedback), system=DRAFT_SYSTEM, temperature=0.8
    )
    return DraftPayload(
        titles=[str(t).strip() for t in (data.get("titles") or []) if str(t).strip()][:3],
        body=str(data.get("body", "")).strip(),
        tags=[str(t).strip().lstrip("#") for t in (data.get("tags") or []) if str(t).strip()][:8],
        cover_suggestion=str(data.get("cover_suggestion", "")).strip(),
        interaction_line=str(data.get("interaction_line", "")).strip(),
        rationale=str(data.get("rationale", "")).strip(),
    )


# ---------------------------------------------------------------- qa
def _rule_qa(dna: dict, draft: DraftPayload, mat: StructuredMaterial) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    limits = dict(dna.get("limits") or {})
    body_max = int(limits.get("body_chars_max", 1000))
    tag_max = int((dna.get("tags") or {}).get("count_max", 8))

    body_len = len(draft.body)
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


def _llm_qa(
    dna: dict, draft: DraftPayload, mat: StructuredMaterial, issues: list[str]
) -> QaReport:
    """真实模式：规则 + LLM 自评（按平台爆款清单 + 事实一致性）。"""
    data = llm.chat_json(
        build_qa_prompt(dna, draft, mat), system=QA_SYSTEM, temperature=0.2
    )
    llm_issues = [str(x).strip() for x in (data.get("issues") or []) if str(x).strip()]
    llm_warns = [str(x).strip() for x in (data.get("warnings") or []) if str(x).strip()]
    passed = bool(data.get("passed")) and not issues
    return QaReport(passed=passed, issues=issues + llm_issues[:3], warnings=llm_warns)


def run_qa(dna: dict, draft: DraftPayload, mat: StructuredMaterial) -> QaReport:
    issues, warnings = _rule_qa(dna, draft, mat)
    if llm.is_mock():
        return QaReport(passed=not issues, issues=issues, warnings=warnings)
    return _llm_qa(dna, draft, mat, issues)


# ---------------------------------------------------------------- orchestrator
def _generate_one(code: str, mat: StructuredMaterial, tone_override: str | None) -> PlatformDraft:
    dna = dna_lib.load_dna(code)
    brief = run_brief(code, dna, mat, tone_override)
    draft = run_draft(code, dna, mat, brief)
    qa = run_qa(dna, draft, mat)

    # 真实模式下 QA 未通过 → 带反馈重写一轮
    if not qa.passed and not llm.is_mock():
        feedback = "；".join(qa.issues)
        draft = run_draft(code, dna, mat, brief, feedback=feedback)
        qa = run_qa(dna, draft, mat)

    return PlatformDraft(
        platform_code=code,
        platform_name=dna.get("name", code),
        brief=brief,
        draft=draft,
        qa=qa,
    )


def generate(req: GenerateRequest) -> tuple[dict, list[PlatformDraft], bool]:
    """全链路：素材理解 → 每平台 brief/draft/qa（平台并行）。"""
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
    with ThreadPoolExecutor(max_workers=min(4, len(req.platforms))) as ex:
        futures = [
            ex.submit(_generate_one, code, structured, req.tone_override)
            for code in req.platforms
        ]
        for f in futures:
            results.append(f.result())

    structured_dict = json.loads(structured.model_dump_json())
    return structured_dict, results, used_mock
