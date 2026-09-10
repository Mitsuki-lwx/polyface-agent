"""FR-63 示例学习测试：mock 规则拆解 + learn prompt + /learn 端点。"""
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.learn import first_clause, guess_voice, run_learn, split_paragraphs
from app.pipeline.prompts import build_learn_prompt
from app.schemas_gen import LearnRequest

client = TestClient(app)

SAMPLE = (
    "我以前也踩过这个坑，做了半年没什么起色。\n\n"
    "后来我发现问题不在努力，而在选题方向。\n\n"
    "第一步是先看数据反馈，找出真正有人看的角度。\n\n"
    "第二步是聚焦单一方向，不要什么都写。\n\n"
    "第三步是持续复盘迭代，每篇都做小结。\n\n"
    "你遇到过类似的情况吗？评论区聊聊。"
)

SAMPLE_NO_INTERACTION = (
    "这个方法我用了三年，效果一直很稳定。\n\n"
    "第一步是明确目标，把大目标拆成可执行的小步骤。\n\n"
    "第二步是每周复盘一次，记录哪些做法有效。\n\n"
    "以上就是完整流程，按步骤执行即可。"
)


def test_learn_endpoint_returns_decomposed_template():
    r = client.post("/learn", json={"sample_text": SAMPLE, "source_note": "9月爆款"})
    assert r.status_code == 200
    body = r.json()
    assert body["used_mock"] is True
    t = body["template"]
    assert t["name"] == "学习：9月爆款"
    assert t["opening"].startswith("我以前也踩过这个坑")
    assert len(t["structure"]) >= 1
    assert t["closing"].startswith("你遇到过类似的情况吗")
    assert t["rationale"]


def test_mock_structure_items_are_short_phrases():
    learned, _ = run_learn(LearnRequest(sample_text=SAMPLE, source_note=None))
    assert learned.structure
    for item in learned.structure:
        assert len(item) <= 15, item
    assert len(learned.structure) <= 6


def test_mock_closing_empty_without_interaction():
    learned, _ = run_learn(LearnRequest(sample_text=SAMPLE_NO_INTERACTION, source_note=None))
    assert learned.closing == ""
    assert learned.opening.startswith("这个方法我用了三年")


def test_guess_voice_variants():
    assert guess_voice("我觉得这件事值得说说") == "真诚分享"
    assert guess_voice("这里有三步方法可以拆解") == "理性干货"
    assert guess_voice("大家都错了，真相是这样的") == "犀利观点"
    assert guess_voice("今天天气不错") == "通用"


def test_learn_name_falls_back_to_date():
    learned, _ = run_learn(LearnRequest(sample_text=SAMPLE, source_note=None))
    assert learned.name.startswith("学习：示例（")


def test_learn_prompt_carries_sample_only():
    p = build_learn_prompt(SAMPLE, "9月爆款")
    assert "sample_text" in p
    assert "9月爆款" in p
    assert "我以前也踩过这个坑" in p
    # 未传备注时不应出现 source_note 字段
    assert "source_note" not in build_learn_prompt("短示例", None)


def test_split_paragraphs_falls_back_to_lines():
    assert len(split_paragraphs("一行\n二行\n三行")) == 3
    assert split_paragraphs("") == []


def test_first_clause_strips_ordinal_and_bullet():
    assert first_clause("1. 先看数据反馈，再决定方向。") == "先看数据反馈"
    assert first_clause("- 聚焦单一方向") == "聚焦单一方向"
