"""Polyface LLM 服务入口：素材解析 / 多平台成稿等 LLM 计算能力（FastAPI :8000）。"""
import os
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import dna, guard, lifecycle as _lifecycle, llm, observability, trace, usage
from .guard import RequestGuard
from pathlib import Path

from .config import get_settings, parse_cors_origins, service_version
from .pipeline.cover import CoverInvalid, CoverToolMissing, compose_cover
from .pipeline.generate import generate
from .pipeline.ingest import run_probe, run_transcribe
from .pipeline.learn import run_learn
from .pipeline import timeline as tlib
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
    FrameItem, FramesRequest, FramesResponse, ReferenceRequest,
    DescribeImageRequest, DescribeImageResponse,
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
# 崩溃取证（openspec: service-crash-forensics）：**必须在 app 创建前就挂上** ——
# 之后任何时刻的致命信号（SIGSEGV/SIGABRT/SIGFPE…）都会把 Python 栈写进文件。
# 失败只告警，绝不影响服务启动。
_lifecycle.crash_log_path()  # noqa: B018 —— 让 POLYFACE_CRASH_LOG 的解析发生在导入期
_lifecycle.enable_crash_log()
_lifecycle.log_startup()
_lifecycle.install_atexit()

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
# 来源准入：**最后注册 = 最外层 = 最先执行**（Starlette 的 add_middleware 是前置插入）
# 放在读正文之前拒绝，避免"请求已经产生副作用"
app.add_middleware(RequestGuard)


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


@app.post("/split")
def do_split(req: dict) -> dict:
    """素材拆分（`docs/spec_split.md`）：一个素材 → 多个素材源。

    请求：`{"raw_text", "structured": {…}, "angles": [...], "formats": [...]}`
    —— `angles` 与 `formats` 至少要给一个（选题与形态是两个维度，
    黑客松原句写的是 "channels **and formats**"）。

    返回 `{"items": [{angle, format, title, core_message, facts:[…]}]}`。

    **事实不许凭空造**：模型只返回父素材事实的**序号**，这里按序号取回原文 ——
    越界序号会被 `split_material` 当场拦下（不是靠"叮嘱模型别编"）。
    """
    from .pipeline import split as sp
    from .schemas import StructuredMaterial
    try:
        mat = StructuredMaterial.model_validate(req.get("structured") or {})
        # `temperature` 可选：拆分的"角度多样性"靠它调（spec §3 第 4 条要求它可调）
        temp = req.get("temperature")
        items = sp.split_material(mat, str(req.get("raw_text") or ""),
                                  angles=req.get("angles") or [],
                                  formats=req.get("formats") or [],
                                  **({"temperature": float(temp)} if temp is not None else {}))
    except sp.SplitError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"items": items}


@app.get("/tts/plugins")
def tts_plugins() -> dict:
    """配音插件清单（`docs/spec_tts_plugin.md`）。

    **一张清单，不是"选后端"** —— 内置的、用户自己放的、以及每个音色/参数/是否联网
    全在这儿。UI 直接把它摆出来让用户挑。

    `network=true` 的插件会**改变数据边界**（ADR-017），所以必须如实转述，不能藏。
    """
    from .pipeline import tts_plugin as tp
    return tp.catalog()


@app.post("/timeline/validate")
def timeline_validate(req: dict) -> dict:
    """校验一条时间线（`docs/spec_timeline.md`）。

    **这是唯一一份校验实现** —— Java 侧不重复写一遍。两边各写一遍必然分叉，
    而分叉的后果是"界面说没问题、渲染却剪错"（仓库里已有"数字口径统一在 numberish"的先例）。

    请求：`{"timeline": {...}, "source_duration": 123.4}`（时长可省）。
    通过 → 200 `{"ok": true, "normalized": {...}}`；不通过 → 400 + 可读理由（指出是哪一段）。
    """
    try:
        normalized = tlib.parse(req.get("timeline"), source_duration=req.get("source_duration"))
    except tlib.TimelineError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"ok": True, "normalized": normalized}


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


# ============================================================ 视频关键帧(video-frame-understanding)

