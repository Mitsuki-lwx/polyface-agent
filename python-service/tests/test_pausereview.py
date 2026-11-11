"""停顿复核（openspec/changes/pause-review-cuts）。

只测**会被用户看见的行为**：
- 候选怎么选（下限、上限、上下文、rule_cut）
- 默认关 → 一次模型都不调
- 降级 → llm_skipped=True，而不是静默当作"模型说没问题"
- 判定怎么改剪点（两个方向 + 边界）
"""

import pytest

from app.pipeline import pausereview as pr
from app.pipeline import prompts


SENTENCES = [
    {"start": 0.0, "end": 2.5, "text": "这件事我想了很久。"},
    {"start": 5.6, "end": 9.0, "text": "你只需要做一件事。"},
]


def test_scene_is_registered():
    """版本号那一层会扫 `scene="..."`；漏登记就红（实测踩过）。"""
    assert prompts.SCENE_BUILDERS["pause_review"].endswith("build_pause_review_prompt")


def test_candidates_filter_short_pauses_and_attach_context():
    pauses = [(1.0, 1.2), (3.0, 5.4)]
    cuts = [(3.08, 5.32)]
    cands = pr.build_candidates(pauses, cuts, SENTENCES, margin=0.08, min_sec=0.6)
    assert len(cands) == 1
    c = cands[0]
    assert c.rule_cut is True
    assert c.prev == "这件事我想了很久。"
    assert c.nxt == "你只需要做一件事。"
    assert c.gap == pytest.approx(2.4)


def test_rule_cut_matches_with_margin():
    """`rule_cut` 要和真正的剪点对得上 —— 对不上就等于告诉模型错的前提。"""
    pauses = [(3.0, 5.4)]
    cands = pr.build_candidates(pauses, [(3.08, 5.32)], None, margin=0.08, min_sec=0.6)
    assert cands[0].rule_cut is True
    cands = pr.build_candidates(pauses, [(3.5, 5.32)], None, margin=0.08, min_sec=0.6)
    assert cands[0].rule_cut is False


def test_candidate_cap_keeps_longest_then_sorts_by_time():
    pauses = [(0.0, 0.7), (10.0, 10.9), (20.0, 23.5), (30.0, 31.0)]
    cands = pr.build_candidates(pauses, [], None, min_sec=0.6, max_candidates=2)
    # 最长两条是 3.5s(20s) 与 1.0s(30s)；0.9s(10s) 被挤掉
    assert [round(c.gap, 1) for c in cands] == [3.5, 1.0]
    assert [c.id for c in cands] == [0, 1]
    assert cands[0].start == 20.0 and cands[1].start == 30.0


def test_default_off_calls_nothing():
    called = []

    def chat(*a, **k):
        called.append(1)
        raise AssertionError("默认关时不该调用模型")

    cands = pr.build_candidates([(3.0, 5.4)], [], SENTENCES, min_sec=0.6)
    verdicts, skipped = pr.review_candidates(cands, chat=chat, enabled=False)
    assert verdicts == [] and skipped is False and called == []


def test_no_candidates_calls_nothing():
    called = []

    def chat(*a, **k):
        called.append(1)
        return {"verdicts": []}

    verdicts, skipped = pr.review_candidates([], chat=chat, enabled=True)
    assert verdicts == [] and skipped is False and called == []


def test_model_failure_degrades_loudly():
    def chat(*a, **k):
        raise RuntimeError("429")

    cands = pr.build_candidates([(3.0, 5.4)], [], SENTENCES, min_sec=0.6)
    verdicts, skipped = pr.review_candidates(cands, chat=chat, enabled=True)
    assert verdicts == [] and skipped is True
    assert "未执行" in pr.format_report({"candidates": cands, "verdicts": [],
                                         "llm_skipped": True})


def test_bad_shape_degrades_loudly():
    cands = pr.build_candidates([(3.0, 5.4)], [], SENTENCES, min_sec=0.6)
    verdicts, skipped = pr.review_candidates(
        cands, chat=lambda *a, **k: {"nope": 1}, enabled=True)
    assert verdicts == [] and skipped is True


def test_unknown_id_and_bad_action_are_ignored():
    def chat(*a, **k):
        return {"verdicts": [
            {"id": 0, "action": "keep", "why": "强调"},
            {"id": 99, "action": "cut", "why": "不存在"},
            {"id": 1, "action": "maybe", "why": "非法动作"},
        ]}

    cands = pr.build_candidates([(3.0, 5.4), (8.0, 9.0)], [], SENTENCES, min_sec=0.6)
    verdicts, skipped = pr.review_candidates(cands, chat=chat, enabled=True)
    assert skipped is False
    assert verdicts == [{"id": 0, "action": "keep", "why": "强调"}]


def test_keep_removes_rule_cut():
    pauses = [(3.0, 5.4)]
    cuts = [(3.08, 5.32)]
    cands = pr.build_candidates(pauses, cuts, SENTENCES, margin=0.08, min_sec=0.6)
    new, applied = pr.apply_verdicts(cuts, cands, [{"id": 0, "action": "keep"}],
                                     margin=0.08, total=30.0)
    assert new == [] and applied == {"dropped": 1, "added": 0}


def test_cut_adds_interval_with_margin():
    pauses = [(3.0, 3.9)]                 # 0.9s：规则不剪（<1.5 且无场景切换）
    cands = pr.build_candidates(pauses, [], None, margin=0.08, min_sec=0.6)
    assert cands[0].rule_cut is False
    new, applied = pr.apply_verdicts([], cands, [{"id": 0, "action": "cut"}],
                                     margin=0.08, total=30.0)
    assert new == [(3.08, 3.82)] and applied == {"dropped": 0, "added": 1}


def test_cut_does_not_duplicate_existing_cut():
    pauses = [(3.0, 5.4)]
    cuts = [(3.08, 5.32)]
    cands = pr.build_candidates(pauses, cuts, None, margin=0.08, min_sec=0.6)
    new, applied = pr.apply_verdicts(cuts, cands, [{"id": 0, "action": "cut"}],
                                     margin=0.08, total=30.0)
    assert new == [(3.08, 5.32)] and applied == {"dropped": 0, "added": 0}


def test_verdict_without_candidate_is_ignored():
    new, applied = pr.apply_verdicts([(1.0, 2.0)], [], [{"id": 7, "action": "keep"}],
                                     margin=0.08, total=30.0)
    assert new == [(1.0, 2.0)] and applied == {"dropped": 0, "added": 0}
