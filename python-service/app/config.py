"""应用配置：从 .env 读取，未配置时使用安全默认值（mock 模式）。"""
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM（OpenAI 兼容）
    llm_base_url: str = "https://token.sensenova.cn/v1"
    llm_api_key: str = ""
    llm_model: str = "sensenova-6.8-flash-lite"
    llm_qa_model: str = "sensenova-6.8-flash-lite"
    # true=mock 离线模式（无 Key 也可跑）；false=走真实模型（需 llm_api_key）
    llm_mock: bool = True

    # 服务
    llm_host: str = "127.0.0.1"
    llm_port: int = 8000

    # 音视频入料（FR-51）
    # 留空则使用 PATH 中的 ffmpeg（也可用环境变量 POLYFACE_FFMPEG 指向安装目录或可执行文件）
    ffmpeg_path: str = ""
    # faster-whisper 模型尺寸（可选依赖；可用 POLYFACE_ASR_MODEL 覆盖）
    asr_model: str = "small"

    # ===== LLM 运行时加固（FR-70）=====
    # 单次请求超时（秒）
    llm_timeout_sec: int = 60
    # 单模型最大尝试次数（含首次）
    llm_max_attempts: int = 3
    # 模型降级链的**追加**项（主模型不可用时依次尝试）
    # 实测结论：deepseek-v4-pro / glm-5.2 / sensenova-6.8-flash-lite 稳定可用；
    #          kimi-k3 常限流、sensenova-u1-* 报 model is not found → 不作备选
    # 显式传 model= 时不走链
    llm_fallback_models: str = "deepseek-v4-pro,glm-5.2,sensenova-6.8-flash-lite"
    # 串行化降级时两次调用之间的最小间隔（毫秒）
    llm_min_interval_ms: int = 1500
    # 多平台是否并行。默认 **false（串行）**：实测上游 tpm/rpm 极紧
    #（连续两次调用即可能 429），4 并发会被大面积拒绝；串行 + 间隔才跑得完
    llm_parallel: bool = False

    # ===== 可观测（FR-71/72）=====
    langfuse_enabled: bool = False
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # ===== CORS（安全收紧）=====
    # **默认为空 = 不启用跨域**。浏览器只与 Java 工作台(:8080)同源通信，
    # Java 调 Python 是服务端请求（不受 CORS 约束），所以本服务不需要跨域。
    # 为什么不能是 "*"：本服务无鉴权，`allow_origins=["*"]` 会让**任意网页**
    # 都能 POST 到 127.0.0.1:8000 触发 LLM 调用、消耗用户额度并读回结果。
    # 需要时（如本地前端调试直连）用逗号分隔显式列出。
    #
    # ⚠️ 变量名是 `CORS_ORIGINS`（不是 POLYFACE_CORS_ORIGINS）—— 本类没有
    # `env_prefix`，字段名直接大写即为变量名，与同文件其它项（LLM_MOCK 等）一致。
    # 写在 `python-service/.env` 里：CORS_ORIGINS=http://127.0.0.1:8080
    cors_origins: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


def parse_cors_origins(raw: str) -> list[str]:
    """把逗号分隔的放行来源解析成列表；空串/纯空白 → 空列表（= 不启用跨域）。

    单独抽成函数是为了**可测试**：中间件的注册分支由它决定，
    如果写在 main.py 里就只能靠起服务来验证。
    """
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def read_version_file(start: Path, max_up: int = 4) -> str | None:
    """从 start 向上逐级查找 `VERSION` 文件，返回其内容（去空白）；找不到返回 None。

    `max_up` 限制上溯层数，避免在异常目录结构下一直走到盘根。
    """
    cur = start
    for _ in range(max_up):
        candidate = cur / "VERSION"
        try:
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except OSError:
            return None
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


@lru_cache
def service_version() -> str:
    """本服务的版本号，与仓库根 `VERSION` 保持一致。

    优先级：环境变量 `POLYFACE_VERSION` > 向上查找的 `VERSION` 文件 > "0.0.0-dev"。
    写死字符串会随发版漂移（曾出现 Java 0.4.0 / Python 0.3.0 对不上），
    所以这里统一从单一来源读取。
    """
    env = (os.environ.get("POLYFACE_VERSION") or "").strip()
    if env:
        return env
    return read_version_file(Path(__file__).resolve().parent) or "0.0.0-dev"
