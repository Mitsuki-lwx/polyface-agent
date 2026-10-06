"""口播粗剪单测：错误矩阵 / 参数优先级 / 剪点数学 / 字幕格式。

端到端在 `scripts/e2e_roughcut.py`（真跑 ffmpeg 与 ASR）；这里只测**不需要跑模型**的部分，
让错误分支与参数逻辑能被快速、确定地锁住。
"""
from __future__ import annotations

import json

import pytest

from app.pipeline import asr, roughcut as rc


# ---------------------------------------------------------------- 错误矩阵（docs/checklist_roughcut §4）

def test_missing_file_error_contains_path(tmp_path):
    with pytest.raises(rc.InputMissing) as e:
        rc.probe(tmp_path / "不存在.mp4")
    assert "不存在.mp4" in str(e.value)


def test_no_ffmpeg_error_has_install_hint(monkeypatch):
    monkeypatch.setattr(rc, "has_ffmpeg", lambda: False)
    with pytest.raises(rc.ToolMissing) as e:
        rc.require_ffmpeg()
    assert "ffmpeg" in str(e.value) and "安装" in str(e.value)


def test_four_error_texts_are_distinct():
    """四种失败必须**互不相同** —— 否则用户看不出到底哪儿坏了。"""
    texts = {
        str(rc.InputMissing("输入文件不存在：X")),
        str(rc.NoAudioTrack("这个视频没有音轨：X")),
        str(rc.ToolMissing("未检测到 ffmpeg")),
        str(rc.AsrMissing(asr.INSTALL_HINT)),
    }
    assert len(texts) == 4


def test_asr_missing_hint_mentions_pip(monkeypatch):
    monkeypatch.setattr(asr, "is_available", lambda: False)
    with pytest.raises(RuntimeError) as e:
        asr.transcribe_segments("whatever.wav")
    assert "pip install faster-whisper" in str(e.value)


# ---------------------------------------------------------------- 参数优先级（§2）

def test_defaults_are_the_documented_ones():
    p = rc.RoughcutParams()
    assert (p.pause_sec, p.noise_db, p.keep_margin_sec) == (0.8, -30, 0.08)
    assert p.font == "Microsoft YaHei" and p.font_size == 16 and p.margin_v == 40


def test_config_file_overrides_defaults(tmp_path):
    cfg = tmp_path / "roughcut.json"
    cfg.write_text(json.dumps({"pause_sec": 1.5, "font_size": 24}), encoding="utf-8")
    p = rc.RoughcutParams.load(cfg)
    assert p.pause_sec == 1.5 and p.font_size == 24
    assert p.noise_db == -30, "配置文件没写的项要保持默认"


def test_config_ignores_unknown_keys(tmp_path):
    cfg = tmp_path / "roughcut.json"
    cfg.write_text(json.dumps({"pause_sec": 1.1, "根本不存在": 9}), encoding="utf-8")
    assert rc.RoughcutParams.load(cfg).pause_sec == 1.1


def test_cli_override_wins_and_none_keeps_current():
    base = rc.RoughcutParams.load(None).merged(pause_sec=1.5)
    assert base.pause_sec == 1.5
    assert base.merged(font="X", pause_sec=None).pause_sec == 1.5, "None = 用户没传，不该覆盖"


def test_missing_config_file_falls_back_to_defaults(tmp_path):
    assert rc.RoughcutParams.load(tmp_path / "没有这个文件.json").pause_sec == 0.8


# ---------------------------------------------------------------- 剪点数学（§5/§6）

def test_plan_cuts_applies_margin_and_drops_tiny():
    pauses = [(2.0, 4.0), (10.0, 10.1)]          # 第二段剪掉余量后 <= 0
    cuts = rc.plan_cuts(pauses, keep_margin=0.08, total=20.0)
    assert cuts == [(2.08, 3.92)]


def test_plan_cuts_clamps_to_total():
    cuts = rc.plan_cuts([(9.5, 99.0)], keep_margin=0.0, total=10.0)
    assert cuts == [(9.5, 10.0)]


def test_cut_seconds_sums():
    assert rc.cut_seconds([(0.0, 1.5), (3.0, 4.25)]) == 2.75


def test_select_expr_is_union_of_between_terms():
    expr = rc._select_expr([(1.0, 2.0), (5.0, 6.5)])
    assert expr == "between(t,1.0,2.0)+between(t,5.0,6.5)"


def test_select_expr_empty_is_falsy():
    assert rc._select_expr([]) == "0"


# ---------------------------------------------------------------- 字幕（§7）

def test_srt_timestamp_format():
    assert rc.srt_timestamp(0) == "00:00:00,000"
    assert rc.srt_timestamp(3661.5) == "01:01:01,500"
    assert rc.srt_timestamp(-3) == "00:00:00,000", "负值要夹到 0，不能出负数时间戳"


def test_segments_to_srt_roundtrip():
    srt = rc.segments_to_srt([{"start": 0.0, "end": 2.0, "text": " 大家好 "},
                              {"start": 2.0, "end": 4.5, "text": "第二句"}])
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,000\n大家好\n")
    assert "2\n00:00:02,000 --> 00:00:04,500\n第二句" in srt


def test_simplified_prompt_is_simplified():
    """简体偏置提示词必须是简体中文 —— 这是治"whisper 输出繁体"的那一味药。"""
    assert asr.ZH_SIMPLIFIED_PROMPT == "以下是普通话的句子。"


# ---------------------------------------------------------------- 摘要（§8）

def test_summarize_reports_before_after_and_params():
    text = rc.summarize([(1.0, 2.5)], before=10.0, after=8.5, params=rc.RoughcutParams())
    assert "找到 1 段停顿" in text
    assert "剪掉共 1.50s" in text and "10.00s → 8.50s" in text
    assert "停顿阈值 0.8s" in text


def test_describe_hides_style_when_burning_disabled():
    assert "字幕 关" in rc.RoughcutParams(burn_subs=False).describe()
