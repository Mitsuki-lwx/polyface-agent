"""Langfuse 可观测（FR-71）：**可选启用**；关闭时零开销。

按官方 v4 SDK（OpenTelemetry 架构）接入，遵循 langfuse skill 的 baseline：
- **优先框架集成**：使用 `langfuse.openai.OpenAI` drop-in，自动捕获 model / token / 耗时，
  优于手写埋点（skill 明确建议 "Prefer integrations over manual instrumentation"）
- **trace_id 复用我们的 W3C 32 位 id**，使 Python 与 Java 的观测归入同一 trace
- 观测失败**绝不影响业务**（全部 try/except 降级）

⚠️ 未启用（`LANGFUSE_ENABLED=false`）时**不初始化客户端、不发任何请求**。

参考：https://langfuse.com/docs/observability/sdk/python/instrumentation
"""
from __future__ import annotations

import contextlib
import logging

from .config import get_settings

logger = logging.getLogger(__name__)

_client = None
_init_failed = False
_probe_done = False


def enabled() -> bool:
    s = get_settings()
    return bool(s.langfuse_enabled and s.langfuse_public_key and s.langfuse_secret_key)


def _reachable(timeout: float = 3.0) -> bool:
    """探测 Langfuse 是否可达。

    必要性：服务未启动时，SDK 会在每次上报时超时重试并刷警告日志，
    既拖慢请求又干扰排查。启动前探一次，不可达则整体降级。
    """
    import base64
    import urllib.request

    s = get_settings()
    try:
        token = base64.b64encode(
            f"{s.langfuse_public_key}:{s.langfuse_secret_key}".encode()).decode()
        req = urllib.request.Request(
            f"{s.langfuse_host.rstrip('/')}/api/public/projects",
            headers={"Authorization": f"Basic {token}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception as e:  # noqa: BLE001
        logger.warning("Langfuse 不可达（%s）：观测本次运行降级为关闭", e)
        return False


def _get_client():
    """惰性初始化；不可达或失败则永久降级（只告警一次）。"""
    global _client, _init_failed, _probe_done
    if _client is not None or _init_failed:
        return _client
    if not _probe_done:
        _probe_done = True
        if not _reachable():
            _init_failed = True
            return None
    try:
        from langfuse import Langfuse
        s = get_settings()
        _client = Langfuse(
            public_key=s.langfuse_public_key,
            secret_key=s.langfuse_secret_key,
            host=s.langfuse_host,
        )
        logger.info("Langfuse 已启用：host=%s", s.langfuse_host)
    except Exception as e:  # noqa: BLE001
        _init_failed = True
        logger.warning("Langfuse 初始化失败，观测降级为关闭：%s", e)
    return _client


def openai_client(base_url: str, api_key: str, timeout: float, http_client=None):
    """返回 OpenAI 客户端；启用观测时返回 Langfuse 包装版（自动埋点）。

    `http_client` 由调用方提供（`llm._http_client()`）—— 某些网关的响应信封
    不是标准 OpenAI 形状，需要在那层做兼容，本模块不关心细节。
    """
    if enabled() and _get_client() is not None:
        try:
            from langfuse.openai import OpenAI as LfOpenAI
            return LfOpenAI(base_url=base_url, api_key=api_key, timeout=timeout,
                            http_client=http_client)
        except Exception as e:  # noqa: BLE001
            logger.warning("Langfuse OpenAI 集成不可用，回退原生客户端：%s", e)
    from openai import OpenAI
    return OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0,
                  http_client=http_client)


@contextlib.contextmanager
def trace_span(name: str, trace_id: str | None = None, **attrs):
    """开一个 span；未启用时为 no-op。

    - `trace_id` 非空 → **root 模式**：以该 id 加入 trace（入口用，决定 trace 名）
    - `trace_id` 为空 → **child 模式**：继承当前 OTel 上下文，成为父 span 的子节点
      （配合 `trace.run_in_context` 在线程池中传播，即可让并发生成正确嵌套）

    块内的 LLM 调用（drop-in 自动埋点）会挂在当前 span 下。
    """
    if not enabled():
        yield None
        return
    lf = _get_client()
    if lf is None:
        yield None
        return
    # ⚠️ 关键不变式：观测层**绝不改变业务异常的传播**。
    #
    # 反面写法（曾导致真实缺陷）：把 `yield span` 也放进 try，在 except 里再 `yield None`。
    # 那会让 contextlib 抛 `RuntimeError: generator didn't stop after throw()`，
    # 把业务异常（如 RateLimited）替换成 RuntimeError → llm.py 的异常分类失准 →
    # **限流重试语义被破坏**。
    #
    # 正确做法：仅在「建立 span」阶段容错（失败则降级为无观测）；
    # yield 之后的业务异常原样抛出，并把异常信息交回 span 以记录真实状态。
    try:
        kwargs: dict = {"as_type": "span", "name": name}
        if trace_id:
            kwargs["trace_context"] = {"trace_id": trace_id}
        ctx = lf.start_as_current_observation(**kwargs)
        span = ctx.__enter__()
    except Exception as e:  # noqa: BLE001 — 仅建立阶段失败才降级
        logger.warning("Langfuse span 创建失败（已降级为无观测）：%s", e)
        yield None
        return

    if attrs and span is not None:
        try:
            span.update(metadata={k: str(v) for k, v in attrs.items()})
        except Exception:  # noqa: BLE001 — 属性写入失败不影响业务
            pass

    try:
        yield span
    except BaseException as exc:  # 业务异常：记录状态后**原样抛出**
        try:
            ctx.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:  # noqa: BLE001
            pass
        raise
    else:
        try:
            ctx.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


def flush() -> None:
    """确保缓冲数据发出。未启用时**绝不**触碰客户端。"""
    if not enabled():
        return
    lf = _get_client()
    if lf is not None:
        try:
            lf.flush()
        except Exception:  # noqa: BLE001
            pass
