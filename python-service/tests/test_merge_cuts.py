"""剪点合并与边界回退（`docs/tasks_disfluency.md` T5）。

为什么这些值得单测：剪点错了**肉眼可见**（画面跳、吞字、黑帧），
而这类错误在"只看代码"时完全看不出来。
"""
from __future__ import annotations

from app.pipeline import roughcut as rc


def test_overlapping_cuts_are_merged():
    out = rc.merge_cuts([[(1.0, 3.0)], [(2.0, 4.0)]])
    assert out == [(1.0, 4.0)]


def test_adjacent_cuts_are_joined():
    """间隔 0.03s（小于一帧多一点）→ 当成一段，否则中间会留一小截画面。"""
    out = rc.merge_cuts([[(1.0, 2.0)], [(2.03, 3.0)]])
    assert out == [(1.0, 3.0)]


def test_far_apart_cuts_stay_separate():
    out = rc.merge_cuts([[(1.0, 2.0)], [(5.0, 6.0)]])
    assert out == [(1.0, 2.0), (5.0, 6.0)]


def test_short_cuts_are_dropped():
    """剪掉 0.05 秒没有意义（听不出来），还多一次拼接。"""
    out = rc.merge_cuts([[(1.0, 1.05)], [(5.0, 6.0)]], min_sec=0.2)
    assert out == [(5.0, 6.0)]


def test_min_sec_zero_keeps_everything():
    out = rc.merge_cuts([[(1.0, 1.05)]], min_sec=0.0)
    assert out == [(1.0, 1.05)]


def test_result_is_sorted_and_non_overlapping():
    out = rc.merge_cuts([[(5.0, 6.0)], [(1.0, 2.0)], [(1.5, 2.5)], [(10.0, 11.0)]])
    assert out == [(1.0, 2.5), (5.0, 6.0), (10.0, 11.0)]
    for (s1, e1), (s2, e2) in zip(out, out[1:]):
        assert e1 <= s2


def test_empty_inputs_are_fine():
    assert rc.merge_cuts([]) == []
    assert rc.merge_cuts([[], []]) == []


def test_total_clamps_the_end():
    out = rc.merge_cuts([[(9.0, 12.0)]], total=10.0)
    assert out == [(9.0, 10.0)]


# ---------------------------------------------------------------- 边界回退


def test_shrink_retreats_both_edges():
    """两侧各退回 → 最多多留一点拖沓，不会吞掉下一个字。"""
    out = rc.shrink_cuts([(1.0, 3.0)], margin=0.15)
    assert out == [(1.15, 2.85)]


def test_shrink_drops_intervals_that_vanish():
    out = rc.shrink_cuts([(1.0, 1.2)], margin=0.15)
    assert out == []


def test_shrink_never_extends_a_cut():
    """回退只能**缩小**，绝不能把剪点撑大 —— 撑大就会剪掉正话。"""
    cuts = [(1.0, 3.0), (5.0, 5.3)]
    out = rc.shrink_cuts(cuts, margin=0.1)
    for (s, e), (os_, oe) in zip(out, cuts):
        assert s >= os_ and e <= oe


def test_merged_cuts_do_not_swallow_speech():
    """合并后的剪点必须落在原来的剪点范围内（不侵入语音）。

    这条是"宁少剪"的形式化表述：合并/回退都是**收缩性**操作。
    """
    pauses = [(1.0, 3.0), (10.0, 12.0)]
    redundant = [(3.0, 4.5), (12.0, 13.2)]
    merged = rc.merge_cuts([pauses, redundant])
    assert merged == [(1.0, 4.5), (10.0, 13.2)]
    shrunk = rc.shrink_cuts(merged, margin=0.1)
    for (s, e) in shrunk:
        assert any(s >= a and e <= b for a, b in merged)


def test_shrink_respects_total():
    out = rc.shrink_cuts([(9.0, 12.0)], margin=0.1, total=10.0)
    assert out == [(9.1, 10.0)]
