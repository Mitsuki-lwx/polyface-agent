"""T3 粗剪逐阶段落盘 + 续跑（只跑缺的阶段）。

对应 `docs/spec_resume.md` §5、`docs/checklist_resume.md` §4 §5。
粗剪的续跑判据是**链式**的：剪点变了，下游必须全部失效 —— 否则会产出
"剪点与视频对不上"的结果。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.pipeline import asr, checkpoint as ck, roughcut as rc

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


R = _load("polyface_roughcut_resume", "roughcut.py")

SEGMENTS = [{"start": 0.0, "end": 1.5, "text": "第一句"},
            {"start": 1.5, "end": 3.0, "text": "第二句"}]


@pytest.fixture
def stub_media(monkeypatch):
    """把 ffmpeg / ASR 全 stub 掉，只留编排逻辑（本测试测的就是编排）。"""
    calls: list[str] = []

    monkeypatch.setattr(rc, "probe", lambda p, **kw: {"width": 1080, "height": 1920,
                                                      "duration": 10.0})
    monkeypatch.setattr(rc, "detect_pauses", lambda *a, **kw: [(1.0, 2.0)])
    monkeypatch.setattr(rc, "plan_cuts", lambda pauses, margin, total: [(1.08, 1.92)])

    def fake_cut(src, dst, cuts, **kw):
        calls.append("剪裁")
        Path(dst).write_bytes(b"cut")

    def fake_wav(src, dst, **kw):
        calls.append("抽音轨")
        Path(dst).write_bytes(b"wav")

    def fake_asr(path, **kw):
        calls.append("转写")
        return list(SEGMENTS)

    def fake_asr_words(path, **kw):
        # 去冗余用的是**词级**入口（判定要知道"那个词"在哪儿）
        calls.append("去冗余")
        return [dict(x, words=[]) for x in SEGMENTS]

    def fake_burn(cut, out, srt, params, **kw):
        calls.append("烧字幕")
        Path(out).write_bytes(b"out")

    monkeypatch.setattr(rc, "cut_video", fake_cut)
    monkeypatch.setattr(rc, "extract_wav", fake_wav)
    monkeypatch.setattr(asr, "transcribe_segments", fake_asr)
    monkeypatch.setattr(asr, "transcribe_words", fake_asr_words)
    monkeypatch.setattr(rc, "burn_subtitles", fake_burn)
    return calls


OUT = "out.mp4"


def _run(tmp_path, src, work, extra=None):
    argv = [str(src), "--yes", "--workdir", str(work), "-o", str(tmp_path / OUT),
            "--events", str(tmp_path / "events.jsonl"), "--level", "quiet"]
    argv += list(extra or [])
    return R.main(argv)


@pytest.fixture
def media(tmp_path):
    src = tmp_path / "口播.mp4"
    src.write_bytes(b"fake-video")
    return src


def test_stages_are_persisted(tmp_path, media, stub_media):
    """跑完一遍，每个阶段都留下落盘记录。"""
    work = tmp_path / "w"
    assert _run(tmp_path, media, work) == 0

    # 去冗余在**剪之前**（判定要有原文）—— 它是个独立阶段，可单独续跑
    assert set(ck.list_units(work, "stage")) == {"探测", "去冗余", "找停顿", "剪裁",
                                                 "抽音轨", "转写", "烧字幕"}


def test_resume_skips_everything_when_complete(tmp_path, media, stub_media):
    """全部阶段已完成 → 再跑一次**一个阶段都不重跑**。"""
    work = tmp_path / "w"
    _run(tmp_path, media, work)
    stub_media.clear()

    assert _run(tmp_path, media, work) == 0
    assert stub_media == [], f"全已完成时不该重跑任何阶段，实际跑了 {stub_media}"


def test_resume_reruns_stage_whose_artifact_is_gone(tmp_path, media, stub_media):
    """落盘记录在、但产物文件没了 → 该阶段**重跑**（记录不等于产物还在）。"""
    work = tmp_path / "w"
    _run(tmp_path, media, work)
    stub_media.clear()

    (tmp_path / OUT).unlink()                    # 造出"烧字幕时被中断"的现场

    assert _run(tmp_path, media, work) == 0
    assert stub_media == ["烧字幕"], f"只该跑烧字幕，实际跑了 {stub_media}"


def test_param_change_invalidates_downstream(tmp_path, media, stub_media):
    """**链式判据**：改了停顿阈值 → 找停顿及下游全部失效（绝不复用旧 cut.mp4）。"""
    work = tmp_path / "w"
    _run(tmp_path, media, work)
    stub_media.clear()

    assert _run(tmp_path, media, work, extra=["--pause", "1.5"]) == 0
    # 找停顿、剪裁、抽音轨、转写、烧字幕 都要重跑（探测不依赖参数，可复用）
    assert stub_media == ["剪裁", "抽音轨", "转写", "烧字幕"], f"实际跑了 {stub_media}"


def test_source_change_invalidates_everything(tmp_path, media, stub_media):
    """换了素材文件 → 全部失效（连探测都不该复用）。"""
    work = tmp_path / "w"
    _run(tmp_path, media, work)
    stub_media.clear()

    media.write_bytes(b"another-video-of-different-size")   # size 变了
    assert _run(tmp_path, media, work) == 0
    # 去冗余也要重跑：它读的是**原始素材**的音频（判定必须在剪之前）
    assert stub_media == ["去冗余", "剪裁", "抽音轨", "转写", "烧字幕"]


def test_stage_payload_is_real_json(tmp_path, media, stub_media):
    """落盘内容是真的产物（转写段落原样存），不是摘要。"""
    work = tmp_path / "w"
    _run(tmp_path, media, work)

    payload = ck.read_unit(work, "stage", "转写")
    assert payload["segments"] == SEGMENTS
    assert payload["key"], "每个阶段都要带判据"


def test_corrupt_stage_reruns_from_there(tmp_path, media, stub_media):
    """落盘坏了 → 从该阶段重跑（不猜）。"""
    work = tmp_path / "w"
    _run(tmp_path, media, work)
    (ck.done_dir(work) / "stage-转写.json").write_text("{坏", encoding="utf-8")
    stub_media.clear()

    assert _run(tmp_path, media, work) == 0
    assert "转写" in stub_media, "坏落盘的阶段必须重跑"


def test_failed_stage_is_not_persisted(tmp_path, media, monkeypatch):
    """失败的阶段**不落盘** —— 否则下次续跑会拿它当"已完成"。"""
    monkeypatch.setattr(rc, "probe", lambda p, **kw: {"width": 1, "height": 1, "duration": 10.0})
    monkeypatch.setattr(rc, "detect_pauses", lambda *a, **kw: [(1.0, 2.0)])
    monkeypatch.setattr(rc, "plan_cuts", lambda pauses, margin, total: [(1.08, 1.92)])
    monkeypatch.setattr(rc, "cut_video", lambda src, dst, cuts, **kw: Path(dst).write_bytes(b"c"))
    monkeypatch.setattr(rc, "extract_wav", lambda src, dst, **kw: Path(dst).write_bytes(b"w"))

    def boom(*a, **kw):
        raise RuntimeError("转写崩了")

    monkeypatch.setattr(asr, "transcribe_segments", boom)
    monkeypatch.setattr(asr, "transcribe_words",
                        lambda path, **kw: [dict(x, words=[]) for x in SEGMENTS])

    work = tmp_path / "w"
    assert _run(tmp_path, media, work) == 1          # EXIT_ERR
    units = set(ck.list_units(work, "stage"))
    assert "转写" not in units, "失败的阶段不该被记为已完成"
    assert {"探测", "去冗余", "找停顿", "剪裁", "抽音轨"} <= units
