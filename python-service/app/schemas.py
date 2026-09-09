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
