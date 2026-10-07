"""LLM 客户端适配层（OpenAI 兼容）+ 运行时加固（FR-70）。

模式
- mock（默认，无 Key 可离线跑通全流程）
- 真实：`llm_mock=false` 且已配置 `llm_api_key`

加固
- **错误分类**：限流 / 额度耗尽 / 超时 / 网络 / 解析 —— 各自不同处置
- **重试退避**：`min(2^n, 8) + jitter`
- **模型降级链**：主模型额度耗尽时自动切换（实测网关各模型配额独立）
- **用量记录**：每次调用写本地 JSONL（只存长度与 token，**不存正文**）

⚠️ 本模块是**唯一**的真实 LLM 出口，所有埋点集中于此。
"""
from __future__ import annotations

import json
import logging
import random
import time

import httpx

from . import observability, usage
from .config import get_settings
from .trace import current_trace_id

logger = logging.getLogger(__name__)


# ============================================================ 网关兼容

def unwrap_data_envelope(raw: bytes) -> bytes:
    """把 `{"data": {标准 OpenAI 响应}, "success": true}` 剥成标准形状。

    **为什么需要**：有些网关（实测 Cline `api.cline.bot`）把成功响应多包一层 `data`，
    而 OpenAI SDK 只认顶层 `choices` → `r.choices` 是 `None` → 调用方拿到
    `TypeError: 'NoneType' object is not subscriptable`，**看着完全不像协议问题**
    （排查时先怀疑了额度、模型、网络，都不是）。

    **判据刻意收窄**，只在「有 `data`、`data` 是对象、且 `data` 里有 `choices`」时才剥：
    标准 OpenAI 响应（顶层直接是 `id`/`object`/`choices`）不会被误动，
    错误响应（`{"error": ...}`）也不碰。读不出来就原样放行。
    """
    try:
        d = json.loads(raw)
    except Exception:  # noqa: BLE001 — 不是 JSON 就原样放行
        return raw
    if (isinstance(d, dict) and "choices" not in d
            and isinstance(d.get("data"), dict) and "choices" in d["data"]):
        return json.dumps(d["data"]).encode("utf-8")
    return raw


class _UnwrapDataEnvelope(httpx.HTTPTransport):
    """把响应体交给 `unwrap_data_envelope` 再返回（见该函数的说明）。"""

    def handle_request(self, request):  # noqa: D102
        resp = super().handle_request(request)
        return httpx.Response(resp.status_code, headers=resp.headers,
                              content=unwrap_data_envelope(resp.read()))


def _http_client() -> httpx.Client:
    """带信封兼容的 httpx 客户端。"""
    return httpx.Client(transport=_UnwrapDataEnvelope())


# ============================================================ 错误体系

class LLMError(RuntimeError):
    """LLM 调用失败基类。"""
    retryable = False


class RateLimited(LLMError):
    """限流（tpm/rpm 超限）→ 可重试。"""
    retryable = True


class QuotaExhausted(LLMError):
    """额度耗尽 / 模型未开通 → 重试无意义，直接切换模型。"""
    retryable = False


class LLMTimeout(LLMError):
    retryable = True


class LLMNetworkError(LLMError):
    retryable = True


class LLMParseError(LLMError):
    """返回内容无法解析为预期结构。"""
    retryable = False


def classify(exc: Exception) -> LLMError:
    """把上游异常归类到本模块的错误体系（依据实测报文特征）。"""
    name = type(exc).__name__
    msg = str(exc)
    low = msg.lower()
    status = getattr(exc, "status_code", None)

    if name in ("APITimeoutError", "Timeout", "ReadTimeout", "TimeoutError"):
        return LLMTimeout(f"请求超时：{msg[:160]}")
    if name in ("APIConnectionError", "ConnectError", "ConnectionError"):
        return LLMNetworkError(f"网络连接失败：{msg[:160]}")

    # 额度/不可用类特征优先于限流（二者都可能返回 429）
    # 注：实测网关会返回 "model is not found"(404) —— 该模型不可用，应换模型而非重试
    if ("insufficient_quota" in low or "route not found" in low
            or "model is not found" in low or "not_found_error" in low):
        return QuotaExhausted(msg[:200])
    if status == 429 or "429" in low or "rate_limit" in low or "tpm/rpm" in low:
        return RateLimited(msg[:200])
    if "timeout" in low or "timed out" in low:
        return LLMTimeout(msg[:160])
    if "connection" in low:
        return LLMNetworkError(msg[:160])
    if status and isinstance(status, int) and status >= 500:
        return LLMNetworkError(f"上游 {status}：{msg[:160]}")
    return LLMError(msg[:200])


