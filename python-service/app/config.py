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


@lru_cache
def get_settings() -> Settings:
    return Settings()
