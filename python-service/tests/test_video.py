"""T2/T4 画面与转场（`docs/checklist_b1.md` §5）。

⚠️ **测试图必须有纹理**：纯色图放大后还是纯色，帧比对会给出**假阴性**
（实测踩过 —— 用 `color=red` 造图，四种 zoompan 写法全部"看起来没动"）。
所以这里统一用 `testsrc2`。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.pipeline import roughcut as rc, video as V

pytestmark = pytest.mark.skipif(not rc.has_ffmpeg(), reason="没有 ffmpeg")

SMALL = (270, 480)          # 测试用小尺寸：跑得快，结论一样


def _textured(path: Path) -> Path:
    """有纹理的测试图 —— 纯色图测不出"在动"。"""
    rc._run_ffmpeg([rc._ffmpeg_bin("ffmpeg"), "-y", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc2=s=640x1136", "-frames:v", "1", str(path)])
    return path


def _gray16(p: Path) -> list[int]:
    """把帧降到 16×16 灰度 —— 只在"运动量"这个尺度上比较，不跟编码噪声较劲。"""
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p),
                          "-vf", "scale=16:16,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    return list(out)


def _motion(a: Path, b: Path) -> float:
    """两帧的平均绝对差。

    **别用 md5 比帧**：实测 `zoom=1.0`（应当完全静止）时 md5 也不相等 ——
    `zoompan` 本身有亚像素抖动，编码也有噪声。md5 会把噪声当成"在动"。
    实测分离度很好：静止 0.03 / 缓动 7.19。
    """
    x, y = _gray16(a), _gray16(b)
    return sum(abs(i - j) for i, j in zip(x, y)) / max(1, len(x))


def _dur(p: Path) -> float:
    return rc.probe(p, require_audio=False)["duration"]


def test_ken_burns_duration_and_motion(tmp_path):
    img = _textured(tmp_path / "a.png")
    out = tmp_path / "s.mp4"
    V.ken_burns(img, out, 2.0, size=SMALL, fps=15)

    assert _dur(out) == pytest.approx(2.0, abs=0.15)
    V.frame_at(out, tmp_path / "f0.png", 0.0)
    V.frame_at(out, tmp_path / "f1.png", 1.9)
    assert _motion(tmp_path / "f0.png", tmp_path / "f1.png") > 2.0, \
        "画面必须**在动** —— 静止就退化成 docs/49 §5.1 打击的模板轮播了"


def test_zoom_one_means_still(tmp_path):
    """缓动强度 1.0 = 静止 —— 这条把"缓动真的在起作用"钉住（checklist §2）。"""
    img = _textured(tmp_path / "a.png")
    out = tmp_path / "s.mp4"
    V.ken_burns(img, out, 1.5, zoom=1.0, size=SMALL, fps=15)
    V.frame_at(out, tmp_path / "f0.png", 0.0)
    V.frame_at(out, tmp_path / "f1.png", 1.4)
    assert _motion(tmp_path / "f0.png", tmp_path / "f1.png") < 1.0, "zoom=1.0 应当基本静止"


def test_rejects_zoom_below_one(tmp_path):
    img = _textured(tmp_path / "a.png")
    with pytest.raises(V.VideoError, match="1.0"):
        V.ken_burns(img, tmp_path / "s.mp4", 1.0, zoom=0.9, size=SMALL)


def test_rejects_missing_image(tmp_path):
    with pytest.raises(V.VideoError, match="不存在"):
        V.ken_burns(tmp_path / "没有.png", tmp_path / "s.mp4", 1.0, size=SMALL)


def test_xfade_total_matches_formula(tmp_path):
    """拼接时长必须与 `expected_total` 的式子一致 —— 记录与成片对不上就是这里错了。"""
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    c = tmp_path / "c.mp4"
    for i, p in enumerate((a, b, c)):
        V.ken_burns(_textured(tmp_path / f"i{i}.png"), p, 2.0, size=SMALL, fps=15)

    out = tmp_path / "out.mp4"
    total = V.xfade_chain([a, b, c], out, 0.4, size=SMALL, fps=15)

    expect = V.expected_total([2.0, 2.0, 2.0], 0.4)
    assert expect == pytest.approx(5.2)              # 6 - 2×0.4
    assert total == pytest.approx(expect, abs=0.25)
    assert total < 6.0, "有转场就一定短于各段之和"


def test_single_segment_is_passthrough(tmp_path):
    a = tmp_path / "a.mp4"
    V.ken_burns(_textured(tmp_path / "i.png"), a, 1.5, size=SMALL, fps=15)
    out = tmp_path / "out.mp4"
    assert V.xfade_chain([a], out, 0.4, size=SMALL, fps=15) == pytest.approx(1.5, abs=0.15)


def test_transition_clamped_to_shortest_segment(tmp_path):
    """转场不能长过最短段的一半 —— 否则 xfade 直接报错，不如自己先夹住。"""
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    V.ken_burns(_textured(tmp_path / "i0.png"), a, 1.0, size=SMALL, fps=15)
    V.ken_burns(_textured(tmp_path / "i1.png"), b, 1.0, size=SMALL, fps=15)
    # 要 3 秒转场，但段只有 1 秒 → 必须夹到 0.49
    total = V.xfade_chain([a, b], tmp_path / "o.mp4", 3.0, size=SMALL, fps=15)
    assert total == pytest.approx(V.expected_total([1.0, 1.0], 3.0), abs=0.25)
    assert total > 1.0, "夹住之后仍应拼出成片"


def test_empty_segments_rejected(tmp_path):
    with pytest.raises(V.VideoError, match="没有"):
        V.xfade_chain([], tmp_path / "o.mp4", 0.4)
