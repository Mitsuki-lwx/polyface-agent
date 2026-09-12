"""LLM 用量记录（FR-72）：本地 JSONL 追加 + 聚合查询。

**架构边界**：Python 不碰数据库 —— 故写 JSONL，由 Java 转发读取（保持既有分层）。

**脱敏红线**：只记录**长度与 token**，绝不记录 prompt / response 正文。
完整内容仅在用户**自行启用** Langfuse 时上报到用户自己的实例。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# 聚合时最多回读的字节数（避免大文件拖慢请求）
_MAX_TAIL_BYTES = 2 * 1024 * 1024


def usage_path() -> Path:
    """用量文件位置：`POLYFACE_USAGE_FILE` > `{data_dir}/llm-usage.jsonl`。"""
    explicit = os.getenv("POLYFACE_USAGE_FILE", "").strip()
    if explicit:
        return Path(explicit)
    base = Path(os.getenv("POLYFACE_DATA_DIR", "../data")).resolve()
    return base / "llm-usage.jsonl"


def record_call(*, trace_id: str = "", scene: str = "", model: str = "",
                attempt: int = 1, ok: bool = True, duration_ms: int = 0,
                error_type: str | None = None, platform: str | None = None,
                prompt_chars: int = 0, completion_chars: int = 0,
                prompt_tokens: int | None = None, completion_tokens: int | None = None,
                mock: bool = False) -> None:
    """追加一条用量记录。**失败仅告警，绝不影响主流程**。"""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trace_id": trace_id or "",
        "scene": scene or "",
        "platform": platform,
        "model": model or ("mock" if mock else ""),
        "attempt": attempt,
        "ok": bool(ok),
        "error_type": error_type,
        "duration_ms": int(duration_ms or 0),
        "prompt_chars": int(prompt_chars or 0),
        "completion_chars": int(completion_chars or 0),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "mock": bool(mock),
    }
    try:
        p = usage_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 — 记录失败不得影响业务
        logger.warning("usage record failed: %s", e)


def _read_entries() -> list[dict]:
    """读取用量记录（容错：跳过坏行/半行）。"""
    p = usage_path()
    if not p.is_file():
        return []
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            if size > _MAX_TAIL_BYTES:
                f.seek(size - _MAX_TAIL_BYTES)
                f.readline()  # 丢弃可能被截断的首行
            raw = f.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        logger.warning("usage read failed: %s", e)
        return []

    out: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def summary(limit: int = 50) -> dict:
    """聚合用量摘要（供 GET /usage/summary）。"""
    entries = _read_entries()
    limit = max(1, min(int(limit or 50), 500))

    total = len(entries)
    failures = sum(1 for e in entries if not e.get("ok"))
    retries = sum(max(0, int(e.get("attempt") or 1) - 1) for e in entries)
    durations = [int(e.get("duration_ms") or 0) for e in entries if int(e.get("duration_ms") or 0) > 0]

    by_model: dict[str, int] = {}
    by_scene: dict[str, int] = {}
    for e in entries:
        m = e.get("model") or "unknown"
        s = e.get("scene") or "unknown"
        by_model[m] = by_model.get(m, 0) + 1
        by_scene[s] = by_scene.get(s, 0) + 1

    return {
        "total_calls": total,
        "failures": failures,
        "retries": retries,
        "avg_duration_ms": int(sum(durations) / len(durations)) if durations else 0,
        "by_model": by_model,
        "by_scene": by_scene,
        "recent": list(reversed(entries[-limit:])),
        "usage_file": str(usage_path()),
    }
