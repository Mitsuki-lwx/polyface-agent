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


# ---------------------------------------------------------------- ProRes（真素材回归）


def _make_prores(path, secs=2.0):
    """造一段 ProRes 的 .mov —— 影视飓风素材库里大量是这个编码。"""
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"color=c=red:s=320x240:r=25:d={secs}",
                    "-c:v", "prores_ks", "-pix_fmt", "yuv422p10le", str(path)],
                   check=True, capture_output=True)
    return path


def test_prores_source_gets_a_mov_container(tmp_path):
    """**实测回归**：源是 ProRes 时不能切成 .mp4。

    MP4 容器装不下 ProRes，ffmpeg 会直接报
        Could not find tag for codec prores in stream #0, codec not currently supported in container
    而影视飓风的素材大量是 ProRes 灰片 —— 写死 .mp4 等于这类素材一进来就切不动。
    """
    src = _make_prores(tmp_path / "p.mov")
    assert SS.out_extension(src) == ".mov"


def test_h264_source_still_uses_mp4(tmp_path):
    p = tmp_path / "h.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=25:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)],
                   check=True, capture_output=True)
    assert SS.out_extension(p) == ".mp4"


def test_prores_cut_actually_works(tmp_path):
    """真的切一刀 —— 证明修好了，不只是扩展名算对了。"""
    src = _make_prores(tmp_path / "p2.mov", secs=4.0)
    out = tmp_path / f"seg{SS.out_extension(src)}"
    SS.cut_segment(src, out, 0.0, 2.0)
    assert out.is_file() and out.stat().st_size > 0
    codec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True).stdout.strip()
    assert codec == "prores", f"编码被改掉了：{codec}"


def test_plan_records_the_real_file_name(tmp_path):
    """方案里要带**真实文件名**（含扩展名）—— Java 侧据此登记素材，不要靠猜。"""
    src = _make_prores(tmp_path / "p3.mov", secs=3.0)
    outdir, w = tmp_path / "o", tmp_path / "w"
    r = _run([str(src), "--yes", "--no-snap", "--no-thumb", "--min-sec", "0.5",
              "-o", str(outdir), "--workdir", str(w)])
    assert r.returncode == 0, r.stderr
    plan = json.loads((w / "scene-plan.json").read_text(encoding="utf-8"))
    f = plan["segments"][0]
    assert f["file_name"].endswith(".mov"), f
    assert Path(f["file"]).is_file()


# ---------------------------------------------------------------- 检测结果复用


def test_second_run_reuses_detection(video, tmp_path):
    """**改下游参数重跑不该重新解码视频**（checklist §4）。

    做法：跑第二次时把检测函数换成"一调用就炸" —— 如果它没被调用，说明确实复用了。
    4K 长片上这一步是分钟级，值得省。
    """
    outdir, w = tmp_path / "o", tmp_path / "w"
    base = [str(video), "--dry-run", "--no-snap", "--min-sec", "1",
            "-o", str(outdir), "--workdir", str(w)]
    assert _run(base).returncode == 0
    plan1 = json.loads((w / "scene-plan.json").read_text(encoding="utf-8"))
    assert plan1["raw_cuts"], "第一次应该真的检测出切点"

    import importlib.util
    spec = importlib.util.spec_from_file_location("ss_cache", SCRIPTS / "scene_split.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ss_cache"] = mod
    spec.loader.exec_module(mod)
    mod.sc._real = mod.sc.detect_scene_cuts
    mod.sc.detect_scene_cuts = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("第二次不该重新检测"))
    try:
        pauses, source = mod._load_cached_cuts(w, type("A", (), {"threshold": 27.0})()), None
        assert pauses == plan1["raw_cuts"]
    finally:
        mod.sc.detect_scene_cuts = mod.sc._real


def test_cache_is_invalidated_when_threshold_changes(video, tmp_path):
    """**检测参数变了必须重检** —— 否则改阈值没反应，那才是 bug。"""
    w = tmp_path / "w"
    _run([str(video), "--dry-run", "--no-snap", "--min-sec", "1", "--threshold", "27",
          "-o", str(tmp_path / "o"), "--workdir", str(w)])
    import importlib.util
    spec = importlib.util.spec_from_file_location("ss_cache2", SCRIPTS / "scene_split.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ss_cache2"] = mod
    spec.loader.exec_module(mod)
    A = type("A", (), {"threshold": 40.0})
    assert mod._load_cached_cuts(w, A()) is None, "阈值变了就不该复用"


def test_plan_from_cuts_skips_detection(video, tmp_path):
    """`plan_from_cuts` 不碰视频 —— 只做吸附 + 合并。"""
    from app.pipeline import scenes as sc
    plan = sc.plan_from_cuts([4.0, 8.0], 12.0, segments=[], min_sec=1.0)
    assert plan.raw_cuts == [4.0, 8.0]
    assert plan.segments == [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0)]
