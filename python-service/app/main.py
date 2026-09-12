"""Polyface LLM 服务入口：素材解析 / 多平台成稿等 LLM 计算能力（FastAPI :8000）。"""
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import dna, llm, observability, trace, usage
from .config import get_settings
from .pipeline.generate import generate
from .pipeline.ingest import run_probe, run_transcribe
from .pipeline.learn import run_learn
from .pipeline.media import MediaInvalid, MediaToolMissing
from .pipeline.understand import run_understand
from .schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ProbeRequest,
    ProbeResponse,
    TranscribeRequest,
    TranscribeResponse,
)
from .schemas_gen import (
    GenerateRequest,
    GenerateResponse,
    LearnRequest,
    LearnResponse,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("polyface-llm")

settings = get_settings()
app = FastAPI(title="Polyface LLM Service", version="0.3.0",
              description="素材解析 / 平台策略 / 成稿 / QA / 音视频入料 / 示例学习")

# 仅本机访问；放行便于前端本地调试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def trace_middleware(request, call_next):
    """链路标识（FR-71）：读取 Java 传来的 X-Trace-Id，注入上下文并回写响应头。

    无 header 时自动生成 —— 保证任何入口都有 trace_id。
    """
    trace.set_trace_id(request.headers.get(trace.HEADER))
    response = await call_next(request)
    response.headers[trace.HEADER] = trace.current_trace_id()
    return response


@app.get("/usage/summary")
def usage_summary(limit: int = 50) -> dict:
    """LLM 用量聚合（FR-72）：总量/失败/重试/均耗时/按模型/按场景/最近记录。

    注：**不含 prompt 正文**，只含长度与 token。
    """
    return usage.summary(limit)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "polyface-llm",
        "mock": llm.is_mock(),
        "model": settings.llm_model,
        "model_chain": llm.model_chain(),
        "langfuse": settings.langfuse_enabled,
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
    """素材 → 多平台成稿：understand → 逐平台 brief/draft/qa。

    观测（FR-71）：整个生成作为 **root span**，使 trace 名有意义
    （`generate:xhs,douyin`），且各阶段 span 与 LLM generation 正确嵌套其下。
    """
    trace_name = f"generate:{','.join(req.platforms)}"
    try:
        with observability.trace_span(trace_name, trace.current_trace_id()):
            structured_dict, drafts, used_mock = generate(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    observability.flush()
    return GenerateResponse(structured=structured_dict, drafts=drafts, used_mock=used_mock)


@app.post("/learn", response_model=LearnResponse)
def do_learn(req: LearnRequest) -> LearnResponse:
    """示例学习（FR-63）：示例文本 → 模板参数（草稿，须人工确认后启用）。"""
    learned, used_mock = run_learn(req)
    return LearnResponse(template=learned, used_mock=used_mock)


# ============================================================ 音视频入料(FR-51)

@app.post("/probe", response_model=ProbeResponse)
def do_probe(req: ProbeRequest) -> ProbeResponse:
    """媒体探测：时长 / 音轨 / 字幕轨 → 推荐入料模式。"""
    try:
        return ProbeResponse(**run_probe(req.path))
    except (MediaInvalid, MediaToolMissing) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/transcribe", response_model=TranscribeResponse)
def do_transcribe(req: TranscribeRequest) -> TranscribeResponse:
    """音视频 → 文字：字幕优先，ASR 可选，失败降级 needs_manual（不抛 5xx）。"""
    return run_transcribe(req.path, req.mode)
