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

    t = _clamp_transition(transition, durs)

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


def segment_durations(scene_seconds: list[float], transition: float) -> list[float]:
    """分镜时长 → **画面段**时长（转场重叠的补偿）。

    为什么需要它：`xfade` 让成片比各段之和短 `(n-1)×转场`。而**配音不能重叠**
    （两句话叠在一起就听不清了）—— 直接拼会让成片越往后越比配音短，最后几条字幕全错位。

    做法：每段多给一个转场时长（最后一段除外）。重叠之后总长正好 = 配音总长。
    """
    if not scene_seconds:
        return []
    if len(scene_seconds) == 1:
        return [round(scene_seconds[0], 3)]
    t = _clamp_transition(transition, scene_seconds)
    return [round(d + t, 3) for d in scene_seconds[:-1]] + [round(scene_seconds[-1], 3)]


def _clamp_transition(transition: float, durations: list[float]) -> float:
    """转场不能长过最短段的一半，否则 xfade 直接报错 —— 先自己夹住。"""
    if not durations:
        return max(0.05, float(transition))
    return max(0.05, min(float(transition), min(durations) / 2 - 0.01))


def title_card(img: str | Path, out: str | Path, seconds: float, title: str, *,
               size: tuple[int, int] = DEFAULT_SIZE, fps: int = DEFAULT_FPS,
               font_file: str | None = None, title_ratio: float = 0.085,
               bg: str = "0x0E0E14", zoom: float = 1.03, crf: int = 20,
               on_progress=None, on_raw=None) -> None:
    """**深色底 + 居中大标题**（用户给的参考片就是这种调性）。

    做法：先渲一张卡片 PNG，再交给 `ken_burns` 缓动 —— 复用现有那条路，
    不为了"换个背景"再写一套运动逻辑。

    文字走 `textfile=` 而不是把内容拼进滤镜串：`drawtext` 的转义规则很坑
    （冒号、引号、百分号都要小心），写文件一劳永逸。
    """
    rc.require_ffmpeg()
    src = Path(img)                                  # 复用签名：这里当"临时目录"用
    work = src if src.is_dir() else src.parent
    work.mkdir(parents=True, exist_ok=True)
    w, h = size
    font = font_file or _cjk_font()
    if not font:
        raise VideoError("找不到中文字体文件（试过 msyh.ttc / Deng.ttf / simhei.ttf）")

    txt = work / f"_card-{abs(hash((title, seconds, size))) % 10**8}.txt"
    txt.write_text((title or " ").replace("\n", "\n"), encoding="utf-8", newline="\n")
    card = work / f"_card-{abs(hash((title, seconds, size))) % 10**8}.png"

    # 大标题字号：**按宽度自适应**，不能只看高度。
    # 实测踩过：8 个汉字 × 54px = 432px > 360px 画宽，两侧直接被切掉。
    fs_h = int(h * title_ratio)
    fs_w = int((w * 0.86) / max(1.0, _em_width(title)))
    fs = max(20, min(fs_h, fs_w))
    line = int(fs * 1.25)
    vf = (f"drawtext=fontfile='{_esc_path(font)}':textfile='{_esc_path(str(txt))}':"
          f"fontcolor=0xFFFFFF:fontsize={fs}:line_spacing={int(fs * 0.25)}:"
          f"x=(w-text_w)/2:y=(h-text_h)/2")
    code, err = rc._run_ffmpeg([rc._ffmpeg_bin("ffmpeg"), "-y", "-v", "error",
                                "-f", "lavfi", "-i", f"color=c={bg}:s={w}x{h}",
                                "-vf", vf, "-frames:v", "1", str(card)], on_raw=on_raw)
    if code != 0:
        raise VideoError(f"渲染标题卡失败：{err.strip()[:300]}")

    ken_burns(card, out, seconds, zoom=zoom, size=size, fps=fps, crf=crf,
              on_progress=on_progress, on_raw=on_raw)
    try:
        txt.unlink()
        card.unlink()
    except OSError:
        pass


def _em_width(text: str) -> float:
    """最长一行大约多少个**字宽**（汉字算 1、ASCII 算 0.55）。

    只是估个量级，够用 —— 目的是别让字撑出画面，不是做精确排版。
    """
    best = 0.0
    for line in (text or " ").split("\n"):
        w = sum(1.0 if ord(ch) > 0x2E80 else 0.55 for ch in line)
        best = max(best, w)
    return best


def _esc_path(p: str) -> str:
    """滤镜串里的路径：Windows 盘符的 `:` 必须转义成 `\\:`（**一层**）。

    实测：一层可用、两层反而报 `No option name near '/Windows/...'` —— 试出来的，
    不是猜的。
    """
    return str(p).replace("\\", "/").replace(":", "\\:")


_CJK_FONTS = ("C:/Windows/Fonts/Deng.ttf", "C:/Windows/Fonts/msyh.ttc",
              "C:/Windows/Fonts/simhei.ttf", "/System/Library/Fonts/PingFang.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")


def _cjk_font() -> str | None:
    for p in _CJK_FONTS:
        if Path(p).is_file():
            return p
    return None


