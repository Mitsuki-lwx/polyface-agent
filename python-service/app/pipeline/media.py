"""音视频媒体处理（FR-51）：探测 / 字幕提取 / 音频抽取。

- **全本地**：不上传任何数据（UC-12 硬约束）
- **唯一硬依赖** ffmpeg/ffprobe；不可用时抛 `MediaToolMissing`，由调用方降级为 needs_manual
- 字幕提取优先：口播类视频常带字幕轨，属零算力的秒级路径
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..config import get_settings

logger = logging.getLogger(__name__)

PROBE_TIMEOUT = 30
FFMPEG_TIMEOUT = 300

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v", ".wmv", ".mpg", ".mpeg", ".ts"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT


class MediaToolMissing(RuntimeError):
    """ffmpeg / ffprobe 不可用。"""


class MediaInvalid(RuntimeError):
    """文件不存在、扩展名不支持，或无法识别为媒体。"""


def _ffmpeg_bin(name: str) -> str:
    """定位 ffmpeg/ffprobe：配置项 > 环境变量 POLYFACE_FFMPEG > PATH。"""
    setting = (getattr(get_settings(), "ffmpeg_path", "") or os.getenv("POLYFACE_FFMPEG", "")).strip()
    if setting:
        p = Path(setting)
        cand = p / name if p.is_dir() else p
        for probe_path in (cand, cand.with_suffix(".exe")):
            if probe_path.is_file():
                return str(probe_path)
    found = shutil.which(name)
    if not found:
        raise MediaToolMissing(f"未检测到 {name}，请安装 ffmpeg 后重试")
    return found


def has_ffmpeg() -> bool:
    try:
        _ffmpeg_bin("ffmpeg")
        _ffmpeg_bin("ffprobe")
        return True
    except MediaToolMissing:
        return False


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def _validate(path: str) -> Path:
    src = Path(path)
    if not src.is_file():
        raise MediaInvalid("文件不存在或不是普通文件")
    if src.suffix.lower() not in MEDIA_EXT:
        raise MediaInvalid(f"不支持的扩展名：{src.suffix or '(无扩展名)'}")
    return src


def probe(path: str) -> dict:
    """探测媒体信息并给出推荐入料模式。"""
    src = _validate(path)
    ffprobe = _ffmpeg_bin("ffprobe")
    try:
        cp = _run([ffprobe, "-v", "error", "-print_format", "json",
                   "-show_streams", "-show_format", str(src)], PROBE_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise MediaInvalid("探测超时，文件可能已损坏") from e

    if cp.returncode != 0:
        raise MediaInvalid("无法识别为媒体文件（ffprobe 解析失败）")

    try:
        data = json.loads(cp.stdout or "{}")
    except json.JSONDecodeError as e:
        raise MediaInvalid("媒体信息解析失败") from e

    streams = data.get("streams", []) or []
    fmt = data.get("format", {}) or {}
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]

    duration = 0.0
    candidates = [fmt.get("duration")] + [s.get("duration") for s in audio_streams]
    for cand in candidates:
        try:
            val = float(cand)
            if val > 0:
                duration = round(val, 2)
                break
        except (TypeError, ValueError):
            continue

    subtitles = [
        {
            "index": s.get("index"),
            "codec": s.get("codec_name", ""),
            "lang": ((s.get("tags") or {}).get("language") or ""),
        }
        for s in sub_streams
    ]

    has_audio = bool(audio_streams)
    has_subtitle = bool(subtitles)
    recommended = "subtitle" if has_subtitle else ("asr" if has_audio else "manual")

    return {
        "filename": src.name,
        "format": (fmt.get("format_name") or src.suffix.lstrip(".")).split(",")[0],
        "duration_sec": duration,
        "has_audio": has_audio,
        "has_subtitle": has_subtitle,
        "subtitle_streams": subtitles,
        "recommended_mode": recommended,
    }


_TIMELINE = re.compile(r"-->")
_TAG = re.compile(r"<[^>]+>|\{\\[^}]*\}")
_ORDINAL = re.compile(r"^\s*\d+\s*$")


def srt_to_text(srt: str) -> str:
    """SRT → 纯文本：去序号、去时间轴、剥标记、相邻重复去重。"""
    out: list[str] = []
    for raw in (srt or "").splitlines():
        line = raw.strip()
        if not line or _ORDINAL.match(line) or _TIMELINE.search(line):
            continue
        line = _TAG.sub("", line).strip()
        if not line:
            continue
        if out and out[-1] == line:      # 卡拉OK式字幕常见相邻重复
            continue
        out.append(line)
    return "\n".join(out)


def extract_subtitle(path: str, stream_index: int = 0) -> str:
    """提取内嵌字幕轨为纯文本；无字幕轨或提取失败返回空串（不抛异常）。"""
    ffmpeg = _ffmpeg_bin("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="polyface-sub-") as td:
        out = Path(td) / "sub.srt"
        try:
            cp = _run([ffmpeg, "-y", "-v", "error", "-i", str(path),
                       "-map", f"0:s:{stream_index}", "-f", "srt", str(out)], FFMPEG_TIMEOUT)
        except subprocess.TimeoutExpired:
            logger.warning("subtitle extraction timeout: %s", path)
            return ""
        if cp.returncode != 0 or not out.exists():
            logger.warning("subtitle extraction failed: %s", (cp.stderr or "")[:200])
            return ""
        return srt_to_text(out.read_text(encoding="utf-8", errors="replace"))


def extract_audio_wav(path: str) -> str:
    """抽取 16kHz 单声道 wav 供 ASR 使用。返回临时文件路径，**调用方负责删除**。"""
    ffmpeg = _ffmpeg_bin("ffmpeg")
    fd, wav = tempfile.mkstemp(suffix=".wav", prefix="polyface-asr-")
    os.close(fd)
    try:
        cp = _run([ffmpeg, "-y", "-v", "error", "-i", str(path),
                   "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", wav], FFMPEG_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        Path(wav).unlink(missing_ok=True)
        raise MediaInvalid("音频抽取超时") from e
    if cp.returncode != 0 or not Path(wav).exists():
        Path(wav).unlink(missing_ok=True)
        raise MediaInvalid("音频抽取失败（无音轨或文件损坏）")
    return wav
