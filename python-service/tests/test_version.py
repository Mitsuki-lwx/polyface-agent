"""版本号单一来源（VERSION 文件）的一致性测试。

背景：发版前发现 Java 是 0.4.0、Python 服务却写死 0.3.0，两个数字对不上。
根因是版本号被硬编码在多处。现在统一从仓库根 `VERSION` 读取，
这里断言"读到的就是文件里的值"，防止以后再有人写死字符串。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import config
from app.config import read_version_file, service_version

APP_DIR = Path(config.__file__).resolve().parent
REPO_ROOT = APP_DIR.parent.parent


def test_read_version_file_finds_repo_version():
    assert read_version_file(APP_DIR) == (REPO_ROOT / "VERSION").read_text(
        encoding="utf-8").strip()


def test_read_version_file_returns_none_when_absent(tmp_path):
    assert read_version_file(tmp_path) is None


def test_read_version_file_treats_blank_as_absent(tmp_path):
    (tmp_path / "VERSION").write_text("   \n", encoding="utf-8")
    assert read_version_file(tmp_path) is None


def test_read_version_file_respects_max_up(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    (tmp_path / "VERSION").write_text("9.9.9", encoding="utf-8")
    # 距 VERSION 有 5 层，max_up=2 时不应该找到
    assert read_version_file(deep, max_up=2) is None
    assert read_version_file(deep, max_up=6) == "9.9.9"


def test_read_version_file_stops_at_filesystem_root(tmp_path):
    """上溯到盘根不应死循环。"""
    assert read_version_file(tmp_path, max_up=99) is None


def test_service_version_prefers_env(monkeypatch):
    monkeypatch.setenv("POLYFACE_VERSION", "1.2.3-test")
    config.service_version.cache_clear()
    try:
        assert service_version() == "1.2.3-test"
    finally:
        config.service_version.cache_clear()


def test_service_version_matches_version_file(monkeypatch):
    monkeypatch.delenv("POLYFACE_VERSION", raising=False)
    config.service_version.cache_clear()
    try:
        expected = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        assert service_version() == expected
    finally:
        config.service_version.cache_clear()


def test_fastapi_app_version_matches_version_file():
    """FastAPI 文档里显示的版本也必须来自 VERSION。"""
    from app.main import app

    expected = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert app.version == expected


def test_health_reports_version():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        body = c.get("/health").json()
    assert body["version"] == (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