def expected_total(durations: list[float], transition: float) -> float:
    """转场拼接后的理论时长。**与 `xfade_chain` 用同一个式子** —— 两边各算一遍必然分叉。"""
    if not durations:
        return 0.0
    if len(durations) == 1:
        return round(durations[0], 3)
    t = _clamp_transition(transition, durations)
    return round(sum(durations) - (len(durations) - 1) * t, 3)


def mix_audio(voice_wav: str | Path, out: str | Path, *, total: float,
              bgm: str | Path | None = None, bgm_offset: float = 0.0,
              bgm_volume_db: float = -18.0, bgm_fade_out: float = 1.5,
              on_raw=None) -> None:
    """配音 + BGM → 一条音轨。

    - `bgm_offset < 0`：**跳过 BGM 开头** |offset| 秒（音乐前奏通常不在重拍上）
    - `bgm_offset > 0`：BGM 延迟 offset 秒开始（开头留静音 —— 所以上游尽量不选正偏移）
    - BGM 比成片短时**循环**；结尾淡出
    - `total` 之后一律截断：音轨不能长过画面
    """
    rc.require_ffmpeg()
    voice = Path(voice_wav)
    if not voice.is_file():
        raise VideoError(f"配音文件不存在：{voice}")
    if bgm is None:
        code, err = rc._run_ffmpeg([rc._ffmpeg_bin("ffmpeg"), "-y", "-v", "error",
                                    "-i", str(voice), "-t", f"{total:.3f}",
                                    "-c:a", "aac", "-b:a", "192k", str(out)], on_raw=on_raw)
        if code != 0:
            raise VideoError(f"音轨输出失败：{err.strip()[:200]}")
        return

    bgm_p = Path(bgm)
    if not bgm_p.is_file():
        raise VideoError(f"BGM 不存在：{bgm_p}")

    fade = max(0.0, min(float(bgm_fade_out), total / 2))
    chain = [f"volume={float(bgm_volume_db)}dB"]
    if bgm_offset < 0:
        chain.append(f"atrim=start={abs(float(bgm_offset)):.3f}")
    chain.append("asetpts=PTS-STARTPTS")
    if bgm_offset > 0:
        chain.append(f"adelay={int(float(bgm_offset) * 1000)}|{int(float(bgm_offset) * 1000)}")
    if fade > 0:
        chain.append(f"afade=t=out:st={max(0.0, total - fade):.3f}:d={fade:.3f}")
    bgm_chain = ",".join(chain)

    filt = (f"[1:a]{bgm_chain}[bg];"
            f"[0:a]aresample=48000,asetpts=PTS-STARTPTS[vo];"
            f"[bg]aresample=48000[bg2];"
            # duration=first：以配音长度为基准，BGM 不会把成片拖长
            f"[vo][bg2]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
    cmd = [rc._ffmpeg_bin("ffmpeg"), "-y", "-v", "error",
           "-i", str(voice), "-stream_loop", "-1", "-i", str(bgm_p),
           "-filter_complex", filt, "-map", "[a]", "-t", f"{total:.3f}",
           "-c:a", "aac", "-b:a", "192k", str(out)]
    code, err = rc._run_ffmpeg(cmd, on_raw=on_raw)
    if code != 0:
        raise VideoError(f"混音失败：{err.strip()[:300]}")


def mux(video: str | Path, audio: str | Path, out: str | Path,
        srt: str | Path | None, params, *, crf: int = 20, preset: str = "veryfast",
        on_progress=None, on_raw=None) -> None:
    """画面 + 音轨（+ 可选烧字幕）→ 最终成片。

    烧字幕复用 `roughcut._subtitle_vf` —— 它处理了"滤镜串里的 Windows 盘符与空格"
    那个坑（把 cwd 切到字幕目录、滤镜里只给文件名），不重新踩一遍。
    """
    rc.require_ffmpeg()
    # 烧字幕那条路会把 cwd 切到字幕目录（`_subtitle_vf` 的设计：滤镜串里只放文件名，
    # 绕开 Windows 盘符/空格的坑）—— 于是所有**相对路径都会失效**。全部先绝对化。
    vid, aud, dst = Path(video).resolve(), Path(audio).resolve(), Path(out).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    base = [rc._ffmpeg_bin("ffmpeg"), "-y", "-v", "error",
            "-i", str(vid), "-i", str(aud)]
    if srt is not None and getattr(params, "burn_subs", False):
        vf, cwd = rc._subtitle_vf(Path(srt).resolve(), params)
        cmd = base + ["-vf", vf, "-map", "0:v", "-map", "1:a",
                      "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                      "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                      "-shortest", str(dst)]
        code, err = rc._run_ffmpeg(cmd, cwd=cwd, on_progress=on_progress, on_raw=on_raw)
    else:
        cmd = base + ["-map", "0:v", "-map", "1:a",
                      "-c:v", "copy", "-c:a", "copy", "-shortest", str(dst)]
        code, err = rc._run_ffmpeg(cmd, on_progress=on_progress, on_raw=on_raw)
    if code != 0:
        raise VideoError(f"合成失败：{err.strip()[:300]}")
