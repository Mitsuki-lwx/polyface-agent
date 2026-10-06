"""Polyface LLM 服务入口：素材解析 / 多平台成稿等 LLM 计算能力（FastAPI :8000）。"""
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import dna, llm, observability, trace, usage
from .config import get_settings, parse_cors_origins, service_version
from .pipeline.cover import CoverInvalid, CoverToolMissing, compose_cover
from .pipeline.generate import generate
from .pipeline.ingest import run_probe, run_transcribe
from .pipeline.learn import run_learn
from .pipeline.media import MediaInvalid, MediaToolMissing
from .pipeline.understand import run_understand
from .schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    CoverRequest,
    CoverResponse,
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


def install_cors(target: FastAPI, raw_origins: str) -> list[str]:
    """按配置注册 CORS 中间件，返回**实际放行**的来源列表（空列表 = 未启用）。

    抽成函数而非内联，是为了让"注册 / 不注册"这个分支可以被单测直接覆盖
    —— 内联时只能靠改环境变量 + 重载模块来验证，既绕又不可靠。
    """
    origins = parse_cors_origins(raw_origins)
    if not origins:
        logger.info("CORS 未启用（默认）：仅接受同源页面与服务端调用")
        return []
    if "*" in origins:
        logger.warning(
            "CORS 放行了任意来源（CORS_ORIGINS 含 *）。本服务无鉴权，"
            "任何网页都能调用 127.0.0.1:8000 消耗你的模型额度并读走结果，"
            "请仅在临时调试时使用，用完即改回。"
        )
    target.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", trace.HEADER],
    )
    logger.info("CORS 已启用，放行来源：%s", origins)
    return origins


settings = get_settings()
app = FastAPI(title="Polyface LLM Service", version=service_version(),
              description="素材解析 / 平台策略 / 成稿 / QA / 音视频入料 / 示例学习")

# ============================================================ CORS（默认关闭）
# 默认 **不注册** CORSMiddleware：浏览器只跟 Java 工作台(:8080)同源通信，
# Java 调本服务是服务端请求（不受 CORS 约束），因此本服务**不需要**跨域。
#
# 为什么不能默认 `allow_origins=["*"]`：本服务无任何鉴权，且监听 127.0.0.1。
# 放行 `*` 后，用户浏览任意恶意网页时，该页面的 JS 就能 POST 到
# http://127.0.0.1:8000/generate 触发真实 LLM 调用 —— 既消耗用户自付的模型额度，
# 又能把返回的稿件正文读走。默认关闭 = 浏览器侧根本发不出这个跨域请求。
#
# 需要本地前端直连调试时，在 `python-service/.env` 里显式配置（逗号分隔）：
#   CORS_ORIGINS=http://127.0.0.1:8080,http://localhost:5173
install_cors(app, settings.cors_origins)


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
        "version": service_version(),
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
            structured_dict, drafts, used_mock, failures = generate(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    observability.flush()
    if failures:
        logger.warning("generate 部分失败 trace=%s failures=%s", trace.current_trace_id(), failures)
    return GenerateResponse(structured=structured_dict, drafts=drafts, used_mock=used_mock,
                            failures=failures)


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


# ============================================================ 封面成图(M6-1)

@app.post("/compose/cover", response_model=CoverResponse)
def do_compose_cover(req: CoverRequest) -> CoverResponse:
    """标题 → 平台封面 PNG（后台编辑器 gimpish 渲染，见 ADR-019）。

    工具缺失/渲染失败一律降级 `needs_manual` + hint，**不抛 5xx**（同入料策略）；
    只有入参非法（空标题/未知平台）才是 400。
    """
    try:
        return CoverResponse(**compose_cover(
            title=req.title, subtitle=req.subtitle, platform=req.platform,
            theme=req.theme, out_dir=req.out_dir, file_stem=req.file_stem))
    except (CoverInvalid, CoverToolMissing) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
