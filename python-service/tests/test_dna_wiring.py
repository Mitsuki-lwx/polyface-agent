"""平台 DNA 的**接线守卫**（2026-10-09 审计后加）。

**为什么需要**：DNA 里调研出来的知识，**不一定真的传到了模型手里**。
审计时发现两个字段从来没被任何 prompt 用过：

| 字段 | 大小 | 后果 |
|---|---|---|
| `specs` | 103 字符 | 标题字数上限在里面 —— 模型压根不知道小红书标题要 ≤20 字 |
| `distribution` | 164 字符 | "平台靠什么给流量"的机制 —— 比如"搜索分发权重高，标题需埋真实搜索词（这也是标题 20 字内关键词前置的原因）" |

两者都不是"忘了用"，而是**加了字段、没人接线** —— 和 `run_draft` 漏传 `facts_used`
是同一类静默失效：不报错、测试全绿，功能就是不生效。

所以这里用**穷举**守住：DNA 里除溯源字段外，每个字段都必须出现在某个 prompt 里。
以后谁往 DNA 加了字段却忘了接，这条立刻红。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "eval"))

from _bootstrap import ROOT  # noqa: E402,F401

from app import dna  # noqa: E402
from app.pipeline import prompts  # noqa: E402
from app.schemas import Fact, StructuredMaterial  # noqa: E402
from app.schemas_gen import Brief, DraftPayload  # noqa: E402

# 溯源 / 元信息：**本来就该只给人看**，不进 prompt。
# 加新字段到这个集合时要想清楚：它真的不该影响产出吗？
PROVENANCE_ONLY = {"code", "name", "platform", "display_name", "version",
                   "updated_at", "sources", "verify"}


def _blobs() -> dict[str, str]:
    d = dna.load_dna("xhs")
    mat = StructuredMaterial(core_message="c", tone="t", audience="a",
                             facts=[Fact(text="x", type="data")])
    brief = Brief(platform_code="xhs", angle="a", hooks=["h"], structure_plan="s")
    return {
        "brief": prompts.build_brief_prompt(d, mat, None),
        "draft": prompts.build_draft_prompt(d, mat, brief),
        "qa": prompts.build_qa_prompt(d, DraftPayload(titles=["t"], body="b"), mat),
    }


def test_every_dna_field_is_wired_into_some_prompt():
    """**穷举守卫**：DNA 里除溯源字段外，每个字段都必须出现在某个 prompt 里。

    审计发现 `specs` 与 `distribution` 从没被任何 prompt 用过 ——
    而它们恰好是"标题为什么必须 ≤20 字"和"平台靠什么给流量"的依据。
    """
    d = dna.load_dna("xhs")
    blobs = _blobs()
    missing = [k for k in d
               if k not in PROVENANCE_ONLY
               and not any(f'"{k}"' in b for b in blobs.values())]
    assert not missing, (
        f"这些 DNA 字段没接进任何 prompt：{missing} —— "
        f"要么接线，要么明确加进 PROVENANCE_ONLY 并说明为什么它不影响产出")


def test_specs_and_distribution_are_actually_passed():
    """点名守两条最要紧的（审计时漏掉的）。"""
    d = dna.load_dna("xhs")
    blobs = _blobs()
    assert '"specs"' in blobs["draft"], "specs（含标题字数上限）没进 draft"
    assert '"distribution"' in blobs["draft"], "distribution（分发机制）没进 draft"
    assert '"distribution"' in blobs["brief"], "distribution 没进 brief"


def test_title_limit_is_still_passed():
    """标题字数上限是最容易漏的一条（漏了 → 全平台标题超限）。"""
    d = dna.load_dna("xhs")
    blobs = _blobs()
    assert '"title_chars_max"' in blobs["draft"]
    assert "title_chars_max" in prompts.DRAFT_SYSTEM
