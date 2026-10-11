"""T3 从时间线渲染：喂给 ffmpeg 的是「剪掉」的区间；字幕是成片轴；改时间线会失效。

对应 `docs/checklist_timeline.md` §5 §6。
最容易错的两处：**补集方向**（喂错就剪成反面）与**字幕轴**（喂错就错位）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.pipeline import checkpoint as ck, roughcut as rc, timeline as tlib

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str, filename: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))     # render_timeline 里 `from roughcut import …`
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


RT = _load("polyface_render_timeline", "render_timeline.py")

SEGS_SRC = [{"start": 2.0, "end": 4.0, "text": "第一句"},
            {"start": 25.0, "end": 27.0, "text": "第三句"}]


@pytest.fixture
def stub_media(monkeypatch):
    calls: dict = {"cuts": None, "srt": None}

    monkeypatch.setattr(rc, "probe", lambda p, **kw: {"width": 640, "height": 360,
                                                      "duration": 30.0})

    def fake_cut(src, dst, cuts, **kw):
        calls["cuts"] = [list(c) for c in cuts]
        Path(dst).write_bytes(b"cut")

    def fake_burn(cut, out, srt, params, **kw):
        calls["srt"] = Path(srt).read_text(encoding="utf-8")
        Path(out).write_bytes(b"out")

    monkeypatch.setattr(rc, "cut_video", fake_cut)
    monkeypatch.setattr(rc, "burn_subtitles", fake_burn)
    return calls


def _timeline(tmp_path, keep, segments):
    src = tmp_path / "源.mp4"
    if not src.exists():
        src.write_bytes(b"source-bytes")
    t = tlib.make({"path": str(src), "duration": 30.0}, keep, segments)
    p = tmp_path / "t.timeline.json"
    p.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    return p, src


def test_feeds_cuts_not_keep_to_ffmpeg(tmp_path, stub_media):
    """**方向验证**：时间线存的是"保留"，但 `cut_video` 收的是"剪掉"。"""
    t, _ = _timeline(tmp_path, [[0.0, 10.0], [20.0, 30.0]], SEGS_SRC)
    assert RT.main([str(t), "-o", str(tmp_path / "out.mp4"),
                    "--workdir", str(tmp_path / "w"), "--level", "quiet"]) == 0

    assert stub_media["cuts"] == [[10.0, 20.0]], "喂给 ffmpeg 的必须是剪掉的那段"


def test_subtitles_are_on_film_axis(tmp_path, stub_media):
    """字幕必须换算到成片轴：源 25s → 成片 15s（不是 25）。"""
    t, _ = _timeline(tmp_path, [[0.0, 10.0], [20.0, 30.0]], SEGS_SRC)
    RT.main([str(t), "-o", str(tmp_path / "out.mp4"),
             "--workdir", str(tmp_path / "w"), "--level", "quiet"])

    srt = stub_media["srt"]
    assert "第一句" in srt and "第三句" in srt
    assert "00:00:15" in srt, f"第三句应在成片 15s 处，实际 srt：\n{srt}"
    assert "00:00:25" not in srt, "不该出现源轴时间（那就是错位）"


def test_output_duration_matches_keep(tmp_path, stub_media):
    """成片时长 == keep 之和（容差内）。"""
    t, _ = _timeline(tmp_path, [[0.0, 10.0], [20.0, 30.0]], [])
    # probe(out) 也走 stub → 返回 30s；这里改成返回 20s 以模拟"渲染正确"
    import app.pipeline.roughcut as _rc
    orig = _rc.probe
    _rc.probe = lambda p, **kw: {"width": 1, "height": 1,
                                 "duration": 30.0 if str(p).endswith("源.mp4") else 20.0}
    try:
        assert RT.main([str(t), "-o", str(tmp_path / "out.mp4"),
                        "--workdir", str(tmp_path / "w"), "--level", "quiet"]) == 0
    finally:
        _rc.probe = orig


def test_editing_timeline_invalidates_resume(tmp_path, stub_media):
    """**改了时间线就必须重剪** —— 否则用户改了却看不到变化。"""
    work = tmp_path / "w"
    t, _ = _timeline(tmp_path, [[0.0, 10.0], [20.0, 30.0]], [])
    RT.main([str(t), "-o", str(tmp_path / "out.mp4"), "--workdir", str(work),
             "--level", "quiet"])
    first = stub_media["cuts"]

    # 改时间线：把第二段起点从 20 改到 22
    t2, _ = _timeline(tmp_path, [[0.0, 10.0], [22.0, 30.0]], [])
    stub_media["cuts"] = None
    RT.main([str(t2), "-o", str(tmp_path / "out2.mp4"), "--workdir", str(work),
             "--level", "quiet"])

    assert stub_media["cuts"] is not None, "改过时间线后必须重剪（不能复用旧成片）"
    assert stub_media["cuts"] == [[10.0, 22.0]], f"实际 {stub_media['cuts']}"


def test_unchanged_timeline_resumes(tmp_path, stub_media):
    """没改 → 续跑**不重剪**（省时间）。"""
    work = tmp_path / "w"
    t, _ = _timeline(tmp_path, [[0.0, 10.0], [20.0, 30.0]], [])
    out = tmp_path / "out.mp4"
    RT.main([str(t), "-o", str(out), "--workdir", str(work), "--level", "quiet"])

    stub_media["cuts"] = None
    RT.main([str(t), "-o", str(out), "--workdir", str(work), "--level", "quiet"])
    assert stub_media["cuts"] is None, "没改时间线不该重剪"


def test_rejects_invalid_timeline_before_touching_ffmpeg(tmp_path, stub_media):
    """不合法的时间线要**在碰 ffmpeg 之前**就拒掉。"""
    src = tmp_path / "源.mp4"
    src.write_bytes(b"x")
    bad = {"version": 1, "source": {"path": str(src)},
           "keep": [[0.0, 10.0], [5.0, 15.0]], "segments": []}   # 重叠
    p = tmp_path / "bad.timeline.json"
    p.write_text(json.dumps(bad), encoding="utf-8")

    assert RT.main([str(p), "-o", str(tmp_path / "o.mp4"),
                    "--workdir", str(tmp_path / "w"), "--level", "quiet"]) == 1
    assert stub_media["cuts"] is None


def test_rejects_missing_source(tmp_path, stub_media):
    t = tmp_path / "t.timeline.json"
    t.write_text(json.dumps({"version": 1, "source": {"path": str(tmp_path / "没有.mp4")},
                             "keep": [[0.0, 1.0]], "segments": []}), encoding="utf-8")
    assert RT.main([str(t), "--workdir", str(tmp_path / "w"), "--level", "quiet"]) == 1


def test_no_burn_writes_without_subtitles(tmp_path, monkeypatch):
    """关掉烧字幕 → 不调 burn，但仍出片（且时长仍对）。"""
    called = {"burn": False}
    monkeypatch.setattr(rc, "probe", lambda p, **kw: {"width": 1, "height": 1, "duration": 30.0})
    monkeypatch.setattr(rc, "cut_video", lambda s, d, c, **kw: Path(d).write_bytes(b"c"))
    monkeypatch.setattr(rc, "burn_subtitles",
                        lambda *a, **kw: called.__setitem__("burn", True))

    t, _ = _timeline(tmp_path, [[0.0, 10.0]], [])
    out = tmp_path / "o.mp4"
    assert RT.main([str(t), "-o", str(out), "--workdir", str(tmp_path / "w"),
                    "--no-burn", "--level", "quiet"]) == 0
    assert called["burn"] is False
    assert out.exists()
