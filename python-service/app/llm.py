"""LLM 客户端适配层（OpenAI 兼容）。

- mock 模式（默认，无 Key 可离线跑通全流程）
- 真实模式：llm_mock=false 且已配置 llm_api_key
"""
from __future__ import annotations

import logging

from .config import get_settings

logger = logging.getLogger(__name__)


def is_mock() -> bool:
    s = get_settings()
    if s.llm_mock:
        return True
    if not s.llm_api_key:
        logger.warning("LLM_MOCK=false 但未配置 LLM_API_KEY，回退到 mock 模式")
        return True
    return False


def chat(prompt: str, system: str | None = None, *, temperature: float = 0.4) -> str:
    """调用真实 LLM（OpenAI 兼容），返回文本。"""
    from openai import OpenAI

    s = get_settings()
    client = OpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(
        model=s.llm_model,
        messages=messages,  # type: ignore[arg-type]
        temperature=temperature,
    )
    content = resp.choices[0].message.content or ""
    logger.info("LLM call ok: model=%s len=%d", s.llm_model, len(content))
    return content


def chat_json(prompt: str, system: str | None = None, *, temperature: float = 0.2) -> dict:
    """调用真实 LLM 并解析为 JSON（容错剥离 markdown fence）。"""
    raw = chat(prompt, system=system, temperature=temperature)
    return parse_json(raw)


def parse_json(raw: str) -> dict:
    """从 LLM 输出中稳健提取 JSON 对象。

    容错：剥离 ```json ... ``` 围栏、找到首个 { 到末尾 }。
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"LLM 输出中未找到 JSON 对象: {raw[:200]!r}")
    import json

    return json.loads(text[start : end + 1])
