"""Polyface LLM 服务入口：素材解析 / 多平台成稿等 LLM 计算能力（FastAPI :8000）。"""
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import dna, llm
from .config import get_settings
from .pipeline.generate import generate
from .pipeline.learn import run_learn
from .pipeline.understand import run_understand
from .schemas import AnalyzeRequest, AnalyzeResponse
from .schemas_gen import (
    GenerateRequest,
    GenerateResponse,
    LearnRequest,
    LearnResponse,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("polyface-llm")

settings = get_settings()
app = FastAPI(title="Polyface LLM Service", version="0.2.0", description="素材解析 / 平台策略 / 成稿 / QA")

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


@app.get("/platforms")
def platforms() -> dict:
    """列出当前 DNA 目录中实际可用的平台。"""
    return {"platforms": dna.list_platforms()}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    """素材解析：raw text -> 核心观点 + 事实清单(防幻觉依据)。"""
    structured, used_mock = run_understand(req)
    return AnalyzeResponse(structured=structured, used_mock=used_mock)


@app.post("/generate", response_model=GenerateResponse)
def do_generate(req: GenerateRequest) -> GenerateResponse:
    """素材 → 多平台成稿：understand → 逐平台 brief/draft/qa（平台并行）。"""
    try:
        structured_dict, drafts, used_mock = generate(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return GenerateResponse(structured=structured_dict, drafts=drafts, used_mock=used_mock)


@app.post("/learn", response_model=LearnResponse)
def do_learn(req: LearnRequest) -> LearnResponse:
    """示例学习（FR-63）：示例文本 → 模板参数（草稿，须人工确认后启用）。"""
    learned, used_mock = run_learn(req)
    return LearnResponse(template=learned, used_mock=used_mock)
