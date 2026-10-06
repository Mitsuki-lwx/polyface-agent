"""FR-51 音视频入料测试：SRT 清洗 / 媒体探测 / 状态矩阵 / ffmpeg 缺失降级。

样本由 ffmpeg 现场生成，测试可独立运行（不依赖外部素材）。
"""
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import asr, ingest, media
from app.pipeline.media import MediaInvalid, srt_to_text

client = TestClient(app)

requires_ffmpeg = pytest.mark.skipif(not media.has_ffmpeg(), reason="ffmpeg 不可用")


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    """现场造样本：base.mp4（无字幕）/ withsub.mp4（带 mov_text 字幕轨）。"""
    d = tmp_path_factory.mktemp("media")
    base = d / "base.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=black:s=320x240:d=3",
         "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-shortest",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(base)],
        check=True, capture_output=True)
    srt = d / "s.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,500\n第一句\n\n"
        "2\n00:00:01,500 --> 00:00:02,400\n第二句\n\n"
        "3\n00:00:02,400 --> 00:00:03,000\n第二句\n", encoding="utf-8")
    withsub = d / "withsub.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(base), "-i", str(srt),
         "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text", str(withsub)],
        check=True, capture_output=True)
    return {"base": base, "withsub": withsub}


# ---------------- SRT 清洗（不依赖 ffmpeg） ----------------

def test_srt_to_text_strips_ordinal_timeline_tags_and_dupes():
    raw = ("1\n00:00:00,000 --> 00:00:01,000\n<i>第一句</i>\n\n"
           "2\n00:00:01,000 --> 00:00:02,000\n第一句\n\n"
           "3\n00:00:02,000 --> 00:00:03,000\n{\\an8}第二句\n")
    assert srt_to_text(raw) == "第一句\n第二句"


def test_srt_to_text_handles_empty():
    assert srt_to_text("") == ""
    assert srt_to_text("1\n00:00:00,000 --> 00:00:01,000\n\n") == ""


# ---------------- 媒体探测 ----------------

@requires_ffmpeg
def test_probe_detects_subtitle_stream(samples):
    info = ingest.run_probe(str(samples["withsub"]))
    assert info["has_subtitle"] is True
    assert info["has_audio"] is True
    assert info["recommended_mode"] == "subtitle"
    assert info["duration_sec"] > 0
    assert info["subtitle_streams"][0]["codec"]


@requires_ffmpeg
def test_probe_without_subtitle_recommends_asr(samples):
    info = ingest.run_probe(str(samples["base"]))
    assert info["has_subtitle"] is False
    assert info["has_audio"] is True
    assert info["recommended_mode"] == "asr"


def test_probe_rejects_missing_bad_ext_and_dir(tmp_path):
    with pytest.raises(MediaInvalid):
        media.probe(str(tmp_path / "nope.mp4"))
    bad = tmp_path / "x.txt"
    bad.write_text("hello", encoding="utf-8")
    with pytest.raises(MediaInvalid):
        media.probe(str(bad))
    with pytest.raises(MediaInvalid):
        media.probe(str(tmp_path))


# ---------------- 状态矩阵 ----------------

@requires_ffmpeg
def test_transcribe_subtitle_path(samples):
    r = ingest.run_transcribe(str(samples["withsub"]), "auto")
    assert r.status == "subtitle_extracted"
    assert r.source == "subtitle"
    assert "第一句" in r.text and "第二句" in r.text
    assert r.chars == len(r.text)
    assert r.hint


@requires_ffmpeg
def test_transcribe_needs_manual_without_asr(samples, monkeypatch):
    monkeypatch.setattr(ingest.asr, "is_available", lambda: False)
    r = ingest.run_transcribe(str(samples["base"]), "auto")
    assert r.status == "needs_manual"
    assert r.source == "none"
    assert r.text == ""
    assert "faster-whisper" in r.hint          # 指引必须可执行


@requires_ffmpeg
def test_transcribe_bad_file_degrades_instead_of_raising(tmp_path):
    bad = tmp_path / "fake.mp4"
    bad.write_bytes(b"not a real video")
    r = ingest.run_transcribe(str(bad), "auto")
    assert r.status == "needs_manual"
    assert r.hint


def test_transcribe_ffmpeg_missing_degrades(monkeypatch):
    monkeypatch.setattr(ingest.media, "has_ffmpeg", lambda: False)
    r = ingest.run_transcribe("whatever.mp4", "auto")
    assert r.status == "needs_manual"
    assert "ffmpeg" in r.hint


# ---------------- HTTP 端点 ----------------

def test_probe_endpoint_400_on_missing_file():
    r = client.post("/probe", json={"path": "D:/definitely/not/here.mp4"})
    assert r.status_code == 400


@requires_ffmpeg
def test_probe_endpoint_ok(samples):
    r = client.post("/probe", json={"path": str(samples["withsub"])})
    assert r.status_code == 200
    body = r.json()
    assert body["recommended_mode"] == "subtitle"
    assert body["ffmpeg_available"] is True
    # 断言"**如实反映**本机装没装 ASR"，而不是"一定没装" ——
    # 后者钉的是环境而不是行为：装了 faster-whisper 的机器上会假失败（本机刚装就踩了）
    assert body["asr_available"] == asr.is_available()


@requires_ffmpeg
def test_transcribe_endpoint_never_returns_5xx(samples):
    r = client.post("/transcribe", json={"path": str(samples["base"]), "mode": "auto"})
    assert r.status_code == 200
    assert r.json()["status"] == "needs_manual"