# ============================================================ 模型链

def model_chain(explicit: str | None = None) -> list[str]:
    """构建模型降级链。

    - 显式传入 model= → 只用它（尊重调用方，不走链）
    - 否则 → [LLM_MODEL] + LLM_FALLBACK_MODELS（去重保序）
    """
    if explicit and explicit.strip():
        return [explicit.strip()]
    s = get_settings()
    chain: list[str] = []
    if s.llm_model:
        chain.append(s.llm_model.strip())
    for m in (s.llm_fallback_models or "").split(","):
        m = m.strip()
        if m and m not in chain:
            chain.append(m)
    return chain or ["deepseek-v4-flash"]


def backoff_seconds(attempt_index: int) -> float:
    """退避序列：1s, 2s, 4s ... 上限 8s，附加 0~0.5s 抖动。"""
    return min(2 ** attempt_index, 8) + random.uniform(0, 0.5)


# ============================================================ 模式判定

def is_mock() -> bool:
    s = get_settings()
    if s.llm_mock:
        return True
    if not s.llm_api_key:
        logger.warning("LLM_MOCK=false 但未配置 LLM_API_KEY，回退到 mock 模式")
        return True
    return False


# ============================================================ 主入口

def _extract_usage(resp):
    u = getattr(resp, "usage", None)
    if u is None:
        return None, None
    return getattr(u, "prompt_tokens", None), getattr(u, "completion_tokens", None)


def chat(prompt: str, system: str | None = None, *, temperature: float = 0.4,
         model: str | None = None, scene: str = "", platform: str | None = None) -> str:
    """调用真实 LLM（OpenAI 兼容），带重试 / 降级 / 用量记录。

    失败时抛出本模块的错误体系异常，由上层决定如何呈现。
    """
    s = get_settings()
    tid = current_trace_id()
    chain = model_chain(model)
    last_error: LLMError | None = None

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    for mdl in chain:
        # 启用 Langfuse 时返回 drop-in 包装客户端（自动捕获 model/token）
        client = observability.openai_client(s.llm_base_url, s.llm_api_key,
                                             float(s.llm_timeout_sec), _http_client())
        for attempt in range(1, max(1, s.llm_max_attempts) + 1):
            started = time.time()
            try:
                # 观测：child 模式（不带 trace_id）→ 继承当前上下文，
                # 使各阶段 span 正确嵌套在 root（generate:xxx）之下
                with observability.trace_span(scene or "llm.chat"):
                    resp = client.chat.completions.create(
                        model=mdl, messages=messages,  # type: ignore[arg-type]
                        temperature=temperature)
                content = resp.choices[0].message.content or ""
                pt, ct = _extract_usage(resp)
                usage.record_call(trace_id=tid, scene=scene, platform=platform, model=mdl,
                                  attempt=attempt, ok=True,
                                  duration_ms=int((time.time() - started) * 1000),
                                  prompt_chars=len(prompt), completion_chars=len(content),
                                  prompt_tokens=pt, completion_tokens=ct)
                logger.info("LLM ok trace=%s scene=%s model=%s attempt=%d dur=%dms len=%d",
                            tid, scene or "-", mdl, attempt,
                            int((time.time() - started) * 1000), len(content))
                return content

            except Exception as exc:  # noqa: BLE001 — 统一分类后处置
                err = classify(exc)
                usage.record_call(trace_id=tid, scene=scene, platform=platform, model=mdl,
                                  attempt=attempt, ok=False, error_type=type(err).__name__,
                                  duration_ms=int((time.time() - started) * 1000),
                                  prompt_chars=len(prompt))
                last_error = err

                if isinstance(err, QuotaExhausted):
                    logger.warning("LLM trace=%s model=%s 额度耗尽 → 切换下一个模型", tid, mdl)
                    break  # 直接换模型，不浪费重试
                if isinstance(err, RateLimited) and attempt < s.llm_max_attempts:
                    wait = backoff_seconds(attempt - 1)
                    logger.warning("LLM trace=%s model=%s 限流(第%d次) → %.1fs 后重试",
                                   tid, mdl, attempt, wait)
                    time.sleep(wait)
                    continue
                if err.retryable and attempt < s.llm_max_attempts:
                    wait = backoff_seconds(attempt - 1)
                    logger.warning("LLM trace=%s model=%s %s(第%d次) → %.1fs 后重试",
                                   tid, mdl, type(err).__name__, attempt, wait)
                    time.sleep(wait)
                    continue
                # 不可重试，或该模型已耗尽尝试次数 → 换下一个模型
                logger.warning("LLM trace=%s model=%s %s 放弃（attempt=%d）",
                               tid, mdl, type(err).__name__, attempt)
                break

    tried = "、".join(chain)
    if last_error is None:
        raise LLMError("LLM 调用失败（未知原因）")
    if isinstance(last_error, QuotaExhausted):
        raise QuotaExhausted(f"所有模型均不可用（已尝试：{tried}）。上游原因：{last_error}") from last_error
    raise type(last_error)(f"所有模型均失败（已尝试：{tried}）。最后错误：{last_error}") from last_error


