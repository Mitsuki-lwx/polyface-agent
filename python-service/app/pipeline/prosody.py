"""本地声学特征：停顿前后**听起来**是什么样（`openspec/changes/pause-prosody`）。

为什么需要它（模型听不到音频）：

    停顿复核只看文字。文字里没有"这句是渐弱收尾还是说断就断"，
    也没有"下一句是起势还是接着上一句"—— 而这恰好是判断
    "留白" vs "卡壳" 最直接的证据。

两条硬规矩：

1. **音频不出本机** —— 只产出几个数字，发出去的是数字，不是声音。
2. **程序算，模型读** —— 与"语速/切换次数本地算"同一条纪律（模型不数数）。

代价：只算候选停顿前后各 `WINDOW_SEC` 秒，不碰整条音频。
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

#: 每条候选前后各看多久（秒）。
WINDOW_SEC = 1.5
#: 斜率 = 末尾 EDGE_SEC 均值 − 开头 EDGE_SEC 均值（避免把词间小停顿算进去）。
EDGE_SEC = 0.30
#: 低于这个平均能量视作没人声（静音/气声），不给音高。
VOICED_RMS = 0.01
FMIN, FMAX = 70, 400
_HOP = 160                      # 10ms 一帧


def _envelope(y, sr):
    import librosa
    return librosa.feature.rms(y=y, hop_length=_HOP)[0]


def analyze_pauses(wav_path: str | Path, pauses: list[tuple[float, float]], *,
                   indices: list[int] | None = None,
                   window_sec: float = WINDOW_SEC) -> list[dict | None]:
    """批量算候选停顿的声学特征：**一次加载音频**。

    返回与 `indices` 一一对应（`None` = 这一条算不出来）。
    librosa 不可用 / 音频读不出 → 全 `None` + warning，**不抛**：
    声学特征是增益，不是前置条件。
    """
    want = list(range(len(pauses))) if indices is None else list(indices)
    try:
        import librosa
        import numpy as np
        y, sr = librosa.load(str(wav_path), sr=16000, mono=True)
    except Exception as e:                       # noqa: BLE001
        log.warning("声学特征不可用（缺依赖或读不出音频）：%s", e)
        return [None] * len(want)

    total = len(y) / sr
    edge = max(2, int(EDGE_SEC * sr / _HOP))

    def _rms(t0: float, t1: float):
        seg = y[int(t0 * sr):int(t1 * sr)]
        if len(seg) < int(0.1 * sr):
            return None, None
        env = _envelope(seg, sr)
        if len(env) < 6:
            return None, None
        return float(env.mean()), float(env[-edge:].mean() - env[:edge].mean())

    def _pitch(t0: float, t1: float):
        seg = y[int(t0 * sr):int(t1 * sr)]
        if len(seg) < int(0.1 * sr):
            return None
        env = _envelope(seg, sr)
        if env.size == 0 or float(env.mean()) < VOICED_RMS:
            return None
        try:
            f0 = librosa.yin(seg, fmin=FMIN, fmax=FMAX, sr=sr, frame_length=1024)
        except Exception:                        # noqa: BLE001 — 音高算不出不该毁掉判定
            return None
        f0 = f0[np.isfinite(f0)]
        return round(float(np.median(f0)), 1) if f0.size else None

    out: list[dict | None] = []
    for i in want:
        if i < 0 or i >= len(pauses):
            out.append(None)
            continue
        start, end = float(pauses[i][0]), float(pauses[i][1])
        a0, a1 = max(0.0, start - window_sec), min(total, start)
        b0, b1 = max(0.0, end), min(total, end + window_sec)
        rms_b, slope = _rms(a0, a1)
        rms_a, _ = _rms(b0, b1)
        p_b, p_a = _pitch(a0, a1), _pitch(b0, b1)
        out.append({
            "rms_before": round(rms_b, 3) if rms_b is not None else None,
            "rms_after": round(rms_a, 3) if rms_a is not None else None,
            "rms_slope": round(slope, 3) if slope is not None else None,
            "pitch_before": p_b,
            "pitch_after": p_a,
            "pitch_delta": round(p_a - p_b, 1) if (p_b is not None and p_a is not None) else None,
        })
    return out


def summarize(feat: dict | None) -> str:
    """一句人话（报告/日志用）。没有数据就说没有，不编。"""
    if not feat:
        return "声学特征：不可用"
    parts = []
    if feat.get("rms_slope") is not None:
        slope = feat["rms_slope"]
        parts.append("停顿前能量" + ("渐弱" if slope < -0.005 else
                                     "渐强" if slope > 0.005 else "平稳")
                     + f"（{slope:+.3f}）")
    if feat.get("pitch_delta") is not None:
        d = feat["pitch_delta"]
        parts.append("音高" + ("升高" if d > 8 else "降低" if d < -8 else "持平")
                     + f"（{feat['pitch_before']}→{feat['pitch_after']}Hz）")
    return "声学特征：" + "；".join(parts) if parts else "声学特征：无有效数据"
