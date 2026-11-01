"""请求来源准入（`openspec/changes/local-service-request-guard`）。

**为什么必须有测试**：这道闸门挡的正是"任意网页驱动本机服务"（DNS rebinding / 跨站），
而它**默认开着**、一旦判错就会把正常链路（工作台、命令行、Java→Python）一起打死 ✗。
所以两个方向都要钉：**该拒的必拒**、**不该拒的一条也不许拒**。

conftest 里把闸门**显式关掉**给其它测试用；这里用 `monkeypatch.delenv` 重新打开。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import guard, trace
from app.main import app

LOOPBACK = "http://127.0.0.1:8080"


@pytest.fixture()
def client(monkeypatch):
    """打开闸门，并用回环 base_url（让 `Host` 是 `127.0.0.1:8080`）。"""
    monkeypatch.delenv("POLYFACE_GUARD_DISABLED", raising=False)
    return TestClient(app, base_url=LOOPBACK)


@pytest.fixture()
def evil(client, monkeypatch):
    """同一个 app，但 base_url 是攻击者域名 —— 等价于"浏览器认为同源"。"""
    return TestClient(app, base_url="http://evil.example.com")


# ---------------------------------------------------------------- 纯函数

def test_strip_port_handles_ipv6():
    assert guard.strip_port("127.0.0.1:8080") == "127.0.0.1"
    assert guard.strip_port("[::1]:8000") == "::1"
    assert guard.strip_port("localhost") == "localhost"


def test_host_must_be_loopback():
    for h in ("127.0.0.1", "127.0.0.1:8080", "localhost:8090", "[::1]:8000"):
        assert guard.host_allowed(h), h
    for h in ("evil.example.com", "evil.example.com:8080", "", "192.168.1.5"):
        assert not guard.host_allowed(h), h


def test_origin_default_is_loopback_any_port():
    for o in ("http://127.0.0.1:8080", "https://localhost:3000", "http://[::1]:8000"):
        assert guard.origin_allowed(o), o
    for o in ("https://evil.example.com", "null", "", "http://127.0.0.1.evil.com:"):
        assert not guard.origin_allowed(o), o


def test_extra_origins_are_honoured():
    extra = "https://my.example.com, https://other.example.com/"
    assert guard.origin_allowed("https://my.example.com", extra)
    assert guard.origin_allowed("https://other.example.com", extra)
    assert not guard.origin_allowed("https://evil.example.com", extra)


# ---------------------------------------------------------------- 端到端（走 ASGI 栈）

def test_forged_host_is_rejected_without_data(evil):
    """① DNS rebinding：伪造 Host → 403，且**不返回业务数据**。"""
    r = evil.get("/usage/summary")
    assert r.status_code == 403
    assert "POLYFACE_ALLOWED_ORIGINS" in r.text, "403 正文要指出配置项名"
    assert "total_calls" not in r.text, "绝不能漏数据"


def test_cross_site_origin_is_rejected(evil):
    """② 跨站：带 evil Origin → 403（请求**不产生副作用**）。"""
    r = evil.post("/analyze", json={"raw_text": "x", "source_kind": "longform"},
                  headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_loopback_without_origin_passes(client):
    """命令行 / 服务间调用不带 Origin → 放行。"""
    r = client.get("/health")
    assert r.status_code == 200


def test_loopback_with_loopback_origin_passes(client):
    """工作台是同源页面 → 放行。"""
    r = client.get("/health", headers={"Origin": LOOPBACK})
    assert r.status_code == 200


def test_rejection_is_logged_with_host_and_origin(evil, caplog):
    with caplog.at_level("WARNING"):
        evil.get("/health", headers={"Origin": "https://evil.example.com"})
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "拒绝请求" in msgs and "evil.example.com" in msgs


def test_guard_can_be_disabled_explicitly(monkeypatch):
    """显式关掉（只给测试/排障）时，伪造 Host 也能过。"""
    monkeypatch.setenv("POLYFACE_GUARD_DISABLED", "1")
    c = TestClient(app, base_url="http://evil.example.com")
    assert c.get("/health").status_code == 200
