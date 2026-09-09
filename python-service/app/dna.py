"""平台 DNA 载入器：读取 repo 根目录 platform-dna/*.yaml 为结构化规则。

每个平台一个 YAML（如 xiaohongshu.yaml），描述该平台的内容形态、语言风格、
结构模板、标题规则、标签策略、字数/标签上限、红线与爆款逻辑。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# 平台代码 -> DNA 文件名（与 docs 保持一致）
PLATFORM_FILES = {
    "xhs": "xiaohongshu.yaml",
    "douyin": "douyin.yaml",
    "gzh": "gongzhonghao.yaml",
    "zhihu": "zhihu.yaml",
    "bilibili": "bilibili.yaml",
    # 海外平台（M2 后扩展）
    "x": "x.yaml",
    "instagram": "instagram.yaml",
    "facebook": "facebook.yaml",
    "youtube": "youtube.yaml",
}

PLATFORM_NAMES = {
    "xhs": "小红书",
    "douyin": "抖音",
    "gzh": "微信公众号",
    "zhihu": "知乎",
    "bilibili": "B站",
    "x": "X(Twitter)",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "youtube": "YouTube",
}


def _dna_dir() -> Path:
    """DNA 目录：优先环境变量 POLYFACE_DNA_DIR，否则用仓库内 platform-dna/。"""
    import os

    env = os.environ.get("POLYFACE_DNA_DIR")
    if env:
        return Path(env)
    # app/dna.py -> python-service/app -> python-service -> 仓库根
    return Path(__file__).resolve().parents[2] / "platform-dna"


@lru_cache(maxsize=32)
def load_dna(platform_code: str) -> dict:
    """载入某平台 DNA。未知/文件缺失抛 ValueError。"""
    code = (platform_code or "").strip().lower()
    filename = PLATFORM_FILES.get(code)
    if not filename:
        raise ValueError(f"unknown platform: {platform_code}")
    path = _dna_dir() / filename
    if not path.exists():
        raise ValueError(f"platform DNA not found: {filename} (dir={_dna_dir()})")
    with open(path, "r", encoding="utf-8") as f:
        dna = yaml.safe_load(f) or {}
    dna["code"] = code
    dna["name"] = PLATFORM_NAMES.get(code, code)
    return dna


def list_platforms() -> list[dict]:
    """返回 DNA 文件实际存在的平台（代码+名称）。"""
    out = []
    for code, filename in PLATFORM_FILES.items():
        if (_dna_dir() / filename).exists():
            out.append({"code": code, "name": PLATFORM_NAMES.get(code, code)})
    return out


def normalize_limits(dna: dict) -> dict:
    """把 YAML 中可选的 limits/tags 规整为带默认值的 dict。"""
    limits = dict(dna.get("limits") or {})
    limits.setdefault("body_chars_max", 1000)
    limits.setdefault("titles_max", 3)
    tags = dict(dna.get("tags") or {})
    tags.setdefault("count_max", 8)
    return {"limits": limits, "tags": tags}
