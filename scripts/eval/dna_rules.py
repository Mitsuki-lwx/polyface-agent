"""把 `platform-dna/*.yaml` 的约束翻译成评测断言阈值。

**为什么需要**：评测集原先只用 `DEFAULT_RULES`，`platform-dna` 里的
`body_chars_max` / `tags.count_max` / `title_chars_max` **一条都没用上** ——
于是「改了某平台的 DNA，产出有没有变好」这个问题根本答不了。

实测例子：`m02/bilibili` 的「正文超长 1024字 > 上限1000字」是**产品自己的 QA**（A7）抓到的，
A4 用的默认 `body_max=20000` 根本不会报。

接上之后，评测集就成了**平台 DNA 的实验台**：
改一条规则 → 只跑该平台 → 看断言通过率与失败明细。

**取值优先级**（DNA 里字段名各平台不统一，这是实测的坑）：
    正文上限：specs.body_chars_max → specs.desc_chars_max → specs.caption_chars_max
              → limits.body_chars_max → 默认
    标题上限：specs.title_chars_max
    标签上限：tags.count_max（**上限**，不是下限 —— 平台罚的是堆砌）
    正文下限：specs.body_chars_min_suggest（知乎这种"无上限但太短会折叠"的平台用得上）
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import ROOT  # noqa: E402,F401  （副作用：sys.path + .env）

from app import dna  # noqa: E402

# DNA 里写 0 表示"无上限"（知乎回答就是），断言里不能真用 0
_NO_LIMIT = 100000


def rules_for(platform: str) -> dict:
    """某平台的断言阈值。读不到就退回空 dict（断言侧用默认值，且**不静默假装读到了**）。"""
    try:
        d = dna.load_dna(platform)
    except ValueError:
        return {}

    specs = d.get("specs") or {}
    norm = dna.normalize_limits(d)
    out: dict = {}

    title_max = specs.get("title_chars_max")
    if isinstance(title_max, int) and title_max > 0:
        out["title_max"] = title_max

    body_max = None
    for key in ("body_chars_max", "desc_chars_max", "caption_chars_max"):
        v = specs.get(key)
        if isinstance(v, int):
            # DNA 里写 0 是**显式声明"无上限"**（知乎回答就是），必须在这里就处理掉 ——
            # 否则会掉到 normalize_limits 的默认值 1000 上，凭空给知乎加了个上限
            body_max = v if v > 0 else _NO_LIMIT
            break
    if body_max is None:
        v = norm["limits"].get("body_chars_max")
        if isinstance(v, int) and v > 0:
            body_max = v
    if body_max is not None:
        out["body_max"] = body_max

    body_min = specs.get("body_chars_min_suggest")
    if isinstance(body_min, int) and body_min > 0:
        out["body_min"] = body_min

    tags_max = (d.get("tags") or {}).get("count_max")
    if isinstance(tags_max, int) and tags_max > 0:
        out["tags_max"] = tags_max

    return out


def rules_for_all() -> dict[str, dict]:
    """{平台代码: 阈值}，只含 DNA 文件实际存在的平台。"""
    return {p["code"]: rules_for(p["code"]) for p in dna.list_platforms()}


def fingerprint() -> dict[str, str]:
    """每个平台 DNA 的内容指纹（解析后的 YAML，与注释/格式无关）。

    **为什么需要**：报告里若没有它，「这次通过率变了」分不清是 **prompt 改了**、
    **DNA 改了**、还是**模型换了**。和 prompt 指纹是一回事。
    """
    out: dict[str, str] = {}
    for p in dna.list_platforms():
        code = p["code"]
        try:
            d = dna.load_dna(code)
        except ValueError:
            continue
        # 只取会影响产出的部分：sources / verify / updated_at 变了不算行为变化
        payload = {k: d.get(k) for k in
                   ("specs", "style", "structure_template", "title_rules", "tags",
                    "limits", "hooks", "viral_logic", "content_forms", "distribution")}
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        out[code] = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    return out


def combined(fp: dict[str, str] | None = None) -> str:
    fp = fp or fingerprint()
    joined = "|".join(f"{k}={fp[k]}" for k in sorted(fp))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def staleness() -> list[dict]:
    """哪些平台的 DNA 已经放了很久（`updated_at` 起算）。给 doctor 用。"""
    import datetime as _dt
    out = []
    for p in dna.list_platforms():
        code = p["code"]
        try:
            d = dna.load_dna(code)
        except ValueError:
            continue
        raw = str(d.get("updated_at") or "").strip()
        if not raw:
            out.append({"platform": code, "updated_at": "", "days": None})
            continue
        try:
            when = _dt.date.fromisoformat(raw)
        except ValueError:
            out.append({"platform": code, "updated_at": raw, "days": None})
            continue
        days = (_dt.date.today() - when).days
        out.append({"platform": code, "updated_at": raw, "days": days})
    return out


if __name__ == "__main__":
    print(json.dumps({"rules": rules_for_all(), "fingerprint": fingerprint(),
                      "combined": combined(), "staleness": staleness()},
                     ensure_ascii=False, indent=2))
