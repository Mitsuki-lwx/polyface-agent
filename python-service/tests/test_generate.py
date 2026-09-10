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


def test_5_platforms_available():
    codes = [p["code"] for p in dna.list_platforms()]
    assert {"xhs", "douyin", "gzh", "zhihu", "bilibili"} <= set(codes)


def test_video_platform_returns_clip_sheet():
    """视频平台(douyin)应附带剪辑单 FR-50；图文平台(xhs)不应有。"""
    r = client.post(
        "/generate",
        json={"raw_text": SAMPLE, "platforms": ["douyin", "xhs"]},
    )
    assert r.status_code == 200
    by_code = {d["platform_code"]: d for d in r.json()["drafts"]}

    douyin = by_code["douyin"]
    assert douyin["draft"]["clip_sheet"] is not None
    scenes = douyin["draft"]["clip_sheet"]["scenes"]
    assert len(scenes) >= 1
    assert scenes[0]["script"] and scenes[0]["duration_hint"]

    assert by_code["xhs"]["draft"].get("clip_sheet") is None


def test_user_template_injected_in_mock():
    """ADR-014：用户模板 opening/closing 应体现在成稿中（mock）。"""
    tpl = {
        "name": "老张的开场",
        "voice": "直接不废话",
        "opening": "大家好，我是老张，今天说点干的。",
        "structure": ["先说结论", "给证据"],
        "closing": "关注老张，下期继续。",
    }
    r = client.post(
        "/generate",
        json={"raw_text": SAMPLE, "platforms": ["xhs"], "template": tpl},
    )
    assert r.status_code == 200
    body = r.json()["drafts"][0]["draft"]["body"]
    assert tpl["opening"] in body
    assert tpl["closing"] in body


def test_qa_rule_flags_overlong_body():
    dna_xhs = dna.load_dna("xhs")
    draft = DraftPayload(titles=["标题"], body="长" * 5000, tags=["干货"])
    qa = run_qa(dna_xhs, draft, _structured())
    assert qa.passed is False
    assert any("超长" in i for i in qa.issues)


# ============ M4: 创作者画像(FR-32) + 复盘回写(FR-33) ============

def test_creator_profile_appears_in_rationale():
    """画像字段应出现在 brief.rationale（mock）。"""
    r = client.post(
        "/generate",
        json={
            "raw_text": SAMPLE, "platforms": ["xhs"],
            "creator_profile": {"brand_voice": "理性干货", "domain": "自由职业",
                                "audience": "职场人", "avoid": "不要AI味"},
        },
    )
    assert r.status_code == 200
    rationale = r.json()["drafts"][0]["brief"]["rationale"]
    assert "领域=自由职业" in rationale
    assert "声音=理性干货" in rationale
    assert "受众=职场人" in rationale


def test_creator_profile_avoid_appears_in_draft_body():
    """画像中的 avoid 应在 mock 成稿正文中以 ⚠️ 段呈现。"""
    r = client.post(
        "/generate",
        json={
            "raw_text": SAMPLE, "platforms": ["xhs"],
            "creator_profile": {"brand_voice": "", "domain": "", "audience": "",
                                "avoid": "不要堆砌AI词"},
        },
    )
    assert r.status_code == 200
    body = r.json()["drafts"][0]["draft"]["body"]
    assert "不要堆砌AI词" in body
    assert "⚠️" in body
    # rationale 含"已对齐创作者领域"
    rationale = r.json()["drafts"][0]["draft"]["rationale"]
    # domain 为空时不应有"已对齐"
    assert "已对齐创作者领域" not in rationale


def test_retrospect_hints_in_rationale_and_draft():
    """复盘建议应同时出现在 brief.rationale 与 draft.rationale。"""
    hints = ["[douyin] 短钩子效果最好", "[xhs] 长文首屏要加图"]
    r = client.post(
        "/generate",
        json={"raw_text": SAMPLE, "platforms": ["douyin"], "retrospect_hints": hints},
    )
    assert r.status_code == 200
    d = r.json()["drafts"][0]
    assert "[douyin] 短钩子效果最好" in d["brief"]["rationale"]
    assert "已应用2条历史经验" in d["draft"]["rationale"]


def test_profile_and_retro_compose_clean():
    """空画像 + 空复盘 → 与不传等价（不破已有行为）。"""
    r1 = client.post(
        "/generate",
        json={"raw_text": SAMPLE, "platforms": ["xhs"]},
    )
    r2 = client.post(
        "/generate",
        json={"raw_text": SAMPLE, "platforms": ["xhs"],
              "creator_profile": {}, "retrospect_hints": []},
    )
    assert r1.status_code == r2.status_code == 200
    # 都不应出现画像/复盘标记
    r2_draft = r2.json()["drafts"][0]["draft"]
    assert "已对齐创作者领域" not in r2_draft["rationale"]
    assert "已应用" not in r2_draft["rationale"]


# ============ M5: 模板进入真实模式 prompt payload（FR-64 / checklist E5） ============

def test_template_carried_into_brief_and_draft_prompts():
    """真实模式：用户模板字段必须进入 brief/draft 的 prompt payload。"""
    from app import dna as dna_lib
    from app.pipeline.prompts import build_brief_prompt, build_draft_prompt
    from app.schemas_gen import Brief

    d = dna_lib.load_dna("xhs")
    mat = _structured()
    tpl = {"name": "E2E模板", "voice": "直接", "opening": "【开头】",
           "structure": ["S1"], "closing": "【结尾】", "tag_style": "短", "taboo": ["AI味"]}
    brief = Brief(platform_code="xhs", angle="a", hooks=["h"],
                  structure_plan="sp", tag_direction=["t"], rationale="r")

    bp = build_brief_prompt(d, mat, None, tpl)
    dp = build_draft_prompt(d, mat, brief, None, tpl)
    for prompt in (bp, dp):
        assert "user_template" in prompt
        assert "E2E模板" in prompt
        assert "【开头】" in prompt and "【结尾】" in prompt

    # 不传模板时不应出现该字段（保持向后兼容）
    assert "user_template" not in build_brief_prompt(d, mat, None, None)
