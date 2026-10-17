"""词级时间戳转写（`docs/tasks_disfluency.md` T1）。

**不加载真模型**：`_load_model` 换成假的（与 `test_roughcut.py` 的做法一致）——
真模型是可选依赖且要下载，单测不该依赖它。真链路由 T9 的端到端负责。
"""
from __future__ import annotations

import struct
import wave

import pytest

from app.pipeline import asr


def _wav(path, seconds: float = 2.0, rate: int = 16000):
    """写一个 16kHz 单声道 wav（内容无所谓，模型是假的）。"""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return str(path)


class _Word:
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


class _Seg:
    def __init__(self, text, start, end, words):
        self.text, self.start, self.end, self.words = text, start, end, words


class _FakeModel:
    def __init__(self, segments):
        self._segments = segments
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        return iter(self._segments), None


@pytest.fixture
def fake(monkeypatch):
    def install(segments):
        model = _FakeModel(segments)
        monkeypatch.setattr(asr, "is_available", lambda: True)
        monkeypatch.setattr(asr, "_load_model", lambda size: model)
        return model
    return install


def test_words_are_returned_and_monotonic(tmp_path, fake):
    fake([
        _Seg("我们今天要讲的", 0.0, 1.5, [_Word("我们", 0.0, 0.3), _Word("今天", 0.3, 0.7),
                                        _Word("要讲的", 0.7, 1.5)]),
        _Seg("我们今天要讲的是", 1.6, 3.0, [_Word("我们", 1.6, 1.9), _Word("今天", 1.9, 2.3),
                                          _Word("要讲的是", 2.3, 3.0)]),
    ])
    out = asr.transcribe_words(_wav(tmp_path / "a.wav"))
    assert len(out) == 2
    words = [w for seg in out for w in seg["words"]]
    assert len(words) == 6
    # 单调递增，无逆序
    assert all(words[i]["start"] <= words[i + 1]["start"] for i in range(len(words) - 1))
    assert all(w["start"] < w["end"] for w in words)


def test_words_cover_the_segment_span(tmp_path, fake):
    fake([_Seg("测试", 0.0, 2.0, [_Word("测", 0.0, 1.0), _Word("试", 1.0, 2.0)])])
    out = asr.transcribe_words(_wav(tmp_path / "b.wav"))
    seg = out[0]
    assert abs(seg["words"][0]["start"] - seg["start"]) < 0.5
    assert abs(seg["words"][-1]["end"] - seg["end"]) < 0.5


def test_asks_the_model_for_word_timestamps(tmp_path, fake):
    model = fake([_Seg("x", 0.0, 1.0, [_Word("x", 0.0, 1.0)])])
    asr.transcribe_words(_wav(tmp_path / "c.wav"))
    # 不传这个参数，faster-whisper 根本不会给 words
    assert model.kwargs.get("word_timestamps") is True


def test_missing_word_timestamps_raises(tmp_path, fake):
    """拿不到词级时间戳 → **明确报错**，不是静默返回空 words。

    静默返回空会让调用方以为"这段音频没有词"，从而悄悄少做一整类判定。
    """
    fake([_Seg("只有段级", 0.0, 1.0, None), _Seg("没有词", 1.0, 2.0, [])])
    with pytest.raises(RuntimeError, match="词级时间戳"):
        asr.transcribe_words(_wav(tmp_path / "d.wav"))


def test_asr_unavailable_raises_install_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(asr, "is_available", lambda: False)
    with pytest.raises(RuntimeError, match="faster-whisper"):
        asr.transcribe_words(_wav(tmp_path / "e.wav"))


def test_rejects_wrong_sample_rate(tmp_path, fake):
    """给个 44.1kHz 的 wav → 报错，不是拿去乱转。"""
    fake([_Seg("x", 0.0, 1.0, [_Word("x", 0.0, 1.0)])])
    p = tmp_path / "f.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(struct.pack("<h", 0) * 4410)
    with pytest.raises(RuntimeError, match="16kHz"):
        asr.transcribe_words(str(p))


def test_segments_entry_still_works(tmp_path, fake):
    """改动共用读音频后，段级入口不能坏（它已经被粗剪用着）。"""
    fake([_Seg("你好", 0.0, 1.0, [_Word("你好", 0.0, 1.0)]), _Seg("世界", 1.0, 2.0, [])])
    out = asr.transcribe_segments(_wav(tmp_path / "g.wav"))
    assert [s["text"] for s in out] == ["你好", "世界"]
    assert out[0]["start"] == 0.0 and out[1]["end"] == 2.0
