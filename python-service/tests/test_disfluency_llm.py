"""模型复核（`docs/tasks_disfluency.md` T4）。

三条要害，各有对应的用例：
1. 模型**只能否决或确认**规则的疑点，不能凭空加段
2. 模型**不给时间戳** —— 时间戳取自原句对（模型看不到音频）
3. 模型挂了要**降级并标明**，不是静默少做一类判定
"""
from __future__ import annotations

import pytest

from app.pipeline import disfluency as df


def seg(text, start, end):
    return {"text": text, "start": start, "end": end, "words": []}


def pair_segs():
    return [
        seg("这件事情的核心是执行。", 0.0, 2.5),
        seg("这件事的核心就在于执行。", 2.6, 5.0),
        seg("所以我们先把执行做好。", 5.2, 7.5),
    ]


def fake_chat(verdicts=None, *, exc=None):
    def call(prompt, **kwargs):
        if exc:
            raise exc
        call.prompt = prompt
        call.kwargs = kwargs
        return {"verdicts": verdicts if verdicts is not None else []}
    call.prompt = None
    call.kwargs = None
    return call


def test_llm_confirms_a_suspected_repeat():
    segs = pair_segs()
    low = df.find_similar_repeats(segs)
    assert len(low) == 1
    chat = fake_chat([{"index": 0, "same": True, "why": "都在说核心是执行"}])
    out, skipped = df.review_with_llm(segs, low, chat=chat)
    assert skipped is False
    assert len(out) == 1
    assert out[0].source == "llm" and out[0].confidence == "high"
    assert "都在说核心是执行" in out[0].evidence


def test_llm_can_reject_a_rule_suspicion():
    """模型**否决**规则的疑点 → 不标（模型的复核是有意义的，不是走过场）。"""
    segs = pair_segs()
    low = df.find_similar_repeats(segs)
    chat = fake_chat([{"index": 0, "same": False, "why": "是递进，不是重复"}])
    out, skipped = df.review_with_llm(segs, low, chat=chat)
    assert out == [] and skipped is False


def test_llm_failure_degrades_and_is_marked():
    segs = pair_segs()
    low = df.find_similar_repeats(segs)
    out, skipped = df.review_with_llm(segs, low, chat=fake_chat(exc=RuntimeError("网络断了")))
    assert out == []
    assert skipped is True        # **必须标明**，不能静默少做一类判定


def test_bad_json_shape_also_degrades():
    segs = pair_segs()
    low = df.find_similar_repeats(segs)
    chat = fake_chat()
    chat = lambda prompt, **kw: {"nonsense": 1}      # noqa: E731
    out, skipped = df.review_with_llm(segs, low, chat=chat)
    assert out == [] and skipped is True


def test_only_suspect_pairs_are_sent():
    """发给模型的内容**只含可疑句对**，不含全文（成本与跑偏都靠这条控住）。"""
    segs = pair_segs()
    low = df.find_similar_repeats(segs)
    chat = fake_chat([{"index": 0, "same": True, "why": "x"}])
    df.review_with_llm(segs, low, chat=chat)
    prompt = chat.prompt
    assert "这件事情的核心是执行" in prompt and "这件事的核心就在于执行" in prompt
    # 第三句跟谁都不像，不该被塞进去
    assert "所以我们先把执行做好" not in prompt


def test_timestamps_come_from_the_segment_not_the_model():
    """模型给的时间戳（如果它硬给）**一律忽略** —— 它看不到音频。"""
    segs = pair_segs()
    low = df.find_similar_repeats(segs)
    chat = fake_chat([{"index": 0, "same": True, "why": "x",
                       "start": 999.0, "end": 1000.0}])
    out, _ = df.review_with_llm(segs, low, chat=chat)
    assert out[0].start == 2.6 and out[0].end == 5.0      # 取自原句对，不是 999


def test_high_confidence_findings_are_not_sent():
    """完全重复这类高置信的，不需要花模型的钱。"""
    segs = [seg("我们要讲的是自由。", 0.0, 2.0), seg("我们要讲的是自由。", 2.1, 4.0)]
    findings = df.rule_findings(segs)
    chat = fake_chat([])
    out, skipped = df.review_with_llm(segs, findings, chat=chat)
    assert out == [] and skipped is False
    assert chat.prompt is None            # 一次都没调


def test_disabled_does_not_call_the_model():
    segs = pair_segs()
    low = df.find_similar_repeats(segs)
    chat = fake_chat([{"index": 0, "same": True, "why": "x"}])
    out, skipped = df.review_with_llm(segs, low, chat=chat, enabled=False)
    assert out == [] and skipped is False and chat.prompt is None


def test_unknown_index_is_ignored():
    """模型编一个不存在的 index → 忽略，不崩、不产生凭空的时间段。"""
    segs = pair_segs()
    low = df.find_similar_repeats(segs)
    chat = fake_chat([{"index": 7, "same": True, "why": "x"}])
    out, skipped = df.review_with_llm(segs, low, chat=chat)
    assert out == [] and skipped is False