def chat_json(prompt: str, system: str | None = None, *, temperature: float = 0.2,
              model: str | None = None, scene: str = "", platform: str | None = None) -> dict:
    """调用真实 LLM 并解析为 JSON（容错剥离 markdown fence）。

    **解析失败要重试**（`LLM_JSON_RETRIES`，默认 2）：
    `chat()` 的重试只管网络/限流/额度，而 JSON 解析发生在它返回**之后** ——
    不在这里重试的话，模型偶发返回一次坏 JSON 就会让整个平台生成失败。
    实测（2026-10-07 冒烟）：4 次草稿调用里 2 次中招，报 `LLMParseError: Expecting ',' delimiter`。
    """
    s = get_settings()
    attempts = max(1, int(s.llm_json_retries))
    tid = current_trace_id()
    last: LLMParseError | None = None

    for attempt in range(1, attempts + 1):
        raw = chat(prompt, system=system, temperature=temperature,
                   model=model, scene=scene, platform=platform)
        try:
            return parse_json(raw)
        except ValueError as e:
            last = LLMParseError(str(e))
            # 进用量日志：否则「这件事多常发生」只能靠翻日志猜（模型未知 ——
            # chat() 只返回正文，不返回它最终用了链上的哪个模型）
            usage.record_call(trace_id=tid, scene=scene, platform=platform,
                              model="", attempt=attempt, ok=False,
                              error_type="LLMParseError", prompt_chars=len(prompt),
                              completion_chars=len(raw or ""))
            if attempt < attempts:
                wait = backoff_seconds(attempt - 1)
                logger.warning("LLM JSON 解析失败(第%d/%d次) → %.1fs 后重试：%s",
                               attempt, attempts, wait, e)
                time.sleep(wait)
                continue
            logger.warning("LLM JSON 解析失败，放弃（attempt=%d/%d）：%s", attempt, attempts, e)

    raise last or LLMParseError("解析失败")


def parse_json(raw: str) -> dict:
    """从 LLM 输出中稳健提取 JSON 对象。

    容错：剥离 ```json ... ``` 围栏、找到首个 { 到末尾 }。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"LLM 输出中未找到 JSON 对象: {raw[:200]!r}")
    return json.loads(text[start:end + 1])
