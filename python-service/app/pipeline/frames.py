"""视频关键帧抽取（`openspec/changes/video-frame-understanding`）。

**为什么需要**：视频导入一直只做**转写** ✗ —— 画面从没进过任何环节：
有画面没说话的素材（风景、演示）转写是空的；画面里的关键信息（PPT/板书）看不见；
粗剪只能按时间顺序切，是**盲剪** ✓。

而 `deepseek-v4.1-flash-1017` **实测能看图** ✓（发一张朱红方块+墨色圆的图，形状与颜色都答对）。

这里只做**抽**：场景变化优先，不够再等距补足；帧压到 640 宽存盘。**描述**在 `vision.py`。
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

FRAME_WIDTH = 640          # 送模型不需要原分辨率；640 够认内容，token 与传输都小
SCENE_THRESHOLD = 0.3      # 经验值：太低会把轻微抖动当切镜，太高会漏掉慢切
DEFAULT_COUNT = 8

_PTS_RE = re.compile(r"pts_time:\s*([0-9.]+)")


class FrameError(RuntimeError):
    """抽帧失败（无 ffmpeg / 读不出时长 / 视频损坏）。"""


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FrameError("找不到 ffmpeg（本项目的抽帧与粗剪都依赖它）")
    return exe


def duration_sec(video: Path) -> float:
    """用 ffprobe 取时长；读不出就报错（**不猜** ✗）。"""
    exe = shutil.which("ffprobe")
    if not exe:
        raise FrameError("找不到 ffprobe")
    out = subprocess.run(
        [exe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True, timeout=120,
    )
    raw = (out.stdout or "").strip()
    try:
        return float(raw)
    except ValueError:
        raise FrameError(f"读不出视频时长：{raw or out.stderr[:120]}") from None


def extract_keyframes(video: str | Path, out_dir: str | Path,
                      count: int = DEFAULT_COUNT) -> list[dict]:
    """抽出最多 `count` 张关键帧。返回 `[{t_sec, path}]`，按时间升序。

    - 先按**场景变化**取（一个 10 分钟固定机位直播，等距会得到 10 张一样的图 ✗）
    - 不足 `count` 张时用**等距**补足（短视频/静态画面也得有帧 ✓）
    - 视频短于 1 秒 → 只取 1 帧（不返回 0 帧假装成功 ✗）
    """
    video = Path(video)
    if not video.is_file():
        raise FrameError(f"视频不存在：{video}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame-*.jpg"):
        old.unlink(missing_ok=True)

    total = duration_sec(video)
    wanted = 1 if total < 1.0 else max(1, count)

    frames = _scene_frames(video, out_dir, wanted)
    if len(frames) < wanted:
        frames = _fill_by_interval(video, out_dir, total, wanted, frames)

    frames.sort(key=lambda f: f["t_sec"])
    logger.info("抽帧完成：%s → %d 帧（时长 %.1fs）", video.name, len(frames), total)
    return frames


def _scene_frames(video: Path, out_dir: Path, wanted: int) -> list[dict]:
    """场景变化帧。文件名按 ffmpeg 输出顺序，时间来自 `showinfo` 的 pts_time。"""
    pattern = out_dir / "frame-%03d.jpg"
    cmd = [
        _ffmpeg(), "-hide_banner", "-nostats", "-y", "-i", str(video),
        "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo,scale={FRAME_WIDTH}:-1",
        "-vsync", "vfr", "-frames:v", str(wanted), str(pattern),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    times = [float(x) for x in _PTS_RE.findall(proc.stderr or "")]
    files = sorted(out_dir.glob("frame-*.jpg"))
    frames = []
    for i, f in enumerate(files):
        # showinfo 只在 select 命中时打印 pts —— 顺序与输出文件一一对应
        t = times[i] if i < len(times) else 0.0
        frames.append({"t_sec": round(t, 2), "path": str(f)})
    if not frames:
        logger.info("场景检测没抽到帧（画面变化少），改用等距")
    return frames


def _fill_by_interval(video: Path, out_dir: Path, total: float,
                      wanted: int, existing: list[dict]) -> list[dict]:
    """不足时按等距补足。已存在的时刻附近（±0.5s）跳过，避免重复帧。"""
    frames = list(existing)
    used = {round(f["t_sec"]) for f in frames}
    # ⚠️ 编号必须接在**已有文件最大编号**之后，不能用 len(frames)：
    # 场景帧可能产出 001，而 frames 只有 1 条 → len=1 → 又写 001 → **覆盖** ✓（实测踩到）
    existing_idx = [int(m.group(1)) for f in frames
                    for m in [__import__("re").search(r"frame-(\d+)\.jpg", f["path"])] if m]
    idx = (max(existing_idx) + 1) if existing_idx else 1
    step = total / wanted if wanted else total
    for k in range(wanted * 2):          # 多试几轮，跳过的会被下一轮补上
        if len(frames) >= wanted:
            break
        t = step * (len(frames) + 1) if step else total / 2
        if t >= total:
            t = max(0.0, total - 0.2)
        if round(t) in used:
            t = min(total - 0.2, t + step / 2)
        if round(t) in used or t < 0:
            break
        out = out_dir / f"frame-{idx:03d}.jpg"
        proc = subprocess.run(
            [_ffmpeg(), "-hide_banner", "-nostats", "-y",
             "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-1", str(out)],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode == 0 and out.is_file() and out.stat().st_size > 0:
            frames.append({"t_sec": round(t, 2), "path": str(out)})
            used.add(round(t))
            idx += 1
    return frames


def extract_at(video: str | Path, out_dir: str | Path, times: list[float]) -> list[dict]:
    """在**指定时刻**各抽一帧（剪辑联动用：每一刀保留段取中间点）。

    和 `extract_keyframes` 的区别：那个是"找有代表性的画面"，这个是"给我这几个时刻的画面" ✓。
    """
    video = Path(video)
    if not video.is_file():
        raise FrameError(f"视频不存在：{video}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame-*.jpg"):
        old.unlink(missing_ok=True)

    total = duration_sec(video)
    frames: list[dict] = []
    for i, t in enumerate(times, 1):
        # 末尾 0.5s 内 often 抽不到关键帧（GOP 边界），夹到 total-0.5 更稳 ✓
        t = max(0.0, min(float(t), max(0.0, total - 0.5)))
        out = out_dir / f"frame-{i:03d}.jpg"
        proc = subprocess.run(
            [_ffmpeg(), "-hide_banner", "-nostats", "-y",
             "-ss", f"{t:.3f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-1", str(out)],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode == 0 and out.is_file() and out.stat().st_size > 0:
            frames.append({"t_sec": round(t, 3), "path": str(out)})
        else:
            logger.warning("时刻 %.2fs 抽帧失败：%s", t, (proc.stderr or "")[-160:])
    return frames


# ---------------------------------------------------------------- 画面质量（纯本地，不花模型）

_QUAL_RE = re.compile(r"lavfi\.(blur|signalstats\.YAVG|signalstats\.YMIN|signalstats\.YMAX)=([0-9.]+)")


def frame_quality(frame_path: str | Path) -> dict:
    """一帧的**技术质量**（本地 ffmpeg 算，不调模型 ✓）。

    返回 `{blur, yavg, ymin, ymax, verdict, why}`：
      - `blur` 是**模糊度**：**越大越糊** ✗（实测：清晰口播帧 8，高斯模糊后 27）
      - `yavg` 亮度均值（0~255）
      - `verdict`: `ok` / `blurry` / `dark` / `overexposed`

    ⚠️ 阈值是**经验值**，故意保守（宁可漏报也别误杀 ✓）：口播画面 7~9 通常够清晰，
    低于 3 才判糊；亮度 < 25 判太暗、> 235 判过曝。
    """
    exe = _ffmpeg()
    proc = subprocess.run(
        [exe, "-hide_banner", "-i", str(frame_path),
         "-vf", "blurdetect,signalstats,metadata=print:file=-", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    # metadata=print:file=- 把指标写到 **stdout**，ffmpeg 自身日志在 stderr —— 两处都找 ✓
    blob = (proc.stdout or "") + (proc.stderr or "")
    vals = {k: float(v) for k, v in _QUAL_RE.findall(blob)}
    blur = vals.get("blur")
    yavg = vals.get("signalstats.YAVG")
    out = {
        "blur": round(blur, 2) if blur is not None else None,
        "yavg": round(yavg, 1) if yavg is not None else None,
        "ymin": vals.get("signalstats.YMIN"),
        "ymax": vals.get("signalstats.YMAX"),
        "verdict": "ok",
        "why": "",
    }
    why: list[str] = []
    if blur is not None and blur > 20.0:
        out["verdict"] = "blurry"
        why.append(f"偏糊（模糊度 {blur:.1f} > 20）")
    if yavg is not None and yavg < 25:
        out["verdict"] = "dark"
        why.append(f"太暗（亮度 {yavg:.0f} < 25）")
    elif yavg is not None and yavg > 235:
        out["verdict"] = "overexposed"
        why.append(f"过曝（亮度 {yavg:.0f} > 235）")
    out["why"] = "；".join(why)
    return out


def scene_times(video: str | Path, threshold: float = 0.30,
                timeout: int = 600) -> list[float]:
    """画面**切换到另一个镜头**的时刻（秒）。抽帧时已经在用同一个检测器 ✓。

    剪辑要用它：一句话中间的换气不该剪，**镜头换了**才是天然的剪点 ✓。
    读不出就返回空列表（调用方据此退回"只按静音"）✓，不抛错 ✗。
    """
    try:
        proc = subprocess.run(
            [_ffmpeg(), "-hide_banner", "-nostats", "-i", str(video),
             "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("画面切换检测失败（按无场景处理）：%s", e)
        return []
    return sorted({round(float(x), 3) for x in _PTS_RE.findall(proc.stderr or "")})
