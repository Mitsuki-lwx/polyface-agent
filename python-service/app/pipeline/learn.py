"""示例学习（FR-63）：用户自有示例 → 拆解出可复用模板参数（草稿态）。

- mock 模式：规则启发式（分段 → 首段做 opening / 末段互动句做 closing / 段落要点做 structure）
- 真实模式：LLM 拆解，prompt 明确要求「只归纳结构、不复述内容」
- 产物一律为**草稿**，须人工确认后启用（ADR-014 / UC-16）
- 不保存示例原文，只回传拆解结果
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from .. import llm
from ..schemas_gen import LearnedTemplate, LearnRequest
from .prompts import LEARN_SYSTEM, build_learn_prompt

logger = logging.getLogger(__name__)

_INTERACTION_MARKERS = ("？", "?", "评论", "关注", "收藏", "点赞", "留言", "聊聊", "互动", "转发")
_MAX_STRUCTURE = 6
_STRUCTURE_ITEM_MAX = 15
_OPENING_MAX = 60
_CLOSING_MAX = 60


def _clip(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def split_paragraphs(text: str) -> list[str]:
    """按连续空行分段；若只切出一段则退化为按行分段。"""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if len(parts) <= 1:
        parts = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return parts


def first_clause(para: str) -> str:
    """取段首短句作为结构要点（剥离序号与小标题式前缀）。"""
    p = re.sub(r"^\s*[0-9]+[.、)）]\s*", "", (para or "").strip())
    p = re.sub(r"^\s*[-*·•]\s*", "", p)
    chunks = re.split(r"[。！？!?；;，,]", p)
    return (chunks[0] if chunks else p).strip()


def guess_voice(text: str) -> str:
    """mock 语气判定（关键词启发式，兜底「通用」）。"""
    if re.search(r"我觉得|我曾经|以前我|我的经历|我也是", text):
        return "真诚分享"
    if re.search(r"数据|方法|步骤|拆解|框架|逻辑|实操", text):
        return "理性干货"
    if re.search(r"不对|错了|误区|别再|醒醒|真相是", text):
        return "犀利观点"
    return "通用"


def has_interaction(line: str) -> bool:
    """末尾是否为互动式收束（引导评论/关注等）。"""
    return any(m in line for m in _INTERACTION_MARKERS)


def build_name(source_note: str | None) -> str:
    """模板名：有来源备注用备注，否则用日期兜底。"""
    note = (source_note or "").strip()
    if note:
        return f"学习：{_clip(note, 30)}"
    return f"学习：示例（{datetime.now().strftime('%m-%d')}）"


def _mock_learn(sample_text: str, source_note: str | None) -> LearnedTemplate:
    paras = split_paragraphs(sample_text)
    body = "\n".join(paras)

    opening = _clip(paras[0], _OPENING_MAX) if paras else ""

    closing = ""
    if len(paras) >= 2 and len(paras[-1]) <= 120 and has_interaction(paras[-1]):
        closing = _clip(paras[-1], _CLOSING_MAX)

    middle = paras[1:-1] if closing else paras[1:]
    structure: list[str] = []
    for p in middle:
        item = _clip(first_clause(p), _STRUCTURE_ITEM_MAX)
        if item:
            structure.append(item)
        if len(structure) >= _MAX_STRUCTURE:
            break
    if not structure:
        structure = [_clip(first_clause(p), _STRUCTURE_ITEM_MAX) for p in paras][:_MAX_STRUCTURE]

    voice = guess_voice(body)
    rationale = (
        f"识别到 {len(paras)} 个段落；开头为陈述式开场"
        f"{'；结尾为互动式收束' if closing else '；未识别到明显互动收尾'}"
        f"；语气判定为「{voice}」。"
    )
    return LearnedTemplate(
        name=build_name(source_note),
        voice=voice,
        opening=opening,
        structure=structure,
        closing=closing,
        tag_style="",
        taboo=[],
        rationale=rationale,
    )


def _llm_learn(sample_text: str, source_note: str | None) -> LearnedTemplate:
    data = llm.chat_json(
        build_learn_prompt(sample_text, source_note), system=LEARN_SYSTEM, temperature=0.3,
        scene="learn",
    )

    def _s(key: str) -> str:
        return str(data.get(key, "") or "").strip()

    raw_structure = [str(x).strip() for x in (data.get("structure") or []) if str(x).strip()]
    return LearnedTemplate(
        name=build_name(source_note),
        voice=_s("voice"),
        opening=_clip(_s("opening"), _OPENING_MAX),
        structure=[_clip(x, _STRUCTURE_ITEM_MAX) for x in raw_structure[:_MAX_STRUCTURE]],
        closing=_clip(_s("closing"), _CLOSING_MAX),
        tag_style=_s("tag_style"),
        taboo=[str(x).strip() for x in (data.get("taboo") or []) if str(x).strip()],
        rationale=_s("rationale") or "由 LLM 拆解示例结构得出，请核对后启用。",
    )


def run_learn(req: LearnRequest) -> tuple[LearnedTemplate, bool]:
    """示例 → 模板参数（草稿）。返回 (拆解结果, 是否 mock)。"""
    used_mock = llm.is_mock()
    learned = _mock_learn(req.sample_text, req.source_note) if used_mock \
        else _llm_learn(req.sample_text, req.source_note)
    logger.info("learned template from sample (%d chars, mock=%s, structure=%d)",
                len(req.sample_text), used_mock, len(learned.structure))
    return learned, used_mock
