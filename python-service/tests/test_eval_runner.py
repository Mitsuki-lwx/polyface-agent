"""评测跑分器（`scripts/eval/run_eval.py`）的测试 —— 覆盖纯逻辑部分。

跑真实生成的部分由 T12 端到端验证覆盖；这里只钉**判定与汇总**这类
确定性、易退化、又没人会去看的代码。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "eval"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EVAL_DIR / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


R = _load("polyface_eval_runner", "run_eval.py")


# ---------------------------------------------------------------- 素材加载

def write_corpus(tmp_path: Path, ids: list[str], manifest_ids: list[str] | None = None):
    for i in ids:
        (tmp_path / f"{i}.json").write_text(json.dumps(
            {"id": i, "title": f"标题{i}", "raw_text": "素材正文", "features": {}},
            ensure_ascii=False), encoding="utf-8")
    (tmp_path / "MANIFEST.json").write_text(json.dumps({
        "version": "v1", "count": len(manifest_ids or ids),
        "gen": {"prompt_fingerprint": "abc"},
        "items": [{"id": i} for i in (manifest_ids or ids)],
    }, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_load_corpus_ok(tmp_path):
    d = write_corpus(tmp_path, ["m01", "m02"])
    manifest, items = R.load_corpus(d)
    assert manifest["version"] == "v1"
    assert [it["id"] for it in items] == ["m01", "m02"]


def test_load_corpus_rejects_manifest_file_mismatch(tmp_path):
    """清单与磁盘不一致必须**炸**，不能静默少跑一条。

    （checklist §3：「删掉一条素材 → 跑分器明确报错，而不是静默少跑一条」）
    """
    d = write_corpus(tmp_path, ["m01"], manifest_ids=["m01", "m02"])
    with pytest.raises(SystemExit) as e:
        R.load_corpus(d)
    assert "不一致" in str(e.value)


def test_load_corpus_rejects_missing_manifest(tmp_path):
    (tmp_path / "m01.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        R.load_corpus(tmp_path)
    assert "MANIFEST" in str(e.value)


def test_load_corpus_ignores_manifest_json_as_material(tmp_path):
    """Windows 的 glob 不区分大小写 —— MANIFEST.json 绝不能被当成素材。"""
    d = write_corpus(tmp_path, ["m01"])
    _, items = R.load_corpus(d)
    assert len(items) == 1


# ---------------------------------------------------------------- 断言接线

def test_assert_material_separates_call_failure_from_assertion_failure():
    """**调用失败与断言失败必须分开**（spec §4：失败要能归因）。"""
    item = {"id": "m01", "raw_text": "2023年我月入3万。"}
    raw = {"_structured": {"facts": [{"text": "月入3万"}]},
           "xhs": {"call_failed": "RateLimited: 429"}}
    out = R.assert_material(item, raw, ["xhs"])
    assert out["xhs"]["kind"] == "call_failed"
    assert out["xhs"]["ok"] is False


def test_assert_material_missing_cell_is_call_failed_not_pass():
    out = R.assert_material({"id": "m01", "raw_text": "x"}, {"_structured": None}, ["xhs"])
    assert out["xhs"]["kind"] == "call_failed"
    assert out["xhs"]["ok"] is False


def test_assert_material_pass_path():
    item = {"id": "m01", "raw_text": "2023年我月入3万，做了两年。"}
    draft = {"titles": ["一个像样的标题", "另一个像样的标题"],
             "body": "2023年月入3万，做了两年，这段经历让我想清楚很多事，"
                     "也想把它完整地写下来，给同样在犹豫要不要迈出这一步的人看。",
             "tags": ["副业", "写作"],
             "qa": {"passed": True, "issues": []}}
    raw = {"_structured": {"facts": []}, "xhs": {"draft": draft}}
    out = R.assert_material(item, raw, ["xhs"])
    assert out["xhs"]["ok"] is True, out["xhs"]["reason"]


# ---------------------------------------------------------------- 基线对比

def _report(fp: str, model: str, cells: dict) -> dict:
    return {"prompt_fingerprint_combined": fp, "model_configured": model, "cells": cells}


def test_diff_marks_regression_and_improvement(tmp_path):
    base = _report("FP", "m1", {"m01": {"xhs": {"ok": True}, "douyin": {"ok": False}}})
    p = tmp_path / "base.json"
    p.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")

    now = _report("FP", "m1", {"m01": {"xhs": {"ok": False, "kind": "assert"},
                                       "douyin": {"ok": True}}})
    d = R.diff_baseline(now, p)
    assert d["comparable"] is True
    assert d["regressed"] == ["m01/xhs"]
    assert d["improved"] == ["m01/douyin"]


def test_diff_is_incomparable_when_prompt_changed(tmp_path):
    """prompt 指纹不同 → **标为不可比**，不许静默比较。"""
    p = tmp_path / "base.json"
    p.write_text(json.dumps(_report("OLD", "m1", {}), ensure_ascii=False), encoding="utf-8")
    d = R.diff_baseline(_report("NEW", "m1", {}), p)
    assert d["comparable"] is False
    assert "prompt" in d["reason"]


def test_diff_is_incomparable_when_model_changed(tmp_path):
    p = tmp_path / "base.json"
    p.write_text(json.dumps(_report("FP", "old-model", {}), ensure_ascii=False), encoding="utf-8")
    d = R.diff_baseline(_report("FP", "new-model", {}), p)
    assert d["comparable"] is False
    assert d["reason"] == "模型不同"


# ---------------------------------------------------------------- 成本

def test_auto_timeout_scales_with_platforms():
    """**踩过的坑**：写死 600s 会把 5 平台的正常长跑误判成超时，整条素材的格子全废。

    实测单平台 101~250s，5 平台轻松超 10 分钟。
    """
    assert R.auto_timeout(1) >= 600            # 有底
    assert R.auto_timeout(5) > 600             # 必须随平台数增长
    assert R.auto_timeout(5) > R.auto_timeout(2)
    assert R.auto_timeout(0) >= 600            # 防御：平台数为 0 也不崩


def test_estimate_counts_cells_and_calls():
    est = R.estimate([{"id": "m01"}, {"id": "m02"}], ["xhs", "douyin"])
    assert est["materials"] == 2 and est["platforms"] == 2 and est["cells"] == 4
    assert est["approx_llm_calls"] > 0


def test_estimate_grows_with_clip_sheet_platforms():
    """含 video_native 平台时应多算一次剪辑单调用。"""
    a = R.estimate([{"id": "m01"}], ["xhs"])["approx_llm_calls"]
    b = R.estimate([{"id": "m01"}], ["douyin"])["approx_llm_calls"]
    assert b > a


# ---------------------------------------------------------------- 报告

def test_report_md_carries_scope_note(tmp_path):
    """报告抬头必须有那句边界声明（checklist §7）。"""
    report = {"_scope_note": R.SCOPE_NOTE, "ts": "t", "elapsed_sec": 1,
              "corpus": {"version": "v1", "count": 1, "prompt_fingerprint": "abc"},
              "prompt_fingerprint": {}, "prompt_fingerprint_combined": "COMB",
              "model_configured": "m", "mock": False, "platforms": ["xhs"],
              "totals": {"cells": 1, "passed": 1, "assert_failed": 0,
                         "call_failed": 0, "missing": 0, "pass_rate": 1.0},
              "cells": {"m01": {"xhs": {"ok": True}}}}
    md = R.render_md(report, [{"id": "m01", "title": "标题"}])
    assert "不能" in md and "docs/50" in md
    assert "100.0%" in md


def test_scope_note_states_the_boundary():
    assert "同一批输入" in R.SCOPE_NOTE
    assert "docs/50" in R.SCOPE_NOTE


def test_write_report_splits_failure_kinds(tmp_path):
    """断言失败与调用失败在 totals 里分开计数。"""
    cells = {"m01": {"xhs": {"ok": False, "kind": "assert", "reason": "r"},
                     "douyin": {"ok": False, "kind": "call_failed", "reason": "429"}}}
    manifest = {"version": "v1", "gen": {"prompt_fingerprint": "abc"}}
    rep = R.write_report(tmp_path, manifest, cells, ["xhs", "douyin"],
                         [{"id": "m01", "title": "t"}], 1.0, None)
    assert rep["totals"]["assert_failed"] == 1
    assert rep["totals"]["call_failed"] == 1
    assert rep["totals"]["pass_rate"] == 0.0
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "report.md").exists()
