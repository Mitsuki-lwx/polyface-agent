"""抽帧（`app/pipeline/frames.py`）的测试。

**为什么值得钉住**：这里踩过一个真 bug —— 等距补足的编号从 `len(frames)` 起算，
撞上场景帧已用的编号后**覆盖**了一张 ✓（6 条记录只有 5 个文件 ✗）。
所以除了"能抽到帧"，还要钉"**文件与记录一一对应**"。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline import frames as F  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="本机没有 ffmpeg")


def _make_video(path: Path, seconds: float = 6, size: str = "320x240") -> Path:
    """造一段测试视频。用 `testsrc` 生成**会变化**的画面，场景检测才有东西可抓。"""
    cmd = [shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=size={size}:rate=10:duration={seconds}",
           "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, timeout=120, capture_output=True)
    return path


def test_extracts_frames_and_files_match_records(tmp_path):
    video = _make_video(tmp_path / "moving.mp4", seconds=6)
    out = tmp_path / "frames"
    fs = F.extract_keyframes(video, out, count=4)

    assert 1 <= len(fs) <= 4
    # 记录与磁盘文件必须一一对应 —— 覆盖 bug 就是死在这条上
    names = [Path(f["path"]).name for f in fs]
    assert len(set(names)) == len(names), f"帧文件名重复：{names}"
    for f in fs:
        p = Path(f["path"])
        assert p.is_file() and p.stat().st_size > 0
    # 时刻必须升序，且落在视频长度内
    ts = [f["t_sec"] for f in fs]
    assert ts == sorted(ts)
    assert all(0 <= t <= 7 for t in ts)


def test_static_video_falls_back_to_interval(tmp_path):
    """画面几乎不变时场景检测抽不到 → 等距补足，不能返回 0 帧装成功 ✗。"""
    video = tmp_path / "static.mp4"
    subprocess.run(
        [shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=320x240:r=10:d=6",
         "-pix_fmt", "yuv420p", str(video)], check=True, timeout=120, capture_output=True)
    fs = F.extract_keyframes(video, tmp_path / "static_frames", count=3)
    assert len(fs) >= 1, "静态画面也必须给帧"
    assert len({Path(f["path"]).name for f in fs}) == len(fs)


def test_missing_video_raises():
    with pytest.raises(F.FrameError):
        F.extract_keyframes("D:/definitely/not/here.mp4", "x")


def test_duration_of_garbage_raises(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(F.FrameError):
        F.duration_sec(bad)


def test_extract_at_given_times(tmp_path):
    """剪辑联动：给定时刻各抽一帧（每一刀保留段取中间点）。"""
    video = _make_video(tmp_path / "v.mp4", seconds=6)
    fs = F.extract_at(video, tmp_path / "at", [1.0, 3.5, 5.0])
    assert [f["t_sec"] for f in fs] == [1.0, 3.5, 5.0]
    assert len({Path(f["path"]).name for f in fs}) == len(fs)
    assert all(Path(f["path"]).stat().st_size > 0 for f in fs)


def test_extract_at_clamps_out_of_range(tmp_path):
    video = _make_video(tmp_path / "v2.mp4", seconds=3)
    fs = F.extract_at(video, tmp_path / "at2", [-5.0, 999.0])
    assert len(fs) == 2
    assert all(0 <= f["t_sec"] <= 3 for f in fs), "越界时刻必须夹到视频范围内"


# ---------------------------------------------------------------- 画面质量（本地，不花模型）

def _blurred(src: Path, dst: Path, sigma: int = 14) -> Path:
    subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(src), "-vf", f"gblur=sigma={sigma}", str(dst)],
                   check=True, timeout=120, capture_output=True)
    return dst


def test_quality_sharp_frame_is_ok(tmp_path):
    video = _make_video(tmp_path / "q.mp4", seconds=2)
    f = F.extract_at(video, tmp_path / "qf", [0.5])[0]
    q = F.frame_quality(f["path"])
    assert q["blur"] is not None
    assert q["yavg"] is not None
    assert q["verdict"] in ("ok", "blurry"), q


def test_quality_detects_blur(tmp_path):
    """blur 是**模糊度**，越大越糊 —— 方向写反过一次（实测 8 清晰 / 27 糊）。"""
    video = _make_video(tmp_path / "q2.mp4", seconds=2)
    sharp = F.extract_at(video, tmp_path / "qs", [0.5])[0]
    blurred = _blurred(Path(sharp["path"]), tmp_path / "blur.jpg", sigma=14)

    qs, qb = F.frame_quality(sharp["path"]), F.frame_quality(blurred)
    assert qb["blur"] > qs["blur"], "模糊后 blues 值必须变大"
    assert qb["verdict"] == "blurry", qb


def test_quality_detects_dark(tmp_path):
    dark = tmp_path / "dark.jpg"
    subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=320x240", "-frames:v", "1", str(dark)],
                   check=True, timeout=120, capture_output=True)
    q = F.frame_quality(dark)
    assert q["verdict"] == "dark" and q["yavg"] < 25, q
