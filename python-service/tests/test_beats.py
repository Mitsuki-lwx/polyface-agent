"""T5 节拍与卡点（`docs/checklist_b1.md` §6）。

对齐逻辑都是**纯函数** —— 不需要 librosa 就能测。只有真正的节拍检测要跳过。
"""
from __future__ import annotations

import pytest

from app.pipeline import beats


def _grid(step: float, n: int) -> list[float]:
    return [round(i * step, 3) for i in range(n)]


# ---------------- 可选依赖降级 ----------------

def test_describe_reports_or_hints():
    d = beats.describe()
    assert set(d) >= {"available", "backend", "hint"}
    if d["available"]:
        assert d["backend"] == "librosa" and d["hint"] == ""
    else:
        assert d["hint"], "不可用时必须说明**未做卡点**"


def test_forced_off(monkeypatch):
    monkeypatch.setenv("POLYFACE_BEATS", "none")
    assert beats.is_available() is False


def test_detect_without_librosa_raises_but_is_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setenv("POLYFACE_BEATS", "none")
    with pytest.raises(beats.BeatsUnavailable) as e:
        beats.detect(tmp_path / "x.wav")
    assert "卡点" in str(e.value)         # 是给人看的说明，不是栈


# ---------------- 对齐占比（必须**算出来**）----------------

def test_aligned_ratio_counts_within_tolerance():
    grid = _grid(0.5, 10)                 # 0, 0.5, 1.0, ...
    assert beats.aligned_ratio([0.0, 1.0, 2.0], grid, tolerance=0.12) == 1.0
    # 偏移 0.3 超出了 0.12 的窗口
    assert beats.aligned_ratio([0.3, 1.3], grid, tolerance=0.12) == 0.0
    # 放宽窗口就能命中 —— 证明这个数是**算出来的**，不是写死的
    assert beats.aligned_ratio([0.3, 1.3], grid, tolerance=0.35) == 1.0


def test_aligned_ratio_empty():
    assert beats.aligned_ratio([], _grid(0.5, 4)) == 0.0
    assert beats.aligned_ratio([1.0], []) == 0.0


# ---------------- 配音优先：只平移 BGM，不改时长 ----------------

def test_best_offset_improves_alignment():
    """构造一个"原本错开、平移后能对上"的局面。"""
    grid = _grid(0.5, 60)                 # 0, 0.5, ..., 29.5
    boundaries = [2.0, 4.0, 6.0]          # 都落在 0.5 的整数倍上 → 本来就对得上
    off, ratio = beats.best_offset(boundaries, grid, tolerance=0.12)
    assert ratio == 1.0

    # 边界整体偏 0.2（不在节拍上）。把 BGM **往前挪** 0.3s 就能全部对上：
    # 2.2-(-0.3)=2.5、4.2+0.3=4.5、6.2+0.3=6.5 —— 都在节拍上
    shifted = [b + 0.2 for b in boundaries]
    off2, ratio2 = beats.best_offset(shifted, grid, tolerance=0.12)
    assert ratio2 == 1.0, f"应当找到能对上的偏移，实际 {off2} → {ratio2}"
    assert off2 == pytest.approx(-0.3, abs=1e-6), \
        "正偏移意味着 BGM 晚开始（开头留一段静音），所以优先取负偏移"


def test_best_offset_prefers_no_silent_gap():
    """`off > tolerance` 会留一段静音开头 —— 不该选它（除非实在对不上）。"""
    grid = _grid(0.5, 60)
    boundaries = [2.2, 4.2]               # 加 0.2 能对上，减 0.3 也能对上
    off, ratio = beats.best_offset(boundaries, grid, tolerance=0.12)
    assert ratio == 1.0
    assert off <= 0.12, f"不该选一个会留静音开头的正偏移：{off}"


def test_best_offset_never_changes_boundaries():
    """配音优先的**全部要点**：时长不动。"""
    grid = _grid(0.5, 60)
    boundaries = [3.1, 7.3, 11.9]
    before = list(boundaries)
    beats.best_offset(boundaries, grid)
    assert boundaries == before


def test_best_offset_does_not_jump_far_into_the_track():
    """偏移不能大到"这段音乐听起来像是从中间开始" —— 搜索限在 window 内。"""
    grid = _grid(0.5, 200)                # 长到 100s
    off, _ = beats.best_offset([50.0], grid, window=5.0)
    assert abs(off) <= 5.0


# ---------------- 卡点优先：允许改时长 ----------------

def test_snap_moves_boundaries_onto_beats():
    grid = _grid(0.5, 60)
    out = beats.snap_durations([2.1, 2.1, 2.1], grid, tolerance=0.12)
    assert sum(out) == pytest.approx(6.3, abs=0.05), "总时长不该变（配音总量不变）"
    # 第一、二个边界应当被挪到 2.0 / 4.0 附近（节拍上）
    b1 = out[0]
    b2 = out[0] + out[1]
    assert min(abs(b1 - t) for t in grid) <= 0.12
    assert min(abs(b2 - t) for t in grid) <= 0.12


def test_snap_keeps_total_and_avoids_tiny_segments():
    grid = _grid(0.5, 60)
    out = beats.snap_durations([0.6, 0.6, 0.6], grid, min_sec=0.5)
    assert all(d >= 0.5 for d in out), f"不该产出碎段：{out}"
    assert sum(out) == pytest.approx(1.8, abs=0.05)


def test_snap_without_beats_is_identity():
    assert beats.snap_durations([1.0, 2.0], []) == [1.0, 2.0]


def test_two_strategies_really_differ():
    """**两种策略必须真的不同** —— checklist §6 的核心断言。"""
    grid = _grid(0.5, 60)
    durations = [2.13, 2.13, 2.13]

    off, ratio_voice = beats.best_offset([sum(durations[:i]) for i in (1, 2, 3)], grid)
    snapped = beats.snap_durations(durations, grid)
    bounds_beat = [snapped[0], snapped[0] + snapped[1]]
    ratio_beat = beats.aligned_ratio(bounds_beat, grid)

    assert snapped != durations, "卡点优先应当改变时长"
    assert ratio_beat >= ratio_voice, "卡点优先的对齐度不该更差"
