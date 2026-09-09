"""素材解析管线：raw text -> StructuredMaterial（核心观点 + 事实清单）。"""
from __future__ import annotations

import logging
import re

from .. import llm
from ..schemas import AnalyzeRequest, Fact, StructuredMaterial
from .prompts import UNDERSTAND_SYSTEM, build_understand_prompt

logger = logging.getLogger(__name__)

# 宽松的句子切分
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;\n])\s*")


def _mock_understand(req: AnalyzeRequest) -> StructuredMaterial:
    """离线 mock：启发式抽取事实，保证无 Key 时可演示与测试。"""
    text = req.raw_text.strip()
    # 切句并去掉过短残片
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if len(s.strip()) >= 4]
    if not sentences:  # 兜底：按长度粗切
        sentences = [s for s in re.split(r"[,，\n]", text) if s.strip()][:10]

    facts: list[Fact] = []
    for s in sentences:
        s = s.strip("，。！？!?；; \n")
        if not s:
            continue
        if re.search(r"\d+", s):
            ftype = "data"
        elif re.search(r"[我我们咱自己曾经历过当时那年]", s):
            ftype = "story"
        else:
            ftype = "opinion"
        facts.append(Fact(text=s, type=ftype))  # type: ignore[arg-type]
        if len(facts) >= 8:
            break

    core = " ".join(sentences[:2])
    if len(core) > 80:
        core = core[:80] + "……"
    return StructuredMaterial(
        core_message=core or "(素材过短，未提炼到核心观点)",
        tone="中性/干货（mock 模式，未走真实模型）",
        audience="对话题感兴趣的读者（mock 模式）",
        facts=facts,
    )


def _coerce_type(raw: str) -> str:
    t = (raw or "").strip().lower()
    return t if t in {"data", "story", "opinion"} else "opinion"


def run_understand(req: AnalyzeRequest) -> tuple[StructuredMaterial, bool]:
    """执行素材解析。返回 (结构化素材, 是否 mock)。"""
    used_mock = llm.is_mock()
    if used_mock:
        logger.info("understand: mock 模式")
        return _mock_understand(req), True

    prompt = build_understand_prompt(req.raw_text, req.source_kind, req.title)
    data = llm.chat_json(prompt, system=UNDERSTAND_SYSTEM, temperature=0.2)

    facts = []
    for f in data.get("facts") or []:
        text = str(f.get("text", "")).strip() if isinstance(f, dict) else str(f).strip()
        if not text:
            continue
        t = f.get("type", "opinion") if isinstance(f, dict) else "opinion"
        facts.append(Fact(text=text, type=_coerce_type(str(t))))  # type: ignore[arg-type]

    structured = StructuredMaterial(
        core_message=str(data.get("core_message", "")).strip() or "(未提炼)",
        tone=str(data.get("tone", "")).strip() or "中性",
        audience=str(data.get("audience", "")).strip() or "大众读者",
        facts=facts,
    )
    return structured, False
