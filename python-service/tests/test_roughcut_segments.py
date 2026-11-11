"""粗剪保留段（剪辑联动用）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.roughcut import kept_segments, segment_midpoints


def test_kept_segments_is_complement_of_cuts():
    assert kept_segments([(2.0, 3.0), (6.0, 7.5)], 10.0) == [(0.0, 2.0), (3.0, 6.0), (7.5, 10.0)]


def test_kept_segments_handles_edge_and_overlap():
    # 开头的刀、结尾的刀、以及重叠的刀都要正确处理
    assert kept_segments([(0.0, 1.0)], 5.0) == [(1.0, 5.0)]
    assert kept_segments([(4.0, 5.0)], 5.0) == [(0.0, 4.0)]
    assert kept_segments([(1.0, 2.0), (1.5, 3.0)], 5.0) == [(0.0, 1.0), (3.0, 5.0)]


def test_kept_segments_drops_degenerate():
    assert kept_segments([], 5.0) == [(0.0, 5.0)]
    assert kept_segments([(0.0, 5.0)], 5.0) == []


def test_midpoints():
    assert segment_midpoints([(0.0, 2.0), (3.0, 6.0)]) == [1.0, 4.5]


# ---------------------------------------------------------------- 场景过滤（默认按业界做法）

def test_scene_filter_skips_mid_sentence_pause():
    """**踩到的草率之处**：见静音就剪会把一句话切碎 ✗。

    规则：停顿 ≥ long_pause（1.5s）一定剪；否则只有贴着画面切换才剪。
    """
    from app.pipeline.roughcut import plan_cuts

    pauses = [(3.0, 4.0), (10.0, 11.0), (30.0, 32.0)]   # 1.0s / 1.0s / 2.0s
    scenes = [11.2]                                      # 只有第二处旁边切了镜头

    # 无场景 → 旧行为：三处都剪
    assert len(plan_cuts(pauses, 0.08, 60)) == 3
    # 有场景 → 3.0 那处镜头没变、停得也不久 → 不剪 ✓
    got = plan_cuts(pauses, 0.08, 60, scenes=scenes)
    assert got == [(10.08, 10.92), (30.08, 31.92)], got


def test_long_pause_cuts_even_without_scene_change():
    from app.pipeline.roughcut import plan_cuts

    assert plan_cuts([(5.0, 7.0)], 0.08, 60, scenes=[]) == [(5.08, 6.92)]
    assert plan_cuts([(5.0, 6.0)], 0.08, 60, scenes=[]) == []


def test_scene_window_boundary():
    from app.pipeline.roughcut import plan_cuts

    # 停顿 9.5±0.5 → 起点 9.08，距切换 9.5 只有 0.42 ≤ 0.60 → 剪
    assert plan_cuts([(9.0, 10.0)], 0.08, 60, scenes=[9.5]) == [(9.08, 9.92)]
    # 切换点 20.0 离它 10 秒 → 不剪
    assert plan_cuts([(9.0, 10.0)], 0.08, 60, scenes=[20.0]) == []
