"""本地 ASR 适配层（FR-51）：faster-whisper 作为**可选**依赖。

设计原则
- **不做硬依赖**：检测到就用，没有则由调用方降级为 `needs_manual` + 安装指引。
  否则 torch/模型（数百 MB~数 GB）会把「本地一键启动」这个核心卖点拖垮。
- **本地推理**：不上传任何数据（UC-12 硬约束），排除一切云 ASR。
- **模型缓存**：进程内复用，避免每次请求重新加载。
"""
from __future__ import annotations

import logging
import os

from ..config import get_settings

logger = logging.getLogger(__name__)

_MODEL_CACHE: dict[str, object] = {}

INSTALL_HINT = (
    "未检测到本地转写组件。可执行 `pip install faster-whisper` 后重启服务"
    "（首次使用会下载模型，约 244MB）；或直接粘贴文案后再解析。"
)

# Whisper 对中文常输出**繁体**（实测同一段音频："怎麽把一篇長文"）。
# 给一句简体中文的 initial_prompt 就能把它拉回简体（实测繁体字数 7 → 0），
# 且标点保留完整；用 "简体中文" 这种短词反而会把标点吃掉，所以用完整句子。
ZH_SIMPLIFIED_PROMPT = "以下是普通话的句子。"


def is_available() -> bool:
    """faster-whisper 是否可导入。"""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def model_size() -> str:
    """模型尺寸：环境变量 POLYFACE_ASR_MODEL > 配置项 asr_model > small。"""
    return (os.getenv("POLYFACE_ASR_MODEL") or "").strip() \
        or (getattr(get_settings(), "asr_model", "") or "").strip() \
        or "small"


def _load_model(size: str):
    if size not in _MODEL_CACHE:
        from faster_whisper import WhisperModel
        logger.info("loading faster-whisper model=%s device=cpu compute_type=int8", size)
        _MODEL_CACHE[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _MODEL_CACHE[size]


def transcribe(wav_path: str, language: str = "zh") -> str:
    """转写 16kHz 单声道 wav。ASR 不可用或失败时抛 RuntimeError（调用方降级处理）。"""
    if not is_available():
        raise RuntimeError(INSTALL_HINT)
    size = model_size()
    model = _load_model(size)
    segments, _info = model.transcribe(wav_path, language=language, vad_filter=True)
    return "".join(getattr(s, "text", "") for s in segments).strip()


def transcribe_segments(wav_path: str, language: str = "zh", *,
                        on_progress=None, total_sec: float | None = None) -> list[dict]:
    """带时间戳的转写：返回 `[{start, end, text}]`。粗剪出字幕用（FR-52 的前置）。

    `on_progress`：可选进度回调。`segments` 是**生成器**，边出边报，
    所以进度是真实的（不是猜的），百分比按 `seg.end / total_sec` 换算。

    **为什么不用 `transcribe()`**：它把 `segments` 拼成纯文本，时间戳直接丢了；
    而字幕没有时间戳就等于没有。

    **为什么要自己解码音频**（而不是把视频路径直接丢给模型）：
    实测 `faster-whisper 1.2.1` 与 `av 19` 不兼容 —— 它内部走 PyAV 解码时会调
    `av.open(..., metadata_errors="ignore")`，而 av19 已移除该参数，直接抛 `TypeError`。
    传 **float32 数组**时不会走那条解码路径，于是绕开了这个坑。
    （音频仍由 ffmpeg 解成 16k 单声道 wav —— 那是我们本来就要做的一步。）
    """
    if not is_available():
        raise RuntimeError(INSTALL_HINT)
    import wave

    import numpy as np

    with wave.open(str(wav_path), "rb") as w:
        if w.getnchannels() != 1 or w.getframerate() != 16000:
            raise RuntimeError(
                f"转写要求 16kHz 单声道 wav，收到 {w.getframerate()}Hz/{w.getnchannels()}ch")
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0

    model = _load_model(model_size())
    raw, _info = model.transcribe(audio, language=language, vad_filter=True,
                                  initial_prompt=ZH_SIMPLIFIED_PROMPT)
    total = total_sec or (len(audio) / 16000)
    out: list[dict] = []
    for s in raw:
        text = (s.text or "").strip()
        if text:
            out.append({"start": float(s.start), "end": float(s.end), "text": text})
        if on_progress and total > 0:
            on_progress(min(100.0, float(s.end) / total * 100))
    if on_progress:
        on_progress(100.0)
    return out
