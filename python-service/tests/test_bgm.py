"""带 BGM 的口播（`docs/spec_bgm.md`、`docs/checklist_bgm.md`）。

这个模块原先**一个测试都没有** ✗ —— 核心结论（"带不带 BGM，停顿判定基本一致"）
只在 2026-10-17 手工对拍过一次，没有回归防护。
这份文件补的是**能机器钉住的那部分**：纯函数、三态判定、失败要说清。
"""
from __future__ import annotations

import math
import struct
import wave

import pytest

from app.pipeline import bgm


# ---------------------------------------------------------------- 造素材

def _wav(path, segments, rate: int = 16000):
    """`segments`: [(时长秒, 频率Hz 或 None=静音, 幅度 0..1)]"""
    frames = bytearray()
    for sec, hz, amp in segments:
        n = int(rate * sec)
        for i in range(n):
            v = 0 if hz is None else int(amp * 32767 * math.sin(2 * math.pi * hz * i / rate))
            frames += struct.pack("<h", max(-32768, min(32767, v)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


# ---------------------------------------------------------------- 抽伴奏的窗口（T4）

def test_pick_bed_clip_takes_the_longest():
    """**选最长的**：伴奏与原曲同相位同音色，越长越不容易听出循环周期。"""
    wins = [(0.0, 1.0), (5.0, 9.0), (12.0, 15.5), (20.0, 21.0)]   # 依次 1.0 / 4.0 / 3.5 / 1.0
    assert bgm.pick_bed_clip(wins, min_loop_sec=2.0) == (5.0, 9.0)


def test_pick_bed_clip_skips_windows_shorter_than_min_loop():
    """太短的窗口**不用** —— 2 秒的伴奏垫满 20 秒会听出明显的循环。"""
    assert bgm.pick_bed_clip([(0.0, 1.0), (3.0, 3.5)], min_loop_sec=2.0) is None
    assert bgm.pick_bed_clip([(4.0, 6.5), (10.0, 11.9)], min_loop_sec=2.0) == (4.0, 6.5)


def test_pick_bed_clip_returns_none_rather_than_stretching():
    """一个够长的窗口都没有 → **明确失败**，不硬凑（"不硬凑"这条是判据）。"""
    assert bgm.pick_bed_clip([]) is None
    assert bgm.pick_bed_clip([(0.0, 1.99)], min_loop_sec=2.0) is None


# ---------------------------------------------------------------- 语音窗口 → 停顿

def test_pauses_between_includes_head_and_tail():
    """片头/片尾的空隙**也算停顿** —— 很多人开机后要先愣两秒。"""
    got = bgm.pauses_between([(1.0, 3.0)], 5.0, min_pause=0.8)
    assert got == [(0.0, 1.0), (3.0, 5.0)]


def test_pauses_between_ignores_gaps_under_threshold():
    """空隙低于阈值 → **不算**停顿（`checklist_bgm` §1 第三条）。"""
    got = bgm.pauses_between([(0.0, 2.0), (2.5, 4.0)], 4.0, min_pause=0.8)
    assert got == [], f"0.5s 的空隙不该算停顿：{got}"


def test_pauses_between_handles_overlapping_windows():
    """VAD 的窗口偶尔会重叠 —— 不该算出负长度或倒退。"""
    got = bgm.pauses_between([(0.0, 2.0), (1.5, 3.0)], 3.0, min_pause=0.5)
    for s, e in got:
        assert e > s
    assert got == []


# ---------------------------------------------------------------- BGM 检测（T2）：三态

def test_detect_bgm_true_when_quiet_windows_still_loud(tmp_path):
    """"没人说话"的窗口里**仍然不低** → 那声音只能是 BGM。"""
    # 全程一条 -20dB 的 1kHz 伴奏；"人声"窗口再叠一条更响的 300Hz
    wav = _wav(tmp_path / "bgm.wav", [(2.0, 1000, 0.10), (2.0, 300, 0.35)])
    info = bgm.detect_bgm(wav, windows=[(2.0, 4.0)], total=4.0, min_contrast_db=15.0)
    assert info.has_bgm is True, info.as_dict()
    assert info.quiet_windows, "无人声窗口要一并返回（抽伴奏要用）"


def test_detect_bgm_false_when_quiet_windows_are_silent(tmp_path):
    """空隙真的安静 → 没有 BGM。"""
    wav = _wav(tmp_path / "clean.wav", [(2.0, None, 0.0), (2.0, 300, 0.35)])
    info = bgm.detect_bgm(wav, windows=[(2.0, 4.0)], total=4.0, min_contrast_db=15.0)
    assert info.has_bgm is False, info.as_dict()


def test_detect_bgm_is_none_when_it_cannot_measure(tmp_path):
    """**测不出来 ≠ 没有** —— 混成 False 会让报告骗人（实测踩过）。"""
    wav = _wav(tmp_path / "x.wav", [(2.0, None, 0.0)])
    # 没有无人声窗口（窗口铺满整条）→ 无从测量
    info = bgm.detect_bgm(wav, windows=[(0.0, 2.0)], total=2.0)
    assert info.has_bgm is None
    assert info.as_dict()["has_bgm_known"] is False


def test_mean_volume_db_raises_instead_of_returning_none(tmp_path):
    """区间超出文件末尾时**明确报错**，不静默返回 None。

    实测踩过：静默 None → 上层 `None > 阈值` 恒为 False → `has_bgm` 错得**无声** ✗。
    """
    wav = _wav(tmp_path / "short.wav", [(1.0, 300, 0.2)])
    with pytest.raises(ValueError, match="没有采样|超出"):
        bgm.mean_volume_db(wav, start=5.0, end=6.0)


# ---------------------------------------------------------------- 降级要说清用了哪条路（T3）

def test_pauses_vad_reports_unavailable_instead_of_pretending(tmp_path, monkeypatch):
    """VAD 不可用 → `source="unavailable"`，**不许**假装是 vad 的结果。

    调用方（`scripts/roughcut.py`）据此退回音量门限，并在报告里写明用了哪条路 ✓。
    """
    def boom(*a, **k):
        raise RuntimeError("没有 Silero")

    monkeypatch.setattr(bgm, "vad_speech_windows", boom)
    res = bgm.pauses_vad(tmp_path / "x.wav", 3.0)
    assert res.source == "unavailable"
    assert res.pauses == []
    assert "VAD 不可用" in res.note
