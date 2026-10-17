"""按场景切段 CLI（`docs/tasks_scene_split.md` T4~T6）。

用法：
    python scripts/scene_split.py <视频> [选项]

两段式（与粗剪一致）：
    --dry-run            只出方案（含缩略图），**不产出任何视频**
    --pick 1,3,5         只切这几段（不传 = 全切）
    --yes                不问，直接按 --pick 执行

典型流程：
    scene_split.py long.mp4 --dry-run            # 看切成哪几段 + 缩略图
    scene_split.py long.mp4 --pick 2,5 --yes     # 只切第 2、5 段
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python-service"))

from app.pipeline import asr  # noqa: E402
from app.pipeline import scenes as sc  # noqa: E402
from app.pipeline.media import _ffmpeg_bin, has_ffmpeg  # noqa: E402

EXIT_OK, EXIT_ERR = 0, 1


def probe_duration(path: Path) -> float:
    """用 ffprobe 拿时长（不依赖 PySceneDetect）。"""
    out = subprocess.run([_ffmpeg_bin("ffprobe"), "-v", "error",
                          "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise RuntimeError(f"拿不到时长（ffprobe 输出：{out.stdout.strip() or out.stderr.strip()[:200]}）")


def cut_segment(src: Path, dst: Path, start: float, end: float) -> None:
    """切一段。**流复制**（`-c copy`）：不重编码、快、源文件不动。

    **用 `-t`（时长）而不是 `-to`（截止时刻）**：实测踩到 ——
    `-ss` 放在 `-i` **前面**（快速定位）时，`-to` 的参考点不是那个 `-ss`，
    于是 `-ss 15 -to 24` 切出来是 16.1 秒（应该是 9 秒）。`-t` 没有这个歧义。

    代价是切点落在关键帧上（可能差几百毫秒，实测 8.00s → 8.13s）。
    对这个用途够用 —— 段的意义是"能各自去加工"，后面的粗剪会再精确剪一遍。
    """
    dur = end - start
    # **不要加 `-avoid_negative_ts make_zero`**（实测踩到）：它把首帧时间戳挪到 0，
    # 与流复制组合时会让容器时长算错 —— 15.00→24.00 切出来是 **16.1 秒**（应 9 秒）。
    # 那是为拼接准备的参数，这里用不上。
    cp = subprocess.run([_ffmpeg_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                         "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
                         "-c", "copy", str(dst)],
                        capture_output=True, text=True)
    if cp.returncode != 0:
        raise RuntimeError(f"切第 {dst.name} 失败：{(cp.stderr or '').strip()[:300]}")


def thumbnail(src: Path, dst: Path, at: float) -> None:
    """抽一帧做缩略图。**方案的必需部分** —— 只有时间戳的话人认不出哪段是哪段。"""
    cp = subprocess.run([_ffmpeg_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                         "-ss", f"{max(0.0, at):.3f}", "-i", str(src), "-frames:v", "1",
                         "-vf", "scale=320:-2", str(dst)],
                        capture_output=True, text=True)
    if cp.returncode != 0:
        raise RuntimeError(f"抽缩略图失败：{(cp.stderr or '').strip()[:200]}")


def transcript_segments(src: Path, work: Path) -> list[dict]:
    """拿语音边界。缺 ASR 时**降级为纯画面切点并说明**（spec §3 第 8 条）。"""
    wav = work / "audio.wav"
    if not wav.is_file():
        subprocess.run([_ffmpeg_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                       capture_output=True, text=True)
    try:
        return asr.transcribe_segments(str(wav))
    except Exception as e:                                   # noqa: BLE001
        print(f"[警告] 拿不到语音边界（{str(e)[:120]}）→ 降级为纯画面切点。\n"
              f"       这意味着切点可能落在说话中间。", file=sys.stderr)
        return []


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="按场景切段（一条长录像 → N 条素材）")
    p.add_argument("input", help="输入视频")
    p.add_argument("-o", "--outdir", default="", help="输出目录（默认 <输入名>-scenes）")
    p.add_argument("--workdir", default="", help="中间产物目录")
    g = p.add_argument_group("可调参数（默认值只是起点）")
    g.add_argument("--threshold", type=float, default=sc.DEFAULT_THRESHOLD,
                   help=f"检测阈值，越大越不敏感（默认 {sc.DEFAULT_THRESHOLD}）")
    g.add_argument("--min-sec", type=float, default=sc.DEFAULT_MIN_SEC, dest="min_sec",
                   help=f"最短段长，低于它并入前一段（默认 {sc.DEFAULT_MIN_SEC}s）")
    g.add_argument("--max-snap", type=float, default=sc.DEFAULT_MAX_SNAP_SEC, dest="max_snap",
                   help=f"吸附时允许的最大平移（默认 {sc.DEFAULT_MAX_SNAP_SEC}s）")
    g.add_argument("--no-snap", action="store_true", default=False, dest="no_snap",
                   help="不吸附到语音边界（跳过 ASR，快；但可能把话切断）")
    g.add_argument("--no-thumb", action="store_true", default=False, dest="no_thumb",
                   help="不抽缩略图")
    g = p.add_argument_group("行为")
    g.add_argument("--dry-run", action="store_true", default=False, dest="dry_run",
                   help="只出方案，不产出任何视频")
    g.add_argument("--pick", default="", help="只切这几段，如 1,3,5（默认全切）")
    g.add_argument("--yes", action="store_true", default=False,
                   help="不问，直接按 --pick 执行")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    src = Path(args.input)
    if not src.is_file():
        print(f"[错误] 文件不存在：{src}", file=sys.stderr)
        return EXIT_ERR
    if not has_ffmpeg():
        print("[错误] 找不到 ffmpeg。", file=sys.stderr)
        return EXIT_ERR
    if not sc.is_available():
        print(f"[错误] {sc.INSTALL_HINT}", file=sys.stderr)
        return EXIT_ERR

    outdir = Path(args.outdir) if args.outdir else src.with_suffix("").with_name(
        src.stem + "-scenes")
    work = Path(args.workdir) if args.workdir else outdir / "work"
    work.mkdir(parents=True, exist_ok=True)

    try:
        total = probe_duration(src)
        print(f"▶ 探测  {src.name}  {total:.2f}s")

        # ① 语音边界（可跳过）
        segs = [] if args.no_snap else transcript_segments(src, work)
        if args.no_snap:
            print("  （--no-snap：跳过语音吸附，切点可能落在说话中间）")

        # ② 检测 + 吸附 + 合并
        print("▶ 检测画面切点")
        plan = sc.plan(src, total, segments=segs, threshold=args.threshold,
                       min_sec=args.min_sec, max_snap_sec=args.max_snap)
        d = plan.as_dict()
        if not d["speech_aligned"]:
            print("  ⚠️ 没有语音边界 → 切点未吸附（可能把话切断）")

        # ③ 缩略图（方案的必需部分 —— 只有时间戳人认不出哪段是哪段）
        if not args.no_thumb:
            thumbdir = work / "thumbs"
            thumbdir.mkdir(exist_ok=True)
            for s in d["segments"]:
                at = s["start"] + (s["end"] - s["start"]) / 3
                f = thumbdir / f"scene-{s['index']:03d}.jpg"
                if not f.is_file():
                    thumbnail(src, f, at)
                s["thumb"] = str(f)
            print(f"  缩略图 {len(d['segments'])} 张 → {thumbdir}")

        # ④ 方案落盘 —— **必须在缩略图之后**：实测踩到，写在前面时 dry-run 的
        #    plan.json 里没有 thumb 字段（缩略图产出了，但没人知道它在哪）。
        #    落盘的意义是"调参重跑不必重新解码"，所以它得是**完整**的方案。
        def save_plan():
            (work / "scene-plan.json").write_text(
                json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        save_plan()

        # ⑤ 方案给人看
        print(f"\n切成 {d['count']} 段（检测器：{d['detector']}，阈值 {args.threshold}，"
              f"最短 {args.min_sec}s，吸附 {d['snapped']} 处）：")
        for s in d["segments"]:
            print(f"  {s['index']:>3}. {s['start']:>8.2f} → {s['end']:>8.2f}s  "
                  f"({s['duration']:>6.2f}s){'  ' + Path(s['thumb']).name if s.get('thumb') else ''}")

        if args.dry_run:
            print("\n[dry-run] 只看方案，未产出任何视频。")
            return EXIT_OK

        # ⑥ 勾选
        picks = []
        if args.pick:
            try:
                picks = [int(x) for x in args.pick.split(",") if x.strip()]
            except ValueError:
                print("[错误] --pick 要写成 1,3,5", file=sys.stderr)
                return EXIT_ERR
            bad = [i for i in picks if not (1 <= i <= d["count"])]
            if bad:
                print(f"[错误] 段号超出范围：{bad}（共 {d['count']} 段）", file=sys.stderr)
                return EXIT_ERR
        elif not args.yes:
            ans = input("\n回车=全切 / 输入段号如 1,3 / q=放弃 > ").strip()
            if ans.lower() in ("q", "quit", "exit"):
                print("已放弃，未产出任何文件。")
                return EXIT_OK
            if ans:
                try:
                    picks = [int(x) for x in ans.split(",") if x.strip()]
                except ValueError:
                    print("没听懂，放弃。", file=sys.stderr)
                    return EXIT_ERR

        chosen = [s for s in d["segments"] if not picks or s["index"] in picks]

        # ⑦ 切（源文件不动）
        outdir.mkdir(parents=True, exist_ok=True)
        for s in chosen:
            f = outdir / f"scene-{s['index']:03d}.mp4"
            cut_segment(src, f, s["start"], s["end"])
            s["file"] = str(f)
            print(f"  ✓ {f.name}  {s['duration']:.2f}s")

        d["cut"] = [s["index"] for s in chosen]
        save_plan()
        print(f"\n切出 {len(chosen)} 段 → {outdir}（源文件未改动）")
        return EXIT_OK
    except Exception as e:                                   # noqa: BLE001
        print(f"[错误] {e}", file=sys.stderr)
        return EXIT_ERR


if __name__ == "__main__":
    setup = getattr(sys.modules.get("app.pipeline.media"), "setup_output_encoding", None)
    sys.exit(main())
