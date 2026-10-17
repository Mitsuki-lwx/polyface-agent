"""按场景切段：一条长录像 → N 条能各自发的素材（`docs/spec_scene_split.md`）。

分三步，每步都能单独验：
1. `detect_scene_cuts` —— 画面切点（PySceneDetect）
2. `align_to_speech` —— **吸附到语音边界**（画面切了但话没说完是常态）
3. `merge_short` —— 低于最短时长的段**并入前一段**（不是丢掉）

**PySceneDetect 是可选依赖**（沿用 `asr.py` / `beats.py` 同一条路）：
没装就明确报错 + 安装指引，绝不静默返回空结果 —— 空结果会被误读成"这条视频没有切点"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: 缺依赖时的指引（与 asr.py 的风格一致：给出确切命令）
INSTALL_HINT = (
    "按场景切段需要 PySceneDetect：\n"
    "  python -m pip install scenedetect\n"
    "（它会一并装 opencv-python。发布包里也应带上它。）"
)

#: 默认检测阈值（ContentDetector 的 threshold）。数值越大越不敏感。
DEFAULT_THRESHOLD = 27.0

#: 默认最短段长（秒）。低于它的段并入前一段。
DEFAULT_MIN_SEC = 3.0

#: 吸附时允许的最大平移（秒）。超过它就不吸 —— 宁可不吸，也不把边界挪到另一句话里。
DEFAULT_MAX_SNAP_SEC = 2.0


def is_available() -> bool:
    """PySceneDetect 是否可导入。"""
    try:
        import scenedetect  # noqa: F401
        from scenedetect import SceneManager  # noqa: F401
        return True
    except Exception:                                        # noqa: BLE001
        return False


# ---------------------------------------------------------------- 1) 画面切点


def detect_scene_cuts(path: str | Path, *, threshold: float = DEFAULT_THRESHOLD,
                      min_scene_sec: float = 0.6, on_progress=None) -> list[float]:
    """返回**切点时刻**（秒），升序；不含 0 与片尾。

    为什么返回"切点"而不是"段"：切点是**独立于边界策略**的事实
    （吸附、合并都要在它之后做），先算成段反而不好挪。
    """
    if not is_available():
        raise RuntimeError(INSTALL_HINT)
    from scenedetect import SceneManager, open_video
    from scenedetect.detectors import ContentDetector

    video = open_video(str(path))
    fps = float(video.frame_rate or 25.0)
    sm = SceneManager()
    sm.add_detector(ContentDetector(threshold=threshold,
                                    min_scene_len=max(1, int(min_scene_sec * fps))))
    sm.detect_scenes(video, show_progress=False, callback=on_progress)
    scenes = sm.get_scene_list()
    # scenes = [(start, end), ...] 覆盖全片 → 切点 = 除第一个之外的所有 start
    cuts = [float(s.get_seconds()) for s, _e in scenes[1:]]
    return sorted(round(c, 3) for c in cuts)


def plan_scenes(cuts: list[float], total_sec: float,
                *, min_sec: float = DEFAULT_MIN_SEC) -> list[tuple[float, float]]:
    """切点 + 总长 → **段**（含 0 与片尾），并把过短的段并入前一段。

    "并入前一段"而不是"丢掉"（spec 关键决定二）：丢掉是**有损**的 ——
    用户可能正是想要那个 3 秒的空镜；并进去最多让前一段长一点，还能再切。
    """
    if total_sec <= 0:
        return []
    bounds = [0.0] + [c for c in sorted(cuts) if 0 < c < total_sec] + [total_sec]
    segs: list[list[float]] = [[bounds[i], bounds[i + 1]] for i in range(len(bounds) - 1)]
    segs = [[s, e] for s, e in segs if e - s > 0]
    if not segs:
        return []

    out: list[list[float]] = []
    for s, e in segs:
        if out and (e - s) < min_sec:
            out[-1][1] = e                    # 并入前一段
        else:
            out.append([s, e])
    # 首段自己太短、又没有前一段可并 → 并入**后一段**
    if len(out) >= 2 and (out[0][1] - out[0][0]) < min_sec:
        out[1][0] = out[0][0]
        out.pop(0)
    return [(round(s, 3), round(e, 3)) for s, e in out]


# ---------------------------------------------------------------- 2) 语音吸附


@dataclass
class AlignResult:
    cuts: list[float]
    snapped: int = 0            # 实际被吸附的切点数
    speech_aligned: bool = True  # 有没有语音边界可用


def align_to_speech(cuts: list[float], speech_bounds: list[float],
                    *, max_shift_sec: float = DEFAULT_MAX_SNAP_SEC) -> AlignResult:
    """把每个画面切点**吸附到最近的语音边界**。

    为什么必须做（spec 关键决定一）：画面切点落在说话中间是常态。
    直接按画面切，切出来的段开头缺半句话 —— 而开头缺半句的素材**不能发**。

    `max_shift_sec`：超过它就不吸。**宁可不吸，也不把边界挪到另一句话里** ——
    挪过头会让这一段的结尾变成上一段的开头，两段都不完整。

    `speech_bounds` 为空（无音轨/缺 ASR）→ 原样返回并标记 `speech_aligned=False`，
    **不静默**：调用方要能知道"这一步没做"。
    """
    if not speech_bounds:
        return AlignResult(cuts=list(cuts), snapped=0, speech_aligned=False)
    out, snapped = [], 0
    for c in cuts:
        near = min(speech_bounds, key=lambda b: abs(b - c))
        if abs(near - c) <= max_shift_sec:
            out.append(round(float(near), 3))
            if abs(near - c) > 1e-6:
                snapped += 1
        else:
            out.append(round(float(c), 3))
    # 吸附后可能出现重复或乱序 → 去重 + 排序（吸附是就近平移，不该产生交叠）
    uniq = sorted({c for c in out})
    return AlignResult(cuts=uniq, snapped=snapped, speech_aligned=True)


def speech_bounds_from_segments(segments: list[dict]) -> list[float]:
    """转写段 → 语音边界（每段的起止时刻）。

    用**段的边界**而不是词：段是"按停顿切的"，天然就是句与句之间的气口，
    正好是"可以切"的位置。词级太密，吸附过去会切在词组中间。
    """
    out: list[float] = []
    for s in segments or []:
        for k in ("start", "end"):
            v = s.get(k)
            if isinstance(v, (int, float)):
                out.append(round(float(v), 3))
    return sorted({b for b in out if b > 0})


# ---------------------------------------------------------------- 3) 汇总


@dataclass
class ScenePlan:
    """一次检测的完整结果 —— 落盘/上报都用它。"""

    total_sec: float
    segments: list[tuple[float, float]]
    raw_cuts: list[float] = field(default_factory=list)
    snapped: int = 0
    speech_aligned: bool = True
    threshold: float = DEFAULT_THRESHOLD
    min_sec: float = DEFAULT_MIN_SEC
    detector: str = ""

    def as_dict(self) -> dict:
        return {
            "total_sec": round(self.total_sec, 3),
            "segments": [{"index": i + 1, "start": s, "end": e, "duration": round(e - s, 3)}
                         for i, (s, e) in enumerate(self.segments)],
            "count": len(self.segments),
            "raw_cuts": self.raw_cuts,
            "snapped": self.snapped,
            "speech_aligned": self.speech_aligned,
            "params": {"threshold": self.threshold, "min_sec": self.min_sec},
            "detector": self.detector,
        }


def plan(path: str | Path, total_sec: float, *, segments: list[dict] | None = None,
         threshold: float = DEFAULT_THRESHOLD, min_sec: float = DEFAULT_MIN_SEC,
         max_snap_sec: float = DEFAULT_MAX_SNAP_SEC, on_progress=None) -> ScenePlan:
    """完整走一遍：检测 → 吸附 → 合并。"""
    raw = detect_scene_cuts(path, threshold=threshold, on_progress=on_progress)
    al = align_to_speech(raw, speech_bounds_from_segments(segments or []),
                         max_shift_sec=max_snap_sec)
    segs = plan_scenes(al.cuts, total_sec, min_sec=min_sec)
    detector = ""
    try:
        import scenedetect
        detector = f"PySceneDetect {scenedetect.__version__} ContentDetector"
    except Exception:                                        # noqa: BLE001
        pass
    return ScenePlan(total_sec=total_sec, segments=segs, raw_cuts=raw, snapped=al.snapped,
                     speech_aligned=al.speech_aligned, threshold=threshold, min_sec=min_sec,
                     detector=detector)
