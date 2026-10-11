"""画面与合成（`docs/spec_b1.md` T2/T4/T6）。

**为什么图必须动**：`docs/49` §5.1 记着抖音官方把「模板感强的图片轮播」列为**低质**。
动起来的图与死板轮播在观感上是两回事 —— 缓动不是装饰，是对那条平台规则的直接回应。

复用 `roughcut.py` 的 ffmpeg 底座（`_run_ffmpeg` / `_ffmpeg_bin` / `require_ffmpeg`）：
进度解析、错误文本、超时都在那里统一处理过，不另起一套。
"""
from __future__ import annotations

from pathlib import Path

from . import roughcut as rc

# 竖屏 1080×1920 是抖音/小红书的主场（docs/49 §7 Q4 选定的三个平台里两个是竖屏）
DEFAULT_SIZE = (1080, 1920)
DEFAULT_FPS = 25


class VideoError(rc.RoughcutError):
    """成片失败。继承粗剪的错误基类 —— 上层已有一套错误处理，不必再学一种。"""


def ken_burns(img: str | Path, out: str | Path, seconds: float, *,
              zoom: float = 1.08, size: tuple[int, int] = DEFAULT_SIZE,
              fps: int = DEFAULT_FPS, crf: int = 20, preset: str = "veryfast",
              on_progress=None, on_raw=None) -> None:
    """一张图 + 一个时长 → 一段"缓慢推拉"的视频。

    `zoom=1.0` 就是静止 —— 那会退化成被平台打击的轮播形态，所以默认不是 1.0。
    """
    rc.require_ffmpeg()
    src = Path(img)
    if not src.is_file():
        raise VideoError(f"图片不存在：{src}")
    if seconds <= 0:
        raise VideoError(f"时长必须为正：{seconds}")
    if zoom < 1.0:
        raise VideoError(f"缓动强度不能小于 1.0（那会让画面缩小/抖动）：{zoom}")

    w, h = size
    frames = max(1, int(round(seconds * fps)))
    # 先放大再 zoompan：原图直接 zoompan 容易因为像素级抖动而"呼吸"
    pre = 2
    amount = zoom - 1.0
    vf = (
        f"scale={w * pre}:{h * pre}:force_original_aspect_ratio=increase,"
        f"crop={w * pre}:{h * pre},"
        # on = 当前输出帧号；线性推近到 zoom 倍
        f"zoompan=z='1+{amount:.6f}*on/{frames}':d={frames}"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps},"
        f"format=yuv420p"
    )
    cmd = [rc._ffmpeg_bin("ffmpeg"), "-y", "-v", "error",
           "-i", str(src), "-vf", vf,
           "-frames:v", str(frames),
           "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
           "-pix_fmt", "yuv420p", str(out)]
    code, err = rc._run_ffmpeg(cmd, expected_sec=seconds, on_progress=on_progress, on_raw=on_raw)
    if code != 0:
        raise VideoError(f"生成画面段失败：{err.strip()[:300]}")


def frame_at(src: str | Path, out: str | Path, at: float = 0.0) -> None:
    """抽一帧 —— 核对用（"首尾帧是否不同"这类断言靠它）。"""
    rc.require_ffmpeg()
    code, err = rc._run_ffmpeg([rc._ffmpeg_bin("ffmpeg"), "-y", "-v", "error",
                                "-ss", f"{max(0.0, at):.3f}", "-i", str(src),
                                "-frames:v", "1", str(out)])
    if code != 0:
        raise VideoError(f"抽帧失败：{err.strip()[:200]}")


def xfade_chain(segments: list[Path], out: Path, transition: float, *,
                size: tuple[int, int] = DEFAULT_SIZE, fps: int = DEFAULT_FPS,
                crf: int = 20, preset: str = "veryfast",
                on_progress=None, on_raw=None) -> float:
    """把若干段用交叉淡入拼起来，返回**成片时长**。

    时长 = 各段之和 − (段数−1) × 转场时长。这个式子必须与调用方算的一致，
    否则"记录里的时长"和"实际成片"会对不上（checklist §7 就查这个）。

    每段先统一到同尺寸/帧率/像素格式 —— `xfade` 要求两端一致，不统一会直接报错。
    """
    if not segments:
        raise VideoError("没有可拼接的画面段")
    rc.require_ffmpeg()
    w, h = size
    durs = [rc.probe(s, require_audio=False)["duration"] for s in segments]

    if len(segments) == 1:
        code, err = rc._run_ffmpeg([rc._ffmpeg_bin("ffmpeg"), "-y", "-v", "error",
                                    "-i", str(segments[0]),
                                    "-vf", f"scale={w}:{h},fps={fps},format=yuv420p",
                                    "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                                    "-pix_fmt", "yuv420p", str(out)])
        if code != 0:
            raise VideoError(f"单段输出失败：{err.strip()[:200]}")
        return round(rc.probe(out, require_audio=False)["duration"], 3)

    # 转场不能长过最短段的一半，否则 xfade 会报"时长不足"
    t = max(0.05, min(float(transition), min(durs) / 2 - 0.01))

    norm = [f"[{i}:v]scale={w}:{h},fps={fps},format=yuv420p[n{i}]"
            for i in range(len(segments))]
    chain = []
    prev, acc = "n0", durs[0]
    for i in range(1, len(segments)):
        label = f"x{i}"
        chain.append(f"[{prev}][n{i}]xfade=transition=fade:duration={t:.3f}"
                     f":offset={acc - t:.3f}[{label}]")
        prev = label
        acc = acc + durs[i] - t
    filt = ";".join(norm + chain)

    inputs: list[str] = []
    for s in segments:
        inputs += ["-i", str(s)]
    cmd = [rc._ffmpeg_bin("ffmpeg"), "-y", "-v", "error", *inputs,
           "-filter_complex", filt, "-map", f"[{prev}]",
           "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
           "-pix_fmt", "yuv420p", str(out)]
    code, err = rc._run_ffmpeg(cmd, expected_sec=acc, on_progress=on_progress, on_raw=on_raw)
    if code != 0:
        raise VideoError(f"拼接失败：{err.strip()[:300]}")
    return round(rc.probe(out, require_audio=False)["duration"], 3)


def expected_total(durations: list[float], transition: float) -> float:
    """转场拼接后的理论时长。**与 `xfade_chain` 用同一个式子** —— 两边各算一遍必然分叉。"""
    if not durations:
        return 0.0
    if len(durations) == 1:
        return round(durations[0], 3)
    t = max(0.05, min(float(transition), min(durations) / 2 - 0.01))
    return round(sum(durations) - (len(durations) - 1) * t, 3)
