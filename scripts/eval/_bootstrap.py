"""评测脚本的公共引导：路径 + `.env` 加载。

**为什么需要它**：`app/config.py` 的 `SettingsConfigDict(env_file=".env")` 是
**相对当前工作目录**解析的。从仓库根跑 `python scripts/eval/xxx.py` 时读不到
`python-service/.env` —— 会静默退化成 mock 模式，跑出一份看起来正常、实际全是罐头的报告。

所以这里显式按**绝对路径**加载 `.env`，且**不覆盖**已存在的环境变量
（显式 `set LLM_MOCK=false` 仍应优先于文件）。

用法：
    from _bootstrap import ROOT, EVAL_DIR          # noqa: F401  （先导入，副作用生效）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY_SERVICE = ROOT / "python-service"
ENV_FILE = PY_SERVICE / ".env"
CORPUS_DIR = ROOT / "eval" / "corpus"
REPORTS_DIR = ROOT / "eval" / "reports"

# 让 `from app... import ...` 可用（与 scripts/roughcut.py 同一套做法）
if str(PY_SERVICE) not in sys.path:
    sys.path.insert(0, str(PY_SERVICE))


def load_env(path: Path | None = None) -> dict[str, str]:
    """把 .env 灌进 os.environ（**已存在的键不覆盖**）。返回解析出的键值。"""
    path = path or ENV_FILE
    parsed: dict[str, str] = {}
    if not path.exists():
        return parsed
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        parsed[key] = value
        os.environ.setdefault(key, value)   # 显式环境变量优先
    return parsed


load_env()
