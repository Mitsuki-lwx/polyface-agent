"""第三关判定器（`scripts/stage3/summarize.py`）的测试。

判定门槛来自 `docs/50` §3。这些测试把门槛**钉死** ——
否则将来有人把 `faster_by` 悄悄调松，就再也没人发现"事后找理由"又回来了。
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

STAGE3 = Path(__file__).resolve().parents[2] / "scripts" / "stage3"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, STAGE3 / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


S = _load("polyface_stage3", "summarize.py")


def fill(ctrl_min: float, poly_min: float, fact_err: int = 0,
         reuse: str = "会用", **over) -> dict:
    d = S.template("测试作者")
    d["answers"]["2_下次还会用吗_最卡哪一步"] = reuse
    for m in d["materials"]:
        for r in m["records"]:
            if r["group"] == "control":
                r["minutes"], r["fact_errors"] = ctrl_min, 0
            else:
                r["minutes"], r["fact_errors"] = poly_min, fact_err
    d.update(over)
    return d


def judge(data: dict, **kw):
    return S.verdict(data, kw.pop("faster_by", S.FASTER_BY_DEFAULT))


# ---------------------------------------------------------------- 门槛

def test_threshold_default_is_30_percent():
    """docs/50 §3 写的是「≥ 30%」。这条防止有人把它调松。"""
    assert S.FASTER_BY_DEFAULT == 0.30


def test_fast_and_clean_is_worth_continuing():
    v, _, s = judge(fill(45, 18))
    assert v == "值得继续投入"
    assert s["faster_ratio"] == pytest.approx(0.6, abs=1e-6)


def test_fabricated_fact_kills_direction_even_if_fast():
    """编造事实一票否决 —— 哪怕快 60%。"""
    v, reasons, _ = judge(fill(45, 18, fact_err=1))
    assert v == "方向不成立"
    assert any("素材里没有的事实" in r for r in reasons)


def test_not_faster_is_direction_failed():
    v, reasons, _ = judge(fill(30, 40))
    assert v == "方向不成立"
    assert any("不低于" in r for r in reasons)


def test_equal_time_is_direction_failed():
    """§3 原文是「**不低于**对照组」→ 相等也算失败。"""
    v, _, s = judge(fill(30, 30))
    assert v == "方向不成立"
    assert s["faster_ratio"] == 0.0


def test_slightly_faster_needs_targeted_fix():
    v, _, s = judge(fill(40, 36))
    assert v == "需要修具体环节"
    assert 0 < s["faster_ratio"] < 0.30


def test_threshold_is_configurable():
    """门槛可覆盖，且改了**结论确实会变**。"""
    data = fill(40, 36)                       # 快 10%
    assert judge(data)[0] == "需要修具体环节"
    assert judge(data, faster_by=0.05)[0] == "值得继续投入"


def test_faster_by_zero_boundary():
    """faster_ratio 恰好 = 0 判失败（不低于），>0 且 <门槛 判需修。"""
    assert judge(fill(30, 30))[0] == "方向不成立"
    assert judge(fill(30, 29))[0] == "需要修具体环节"


# ---------------------------------------------------------------- 不猜

def test_empty_template_cannot_be_judged():
    v, reasons, s = S.verdict(S.template("空"), S.FASTER_BY_DEFAULT)
    assert v == "无法判定"
    assert s == {}
    assert any("输入不完整" in r for r in reasons)


def test_missing_one_cell_cannot_be_judged():
    d = fill(45, 18)
    d["materials"][0]["records"][0]["minutes"] = None
    assert S.verdict(d, S.FASTER_BY_DEFAULT)[0] == "无法判定"


def test_non_numeric_minutes_cannot_be_judged():
    d = fill(45, 18)
    d["materials"][0]["records"][0]["minutes"] = "大概四十分钟"
    v, reasons, _ = S.verdict(d, S.FASTER_BY_DEFAULT)
    assert v == "无法判定"
    assert any("不是数字" in r for r in reasons)


def test_missing_group_cannot_be_judged():
    d = fill(45, 18)
    d["materials"][0]["records"] = [r for r in d["materials"][0]["records"]
                                    if r["group"] != "control"]
    v, reasons, _ = S.verdict(d, S.FASTER_BY_DEFAULT)
    assert v == "无法判定"
    assert any("缺 control 组" in r for r in reasons)


# ---------------------------------------------------------------- 渲染

def test_md_renders_verdict_and_numbers():
    d = fill(45, 18)
    v, reasons, s = judge(d)
    md = S.render(d, v, reasons, s)
    assert "值得继续投入" in md
    assert "60.0%" in md
    # 环境摩擦与产品判断分开，是 docs/50 §5 的硬要求
    assert "装不起来" in md and "不算" in md


def test_author_answers_are_echoed():
    d = fill(45, 18, reuse="会用，但等生成太久")
    v, reasons, s = judge(d)
    md = S.render(d, v, reasons, s)
    assert "会用，但等生成太久" in md


def test_template_has_both_groups_for_two_materials():
    t = S.template("某人")
    assert len(t["materials"]) == 2
    for m in t["materials"]:
        assert {r["group"] for r in m["records"]} == {"control", "polyface"}
        assert all(r["minutes"] is None for r in m["records"])
