"""参考视频分析（`app/pipeline/reference.py`）的测试。

**为什么测**：这条链路大部分是"调模型"，只有两处是**纯逻辑** ——
本地数字（不许模型算 ✗）与 prompt 组装（无语音时不许编台词 ✗）。就钉这两处。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline import reference as ref  # noqa: E402


def test_local_numbers_are_computed_locally():
    """数字必须本地算 —— 模型数数不可信 ✗。"""
    n = ref._local_numbers("一二三四五 六七八", 60.0, [10.0, 20.0])
    assert n["chars"] == 8, "空白不计入字数"
    assert n["chars_per_min"] == 8, "60 秒 8 字 → 8 字/分"
    assert n["scene_switches"] == 2
    assert n["avg_shot_sec"] == 20.0, "60s / (2+1) = 20s 平均镜头"


def test_local_numbers_handle_zero_duration():
    n = ref._local_numbers("", 0.0, [])
    assert n["chars_per_min"] == 0 and n["avg_shot_sec"] == 0


def test_prompt_says_no_speech_instead_of_inventing():
    p = ref.build_prompt("", [{"t_sec": 1.0, "desc": "一个人站在门口"}],
                         ref._local_numbers("", 10.0, []), has_speech=False)
    assert "无语音" in p
    assert "不要编台词" in p
    assert "一个人站在门口" in p, "画面描述仍要给 ✓"


def test_prompt_carries_local_numbers_and_caps_long_text():
    long_text = "字" * 9999
    p = ref.build_prompt(long_text, [], ref._local_numbers(long_text, 60.0, [5.0]), has_speech=True)
    assert "语速 9999 字/分" in p
    assert "切换时刻：5.0s" in p
    # 转写截断到 4000 字，避免把整篇塞进 prompt ✗
    assert len(p) < 6000
