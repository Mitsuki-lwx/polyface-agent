"""M2 测试：/generate 全链路（mock 模式）+ 平台 DNA + QA 规则。"""
import pytest
from fastapi.testclient import TestClient

from app import dna
from app.main import app
from app.pipeline.generate import run_qa
from app.schemas import StructuredMaterial
from app.schemas_gen import DraftPayload, GenerateRequest

client = TestClient(app)

SAMPLE = (
    "我2023年裸辞后做自由职业，靠写作从月入0做到3万。"
    "每周复盘对我的帮助最大：能看清哪类选题数据好，哪类没人看。"
    "建议新人至少存够6个月生活费再辞职，给自己试错空间。"
)


def _structured() -> StructuredMaterial:
    r = client.post("/analyze", json={"raw_text": SAMPLE, "source_kind": "长文"})
    assert r.status_code == 200
    return StructuredMaterial.model_validate(r.json()["structured"])


def test_generate_xiaohongshu_mock_full_chain():
    r = client.post(
        "/generate",
        json=GenerateRequest(raw_text=SAMPLE, source_kind="长文", platforms=["xhs"]).model_dump(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["used_mock"] is True
    assert len(body["drafts"]) == 1
    d = body["drafts"][0]
    assert d["platform_code"] == "xhs"
    assert d["platform_name"] == "小红书"
    assert d["qa"]["passed"] is True, d["qa"]
    assert d["brief"]["angle"]
    assert len(d["draft"]["titles"]) >= 1
    assert len(d["draft"]["body"]) <= 1000
    assert all("#" not in t for t in d["draft"]["tags"])


def test_generate_unknown_platform_returns_400():
    r = client.post(
        "/generate",
        json={"raw_text": SAMPLE, "platforms": ["weibo_not_supported"]},
    )
    assert r.status_code == 400
    assert "不支持的平台" in r.json()["detail"]


def test_platforms_endpoint_lists_xhs():
    r = client.get("/platforms")
    assert r.status_code == 200
    codes = [p["code"] for p in r.json()["platforms"]]
    assert "xhs" in codes


def test_dna_loader_normalizes():
    d = dna.load_dna("xhs")
    limits = dna.normalize_limits(d)
    assert limits["limits"]["body_chars_max"] >= 0
    assert limits["tags"]["count_max"] >= 0


def test_qa_rule_flags_overlong_body():
    dna_xhs = dna.load_dna("xhs")
    draft = DraftPayload(titles=["标题"], body="长" * 5000, tags=["干货"])
    qa = run_qa(dna_xhs, draft, _structured())
    assert qa.passed is False
    assert any("超长" in i for i in qa.issues)
