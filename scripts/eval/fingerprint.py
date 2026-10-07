"""prompt 指纹 —— 让「通过率变了」这件事能被归因。

`docs/spec_eval.md` §5 关键决定三：报告里必须记录 6 个 system prompt 的指纹。
否则「这次通过率涨了」分不清是 **prompt 改好了**、**模型换了**、还是 **素材动了**。

指纹只与 prompt 的**内容**有关，与文件行号、注释、缩进无关 ——
所以挪动代码不会让指纹变，改一个字就会变。

用法：
    python scripts/eval/fingerprint.py            # 打印 6 个 + 合并指纹
    python scripts/eval/fingerprint.py --json     # 机器读
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python-service"))

from app.pipeline import prompts  # noqa: E402

# 顺序固定 —— 合并指纹要可复现，不能依赖 dict 顺序
NAMES = (
    "UNDERSTAND_SYSTEM",
    "BRIEF_SYSTEM",
    "DRAFT_SYSTEM",
    "QA_SYSTEM",
    "CLIP_SYSTEM",
    "LEARN_SYSTEM",
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fingerprint() -> dict[str, str]:
    """{prompt 名: 16 位哈希}。缺常量时抛 KeyError（宁可炸，不要静默少一项）。"""
    out: dict[str, str] = {}
    for name in NAMES:
        value = getattr(prompts, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} 不是非空字符串（指纹不可信）")
        out[name] = _hash(value)
    return out


def combined(fp: dict[str, str] | None = None) -> str:
    """6 个合并成一个指纹 —— 报告抬头用它做「可比性」判据。"""
    fp = fp or fingerprint()
    joined = "|".join(f"{n}={fp[n]}" for n in NAMES)
    return _hash(joined)


def main() -> int:
    fp = fingerprint()
    comb = combined(fp)
    if "--json" in sys.argv:
        print(json.dumps({"prompts": fp, "combined": comb},
                         ensure_ascii=False, indent=2))
        return 0
    for name in NAMES:
        print(f"  {name:20s} {fp[name]}")
    print(f"  {'合并':20s} {comb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
