"""按场景切段（`docs/tasks_scene_split.md` T1~T3）。

检测那部分要**真跑 ffmpeg/PySceneDetect**（合成一段三段纯色的视频）——
切点位置对不对是这类功能唯一的硬指标，mock 掉就什么都没验。
吸附与合并是纯逻辑，直接喂数据。
"""
from __future__ import annotations

import subprocess

import pytest

from app.pipeline import scenes as sc


def _make_video(path, colors=("red", "blue", "green"), secs=2.0, size="320x240"):
    """合成一段多场景视频（每段纯色）。返回总时长。"""
    parts = []
    for i, c in enumerate(colors):
        parts.append(f"color=c={c}:s={size}:d={secs}:r=10")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for p in parts:
        cmd += ["-f", "lavfi", "-i", p]
    cmd += ["-filter_complex", f"concat=n={len(colors)}:v=1:a=0", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return secs * len(colors)


# ---------------------------------------------------------------- 1) 检测


def test_detects_cuts_in_a_multi_scene_video(tmp_path):
    p = tmp_path / "three.mp4"
    total = _make_video(p)
    cuts = sc.detect_scene_cuts(p)
    assert len(cuts) >= 2, f"三段纯色应至少检出 2 个切点，实际 {cuts}"
    # 真实边界在 2.0s 与 4.0s
    assert abs(cuts[0] - 2.0) < 0.5
    assert abs(cuts[1] - 4.0) < 0.5
    assert total > 0


def test_single_scene_video_has_no_cuts(tmp_path):
    p = tmp_path / "one.mp4"
    _make_video(p, colors=("red",))
    assert sc.detect_scene_cuts(p) == []


def test_detection_is_deterministic(tmp_path):
    p = tmp_path / "d.mp4"
    _make_video(p)
    assert sc.detect_scene_cuts(p) == sc.detect_scene_cuts(p)


def test_threshold_is_configurable(tmp_path):
    """调高阈值 → 切点不会变多（敏感度确实是可调的）。"""
    p = tmp_path / "t.mp4"
    _make_video(p, colors=("red", "blue", "red", "blue"), secs=1.0)
    loose = sc.detect_scene_cuts(p, threshold=60.0)
    tight = sc.detect_scene_cuts(p, threshold=10.0)
    assert len(tight) >= len(loose)


def test_missing_dependency_raises_with_install_hint(monkeypatch):
    """**缺依赖要报错，不是返回空列表** —— 空列表会被误读成"这条视频没有切点"。"""
    monkeypatch.setattr(sc, "is_available", lambda: False)
    with pytest.raises(RuntimeError, match="scenedetect"):
        sc.detect_scene_cuts("whatever.mp4")


# ---------------------------------------------------------------- 2) 吸附


def test_cut_in_the_middle_of_speech_is_snapped_to_a_boundary():
    """**这条是吸附存在的理由**：画面切了但话没说完。"""
    speech = [0.0, 3.0, 6.0, 9.0, 12.0]
    r = sc.align_to_speech([7.4], speech)          # 切点在 7.4，最近的语音边界是 6.0
    assert r.cuts == [6.0]
    assert r.snapped == 1 and r.speech_aligned is True


def test_snap_does_not_move_beyond_the_limit():
    """挪过头会让两段都不完整 —— 超过上限就**不吸**。"""
    r = sc.align_to_speech([10.0], [0.0, 1.0, 20.0], max_shift_sec=2.0)
    assert r.cuts == [10.0]
    assert r.snapped == 0


def test_no_speech_bounds_is_marked_not_silent():
    """无音轨/缺 ASR → 原样返回 + **标记**这一步没做（不静默）。"""
    r = sc.align_to_speech([3.0, 7.0], [])
    assert r.cuts == [3.0, 7.0]
    assert r.speech_aligned is False


def test_snapped_cuts_stay_sorted_and_unique():
    r = sc.align_to_speech([5.1, 5.2, 8.0], [5.0, 8.0], max_shift_sec=1.0)
    assert r.cuts == [5.0, 8.0]


def test_speech_bounds_from_segments_uses_segment_edges():
    segs = [{"start": 0.0, "end": 3.0}, {"start": 3.4, "end": 7.0}]
    assert sc.speech_bounds_from_segments(segs) == [3.0, 3.4, 7.0]


# ---------------------------------------------------------------- 3) 合并


def test_short_segment_is_merged_into_the_previous_one():
    """**并入前一段，不是丢掉** —— 丢掉是有损的，用户可能正是想要那个空镜。

    这里的 4.0–5.0（1 秒）前一段够长，所以并进 (0,4) → (0,5)。
    """
    segs = sc.plan_scenes([4.0, 5.0, 10.0], total_sec=12.0, min_sec=3.0)
    assert segs == [(0.0, 5.0), (5.0, 12.0)]


def test_first_short_segment_merges_forward():
    """首段太短、又没有前一段可并 → 并入**后一段**。"""
    segs = sc.plan_scenes([1.0], total_sec=10.0, min_sec=3.0)
    assert segs == [(0.0, 10.0)]


def test_total_length_is_conserved():
    """合并只能改变"在哪切"，不能改变总长 —— 守恒是硬指标。"""
    cuts = [1.0, 1.4, 4.0, 4.2, 4.5, 9.0]
    segs = sc.plan_scenes(cuts, total_sec=12.0, min_sec=2.0)
    assert abs(sum(e - s for s, e in segs) - 12.0) < 1e-6
    assert segs[0][0] == 0.0 and segs[-1][1] == 12.0


def test_segments_are_sorted_and_non_overlapping():
    segs = sc.plan_scenes([2.0, 5.0, 5.0, 8.0], total_sec=10.0, min_sec=1.0)
    for (s1, e1), (s2, e2) in zip(segs, segs[1:]):
        assert e1 <= s2
    assert all(e > s for s, e in segs)


def test_min_sec_changes_the_count():
    cuts = [1.0, 2.0, 3.0, 8.0]
    assert len(sc.plan_scenes(cuts, 10.0, min_sec=0.5)) == 5
    assert len(sc.plan_scenes(cuts, 10.0, min_sec=4.0)) < 5


def test_out_of_range_cuts_are_ignored():
    segs = sc.plan_scenes([-1.0, 5.0, 99.0], total_sec=10.0, min_sec=1.0)
    assert segs == [(0.0, 5.0), (5.0, 10.0)]


def test_zero_duration_is_empty():
    assert sc.plan_scenes([1.0], total_sec=0.0) == []


# ---------------------------------------------------------------- 汇总


def test_plan_returns_segments_and_metadata(tmp_path):
    p = tmp_path / "full.mp4"
    total = _make_video(p)
    segs = [{"start": 0.0, "end": 2.0}, {"start": 2.0, "end": 4.0}]
    pl = sc.plan(p, total, segments=segs, min_sec=0.5)
    d = pl.as_dict()
    assert d["count"] == len(pl.segments) >= 2
    assert d["speech_aligned"] is True
    assert "PySceneDetect" in d["detector"]        # 检测器版本要能复核
    assert d["params"]["threshold"] == sc.DEFAULT_THRESHOLD
    assert all("duration" in s for s in d["segments"])


def test_plan_without_speech_is_marked(tmp_path):
    p = tmp_path / "nospeech.mp4"
    total = _make_video(p)
    pl = sc.plan(p, total, segments=[], min_sec=0.5)
    assert pl.speech_aligned is False
