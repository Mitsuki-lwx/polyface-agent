"""口播粗剪（T2–T10）：找停顿 → 剪 → 转写 → 字幕 → 烧字幕。

设计骨架（见 `docs/spec_roughcut.md` §5）：
- **先剪，再转写** —— 时间戳天然落在剪后时间轴上，省掉一整块"时间轴映射"逻辑。
- **所有可调值都外露**：内置默认 < 配置文件 < 命令行参数（见 `RoughcutParams`）。
- **剪/渲染交给 ffmpeg**，不自己解码编码视频；ASR 复用 `asr.py`。

实测得来的两个反直觉结论（别凭直觉改）：
- 噪声门限（-20~-40dB）**几乎不影响**结果；真正敏感的旋钮是**停顿时长阈值**。
- 视频与音频**必须用同一个剪点条件表达式**，否则音画不同步。
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .media import MediaToolMissing, _ffmpeg_bin, has_ffmpeg  # noqa: F401  (复用既有定位逻辑)

# ---------------------------------------------------------------- 失败：要"大声"

class RoughcutError(RuntimeError):
    """粗剪失败。子类各自给**互不相同**的、可执行的提示。"""


class InputMissing(RoughcutError):
    pass


class NoAudioTrack(RoughcutError):
    pass


class ToolMissing(RoughcutError):
    pass


class AsrMissing(RoughcutError):
    pass


# ---------------------------------------------------------------- 参数（T4）

@dataclass
class RoughcutParams:
    """全部可调值。**没有任何一项是写死的**：每一项都能从配置文件或命令行到达。"""

    # 剪
    pause_sec: float = 0.8          # 多长的静音算"停顿"。实测：0.3 会连逗号停顿一起剪
    noise_db: int = -30             # 静音判定门限。实测不敏感（-20~-40 结果几乎相同）
    keep_margin_sec: float = 0.08   # 剪点两端保留的气口，否则切口生硬
    # 字幕
    burn_subs: bool = True
    font: str = "Microsoft YaHei"
    font_size: int = 16
    text_color: str = "&H00FFFFFF"      # ASS 是 BGR：0xFFFFFF 白
    outline_color: str = "&H00000000"   # 黑
    outline: int = 2
    margin_v: int = 40                  # 距画面底部像素
    # ASR
    asr_model: str = "small"

    @classmethod
    def load(cls, config_path: Path | None = None) -> "RoughcutParams":
        """内置默认 ← 配置文件。命令行覆盖由 `merged()` 负责。"""
        p = cls()
        if config_path and config_path.is_file():
            data = json.loads(config_path.read_text(encoding="utf-8"))
            known = {f.name for f in fields(cls)}
            p = cls(**{k: v for k, v in data.items() if k in known})
        return p

    @classmethod
    def config_keys(cls, config_path: Path | None) -> set[str]:
        """配置文件里**真的写了**哪些项。用来在"参数来源"里区分 默认 / 配置文件 / 命令行。"""
        if not config_path or not config_path.is_file():
            return set()
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        known = {f.name for f in fields(cls)}
        return {k for k in data if k in known}

    def merged(self, **overrides) -> "RoughcutParams":
        """命令行覆盖：只接受非 None 的值（None = 用户没传，保持原值）。"""
        data = asdict(self)
        data.update({k: v for k, v in overrides.items() if v is not None})
        return RoughcutParams(**data)

    def describe(self) -> str:
        return (f"停顿阈值 {self.pause_sec}s · 门限 {self.noise_db}dB · 余量 {self.keep_margin_sec}s · "
                f"字幕 {'开' if self.burn_subs else '关'}"
                + (f"（{self.font} {self.font_size}号 距底{self.margin_v}px）" if self.burn_subs else ""))


DEFAULT_CONFIG_NAME = "roughcut.json"


# ---------------------------------------------------------------- 探测（T2）

def _run(cmd: list[str], timeout: int = 600, on_raw=None) -> subprocess.CompletedProcess:
    if on_raw:
        on_raw(" ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _run_in(cwd: Path, cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    """在指定工作目录里跑命令 —— 给"滤镜串里只放文件名"用（见 `_subtitle_vf`）。"""
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd),
                          encoding="utf-8", errors="replace", timeout=timeout)


def _run_ffmpeg(cmd: list[str], *, cwd: Path | None = None, timeout: int = 1800,
                expected_sec: float | None = None,
                on_progress=None, on_raw=None) -> tuple[int, str]:
    """跑 ffmpeg，返回 `(退出码, stderr 文本)`。

    `on_progress` 给了就用 `-progress pipe:1` 解析真实进度：
    **进度走 stdout、错误走临时文件** —— 两路都开管道容易死锁，
    而 stderr 只在最后才需要，落临时文件最简单。

    `on_raw` 只在**详细档**由调用方接上：它看到的是我们**实际在跑什么命令** ——
    这是 normal 与 verbose 之间唯一稳定存在的差异（成功时 ffmpeg 的 stderr 是空的，
    光靠错误行撑不起"详细档更详细"）。
    """
    if on_raw:
        on_raw(" ".join(cmd))
    if on_progress is None:
        cp = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd) if cwd else None,
                            encoding="utf-8", errors="replace", timeout=timeout)
        if on_raw and cp.stderr.strip():
            for line in cp.stderr.strip().splitlines():
                on_raw(line)
        return cp.returncode, cp.stderr

    cmd = cmd[:1] + ["-nostats", "-progress", "pipe:1"] + cmd[1:]
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as errf:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, text=True,
                                encoding="utf-8", errors="replace",
                                cwd=str(cwd) if cwd else None)
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and expected_sec:
                try:
                    pct = float(value) / 1_000_000 / expected_sec * 100
                except ValueError:
                    continue
                on_progress(pct)
            elif key == "progress" and value == "end":
                on_progress(100.0)
        proc.wait(timeout=timeout)
        errf.seek(0)
        err = errf.read()
        if on_raw and err.strip():
            for line in err.strip().splitlines():
                on_raw(line)
        return proc.returncode, err


def require_ffmpeg() -> None:
    if not has_ffmpeg():
        raise ToolMissing("未检测到 ffmpeg。请安装后重试（或设 FFMPEG_PATH / POLYFACE_FFMPEG 指向它）。")


def probe(path: str | Path, *, on_raw=None) -> dict:
    """探测媒体信息。失败时给**互不相同**的明确原因，而不是甩 ffmpeg 日志。"""
    require_ffmpeg()
    src = Path(path)
    if not src.is_file():
        raise InputMissing(f"输入文件不存在：{src}")
    cp = _run([_ffmpeg_bin("ffprobe"), "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(src)], on_raw=on_raw)
    if cp.returncode != 0:
        raise InputMissing(f"无法识别为媒体文件：{src}\n{cp.stderr.strip()[-200:]}")
    info = json.loads(cp.stdout or "{}")
    streams = info.get("streams", [])
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    video = [s for s in streams if s.get("codec_type") == "video"]
    if not video:
        raise InputMissing(f"没有视频轨：{src}")
    if not audio:
        raise NoAudioTrack(f"这个视频没有音轨，无法判断停顿：{src}")
    return {
        "duration": float(info.get("format", {}).get("duration") or 0),
        "width": int(video[0].get("width") or 0),
        "height": int(video[0].get("height") or 0),
        "has_audio": True,
    }


# ---------------------------------------------------------------- 找停顿（T3）

_SIL_START = re.compile(r"silence_start:\s*([\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*([\d.]+)")


def detect_pauses(path: str | Path, noise_db: int, min_sec: float, *, on_raw=None) -> list[tuple[float, float]]:
    """返回静音段 [(起, 止)]。纯解析 ffmpeg 输出，无随机性。"""
    cp = _run([_ffmpeg_bin("ffmpeg"), "-hide_banner", "-nostats",
               "-i", str(path), "-af", f"silencedetect=noise={noise_db}dB:d={min_sec}",
               "-f", "null", "-"], on_raw=on_raw)
    starts = [float(x) for x in _SIL_START.findall(cp.stderr)]
    ends = [float(x) for x in _SIL_END.findall(cp.stderr)]
    return list(zip(starts, ends))


def plan_cuts(pauses: list[tuple[float, float]], keep_margin: float,
              total: float) -> list[tuple[float, float]]:
    """静音段 → 实际要剪掉的区间（两端各留一点气口；过短的丢弃）。"""
    cuts = []
    for start, end in pauses:
        s, e = start + keep_margin, end - keep_margin
        if e - s <= 0:
            continue
        cuts.append((round(s, 3), min(round(e, 3), total)))
    return cuts


def cut_seconds(cuts: list[tuple[float, float]]) -> float:
    return round(sum(e - s for s, e in cuts), 3)


# ---------------------------------------------------------------- 剪（T6）

def _select_expr(cuts: list[tuple[float, float]]) -> str:
    """剪点 → 一条条件表达式。视频音频共用它，否则音画不同步。"""
    return "+".join(f"between(t,{s},{e})" for s, e in cuts) or "0"


def cut_video(src: str | Path, dst: str | Path, cuts: list[tuple[float, float]],
              crf: int = 23, preset: str = "veryfast", *,
              on_progress=None, expected_sec: float | None = None, on_raw=None) -> None:
    if not cuts:
        raise RoughcutError("没有可剪的停顿（未找到停顿或阈值过大）")
    expr = _select_expr(cuts)
    cmd = [_ffmpeg_bin("ffmpeg"), "-y", "-v", "error", "-i", str(src),
           "-vf", f"select='not({expr})',setpts=N/FRAME_RATE/TB",
           "-af", f"aselect='not({expr})',asetpts=N/SR/TB",
           "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", str(dst)]
    rc_, err = _run_ffmpeg(cmd, expected_sec=expected_sec, on_progress=on_progress, on_raw=on_raw)
    if rc_ != 0:
        raise RoughcutError(f"剪裁失败：{err.strip()[-300:]}")


# ---------------------------------------------------------------- 转写与字幕（T7–T9）

def extract_wav(src: str | Path, dst: str | Path, rate: int = 16000, *, on_raw=None) -> None:
    """抽 16k 单声道 wav。**必须走 ffmpeg**：faster-whisper 内部的 PyAV 解码在 av19 上已坏。"""
    cp = _run([_ffmpeg_bin("ffmpeg"), "-y", "-v", "error", "-i", str(src),
               "-vn", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(dst)], on_raw=on_raw)
    if cp.returncode != 0:
        raise RoughcutError(f"抽取音轨失败：{cp.stderr.strip()[-300:]}")


def srt_timestamp(t: float) -> str:
    h, rem = divmod(max(t, 0.0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


def segments_to_srt(segments: list[dict]) -> str:
    out = []
    for i, seg in enumerate(segments, 1):
        out.append(f"{i}\n{srt_timestamp(seg['start'])} --> {srt_timestamp(seg['end'])}\n"
                   f"{seg['text'].strip()}\n")
    return "\n".join(out)


def _style(params: RoughcutParams) -> str:
    return (f"FontName={params.font},FontSize={params.font_size},"
            f"PrimaryColour={params.text_color},OutlineColour={params.outline_color},"
            f"BorderStyle=1,Outline={params.outline},Shadow=0,Alignment=2,"
            f"MarginV={params.margin_v}")


def _subtitle_vf(srt: Path, params: RoughcutParams) -> tuple[str, Path]:
    """返回 (滤镜参数, 工作目录)。

    **为什么不直接把绝对路径拼进滤镜串**：ffmpeg 的滤镜串有自己的分词规则，
    Windows 盘符的 `:` 和路径里的空格都会把参数切碎 —— 实测报错
    `value "…/out.srt" as image size`。而这个坑在**相对路径**下不会出现
    （实验目录里就是相对路径，所以一直没暴露），特别容易漏。
    绕开的办法：把工作目录切到字幕所在目录，滤镜里只给**文件名**。
    """
    return f"subtitles={srt.name}:force_style='{_style(params)}'", srt.parent


def burn_subtitles(src: str | Path, dst: str | Path, srt: str | Path,
                   params: RoughcutParams, *, on_progress=None,
                   expected_sec: float | None = None, on_raw=None) -> None:
    srt = Path(srt).resolve()
    vf, cwd = _subtitle_vf(srt, params)
    cmd = [_ffmpeg_bin("ffmpeg"), "-y", "-v", "error",
           "-i", str(Path(src).resolve()), "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-pix_fmt", "yuv420p", "-c:a", "copy", str(Path(dst).resolve())]
    rc_, err = _run_ffmpeg(cmd, cwd=cwd, expected_sec=expected_sec, on_progress=on_progress, on_raw=on_raw)
    if rc_ != 0:
        raise RoughcutError(f"烧字幕失败：{err.strip()[-300:]}")


def preview_frame(src: str | Path, dst: str | Path, srt: str | Path | None,
                  params: RoughcutParams, at: float = 1.0) -> None:
    """只渲一帧 —— 用来确认字幕样式，不必等整片渲完。"""
    cwd, vf = Path.cwd(), "null"
    if srt and params.burn_subs:
        vf, cwd = _subtitle_vf(Path(srt).resolve(), params)
    cp = _run_in(cwd, [_ffmpeg_bin("ffmpeg"), "-y", "-v", "error", "-ss", str(at),
                       "-i", str(Path(src).resolve()), "-vf", vf,
                       "-frames:v", "1", str(Path(dst).resolve())])
    if cp.returncode != 0:
        raise RoughcutError(f"预览帧渲染失败：{cp.stderr.strip()[-300:]}")


# ---------------------------------------------------------------- 摘要（T10）

def summarize(cuts: list[tuple[float, float]], before: float, after: float,
              params: RoughcutParams) -> str:
    lines = [f"找到 {len(cuts)} 段停顿："]
    for i, (s, e) in enumerate(cuts, 1):
        lines.append(f"  {i}. {s:6.2f}s → {e:6.2f}s  （{e - s:.2f}s）")
    lines.append(f"剪掉共 {cut_seconds(cuts):.2f}s：{before:.2f}s → {after:.2f}s"
                 f"（省 {(before - after) / before * 100:.1f}%）")
    lines.append(f"参数：{params.describe()}")
    return "\n".join(lines)
