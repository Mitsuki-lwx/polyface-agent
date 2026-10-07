"""T3 · 机械性质断言 —— **纯函数，不调 LLM、不写文件**。

`docs/spec_eval.md` §5 关键决定二：只做机械判定，不请 LLM 当裁判
（裁判与被评是同一个模型，它的偏好会掩盖退化）。

设计约束（checklist §4）：
- 输入是 `(素材, 结构化理解, 平台, 产物, 阈值)`，输出是明确的通过/不通过
- **阈值全部从 `rules` 进来**，函数里不写死 —— 改阈值必须能改变结论
- 判定**只依赖传入的产物**，因此是确定性的（同一产物 → 同一结论）
- 不 import 任何 LLM 客户端；「不调 LLM」由测试用 monkeypatch 证明（见 test_eval_assertions.py）

数字提取**复用线上那一套**（`generate._numbers_in`），不自己再造一个 ——
否则评测集判定用的口径与产品实际口径不一致，测出来的东西没有意义。
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import ROOT  # noqa: E402,F401  （副作用：sys.path + .env）

# 数字口径**只有一份**（app/pipeline/numberish.py）—— 产品与评测共用，
# 否则评测集测的不是线上那条路。
from app.pipeline.numberish import numbers_in, num_values  # noqa: E402

# 视频原生平台必须有剪辑单（docs/05 FR-52）
VIDEO_NATIVE = {"douyin", "bilibili"}

# 必须**整词**匹配：`月收入在8000—12000元` 里的 `12000` 不能抠出 `2000` 当年份
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

# 断言阈值默认值。**每一项都必须能被 rules 覆盖**（checklist §1）。
# 跑分器应当用 platform-dna 里的真实约束覆盖它们。
DEFAULT_RULES: dict[str, Any] = {
    "title_min": 4,
    "title_max": 40,
    "max_titles": 5,
    "body_min": 50,
    "body_max": 20000,
    "tags_min": 1,
    "tags_allow_hash": False,
}


@dataclass(frozen=True)
class Result:
    code: str
    ok: bool
    detail: str = ""

    def render(self) -> str:
        return f"[{'PASS' if self.ok else 'FAIL'}] {self.code}" + (
            f" — {self.detail}" if self.detail else "")


def _rules(base: dict[str, Any] | None) -> dict[str, Any]:
    return {**DEFAULT_RULES, **(base or {})}


def _facts_text(structured: dict | None) -> str:
    facts = (structured or {}).get("facts") or []
    parts: list[str] = []
    for f in facts:
        if isinstance(f, dict):
            parts.append(str(f.get("text") or ""))
        else:
            parts.append(str(f))
    return " ".join(parts)


# ---------------------------------------------------------------- 断言

def a1_numbers_supported(ctx: dict) -> Result:
    """正文里的数字必须在**素材或事实清单**里有依据。

    比的是**数值**，不是字面 —— 素材写「九十九」、稿件写「99」算有依据
    （那是改写，不是编造）。中文数字与阿拉伯数字都会被归一化。
    """
    body = str((ctx.get("draft") or {}).get("body") or "")
    allowed = set(num_values(str((ctx.get("material") or {}).get("raw_text") or "")))
    allowed |= set(num_values(_facts_text(ctx.get("structured"))))
    body_vals = num_values(body, strict=True)
    unsupported = [tok for v, tok in sorted(body_vals.items()) if v not in allowed]
    return Result("A1 数字有依据", not unsupported,
                  "" if not unsupported else f"素材里找不到依据的数字：{unsupported}")


def a2_no_foreign_years(ctx: dict) -> Result:
    """不出现素材里没有的年份（AI 自加时间是最典型的一类幻觉）。

    ⚠️ 素材侧的"已知年份"要按**数值**取，不能只比阿拉伯数字字面：
    素材写「两千」、正文写「2000」是同一个年份（与 A1 同一个坑，实测踩到过）。
    """
    body = str((ctx.get("draft") or {}).get("body") or "")
    src = (str((ctx.get("material") or {}).get("raw_text") or "") + " "
           + _facts_text(ctx.get("structured")))
    known = set(num_values(src, strict=False))
    foreign = sorted({y for y in _YEAR_RE.findall(body) if float(y) not in known})
    return Result("A2 无素材外年份", not foreign,
                  "" if not foreign else f"素材里没有的年份：{foreign}")


def a3_titles_ok(ctx: dict) -> Result:
    """标题非空、不重复、条数不超限、长度在区间内。"""
    rules = _rules(ctx.get("rules"))
    titles = [str(t).strip() for t in ((ctx.get("draft") or {}).get("titles") or [])]
    bad: list[str] = []
    if not titles:
        bad.append("一个标题都没有")
    if any(not t for t in titles):
        bad.append("有空标题")
    if len(titles) != len(set(titles)):
        bad.append("标题有重复")
    if len(titles) > rules["max_titles"]:
        bad.append(f"标题 {len(titles)} 条 > 上限 {rules['max_titles']}")
    over = [t for t in titles if not (rules["title_min"] <= len(t) <= rules["title_max"])]
    if over:
        bad.append(f"长度不在 {rules['title_min']}~{rules['title_max']}：{over[:2]}")
    return Result("A3 标题合法", not bad, "；".join(bad))


def a4_body_length_ok(ctx: dict) -> Result:
    rules = _rules(ctx.get("rules"))
    n = len(str((ctx.get("draft") or {}).get("body") or "").strip())
    ok = rules["body_min"] <= n <= rules["body_max"]
    return Result("A4 正文长度达标", ok,
                  "" if ok else f"正文 {n} 字，不在 {rules['body_min']}~{rules['body_max']}")


def a5_tags_ok(ctx: dict) -> Result:
    rules = _rules(ctx.get("rules"))
    tags = [str(t) for t in ((ctx.get("draft") or {}).get("tags") or [])]
    bad: list[str] = []
    if len(tags) < rules["tags_min"]:
        bad.append(f"标签 {len(tags)} 个 < 下限 {rules['tags_min']}")
    if not rules["tags_allow_hash"]:
        hashed = [t for t in tags if t.strip().startswith("#")]
        if hashed:
            bad.append(f"标签带了 #：{hashed[:3]}")
    return Result("A5 标签达标", not bad, "；".join(bad))


def a6_clip_sheet_ok(ctx: dict) -> Result:
    """视频原生平台必须有剪辑单，且分镜序号从 1 起连续。"""
    platform = str(ctx.get("platform") or "")
    if platform not in VIDEO_NATIVE:
        return Result("A6 剪辑单完整", True, f"{platform} 非视频原生平台，跳过")
    sheet = (ctx.get("draft") or {}).get("clip_sheet")
    if not sheet:
        return Result("A6 剪辑单完整", False, f"{platform} 是视频原生平台，但没有 clip_sheet")
    scenes = sheet.get("scenes") or []
    if not scenes:
        return Result("A6 剪辑单完整", False, "clip_sheet 存在但 scenes 为空")
    seqs = [s.get("seq") for s in scenes if isinstance(s, dict)]
    expect = list(range(1, len(scenes) + 1))
    if seqs != expect:
        return Result("A6 剪辑单完整", False, f"分镜序号不连续：{seqs}，应为 {expect}")
    empty_script = [s.get("seq") for s in scenes if not str(s.get("script") or "").strip()]
    if empty_script:
        return Result("A6 剪辑单完整", False, f"这些分镜没有 script：{empty_script}")
    return Result("A6 剪辑单完整", True)


def a7_qa_passed(ctx: dict) -> Result:
    """产物必须通过产品自己的质量门。"""
    qa = (ctx.get("draft") or {}).get("qa")
    if not isinstance(qa, dict):
        return Result("A7 通过质量门", False, "产物里没有 qa 字段")
    passed = bool(qa.get("passed"))
    issues = qa.get("issues") or []
    return Result("A7 通过质量门", passed,
                  "" if passed else f"QA 未通过：{issues[:3]}")


ALL: tuple[Callable[[dict], Result], ...] = (
    a1_numbers_supported, a2_no_foreign_years, a3_titles_ok,
    a4_body_length_ok, a5_tags_ok, a6_clip_sheet_ok, a7_qa_passed,
)


def check_all(ctx: dict) -> list[Result]:
    """跑全部断言。**单条断言抛异常不影响其它**（归因要清楚，不能一起炸）。"""
    out: list[Result] = []
    for fn in ALL:
        try:
            out.append(fn(ctx))
        except Exception as e:  # noqa: BLE001 — 断言实现自身出错也要记下来
            out.append(Result(fn.__name__, False, f"断言自身异常：{type(e).__name__}: {e}"))
    return out


def make_ctx(material: dict, structured: dict | None, platform: str,
             draft: dict, rules: dict | None = None) -> dict:
    return {"material": material, "structured": structured, "platform": platform,
            "draft": draft, "rules": rules or {}}
