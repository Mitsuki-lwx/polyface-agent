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