def _safe_quality(path: str) -> dict | None:
    """质量评分的容错包装：ffmpeg 抽风时**不给结论**，别让整批 500 ✗。"""
    from .pipeline import frames as _fr
    try:
        return _fr.frame_quality(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("画面质量评分失败（忽略）：%s", e)
        return None


def media_root() -> Path:
    """media 根：与 Java 侧 `polyface.media.dir` 同一约定（缺省 `{data_dir}/media`）。"""
    base = Path(os.getenv("POLYFACE_DATA_DIR", "../data")).resolve()
    return Path(os.getenv("POLYFACE_MEDIA_DIR", str(base / "media"))).resolve()


@app.post("/frames", response_model=FramesResponse)
def do_frames(req: FramesRequest) -> FramesResponse:
    """视频 → 关键帧 + 视觉描述。

    帧落到 `{DATA_DIR}/media/frames/<stem>/`（与封面/资产同一片目录），
    返回相对路径，所以剪辑与封面能直接复用 ✓。

    工具缺失/视频读不出 → 400（不是 5xx：这是入参问题 ✗）。
    单帧描述失败 → 该帧 `desc` 为空 + `error`，**不编造** ✗。
    """
    from pathlib import Path as _P
    from .pipeline import frames as fr
    from .pipeline import vision as vis

    video = _P(req.path)
    try:
        secs = fr.duration_sec(video)
        out_dir = media_root() / "frames" / video.stem
        if req.times:
            # 剪辑联动：只抽给定时刻（通常是每一刀保留段的中间点）
            picked = fr.extract_at(video, out_dir, req.times)
        else:
            picked = fr.extract_keyframes(video, out_dir, count=req.count)
    except fr.FrameError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    described = vis.describe_frames(picked) if req.describe else [
        {"t_sec": f["t_sec"], "path": f["path"], "desc": "", "error": None} for f in picked]
    items = []
    for d in described:
        rel = _P(d["path"])
        try:
            rel = rel.relative_to(media_root())
        except ValueError:
            rel = _P("frames") / rel.name
        items.append(FrameItem(t_sec=d["t_sec"], rel_path=str(rel).replace("\\", "/"),
                               desc=d["desc"], error=d["error"],
                               # 质量是本地算的 ✓ —— 失败也不影响描述（给 None 不编 ✗）
                               quality=_safe_quality(d["path"])))
    changed = len(picked) < req.count
    return FramesResponse(
        frames=items, video_sec=round(secs, 2),
        hint=("画面变化较少，已用等距补足" if changed else ""),
    )


# ============================================================ 参考视频分析

@app.post("/analyze-reference")
def do_analyze_reference(req: ReferenceRequest) -> dict:
    """别人的视频 → 结构 / 节奏 / 钩子分析（不落库，返回即用 ✓）。"""
    from .pipeline import reference as ref
    try:
        return ref.analyze_reference(req.path, frame_count=req.frame_count)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("参考视频分析失败")
        raise HTTPException(status_code=502, detail=f"分析失败：{type(e).__name__}: {str(e)[:200]}") from e


# ============================================================ 图片 → 文本

IMAGE_DESCRIBE_PROMPT = (
    "这是一张图片。用 120 字以内说清：画面里是什么（具体物体/人物/场景）、"
    "有没有可读的文字（原样抄，最多 20 字）、整体风格或用途（封面/截图/示意图/照片）。"
    "只写事实，不要评价好不好看 ✗。"
)


@app.post("/describe-image", response_model=DescribeImageResponse)
def do_describe_image(req: DescribeImageRequest) -> DescribeImageResponse:
    """图片 → 描述文本（作为素材母体）。

    图片本身没有文字 ✗，但视觉描述有 ✓ —— 这样"我拍了张照片想写点什么"也能走完整链路。
    失败**不抛 5xx**：`text` 为空 + `error` 说原因（不编造 ✗）。
    """
    from pathlib import Path as _P
    from .pipeline import vision as vis

    path = _P(req.path)
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"图片不存在：{path}")
    prompt = IMAGE_DESCRIBE_PROMPT + (("\n补充要求：" + req.hint) if req.hint else "")
    try:
        text = vis.describe_frame(path, prompt, max_tokens=1500)
    except vis.VisionError as e:
        return DescribeImageResponse(text="", error=str(e)[:200])
    rel = ""
    try:
        rel = str(path.relative_to(media_root())).replace("\\", "/")
    except ValueError:
        pass
    return DescribeImageResponse(text=text, rel_path=rel)


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
