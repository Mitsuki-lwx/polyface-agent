"""CORS 收紧的回归测试。

背景（真实安全问题，非假设）：本服务监听 127.0.0.1:8000 且**无任何鉴权**。
此前 `allow_origins=["*"]` 意味着用户只要在浏览器里打开任意恶意网页，
该网页的 JS 就能 `fetch("http://127.0.0.1:8000/generate", ...)`：
- 触发真实 LLM 调用 → 消耗用户自己付钱的额度
- 并把响应正文（稿件全文）读走 → 素材泄露

而前端 SPA 其实**从不**直连 8000（grep `index.html` 无 `:8000`），
Java 调 Python 是服务端请求，不受 CORS 约束 —— 所以跨域支持本来就是多余的。

修复：默认不注册 CORSMiddleware，只有显式配置 `POLYFACE_CORS_ORIGINS` 才启用。

⚠️ 为什么这些断言必须真的发一次请求：`add_middleware` 是否生效、
响应头是否带上 `Access-Control-Allow-Origin`，只有走完 ASGI 栈才能确认。
只断言"函数返回了空列表"会漏掉中间件被别处注册的情况。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, parse_cors_origins
from app.main import app as real_app
from app.main import install_cors

EVIL = "https://evil.example.com"
WORKBENCH = "http://127.0.0.1:8080"


def _client(target: FastAPI) -> TestClient:
    return TestClient(target)


# ============================================================ parse_cors_origins

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", []),
        ("   ", []),
        (",", []),
        (",,,", []),
        (WORKBENCH, [WORKBENCH]),
        (f"{WORKBENCH},http://localhost:5173", [WORKBENCH, "http://localhost:5173"]),
        # 前后空白与空项要被吃掉，否则 FastAPI 会拿带空格的串去比对 Origin，永远匹配不上
        (f"  {WORKBENCH} ,, http://localhost:5173  ", [WORKBENCH, "http://localhost:5173"]),
        ("*", ["*"]),
    ],
)
def test_parse_cors_origins(raw, expected):
    assert parse_cors_origins(raw) == expected


# ============================================================ 环境变量名

def test_env_var_name_is_cors_origins_not_prefixed(monkeypatch):
    """锁住变量名：`CORS_ORIGINS`。

    `Settings` 没有 `env_prefix`，字段名大写即变量名（与 LLM_MOCK 等一致）。
    写文档时很容易顺手写成 `POLYFACE_CORS_ORIGINS`（本项目另有几个
    `POLYFACE_*` 变量是 `os.environ` 显式读的），用户照抄会**静默失效** ——
    CORS 不生效、跨域调试莫名其妙失败。所以这里把真实名字钉死。
    """
    monkeypatch.setenv("CORS_ORIGINS", WORKBENCH)
    assert Settings(_env_file=None).cors_origins == WORKBENCH


def test_prefixed_env_var_is_ignored(monkeypatch):
    """反向确认：`POLYFACE_CORS_ORIGINS` 不生效（防止文档再写错）。"""
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.setenv("POLYFACE_CORS_ORIGINS", WORKBENCH)
    assert Settings(_env_file=None).cors_origins == ""


def test_default_env_has_cors_off(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert Settings(_env_file=None).cors_origins == ""
    assert parse_cors_origins(Settings(_env_file=None).cors_origins) == []


# ============================================================ 默认：不启用

def test_install_cors_returns_empty_by_default():
    assert install_cors(FastAPI(), "") == []


def test_default_has_no_cors_header():
    """默认配置下，跨域请求拿不到 Access-Control-Allow-Origin —— 浏览器会拒绝。"""
    target = FastAPI()
    install_cors(target, "")

    @target.get("/ping")
    def ping():
        return {"ok": True}

    r = _client(target).get("/ping", headers={"Origin": EVIL})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_real_app_default_has_no_cors_header():
    """生产入口 app 本身也必须是不放行的（防止有人改回 `*`）。"""
    r = _client(real_app).get("/health", headers={"Origin": EVIL})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


# ============================================================ 显式配置：才启用

def _app_with_cors(raw: str) -> FastAPI:
    target = FastAPI()
    install_cors(target, raw)

    @target.get("/ping")
    def ping():
        return {"ok": True}

    return target


def test_configured_origin_is_allowed():
    origins = install_cors(FastAPI(), WORKBENCH)
    assert origins == [WORKBENCH]

    r = _client(_app_with_cors(WORKBENCH)).get("/ping", headers={"Origin": WORKBENCH})
    assert r.headers.get("access-control-allow-origin") == WORKBENCH


def test_unlisted_origin_is_not_allowed():
    """只配了工作台，别的来源仍然拿不到放行头。"""
    r = _client(_app_with_cors(WORKBENCH)).get("/ping", headers={"Origin": EVIL})
    assert r.headers.get("access-control-allow-origin") is None


def test_preflight_allows_trace_header():
    """预检要放行 X-Trace-Id，否则显式配置后前端带 trace 头的请求会被拦。"""
    r = _client(_app_with_cors(WORKBENCH)).options(
        "/ping",
        headers={
            "Origin": WORKBENCH,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-trace-id",
        },
    )
    assert r.status_code in (200, 204)
    allowed = (r.headers.get("access-control-allow-headers") or "").lower()
    assert "x-trace-id" in allowed
    assert "content-type" in allowed


def test_wildcard_still_works_when_explicitly_requested():
    """显式要 `*` 时仍然放行（并会打警告）—— 保证调试路径没被堵死。"""
    assert install_cors(FastAPI(), "*") == ["*"]
    r = _client(_app_with_cors("*")).get("/ping", headers={"Origin": EVIL})
    assert r.headers.get("access-control-allow-origin") == "*"


def test_wildcard_logs_warning(caplog):
    with caplog.at_level("WARNING"):
        install_cors(FastAPI(), "*")
    assert any("任意来源" in rec.getMessage() for rec in caplog.records), \
        "放行 * 必须留一条警告日志，否则用户不会意识到风险"


def test_default_logs_no_warning(caplog):
    with caplog.at_level("WARNING"):
        install_cors(FastAPI(), "")
    assert not [rec for rec in caplog.records if rec.levelno >= 30]
