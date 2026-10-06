"""Pydantic 数据模型：素材解析的输入输出契约。"""
from typing import Literal

from pydantic import BaseModel, Field

# 事实类型：data=客观数据/事实；story=经历/故事；opinion=观点/感受
FactType = Literal["data", "story", "opinion"]


class AnalyzeRequest(BaseModel):
    raw_text: str = Field(..., min_length=1, description="源素材全文")
    source_kind: str = Field("general", description="general|长文|口播稿|大纲|笔记")
    title: str | None = Field(None, description="素材标题(可选)")


class Fact(BaseModel):
    text: str = Field(..., description="事实原文(必须能在素材中找到依据)")
    type: FactType


class StructuredMaterial(BaseModel):
    core_message: str = Field(..., description="核心观点/卖点，一句话")
    tone: str = Field(..., description="素材的语气基调")
    audience: str = Field(..., description="目标受众画像")
    facts: list[Fact] = Field(default_factory=list, description="事实清单(防幻觉依据)")


class AnalyzeResponse(BaseModel):
    structured: StructuredMaterial
    used_mock: bool = Field(..., description="本次是否走 mock(离线)模式")


# ============================================================ 音视频入料(FR-51)
IngestStatus = Literal["subtitle_extracted", "transcribed", "needs_manual"]
IngestSource = Literal["subtitle", "asr", "none"]
IngestMode = Literal["auto", "subtitle", "asr"]


class ProbeRequest(BaseModel):
    path: str = Field(..., min_length=1, description="本机媒体文件绝对路径")


class SubtitleStream(BaseModel):
    index: int | None = None
    codec: str = ""
    lang: str = ""


class ProbeResponse(BaseModel):
    """媒体探测结果。recommended_mode 供调用方决定后续走哪条路径。"""
    filename: str
    format: str = ""
    duration_sec: float = 0.0
    has_audio: bool = False
    has_subtitle: bool = False
    subtitle_streams: list[SubtitleStream] = Field(default_factory=list)
    recommended_mode: Literal["subtitle", "asr", "manual"] = "manual"
    ffmpeg_available: bool = True
    asr_available: bool = False


class TranscribeRequest(BaseModel):
    path: str = Field(..., min_length=1, description="本机媒体文件绝对路径")
    mode: IngestMode = Field("auto", description="auto=按推荐；subtitle=强制字幕；asr=强制转写")


class TranscribeResponse(BaseModel):
    """入料产物。任何失败都降级为 needs_manual + hint，不抛 5xx。"""
    status: IngestStatus
    text: str = ""
    source: IngestSource = "none"
    chars: int = 0
    hint: str = ""


# ============================================================ 封面成图(M6-1)
CoverStatus = Literal["ok", "needs_manual"]
CoverPlatform = Literal["xhs", "douyin", "bilibili"]


class CoverRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=60, description="封面主标题")
    subtitle: str = Field("", max_length=80, description="副标题(可选)")
    platform: CoverPlatform = Field("xhs", description="xhs|douyin|bilibili")
    theme: str = Field("violet", description="配色主题：violet|ink|sunset")
    out_dir: str = Field("", description="输出目录(绝对路径)；留空用 {data-dir}/media/covers")
    file_stem: str = Field("", description="文件名前缀(可选)")


class CoverResponse(BaseModel):
    """封面产物。gimpish 缺失/渲染失败降级为 needs_manual + hint，不抛 5xx。"""
    status: CoverStatus
    editor: str = "gimpish"
    path: str = ""
    scene_path: str = ""
    width: int = 0
    height: int = 0
    elapsed_ms: int = 0
    hint: str = ""
