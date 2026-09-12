"""应用配置：从 .env 读取，未配置时使用安全默认值（mock 模式）。"""
from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
