"""切段 CLI（`docs/tasks_scene_split.md` T4~T6）。

**真跑 ffmpeg** —— 这里的坑（`-to` 的参考点、`-avoid_negative_ts` 把时长算错）
全是实测出来的，mock 掉就一条都验不到。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load_cli():
    spec = importlib.util.spec_from_file_location("scene_split_cli", SCRIPTS / "scene_split.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["scene_split_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


SS = _load_cli()


def _dur(path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip())


def _make_video(path, colors=("red", "blue", "green"), secs=2.0):
    parts = [f"color=c={c}:s=320x240:d={secs}:r=10" for c in colors]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for p in parts:
        cmd += ["-f", "lavfi", "-i", p]
    cmd += ["-filter_complex", f"concat=n={len(colors)}:v=1:a=0", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return secs * len(colors)


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "src.mp4"
    _make_video(p, secs=4.0)          # 12 秒，三段
    return p


def test_cut_duration_matches_the_plan(video, tmp_path):
    """**实测回归**：切点必须落在请求的区间上。

    踩过两次：① `-ss` 在 `-i` 前时 `-to` 的参考点不对；
    ② `-avoid_negative_ts make_zero` 会让容器时长算错
    （15.00→24.00 切出来 16.1 秒）。两者都让"段时长"和方案对不上。
    """
    out = tmp_path / "cut.mp4"
    SS.cut_segment(video, out, 4.0, 8.0)          # 第二段，4 秒
    assert abs(_dur(out) - 4.0) <= 0.5


def test_cut_from_a_non_keyframe_start_is_still_right(video, tmp_path):
    """起点不在场景边界上（关键帧之外）也不能长出几秒。"""
    out = tmp_path / "cut2.mp4"
    SS.cut_segment(video, out, 5.0, 7.0)          # 2 秒，起点在第二段中间
    assert abs(_dur(out) - 2.0) <= 0.5


def test_source_is_untouched(video, tmp_path):
    before = video.stat().st_mtime_ns
    SS.cut_segment(video, tmp_path / "c.mp4", 0.0, 2.0)
    assert video.stat().st_mtime_ns == before


def test_thumbnail_is_written(video, tmp_path):
    out = tmp_path / "t.jpg"
    SS.thumbnail(video, out, 5.0)
    assert out.is_file() and out.stat().st_size > 0


def test_probe_duration(video):
    assert abs(SS.probe_duration(video) - 12.0) <= 0.5


# ---------------------------------------------------------------- CLI 行为


def _run(args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPTS / "scene_split.py"), *args],
                          capture_output=True, text=True, cwd=cwd or str(SCRIPTS.parents[0]))


def test_dry_run_produces_no_video(video, tmp_path):
    """**dry-run 绝不能产出视频**（checklist §4）—— 它是"只看方案"。"""
    outdir = tmp_path / "scenes"
    r = _run([str(video), "--dry-run", "--no-snap", "--min-sec", "1",
              "-o", str(outdir), "--workdir", str(tmp_path / "w")])
    assert r.returncode == 0, r.stderr
    assert list(outdir.glob("*.mp4")) == []
    plan = json.loads((tmp_path / "w" / "scene-plan.json").read_text(encoding="utf-8"))
    assert plan["count"] >= 2
    assert plan["speech_aligned"] is False          # --no-snap → 标记没做吸附


def test_pick_cuts_only_the_chosen_segments(video, tmp_path):
    outdir = tmp_path / "scenes"
    r = _run([str(video), "--no-snap", "--min-sec", "1", "--pick", "2", "--yes",
              "-o", str(outdir), "--workdir", str(tmp_path / "w")])
    assert r.returncode == 0, r.stderr
    files = sorted(p.name for p in outdir.glob("*.mp4"))
    assert files == ["scene-002.mp4"], files


def test_out_of_range_pick_is_rejected(video, tmp_path):
    r = _run([str(video), "--no-snap", "--pick", "99", "--yes",
              "-o", str(tmp_path / "o"), "--workdir", str(tmp_path / "w")])
    assert r.returncode == 1
    assert "超出范围" in r.stderr


def test_plan_records_detector_and_params(video, tmp_path):
    _run([str(video), "--dry-run", "--no-snap", "--min-sec", "1", "--threshold", "40",
          "-o", str(tmp_path / "o"), "--workdir", str(tmp_path / "w")])
    plan = json.loads((tmp_path / "w" / "scene-plan.json").read_text(encoding="utf-8"))
    assert plan["params"] == {"threshold": 40.0, "min_sec": 1.0}
    assert "PySceneDetect" in plan["detector"]


def test_thumbs_are_produced_unless_disabled(video, tmp_path):
    w = tmp_path / "w"
    _run([str(video), "--dry-run", "--no-snap", "--min-sec", "1",
          "-o", str(tmp_path / "o"), "--workdir", str(w)])
    plan = json.loads((w / "scene-plan.json").read_text(encoding="utf-8"))
    thumbs = [s["thumb"] for s in plan["segments"]]
    assert len(thumbs) == plan["count"]
    assert all(Path(t).is_file() and Path(t).stat().st_size > 0 for t in thumbs)

    w2 = tmp_path / "w2"
    _run([str(video), "--dry-run", "--no-snap", "--no-thumb", "--min-sec", "1",
          "-o", str(tmp_path / "o2"), "--workdir", str(w2)])
    plan2 = json.loads((w2 / "scene-plan.json").read_text(encoding="utf-8"))
    assert all("thumb" not in s for s in plan2["segments"])
