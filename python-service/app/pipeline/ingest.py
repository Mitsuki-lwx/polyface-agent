"""音视频入料编排（FR-51）：探测 → 字幕优先 → ASR 可选 → 手填降级。

状态矩阵（**任何失败都降级为 needs_manual + 可执行 hint，绝不抛 5xx**）：

| 场景 | status | source |
|---|---|---|
| 有字幕轨且提取成功 | subtitle_extracted | subtitle |
| 有字幕轨但提取为空 | needs_manual | none |
| 无字幕 + ASR 可用 | transcribed | asr |
| 无字幕 + ASR 未安装 | needs_manual | none（含安装指引） |
| 文件不可读 / 非媒体 | needs_manual | none（含原因） |
| ffmpeg 缺失 | needs_manual | none（含安装指引） |

工具始终可用：至少能引导用户「直接粘贴文案」走原有链路。
"""
from __future__ import annotations

import logging
import os

from ..schemas import TranscribeResponse
from . import asr, media

logger = logging.getLogger(__name__)

HINT_SUBTITLE_OK = "已从视频字幕轨提取文字，请核对后使用。"
HINT_SUBTITLE_EMPTY = "字幕轨为空或无法解析，请手动粘贴文案。"
HINT_ASR_OK = "本地 ASR 转写完成，请重点核对同音字、专有名词与数字。"
HINT_ASR_EMPTY = "转写结果为空，请手动粘贴文案。"
HINT_FFMPEG_MISSING = "未检测到 ffmpeg，请安装后重试（也可用环境变量 POLYFACE_FFMPEG 指向安装目录）。"
HINT_BAD_FILE = "无法识别该文件，请确认为常见音视频格式（mp4/mov/mkv/mp3/m4a/wav 等）。"
HINT_MANUAL = "该文件既无字幕轨也无可用转写组件，请直接粘贴文案后解析。"


def _manual(hint: str) -> TranscribeResponse:
    return TranscribeResponse(status="needs_manual", source="none", hint=hint)


def run_probe(path: str) -> dict:
    """媒体探测（/probe）。异常交由调用方转 400。"""
    info = media.probe(path)
    info["ffmpeg_available"] = media.has_ffmpeg()
    info["asr_available"] = asr.is_available()
    return info


def run_transcribe(path: str, mode: str = "auto") -> TranscribeResponse:
    """入料主流程：按 mode / 推荐路径产出文字，失败一律降级。"""
    if not media.has_ffmpeg():
        return _manual(HINT_FFMPEG_MISSING)

    try:
        info = media.probe(path)
    except media.MediaToolMissing:
        return _manual(HINT_FFMPEG_MISSING)
    except media.MediaInvalid as e:
        return _manual(f"{HINT_BAD_FILE}（{e}）")

    want = info["recommended_mode"] if mode == "auto" else mode

    # 1) 字幕路径（零算力优先）
    if want == "subtitle" and info["has_subtitle"]:
        text = media.extract_subtitle(path)
        if text.strip():
            return TranscribeResponse(status="subtitle_extracted", text=text,
                                      source="subtitle", chars=len(text),
                                      hint=HINT_SUBTITLE_OK)
        return _manual(HINT_SUBTITLE_EMPTY)

    # 2) ASR 路径（可选增强）
    if want == "asr" and info["has_audio"]:
        if not asr.is_available():
            return _manual(asr.INSTALL_HINT)
        wav = None
        try:
            wav = media.extract_audio_wav(path)
            text = asr.transcribe(wav)
            if text.strip():
                return TranscribeResponse(status="transcribed", text=text,
                                          source="asr", chars=len(text),
                                          hint=HINT_ASR_OK)
            return _manual(HINT_ASR_EMPTY)
        except Exception as e:  # noqa: BLE001 — ASR/抽取失败均降级，不冒泡
            logger.warning("asr path failed: %s", e)
            return _manual(f"转写失败（{e}），请手动粘贴文案。")
        finally:
            if wav:
                try:
                    os.unlink(wav)
                except OSError:
                    pass

    # 3) 兜底：即便用户强制 asr，只要有字幕也尝试提取
    if info["has_subtitle"]:
        text = media.extract_subtitle(path)
        if text.strip():
            return TranscribeResponse(status="subtitle_extracted", text=text,
                                      source="subtitle", chars=len(text),
                                      hint=HINT_SUBTITLE_OK)
    return _manual(HINT_MANUAL)
