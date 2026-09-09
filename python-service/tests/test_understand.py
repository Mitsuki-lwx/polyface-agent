"""understand 阶段单测：mock 模式（离线，无需 API Key）。"""
from fastapi.testclient import TestClient

from app import llm
from app.main import app

client = TestClient(app)

SAMPLE_TEXT = (
    "我2023年从互联网大厂裸辞后开始做自由职业，靠自媒体写作月收入从0涨到3万。"
    "自由职业第一年最难的是自律和时间管理，我尝试过番茄钟、OKR、周复盘三种方法。"
    "其中周复盘对内容创作者最有帮助，因为它能让你看清哪类选题的数据最好。"
    "很多人以为自由职业就是躺平，其实比上班更累，但时间自由是真的。"
    "如果你也想尝试，建议先存够6个月生活费再辞职。"
)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "polyface-llm"
    assert body["mock"] is True  # 测试环境无 Key 应回退 mock


def test_analyze_mock_returns_structured():
    r = client.post("/analyze", json={"raw_text": SAMPLE_TEXT, "source_kind": "长文"})
    assert r.status_code == 200
    body = r.json()
    assert body["used_mock"] is True
    s = body["structured"]
    assert s["core_message"]
    assert len(s["facts"]) > 0
    for f in s["facts"]:
        assert f["text"]
        assert f["type"] in {"data", "story", "opinion"}


def test_analyze_requires_text():
    r = client.post("/analyze", json={"raw_text": "", "source_kind": "长文"})
    assert r.status_code == 422


def test_facts_classify_data_and_story():
    """含数字的句子应标为 data，含'我/当时'的叙述应标为 story。"""
    r = client.post("/analyze", json={"raw_text": SAMPLE_TEXT})
    facts = r.json()["structured"]["facts"]
    types = {f["type"] for f in facts}
    assert "data" in types
    assert "story" in types


def test_parse_json_handles_fenced_output():
    raw = '```json\n{"core_message": "hello", "facts": []}\n```'
    obj = llm.parse_json(raw)
    assert obj["core_message"] == "hello"
