"""参数开关 + 编排 + 报告（`docs/tasks_disfluency.md` T6/T7）。

重点验"**改了它，行为确实变了**" —— 每一项可调值都要有这一条，
否则它就只是"看起来可调"（spec §3 第 4 条）。
"""
from __future__ import annotations

import json

from app.pipeline import disfluency as df
from app.pipeline.roughcut import RoughcutParams


def seg(text, start, end, words=None):
    return {"text": text, "start": start, "end": end, "words": words or []}


def word(w, start, end):
    return {"w": w, "start": start, "end": end}


def material():
    """一份"什么毛病都有"的素材。"""
    return [
        seg("我们今天要讲的", 0.0, 1.5),                          # 半句废弃
        seg("我们今天要讲的是自由。", 1.6, 4.0),                   # （完整的）
        seg("这个项目做了一年。", 4.2, 6.0),
        seg("不对，我是说做了两年。", 6.1, 8.5),                   # 自我纠正
        seg("嗯", 9.0, 9.5, [word("嗯", 9.0, 9.5)]),               # 口头语
        seg("这件事情的核心是执行。", 9.8, 12.0),
        seg("这件事的核心就在于执行。", 12.1, 14.5),                # 疑似重复（低置信）
    ]


# ---------------------------------------------------------------- 开关


def test_all_four_kinds_detected_by_default():
    r = df.analyze(material(), RoughcutParams())
    assert set(r["by_kind"]) == {"restart", "self_correct", "filler", "repeat"}


def test_master_switch_off_means_no_detection():
    r = df.analyze(material(), RoughcutParams(dedup_enabled=False))
    assert r["findings"] == [] and "关闭" in r["note"]


def test_each_kind_can_be_switched_off_independently():
    for kind, field in df.KIND_SWITCH.items():
        r = df.analyze(material(), RoughcutParams(**{field: False}))
        assert kind not in r["by_kind"], f"关掉 {field} 后 {kind} 仍然出现"
        # 其它类**不受影响**
        assert set(r["by_kind"]) == set(df.KIND_SWITCH) - {kind}


def test_filler_wordlist_comes_from_params():
    segs = [seg("哎呀", 0.0, 0.5, [word("哎呀", 0.0, 0.5)])]
    assert df.analyze(segs, RoughcutParams())["by_kind"] == {}
    r = df.analyze(segs, RoughcutParams(dedup_fillers=["哎呀"]))
    assert r["by_kind"] == {"filler": 1}


def test_similarity_threshold_changes_results():
    segs = [seg("这件事情的核心是执行。", 0.0, 2.5), seg("这件事的核心就在于执行。", 2.6, 5.0)]
    assert df.analyze(segs, RoughcutParams())["by_kind"] == {"repeat": 1}
    assert df.analyze(segs, RoughcutParams(dedup_similarity=0.99))["by_kind"] == {}


def test_min_restart_chars_changes_results():
    segs = [seg("我们要讲", 0.0, 1.0), seg("我们要讲的是自由。", 1.1, 3.0)]
    assert df.analyze(segs, RoughcutParams())["by_kind"] == {"restart": 1}
    # 要求"半句至少 6 字" → 4 字的"我们要讲"不够格
    assert df.analyze(segs, RoughcutParams(dedup_min_restart_chars=6))["by_kind"] == {}


# ---------------------------------------------------------------- 配置文件


def test_config_file_reaches_the_params(tmp_path):
    """配置文件的词表**真的能到判定里**（不是只存着不用）。"""
    cfg = tmp_path / "roughcut.json"
    cfg.write_text(json.dumps({"dedup_fillers": ["哎呀"], "dedup_similarity": 0.9},
                              ensure_ascii=False), encoding="utf-8")
    p = RoughcutParams.load(cfg)
    assert p.dedup_fillers == ["哎呀"] and p.dedup_similarity == 0.9
    segs = [seg("哎呀", 0.0, 0.5, [word("哎呀", 0.0, 0.5)])]
    assert df.analyze(segs, p)["by_kind"] == {"filler": 1}


def test_cli_override_wins_over_config(tmp_path):
    cfg = tmp_path / "roughcut.json"
    cfg.write_text(json.dumps({"dedup_similarity": 0.9}), encoding="utf-8")
    p = RoughcutParams.load(cfg).merged(dedup_similarity=0.5)
    assert p.dedup_similarity == 0.5


def test_params_used_is_reported():
    """报告里要能看出"这次用的是哪些参数" —— 否则复核无从谈起。"""
    r = df.analyze(material(), RoughcutParams(dedup_similarity=0.8))
    assert r["params_used"]["similarity"] == 0.8
    assert "filler" in r["params_used"]["enabled_kinds"]


# ---------------------------------------------------------------- 报告


def test_report_lists_every_finding_with_reason():
    r = df.analyze(material(), RoughcutParams())
    text = df.format_report(r, merged_sec=r["total_sec"], before_sec=20.0)
    assert "半句废弃" in text and "口头语" in text and "自我纠正" in text
    assert "起头又重说" in text                     # 理由，不只是"这里剪掉"
    assert "剪后时长" in text
    assert len(text.splitlines()) >= len(r["findings"]) + 2


def test_report_marks_llm_degradation():
    r = df.analyze(material(), RoughcutParams(dedup_review=True),
                   chat=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert r["llm_skipped"] is True
    assert "没有跑成" in df.format_report(r)


def test_report_says_zero_is_normal():
    r = df.analyze([seg("这是一段干净的口播。", 0.0, 3.0)], RoughcutParams())
    assert "没有发现冗余" in df.format_report(r)


def test_cut_intervals_are_extractable():
    r = df.analyze(material(), RoughcutParams())
    cuts = df.to_cut_intervals(r)
    assert len(cuts) == len(r["findings"])
    assert all(e > s for s, e in cuts)
