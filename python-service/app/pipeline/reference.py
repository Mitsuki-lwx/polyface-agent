"""参考视频分析（`openspec/changes/reference-video-analysis`）。

**为什么需要**：能读懂自己的视频 ✓（转写 + 关键帧 + 场景切换），但看不了别人的 ✗。
做内容的人最高频的问题是「这条爆了，它为什么爆」—— 想学结构、节奏、钩子 ✓。

这里**不新造模型** ✓：三路证据（转写 / 关键帧描述 / 场景切换）已经都有了，
把本地能算的数（语速、段数、切换次数）算好 ✓ 再交给模型做**判断与措辞** ✓。

⚠️ 模型的算术不可信 ✗ —— 所以数字一律本地算，不让它数。
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from .. import llm
from . import frames as _fr
from . import vision as _vis
from .ingest import run_transcribe

logger = logging.getLogger(__name__)

MAX_FRAMES = 6


def system_prompt() -> str:
    """参考视频分析的系统提示词。

    定义成**函数**而不是模块级常量：提示词版本号那一层要靠函数源码算指纹 ✓
    （模块常量算不出指纹，测试会红 ✗）。
    """
    return (
        "你是一名短视频结构分析师。给你一条**别人拍的**视频的客观材料（转写文本、"
        "关键帧描述、场景切换时刻、本地算好的数字），请输出**一个 JSON 对象**，不要别的话。\n"
        "{\n"
    '  "hook": {"type": "提问|反常识|利益承诺|故事|冲突|其它", "quote": "前3秒原话",\n'
    '           "why": "它靠什么留住人（一句话）"},\n'
    '  "structure": [{"name": "开场|论证|举例|转折|收尾|其它", "start": 0.0, "end": 12.4,\n'
    '                 "purpose": "这段在干什么（一句话）"}],\n'
    '  "visual": {"kind": "口播|演示|素材拼接|混合", "evidence": "从关键帧看出来的依据"},\n'
    '  "takeaways": ["可以直接照做的动作", "…"],\n'
    '  "caveat": "这份分析的局限（例如：只有画面没有声音时写这里）"\n'
        "}\n"
        "规则：\n"
        "1. 每一条判断都要能指回材料里的具体内容；**材料里没有的不要编** ✗。\n"
        "2. 结构**不要强行三段式** ✗ —— 视频没有明确分段就如实说「结构松散，无明显分段」。\n"
        "3. `takeaways` 必须是**动作**（「前3秒先抛反常识」，而不是「内容要优质」✗）。\n"
        "4. 数字（语速/段数/切换次数）用材料给的，**不要自己算** ✗。\n"
    )


def _local_numbers(text: str, total: float, scenes: list[float]) -> dict:
    """本地能算的全在这里 —— 不让模型数数 ✗。"""
    chars = len("".join(text.split()))
    minutes = total / 60 if total > 0 else 0
    return {
        "duration_sec": round(total, 2),
        "chars": chars,
        "chars_per_min": round(chars / minutes) if minutes > 0 else 0,
        "scene_switches": len(scenes),
        # 平均多少秒换一次镜头；没有切换就写 0，不编 ✗
        "avg_shot_sec": round(total / (len(scenes) + 1), 1) if total > 0 else 0,
        "scene_times": [round(t, 1) for t in scenes],
    }


def build_prompt(text: str, descs: list[dict], numbers: dict, has_speech: bool) -> str:
    lines = ["【客观材料】", f"时长 {numbers['duration_sec']}s · 字数 {numbers['chars']} · "
             f"语速 {numbers['chars_per_min']} 字/分 · 镜头切换 {numbers['scene_switches']} 次 · "
             f"平均镜头 {numbers['avg_shot_sec']}s"]
    if numbers["scene_times"]:
        lines.append("切换时刻：" + "、".join(f"{t}s" for t in numbers["scene_times"][:20]))
    lines.append("")
    if has_speech:
        lines.append("【转写全文】")
        lines.append(text[:4000])
    else:
        lines.append("【转写】无语音（这条视频没有说话）—— 只分析画面与节奏，不要编台词 ✗")
    lines.append("")
    lines.append("【关键帧】")
    for d in descs:
        if d.get("desc"):
            lines.append(f"- {d['t_sec']:.1f}s：{d['desc']}")
    return "\n".join(lines)


def analyze_reference(video: str | Path, *, frame_count: int = MAX_FRAMES) -> dict:
    """参考视频 → 结构化分析。失败会抛异常（调用方决定怎么报）。"""
    video = Path(video)
    if not video.is_file():
        raise FileNotFoundError(f"视频不存在：{video}")

    total = _fr.duration_sec(video)

    # 1) 语音
    tr = run_transcribe(str(video), "auto")
    text = (tr.text or "").strip()
    has_speech = bool(text)

    # 2) 画面：关键帧 + 描述
    with tempfile.TemporaryDirectory(prefix="ref-") as tmp:
        picked = _fr.extract_keyframes(video, Path(tmp), count=max(1, frame_count))
        descs = _vis.describe_frames(picked)

    # 3) 节奏：场景切换
    scenes = _fr.scene_times(video)

    numbers = _local_numbers(text, total, scenes)
    data = llm.chat_json(build_prompt(text, descs, numbers, has_speech),
                         system=system_prompt(), temperature=0.3, scene="reference")
    if not isinstance(data, dict):
        raise ValueError(f"分析结果不是 JSON 对象：{type(data).__name__}")

    data["numbers"] = numbers
    data["speech"] = {"has_speech": has_speech, "source": getattr(tr, "source", None)}
    data["frames"] = [{"t_sec": d["t_sec"], "desc": d.get("desc", "")} for d in descs]
    logger.info("参考视频分析完成：%s（%.1fs / %d 帧 / 语音=%s）",
                video.name, total, len(descs), has_speech)
    return data
