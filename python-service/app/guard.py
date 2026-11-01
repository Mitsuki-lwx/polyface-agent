"""本地服务请求来源准入（`openspec/changes/local-service-request-guard`）。

**为什么需要**：Polyface 的两个服务都只监听回环、**没有鉴权**。
"只听 127.0.0.1"拦不住浏览器里的任意网页：

1. **DNS rebinding** —— 攻击者的域名先解析到自己、再改成 `127.0.0.1`，
   浏览器就会认为**同源**；CORS 这时**完全不生效**，页面能读能写。
2. **跨站简单请求** —— `<form>` / multipart 这类请求**不需要预检**，
   浏览器会真的把它发出去（只是读不到响应），足以**消耗用户付费的 LLM 额度**。

实测（2026-10-31，本机实例 + 假数据）：

    curl -H "Host: evil.example.com" http://127.0.0.1:8080/api/materials  → 200 + 真实稿件

**判据**（与 Java 侧同一套语义，见 `docs/08` ADR-027）：

- `Host` 必须是回环（`127.0.0.1` / `localhost` / `[::1]`，可带端口）→ 挡住 rebinding；
- 带 `Origin` 的请求必须命中允许集合 → 挡住跨站；
- **不带 `Origin` 放行** —— 命令行与 Java→Python 的服务端调用都不带它。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: 回环主机名。**不含端口** —— 比较前先剥掉端口。
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: 默认允许的 `Origin` 前缀：本机回环的**任意端口**。
#: 用前缀而不是枚举端口，因为端口本身可配（`POLYFACE_JAVA_PORT`），枚举必漏。
DEFAULT_ORIGIN_PREFIXES = (
    "http://127.0.0.1:", "https://127.0.0.1:",
    "http://localhost:", "https://localhost:",
    "http://[::1]:", "https://[::1]:",
)

#: `Host` 里 IPv6 带方括号（`[::1]:8080`）—— 简单地 `split(":")` 会把它切坏。
_HOST_WITH_PORT = re.compile(r"^\[(?P<v6>[^\]]+)\](?::\d+)?$|^(?P<plain>[^:]+)(?::\d+)?$")


def strip_port(host: str) -> str:
    """`127.0.0.1:8080` → `127.0.0.1`；`[::1]:8080` → `::1`；`localhost` → `localhost`。"""
    h = (host or "").strip()
    m = _HOST_WITH_PORT.match(h)
    if not m:
        return h.lower()
    return (m.group("v6") or m.group("plain") or "").strip().lower()


def host_allowed(host: str) -> bool:
    """`Host` 是不是回环。空 `Host` **不放行**（HTTP/1.1 必带；缺了本身就反常）。"""
    return strip_port(host) in LOOPBACK_HOSTS


def origin_allowed(origin: str, extra: str = "") -> bool:
    """`Origin` 是否命中允许集合：默认回环前缀 + `POLYFACE_ALLOWED_ORIGINS` 追加项。

    `Origin: null`（沙箱 iframe / `file://`）**不放行** —— 它正是跨站的形态之一。
    """
    o = (origin or "").strip()
    if not o or o.lower() == "null":
        return False
    if o.startswith(DEFAULT_ORIGIN_PREFIXES):
        return True
    for item in (extra or "").split(","):
        item = item.strip().rstrip("/")
        if item and (o == item or o.rstrip("/") == item):
            return True
    return False


def judge(host: str, origin: str, extra: str = "") -> tuple[bool, str]:
    """返回 `(放行?, 原因)`。原因在放行时为空串。"""
    if not host_allowed(host):
        return False, f"Host 不是回环（{host or '(空)'}）：疑似 DNS rebinding"
    if origin and not origin_allowed(origin, extra):
        return False, f"Origin 不在允许集合（{origin}）：疑似跨站请求"
    return True, ""


def enabled() -> bool:
    """闸门总开关。**默认开**；`POLYFACE_GUARD_DISABLED=1` 只给测试用。"""
    import os
    return (os.getenv("POLYFACE_GUARD_DISABLED", "").strip().lower()
            not in ("1", "true", "yes", "on"))


class RequestGuard:
    """纯 ASGI 中间件：在**读正文之前**就拒绝，避免"请求已经产生副作用"。

    **必须最后注册**（Starlette 的 `add_middleware` 是前置插入 → 最后加的在最外层、最先跑）。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not enabled():
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in (scope.get("headers") or [])}
        host = headers.get("host", "")
        origin = headers.get("origin", "")
        from .config import get_settings
        extra = get_settings().allowed_origins or ""

        ok, reason = judge(host, origin, extra)
        if not ok:
            logger.warning("拒绝请求（%s）：%s %s host=%r origin=%r",
                           reason, scope.get("method"), scope.get("path"), host, origin)
            body = (
                "403 请求来源不被允许。\n"
                f"原因：{reason}\n"
                "本服务只接受本机（127.0.0.1 / localhost）发起的请求。\n"
                "如果你是把工作台反代到了别的域名，请设置 POLYFACE_ALLOWED_ORIGINS"
                "（逗号分隔，例如 POLYFACE_ALLOWED_ORIGINS=https://my.example.com）。\n"
            ).encode("utf-8")
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)
