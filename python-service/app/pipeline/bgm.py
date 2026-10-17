"""带 BGM 的素材怎么找停顿（`docs/spec_bgm.md`）。

## 为什么要这个模块

「口播粗剪」原先靠 `ffmpeg silencedetect`（**音量门限**）找停顿。但真实口播素材
**普遍带 BGM** —— BGM 把人声之间的空隙填满，于是**一段停顿都找不到**，粗剪等于没跑。

实测（2026-10-17，64.76s 社区口播素材）：

    mean_volume -13.6dB（电平正常）
    门限 -30dB → 0 段静音
    门限 -40dB → 0 段静音
    门限 -50dB → 0 段静音     ← 降到 -50 仍然 0

## 解法：判据换成"**没有语音**"，不是"音量低"

`faster-whisper` 的 `vad_filter=True`（Silero VAD，开源）**本来就在跑** ——
转写这一步已经知道"人在哪儿说话"，而且**与 BGM 无关**。把它当停顿判据，
等于**零新依赖**地解决 BGM 问题。

而且去冗余阶段**已经在原文上转写过一遍**（`docs/spec_disfluency.md` 关键决定一），
那一遍的段边界可以直接复用 —— **不用为找停顿再转写一次**。

## 降级

VAD 不可用（缺 ASR / 模型加载失败）时退回 `ffmpeg arnndn/afftdn` 降噪 + `silencedetect`。
两条路**都要在结果里写明用了哪条** —— 剪点可能不同，用户有权知道。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .media import _ffmpeg_bin

#: 多长的语音空隙算一个停顿（秒）
DEFAULT_MIN_PAUSE_SEC = 0.8

#: 判"有没有 BGM"时，无人声窗口的音量超过这个值就认为有 BGM（dB）
DEFAULT_BGM_LEVEL_DB = -45.0


@dataclass
class PauseResult:
    """停顿判定结果。`source` 必须能被报告出来 —— 降级不能静默。"""

    pauses: list[tuple[float, float]] = field(default_factory=list)
    source: str = "vad"                 # "vad" | "denoise"
    note: str = ""

    def as_dict(self) -> dict:
        return {"pauses": [[round(s, 3), round(e, 3)] for s, e in self.pauses],
                "count": len(self.pauses),
                "source": self.source, "note": self.note}


# ---------------------------------------------------------------- 语音窗口（真 VAD）

#: VAD 判"这一段是语音"的门限（0~1）。Silero 默认 0.5，偏灵敏；0.4 更不容易漏。
DEFAULT_VAD_THRESHOLD = 0.4


def vad_speech_windows(wav: str | Path, *, min_pause_sec: float = DEFAULT_MIN_PAUSE_SEC,
                       threshold: float = DEFAULT_VAD_THRESHOLD,
                       speech_pad_ms: int = 30) -> list[tuple[float, float]]:
    """用 **Silero VAD**（faster-whisper 自带）直接拿语音窗口 —— 本模块的主路径。

    **为什么不能复用转写的段边界**（实测踩到）：`faster-whisper` 的 `vad_filter=True`
    是**为切段服务的**，调得很粗（不能把一句话切成两半）→ 它会把停顿**并进语音段**。
    实测那条 64.76s 的 BGM 口播：转写段边界算出来只有 **1 个**语音窗口 → 0 段停顿。

    所以必须直接调 VAD，并把 `min_silence_duration_ms` 设成"我们想要的停顿长度"。
    这条路**与 BGM 无关**（判据是"有没有人声"，不是"音量低不低"）—— 这正是本 spec 的核心。

    零新依赖：faster-whisper 已经装了（粗剪本来就要它出字幕）。
    """
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    from .asr import _load_audio_16k_mono
    audio = _load_audio_16k_mono(str(wav))
    opts = VadOptions(threshold=threshold,
                      min_silence_duration_ms=max(1, int(min_pause_sec * 1000)),
                      speech_pad_ms=speech_pad_ms)
    stamps = get_speech_timestamps(audio, opts, sampling_rate=16000)
    # Silero 的 start/end 是**采样点**，不是秒
    return [(round(float(t["start"]) / 16000, 3), round(float(t["end"]) / 16000, 3))
            for t in stamps]


def pauses_vad(wav: str | Path, total: float, *,
               min_pause_sec: float = DEFAULT_MIN_PAUSE_SEC,
               threshold: float = DEFAULT_VAD_THRESHOLD) -> PauseResult:
    """主路径：VAD 找停顿。"""
    try:
        win = vad_speech_windows(wav, min_pause_sec=min_pause_sec, threshold=threshold)
    except Exception as e:                                   # noqa: BLE001
        return PauseResult([], source="unavailable", note=f"VAD 不可用：{str(e)[:150]}")
    pauses = pauses_between(win, total, min_pause=min_pause_sec)
    return PauseResult(pauses, source="vad",
                       note=f"VAD 语音窗口 {len(win)} 个，最短静音 {min_pause_sec}s")


# ---------------------------------------------------------------- 语音窗口（由转写段，备用）


def speech_windows(segments: list[dict], total: float,
                   *, merge_gap: float = 0.25) -> list[tuple[float, float]]:
    """转写段 → 语音窗口（**备用**，不是主路径）。

    ⚠️ **实测（2026-10-17）：这条不够用** —— `faster-whisper` 的段是**为切段服务的**，
    调得很粗，会把停顿并进语音段。64.76s 的 BGM 口播用这条只算出 **1 个**窗口 → 0 段停顿。
    主路径是 `vad_speech_windows`（直接调 Silero VAD）。

    保留它是因为：**已经转写过、且 VAD 不可用**时还能拿它兜一下。
    """
    raw: list[list[float]] = []
    for s in segments or []:
        try:
            a, b = float(s["start"]), float(s["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b <= a:
            continue
        if raw and a - raw[-1][1] < merge_gap:
            raw[-1][1] = max(raw[-1][1], b)
        else:
            raw.append([a, b])
    if total > 0:
        for w in raw:
            w[1] = min(w[1], total)
    return [(round(a, 3), round(b, 3)) for a, b in raw if b > a]


def pauses_between(windows: list[tuple[float, float]], total: float,
                   *, min_pause: float = DEFAULT_MIN_PAUSE_SEC,
                   edge_min: float | None = None) -> list[tuple[float, float]]:
    """语音窗口之间的空隙 → **停顿区间**（含片头/片尾那两段）。

    `edge_min`：片头/片尾算不算停顿的最小长度（默认与 `min_pause` 相同）。
    片头那一段特别值得剪 —— 很多人开机后要先愣两秒。
    """
    edge = min_pause if edge_min is None else edge_min
    out: list[tuple[float, float]] = []
    if total <= 0:
        return out
    prev = 0.0
    for i, (a, b) in enumerate(windows):
        gap = a - prev
        need = edge if i == 0 else min_pause
        if gap >= need:
            out.append((round(prev, 3), round(a, 3)))
        prev = max(prev, b)
    if total - prev >= edge:
        out.append((round(prev, 3), round(total, 3)))
    return [(s, e) for s, e in out if e > s]


# ---------------------------------------------------------------- 音量（用于 BGM 检测 / 降噪兜底）


def mean_volume_db(wav: str | Path, *, start: float | None = None,
                   end: float | None = None) -> float | None:
    """测一段音频的平均音量（dB）。`volumedetect` 的输出在 stderr。"""
    cmd = [_ffmpeg_bin("ffmpeg"), "-hide_banner", "-nostats"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(wav)]
    if end is not None and start is not None:
        cmd += ["-t", f"{max(0.0, end - start):.3f}"]
    cmd += ["-af", "volumedetect", "-f", "null", "-"]
    cp = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    for line in (cp.stderr or "").splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0].strip())
            except (IndexError, ValueError):
                return None
    return None


@dataclass
class BgmInfo:
    has_bgm: bool = False
    quiet_level_db: float | None = None      # 无人声窗口的平均音量
    speech_level_db: float | None = None     # 有人声窗口的平均音量
    quiet_windows: list[tuple[float, float]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"has_bgm": self.has_bgm,
                "quiet_level_db": self.quiet_level_db,
                "speech_level_db": self.speech_level_db,
                "quiet_window_count": len(self.quiet_windows),
                "quiet_windows": [[round(s, 3), round(e, 3)] for s, e in self.quiet_windows]}


def detect_bgm(wav: str | Path, windows: list[tuple[float, float]], total: float,
               *, threshold_db: float = DEFAULT_BGM_LEVEL_DB,
               min_window: float = 1.0) -> BgmInfo:
    """素材里有没有 BGM？

    **不是靠猜**：VAD 说"没人说话"的窗口里，如果音量仍然不低 → 那声音只能是 BGM。
    同时把那些窗口返回 —— 它们正是"抽干净伴奏"的候选（见 `pick_bed_clip`）。

    为什么要做这个检测：不检测的话，用户看到"0 段停顿"只会以为工具坏了。
    这正是本 spec 的由来。
    """
    info = BgmInfo()
    quiet = [(s, e) for s, e in _complement(windows, total) if e - s >= min_window]
    info.quiet_windows = quiet
    if not quiet:
        return info
    # 取最长的那个窗口测（样本长，读数稳）
    s, e = max(quiet, key=lambda w: w[1] - w[0])
    info.quiet_level_db = mean_volume_db(wav, start=s, end=e)
    if windows:
        ss, se = max(windows, key=lambda w: w[1] - w[0])
        info.speech_level_db = mean_volume_db(wav, start=ss, end=se)
    info.has_bgm = (info.quiet_level_db is not None
                    and info.quiet_level_db > threshold_db)
    return info


def _complement(windows: list[tuple[float, float]], total: float) -> list[tuple[float, float]]:
    """语音窗口的**补集**（没人说话的那些段）。"""
    out: list[tuple[float, float]] = []
    prev = 0.0
    for a, b in windows:
        if a > prev:
            out.append((prev, a))
        prev = max(prev, b)
    if total > prev:
        out.append((prev, total))
    return out


def pick_bed_clip(quiet_windows: list[tuple[float, float]],
                  *, min_loop_sec: float = 2.0) -> tuple[float, float] | None:
    """从"只有 BGM、没人声"的窗口里挑一段做伴奏底。

    选**最长**的：伴奏与原曲天然同相位/同音色（它就是同一条素材），
    越长越不容易听出循环周期。

    **一个够长的窗口都没有 → 返回 None**（调用方要**明确失败**，不硬凑）。
    """
    usable = [(s, e) for s, e in quiet_windows if e - s >= min_loop_sec]
    if not usable:
        return None
    return max(usable, key=lambda w: w[1] - w[0])


# ---------------------------------------------------------------- 降噪兜底


#: 降噪滤镜（ffmpeg 内置）。`afftdn` 不需要额外模型文件，优先用它。
#: `arnndn` 效果更好但要一个 .rnnn 模型（开源，另下）—— 有就带上，没有就只用 afftdn。
DENOISE_CHAIN = "afftdn=nf=-25"


def denoise_wav(src: str | Path, dst: str | Path, *, chain: str = DENOISE_CHAIN) -> None:
    """降噪到另一个文件（兜底路径用：降噪后再跑 silencedetect）。"""
    cp = subprocess.run([_ffmpeg_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                         "-i", str(src), "-af", chain, "-ac", "1", "-ar", "16000", str(dst)],
                        capture_output=True, text=True, errors="replace")
    if cp.returncode != 0:
        raise RuntimeError(f"降噪失败：{(cp.stderr or '').strip()[:300]}")
