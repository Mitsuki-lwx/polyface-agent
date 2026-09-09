"""Polyface LLM 服务入口：素材解析等 LLM 计算能力（FastAPI :8000）。"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import llm
from .config import get_settings
from .pipeline.understand import run_understand
from .schemas import AnalyzeRequest, AnalyzeResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("polyface-llm")

settings = get_settings()
app = FastAPI(title="Polyface LLM Service", version="0.1.0", description="素材解析 / 平台策略 / 成稿 / QA")

# 仅本机访问；放行便于前端本地调试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "polyface-llm",
        "mock": llm.is_mock(),
        "model": settings.llm_model,
    }


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    """素材解析：raw text -> 核心观点 + 事实清单(防幻觉依据)。"""
    structured, used_mock = run_understand(req)
    return AnalyzeResponse(structured=structured, used_mock=used_mock)
