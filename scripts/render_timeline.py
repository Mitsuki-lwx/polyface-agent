"""从时间线渲染成片：**改完数据 → 出新片**（`docs/spec_timeline.md`）。

    python scripts/render_timeline.py 某条.timeline.json -o 新成片.mp4

与粗剪的关键区别：**从源视频重剪，不从旧成片剪**（spec 关键决定三）。
从成片再剪会叠加压缩损失，而且字幕已经烧进去了 —— 那样"改字幕"就没意义了。
代价是慢（分钟级），所以它和粗剪一样是**可断点续跑**的。

用法：
    python scripts/render_timeline.py t.json                      # 出片（默认烧字幕）
    python scripts/render_timeline.py t.json -o 成片.mp4 --no-burn
    python scripts/render_timeline.py t.json --dry-run            # 只看会剪成什么样
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-service"))

from app.pipeline import checkpoint as ck  # noqa: E402
from app.pipeline import roughcut as rc  # noqa: E402
from app.pipeline import timeline as tlib  # noqa: E402
from app.pipeline.runlog import RunLog  # noqa: E402
from roughcut import setup_output_encoding  # noqa: E402

EXIT_OK, EXIT_ERR = 0, 1

# 阶段的参数依赖：判据变了（比如改了字体）就重跑该阶段与下游
_STAGE_DEPS: dict[str, tuple[str, ...]] = {
    "探测": (),
    "剪裁": (),                                     # 只取决于源 + 时间线（时间线进 src 判据）
    "字幕": (),
    "烧字幕": ("font", "font_size", "text_color", "outline", "margin_v", "burn_subs"),
}


def build_parser(base: rc.RoughcutParams) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="render_timeline.py",
        description="从时间线渲染成片（源视频 + 时间线 → 新成片）")
    p.add_argument("timeline", help="时间线 JSON（粗剪产出，或工作台保存的）")
    p.add_argument("-o", "--output", default="", help="成片路径（默认与时间线同目录同名）")
    p.add_argument("--workdir", default="", help="中间件目录（断点续跑就靠它）")
    p.add_argument("--events", default="", help="事件 JSONL 路径")
    p.add_argument("--level", default="normal", choices=("quiet", "normal", "verbose"))
    p.add_argument("--heartbeat", type=float, default=5.0)
    p.add_argument("--no-burn", action="store_true", help="不烧字幕")
    p.add_argument("--dry-run", action="store_true", help="只打印会剪成什么样，不产出文件")
    p.add_argument("--font", default=base.font)
    p.add_argument("--font-size", dest="font_size", type=int, default=base.font_size)
    p.add_argument("--text-color", dest="text_color", default=base.text_color)
    p.add_argument("--outline", type=int, default=base.outline)
    p.add_argument("--margin-v", dest="margin_v", type=int, default=base.margin_v)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    setup_output_encoding()
    args = build_parser(rc.RoughcutParams()).parse_args(argv)

    # ---- 读时间线（**先校验**：宁可现在报错，也别剪出一个错的东西）----
    tpath = Path(args.timeline)
    if not tpath.is_file():
        print(f"[错误] 时间线不存在：{tpath}", file=sys.stderr)
        return EXIT_ERR
    try:
        raw = json.loads(tpath.read_text(encoding="utf-8"))
    except Exception as e:                                  # noqa: BLE001
        print(f"[错误] 时间线不是合法 JSON：{e}", file=sys.stderr)
        return EXIT_ERR

    src = Path((raw.get("source") or {}).get("path") or "")
    if not str(src) or not src.is_file():
        print(f"[错误] 时间线里的源视频找不到：{src!r}", file=sys.stderr)
        return EXIT_ERR

    try:
        tline = tlib.parse(raw)          # 结构校验（不含源时长）
    except tlib.TimelineError as e:
        print(f"[错误] 时间线不合法：{e}", file=sys.stderr)
        return EXIT_ERR

    params = rc.RoughcutParams().merged(
        font=args.font, font_size=args.font_size, text_color=args.text_color,
        outline=args.outline, margin_v=args.margin_v,
        burn_subs=False if args.no_burn else None)

    out = Path(args.output) if args.output else tpath.with_name(tpath.name.replace(".timeline.json", "") + "-render.mp4")
    work = Path(args.workdir) if args.workdir else out.with_suffix(out.suffix + ".work")
    sink = Path(args.events) if args.events else None
    log = RunLog(sink=sink, level=args.level, heartbeat_sec=args.heartbeat)

    try:
        with log.stage("探测") as r:
            info = rc.probe(src, on_raw=log.raw)
            r.done(f"{info['width']}×{info['height']}，{info['duration']:.2f}s")
        # 探测之后才知道源时长 —— **现在**才能判越界
        tline = tlib.parse(raw, source_duration=info["duration"])
    except rc.RoughcutError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return EXIT_ERR
    except tlib.TimelineError as e:
        print(f"[错误] 时间线不合法：{e}", file=sys.stderr)
        return EXIT_ERR

    keep = tline["keep"]
    total = tlib.keep_seconds(keep)
    cuts = tlib.keep_to_cuts(keep, info["duration"])
    print(f"源：{src}  {info['duration']:.2f}s")
    print(f"保留 {len(keep)} 段，共 {total:.2f}s（剪掉 {info['duration'] - total:.2f}s）")
    for i, (s, e) in enumerate(keep, 1):
        print(f"  {i:2}. {s:8.3f} → {e:8.3f}   ({e - s:6.3f}s)")

    if args.dry_run:
        print("\n[dry-run] 未产出任何文件。")
        return EXIT_OK

    # 判据里必须**带上时间线内容**：改了时间线（剪点或字幕）就该重剪，
    # 否则续跑会拿旧成片当结果 —— 用户改了却看不到变化，还以为是渲染坏了。
    res = ck.DoneBook(work, params, src, _STAGE_DEPS,
                      extra={"keep": keep, "segments": tline["segments"]})
    try:
        work.mkdir(parents=True, exist_ok=True)
        cut = work / "cut.mp4"
        _u = res.get("剪裁")
        if _u is not None and cut.exists():
            print("[续跑] 跳过剪裁（已完成）")
        else:
            with log.stage("剪裁") as r:
                if cuts:
                    rc.cut_video(src, cut, [tuple(c) for c in cuts],
                                 expected_sec=total, on_progress=r.progress, on_raw=log.raw)
                else:
                    cut.write_bytes(src.read_bytes())      # 全保留：没什么可剪的
                r.done(f"{total:.2f}s")
            res.put("剪裁", total=total)

        # ---- 字幕：**源轴 → 成片轴**（这一步就是"字幕跟着剪点走"）----
        _u = res.get("字幕")
        if _u is not None:
            segments = _u["segments"]
            print(f"[续跑] 跳过字幕换算（已完成：{len(segments)} 条）")
        else:
            with log.stage("字幕") as r:
                segments = tlib.segments_to_film(tline["segments"], keep)
                r.done(f"{len(segments)} 条")
            res.put("字幕", segments=segments)

        srt = out.with_suffix(".srt")
        srt.write_text(rc.segments_to_srt(segments), encoding="utf-8")

        _u = res.get("烧字幕")
        if _u is not None and out.exists():
            print("[续跑] 跳过烧字幕（已完成）")
        else:
            if params.burn_subs:
                with log.stage("烧字幕") as r:
                    rc.burn_subtitles(cut, out, srt, params, expected_sec=total,
                                      on_progress=r.progress, on_raw=log.raw)
                    r.done(str(out.name))
            else:
                out.write_bytes(cut.read_bytes())
            res.put("烧字幕", out=str(out))
    except rc.RoughcutError as e:
        print(f"\n[错误] {e}", file=sys.stderr)
        return EXIT_ERR

    if res.skipped:
        print(f"[续跑] {res.summary()}")
    real = rc.probe(out)["duration"]
    print(f"\n成片：{out}")
    print(f"  时长 {real:.2f}s（预计 {total:.2f}s）  字幕 {srt}")
    # 时长对不上要说出来，不要假装没看见
    if abs(real - total) > 0.2:
        print(f"  ⚠️ 与预计差 {abs(real - total):.2f}s —— 超过容差 0.2s", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
