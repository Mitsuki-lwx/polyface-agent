"""节拍与卡点（`docs/spec_b1.md` 关键决定二 / T5）。

**分镜时长不能既由配音决定、又由节拍决定** —— 这是硬冲突。所以提供两种策略：

| 策略 | 行为 | 代价 |
|---|---|---|
| `voice_first`（默认） | 时长**不动**；平移 BGM 的起始偏移，让**尽量多**的边界落在节拍上 | 不保证每个切换都精确踩点 |
| `beat_first` | 每个边界**精确**落在节拍上（不够长就拉、超了就截） | **会改变脚本时长**（配音跟着伸缩/留白） |

默认配音优先 —— "脚本完整"比"踩点精准"重要得多，而边界**多数**落在节拍上已经够"有卡点感"。

librosa 是**可选依赖**（复用 `asr.py` / `tts.py` 同一条路）：没装就跳过卡点并**说明未做**，
绝不因为缺一个可选依赖就让成片失败。
"""
from __future__ import annotations

import os
from pathlib import Path

INSTALL_HINT = "未检测到 librosa，本次**不做卡点**（BGM 从 0 秒起原样铺）。要启用：pip install librosa"

# 判定"落在节拍上"的窗口。0.12s 约等于人耳能察觉的最小偏差量级
DEFAULT_TOLERANCE = 0.12


class BeatsUnavailable(RuntimeError):
    """没有 librosa。**这不是错误** —— 跳过卡点并说明未做即可。"""


def _forced_off() -> bool:
    return (os.environ.get("POLYFACE_BEATS") or "").strip().lower() in ("none", "off", "0")


def is_available() -> bool:
    if _forced_off():
        return False
    try:
        import librosa  # noqa: F401
        return True
    except Exception:                                    # noqa: BLE001
        return False


def describe() -> dict:
    """供 UI/自检：**"启用了"要有可查询证据**。"""
    ok = is_available()
    return {"available": ok, "backend": "librosa" if ok else None,
            "hint": "" if ok else INSTALL_HINT}


def detect(path: str | Path, *, bpm_hint: float | None = None) -> list[float]:
    """检测节拍时间点（秒）。**失败要抛** —— 上层据此降级并说明"未做卡点"。"""
    if not is_available():
        raise BeatsUnavailable(INSTALL_HINT)
    import librosa
    y, sr = librosa.load(str(path), mono=True)
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr, bpm=bpm_hint)
    times = librosa.frames_to_time(frames, sr=sr)
    return sorted(round(float(t), 3) for t in times)


def aligned_ratio(boundaries: list[float], beats: list[float], *,
                  tolerance: float = DEFAULT_TOLERANCE) -> float:
    """有多少比例的边界"落在某个节拍上"。

    **这是算出来的，不是常量** —— checklist §2 专门验"改容差它会变"。
    """
    if not boundaries or not beats:
        return 0.0
    hit = 0
    for b in boundaries:
        if any(abs(b - t) <= tolerance for t in beats):
            hit += 1
    return round(hit / len(boundaries), 4)


def best_offset(boundaries: list[float], beats: list[float], *,
                tolerance: float = DEFAULT_TOLERANCE,
                window: float = 30.0) -> tuple[float, float]:
    """**配音优先**：时长不动，找一个 BGM 起始偏移，让尽量多的边界落在节拍上。

    返回 `(offset, ratio)`。`offset` 为负表示**跳过 BGM 开头**这么多秒
    （音乐的前奏通常不落在重拍上，跳过它是最常见的做法）。

    做法：把每条节拍都当作"第 0 条边界该落的位置"试一遍，取命中率最高的那个。
    搜索范围限在 `window` 秒内 —— 跳过头就没有"这段音乐"的感觉了。
    """
    if not boundaries or not beats:
        return 0.0, 0.0
    b0 = boundaries[0]
    best = (0.0, aligned_ratio(boundaries, beats, tolerance=tolerance))
    for t in beats:
        off = b0 - t                      # 让第 0 条边界正好落在节拍 t 上
        if off > tolerance or off < -window:
            continue
        shifted = [b - off for b in boundaries]
        r = aligned_ratio(shifted, beats, tolerance=tolerance)
        if r > best[1] + 1e-9:
            best = (round(off, 3), r)
    return best


def snap_durations(durations: list[float], beats: list[float], *,
                   tolerance: float = DEFAULT_TOLERANCE,
                   min_sec: float = 0.5) -> list[float]:
    """**卡点优先**：把每个分镜边界精确挪到最近的节拍上。

    **会改变时长** —— 这是它的代价（配音跟着伸缩/留白、字幕跟着变）。
    找不到可用节拍（或会导致某段短于 `min_sec`）时，保留原时长而不是硬凑。
    """
    if not durations or not beats:
        return list(durations)
    out: list[float] = []
    acc = 0.0
    for d in durations[:-1]:              # 最后一段不设边界，跟着前面走
        acc += d
        nearest = min(beats, key=lambda t: abs(t - acc))
        snapped = nearest
        if snapped - sum(out) < min_sec:  # 挪过头会让这段太短 → 不挪
            snapped = acc
        out.append(round(snapped - sum(out), 3))
        acc = snapped
    out.append(round(max(min_sec, sum(durations) - sum(out)), 3))
    return out
