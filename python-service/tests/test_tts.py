"""T1 配音：可选依赖 + 降级（`docs/spec_b1.md` 关键决定三）。

最要紧的一条：**没有 TTS 不是错误**。上层要能降级成"无声 + 字幕"，
绝不因为缺一个可选依赖就整体失败 —— `asr.py` 已经立过这个先例。
"""
from __future__ import annotations

import pytest

from app.pipeline import tts


def test_describe_reports_backend_or_hint():
    d = tts.describe()
    assert set(d) >= {"available", "backend", "forced", "hint"}
    if d["available"]:
        assert d["backend"] in ("windows_sapi", "piper")
        assert d["hint"] == ""
    else:
        assert d["hint"], "不可用时必须给出安装指引"


def test_forced_none_disables(monkeypatch):
    """`POLYFACE_TTS=none` 强制禁用 —— 用来验"没装也能出片"这条路径。"""
    monkeypatch.setenv("POLYFACE_TTS", "none")
    assert tts.is_available() is False
    assert tts.describe()["available"] is False


def test_unavailable_raises_but_is_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setenv("POLYFACE_TTS", "none")
    with pytest.raises(tts.TtsUnavailable) as e:
        tts.synthesize("你好", tmp_path / "a.wav")
    assert "TTS" in str(e.value)          # 是给人看的指引，不是栈


def test_empty_text_needs_no_backend(monkeypatch, tmp_path):
    """空文本 → 一小段静音。**不该因此要求有 TTS**（否则调用方还得特判）。"""
    monkeypatch.setenv("POLYFACE_TTS", "none")
    out = tmp_path / "s.wav"
    assert tts.synthesize("   ", out) == 0.3
    assert out.is_file()


def test_wav_seconds_reads_real_duration(tmp_path):
    import wave
    p = tmp_path / "x.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 32000)
    assert tts.wav_seconds(p) == 2.0


@pytest.mark.skipif(not tts.is_available(), reason="本机没有可用 TTS")
def test_real_synthesis_duration_scales_with_text(tmp_path):
    """**时长随文本变** —— 顺带证明中文没被读成乱码（乱码时长会不成比例）。"""
    short = tts.synthesize("你好。", tmp_path / "s.wav")
    long_ = tts.synthesize("你好。" * 8, tmp_path / "l.wav")
    assert short > 0.3
    assert long_ > short * 2, f"长文本应当明显更长：{long_} vs {short}"


# ---------------- 风格预设 ----------------

def test_presets_have_notes():
    """每个预设都要有 note —— 用户得知道"轻快"到底变了什么。"""
    for x in tts.list_presets():
        assert set(x) >= {"id", "name", "rate", "volume", "pick", "note"}
        assert x["note"], f"{x['id']} 缺 note"


def test_resolve_preset_unknown_raises():
    with pytest.raises(tts.TtsUnavailable, match="不认识的配音预设"):
        tts.resolve_preset("不存在的预设")


def test_resolve_preset_picks_a_real_voice():
    """预设挑出来的音色必须是**真实存在**的（挑不到就交回 None，不编一个名字）。"""
    if not tts.is_available():
        pytest.skip("本机没有可用 TTS")
    ids = [v["id"] for v in tts.list_voices()]
    for x in tts.list_presets():
        r = tts.resolve_preset(x["id"])
        assert r["voice"] is None or r["voice"] in ids, \
            f"{x['id']} 挑出的音色不在可用列表里：{r['voice']}"


@pytest.mark.skipif(not tts.is_available(), reason="本机没有可用 TTS")
def test_presets_really_change_the_audio(tmp_path):
    """**三种预设必须真的产出不同的东西** —— 否则预设只是摆设。

    语速不同 → 同一句话的时长不同。实测 11.75 / 9.41 / 6.78s。
    """
    text = "我2023年3月裸辞，从月入0做到月入3万，用了11个月。"
    out = []
    for x in tts.list_presets()[:3]:
        r = tts.resolve_preset(x["id"])
        out.append(tts.synthesize(text, tmp_path / f"{x['id']}.wav",
                                  voice=r["voice"], rate=r["rate"], volume=r["volume"]))
    assert out[0] > out[1] > out[2], f"语速 -2 / 0 / +3 应当依次变短，实际 {out}"
    assert out[0] - out[2] > 2.0, f"差异太小就不算「几种不同的配音」：{out}"
