"""本地声学特征（openspec/changes/pause-prosody）。

测的是"数字对不对"和"没有数据时不装"—— 不测 librosa 本身。
"""

import numpy as np
import pytest
import soundfile as sf

from app.pipeline import pausereview as pr
from app.pipeline import prosody


SR = 16000


def _write(tmp_path, samples):
    p = tmp_path / "a.wav"
    sf.write(str(p), samples.astype(np.float32), SR)
    return p


def _tone(seconds, amp=0.4, freq=200.0):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_fade_out_before_pause_reads_as_fading(tmp_path):
    """停顿前渐弱 → rms_slope 明显为负（"收尾"的声学特征）。"""
    ramp = np.linspace(0.5, 0.05, int(1.5 * SR))
    t = np.arange(int(1.5 * SR)) / SR
    speech = (ramp * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    audio = np.concatenate([speech, np.zeros(int(1.0 * SR), dtype=np.float32)])
    wav = _write(tmp_path, audio)

    feat = prosody.analyze_pauses(wav, [(1.5, 2.5)])[0]
    assert feat["rms_slope"] < -0.05
    assert feat["pitch_before"] == pytest.approx(200, abs=6)
    assert "渐弱" in prosody.summarize(feat)


def test_steady_before_pause_is_flat(tmp_path):
    """停顿前音量平稳 → 斜率接近 0（"说断就断"不该被误读成收尾）。"""
    audio = np.concatenate([_tone(1.5, 0.4), np.zeros(int(1.0 * SR), dtype=np.float32)])
    wav = _write(tmp_path, audio)
    feat = prosody.analyze_pauses(wav, [(1.5, 2.5)])[0]
    assert abs(feat["rms_slope"]) < 0.02


def test_pause_at_start_has_no_pitch_before(tmp_path):
    """开头就是停顿 → 前面没人声，音高必须是 None（不编）。"""
    audio = np.concatenate([np.zeros(int(1.0 * SR), dtype=np.float32), _tone(1.0, 0.4)])
    wav = _write(tmp_path, audio)
    feat = prosody.analyze_pauses(wav, [(0.0, 1.0)])[0]
    assert feat["pitch_before"] is None
    assert feat["pitch_delta"] is None
    assert feat["pitch_after"] is not None


def test_missing_audio_degrades_to_none(tmp_path):
    """音频读不出 → 全 None，不抛（声学特征是增益，不是前置条件）。"""
    out = prosody.analyze_pauses(tmp_path / "nope.wav", [(1.0, 2.0), (3.0, 4.0)])
    assert out == [None, None]


def test_indices_subset_alignment(tmp_path):
    wav = _write(tmp_path, np.concatenate([_tone(1.0), np.zeros(int(1.0 * SR), dtype=np.float32)]))
    out = prosody.analyze_pauses(wav, [(0.0, 0.5), (1.0, 2.0), (2.0, 3.0)], indices=[1])
    assert len(out) == 1 and out[0] is not None


def test_out_of_range_index_is_none(tmp_path):
    wav = _write(tmp_path, _tone(1.0))
    assert prosody.analyze_pauses(wav, [(0.0, 0.5)], indices=[5]) == [None]


def test_attach_prosody_only_emits_when_present():
    cands = pr.build_candidates([(1.0, 3.0), (5.0, 7.0)], [], None, min_sec=0.6)
    assert "prosody" not in cands[0].as_prompt()
    pr.attach_prosody(cands, {cands[0].pause_index: {"rms_slope": -0.12}})
    assert cands[0].as_prompt()["prosody"] == {"rms_slope": -0.12}
    assert "prosody" not in cands[1].as_prompt()


def test_prompt_explains_prosody_fields():
    from app.pipeline import prompts
    payload = prompts.build_pause_review_prompt(
        [{"id": 0, "gap_sec": 2.0, "rule_would_cut": True, "prev": "a", "next": "b",
          "prosody": {"rms_slope": -0.1}}])
    assert "prosody" in payload and "rms_slope" in payload
    assert "渐弱" in payload
