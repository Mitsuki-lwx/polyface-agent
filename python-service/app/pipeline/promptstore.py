"""提示词模板的**内容寻址**存储（`docs/spec_promptver.md` 关键决定二）。

哈希不可读 ✗ —— 半年后看到一个 `d05380484d60`，谁也不知道它对应什么。
所以除了哈希，还要**把当时的模板全文存一份**，按哈希命名、同哈希只存一次。

存哪：`<data-dir>/prompt-templates/<fp>.txt`（与用量日志同级的观测产物目录）。

**写失败绝不影响调用** —— 与观测的既有约定一致（`usage.py` / `trace.py` 同一条路）。
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def templates_dir() -> Path:
    """模板存放目录。与用量日志同源（同一个 data-dir）。"""
    from ..usage import usage_path
    return usage_path().parent / "prompt-templates"


def save(fp: str, template: str) -> bool:
    """按哈希存模板全文。已存在则**不重复写**（内容寻址）。返回是否新写入。

    失败只告警 —— 观测产物不该阻断业务。
    """
    if not fp or not template:
        return False
    try:
        d = templates_dir()
        f = d / f"{fp}.txt"
        if f.is_file():
            return False                     # 同版本只存一份
        d.mkdir(parents=True, exist_ok=True)
        f.write_text(template, encoding="utf-8")
        return True
    except Exception as e:                                   # noqa: BLE001
        logger.warning("prompt template save failed: %s", e)
        return False


def load(fp: str) -> str:
    """按哈希取模板全文。取不到返回空串。"""
    try:
        f = templates_dir() / f"{fp}.txt"
        return f.read_text(encoding="utf-8") if f.is_file() else ""
    except Exception:                                        # noqa: BLE001
        return ""
