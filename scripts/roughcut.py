"""口播粗剪：一个视频进 → 剪掉长停顿 + 烧字幕 → 一个视频出。

默认**两段式**：先把"打算怎么剪"摆给你看，你确认（或改了参数重算）之后才真的动手。
理由：剪点是否合适是**你的判断**，不是程序的。默认值只负责"第一次跑不用查文档"。

执行期是**可见的**（`docs/spec_observability.md`）：每个阶段一行、慢阶段有心跳、
转写与渲染有百分比、结束后给运行报告；同一份事件流还会落成 JSONL 供事后复核。

用法：
    python scripts/roughcut.py 输入.mp4                       # 预览 → 确认 → 出片
    python scripts/roughcut.py 输入.mp4 --pause 1.2            # 改停顿阈值
    python scripts/roughcut.py 输入.mp4 -o 成片.mp4 --yes       # 不问，直接跑（脚本用）
    python scripts/roughcut.py 输入.mp4 --dry-run              # 只看剪点，不产出文件
    python scripts/roughcut.py 输入.mp4 --preview-frame        # 只渲一帧看字幕样式
    python scripts/roughcut.py 输入.mp4 --level quiet          # 安静档（只要结果）
    python scripts/roughcut.py 输入.mp4 --ask all              # 逐项问参数

可调参数全部见 `--help`，或写进 `roughcut.json`（改一次管很久），命令行可临时覆盖。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-service"))

from app.pipeline import asr, roughcut as rc  # noqa: E402
from app.pipeline.runlog import RunLog  # noqa: E402

EXIT_OK, EXIT_ERR = 0, 1

# 默认只问这几项 —— 其余展示当前值、可改但不主动问（问一长串会烦人）
ASK_KEY = ("pause_sec",)
PARAM_LABEL = {
    "pause_sec": "停顿阈值", "noise_db": "噪声门限", "keep_margin_sec": "剪点余量",
    "burn_subs": "烧字幕", "font": "字幕字体", "font_size": "字幕字号",
    "text_color": "字幕字色", "outline_color": "描边颜色", "outline": "描边宽度",
    "margin_v": "字幕距底", "asr_model": "ASR 模型",
}


def build_parser(base: rc.RoughcutParams) -> argparse.ArgumentParser:
    """帮助文本里的默认值 = **本次实际生效的默认值**（可能来自配置文件）。

    用 `SUPPRESS` 作 argparse 默认值：没传的参数**不会出现在 namespace 里**，
    这样"用户传了"与"没传"能分清 —— 否则 argparse 的默认值会把配置文件的值盖掉。
    """
    S = argparse.SUPPRESS
    p = argparse.ArgumentParser(
        description="口播粗剪：剪掉长停顿 + 烧字幕",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("input", help="输入视频")
    p.add_argument("-o", "--output", default=S, help="输出视频（默认 <输入名>-roughcut.mp4）")
    p.add_argument("--workdir", default=S, help="中间产物目录（默认 <输出名>.work）")

    g = p.add_argument_group("可调参数（默认值只是起点，都可以改；配置文件写 roughcut.json）")
    g.add_argument("--pause", type=float, default=S,
                   help=f"多长的静音算停顿，秒（默认 {base.pause_sec}）")
    g.add_argument("--noise", type=int, default=S,
                   help=f"静音判定门限，dB（默认 {base.noise_db}）")
    g.add_argument("--margin", type=float, default=S,
                   help=f"剪点两端保留的气口，秒（默认 {base.keep_margin_sec}）")
    g.add_argument("--no-burn", action="store_true", default=S, help="不烧字幕，只出独立字幕文件")
    g.add_argument("--font", default=S, help=f"字幕字体（默认 {base.font}）")
    g.add_argument("--font-size", type=int, default=S, dest="font_size",
                   help=f"字幕字号（默认 {base.font_size}）")
    g.add_argument("--text-color", default=S, dest="text_color",
                   help=f"字幕字色，ASS 的 &HAABBGGRR（默认 {base.text_color}）")
    g.add_argument("--outline", type=int, default=S, help=f"字幕描边宽度（默认 {base.outline}）")
    g.add_argument("--margin-v", type=int, default=S, dest="margin_v",
                   help=f"字幕距画面底部像素（默认 {base.margin_v}）")
    g.add_argument("--asr-model", default=S, dest="asr_model",
                   help=f"ASR 模型尺寸（默认 {base.asr_model}）")

    g = p.add_argument_group("可观测（docs/spec_observability.md）")
    g.add_argument("--level", choices=("quiet", "normal", "verbose"), default=S,
                   help="输出级别：quiet 只要结果 / normal 阶段+进度+心跳 / verbose 再加底层输出"
                        f"（默认 normal）")
    g.add_argument("--events", default=S,
                   help=f"事件文件路径（默认 {ROOT / 'data' / 'runs'}/<run_id>.jsonl）")
    g.add_argument("--no-events", action="store_true", default=S, help="不写事件文件")
    g.add_argument("--heartbeat", type=float, default=S,
                   help="慢阶段的心跳间隔，秒（默认 5；0 = 关）")

    g = p.add_argument_group("行为")
    g.add_argument("--config", default=rc.DEFAULT_CONFIG_NAME, help="配置文件路径（不存在则用内置默认）")
    g.add_argument("--ask", choices=("key", "all", "none"), default=S,
                   help="开始前问哪些参数：key=只问最关键的 / all=逐项问 / none=不问（默认 key）")
    g.add_argument("--yes", action="store_true", default=S, help="不问，直接执行（等于 --ask none）")
    g.add_argument("--dry-run", action="store_true", default=S, dest="dry_run",
                   help="只预览剪点，不产出任何文件")
    g.add_argument("--preview-frame", action="store_true", default=S, dest="preview_frame",
                   help="只渲染一帧看字幕样式，不做整片")
    return p


def ask(question: str) -> str:
    try:
        return input(question).strip()
    except EOFError:
        return "q"


def show_params(params: rc.RoughcutParams, sources: dict[str, str], stream=sys.stdout) -> None:
    """把**本次生效的全部参数**摆出来，并标出每一项的来源。"""
    print("参数（本次生效）：", file=stream)
    for f in fields(rc.RoughcutParams):
        value = getattr(params, f.name)
        print(f"  {PARAM_LABEL.get(f.name, f.name):<10} {str(value):<18} {sources.get(f.name, '')}",
              file=stream)


def ask_all(params: rc.RoughcutParams, sources: dict[str, str]) -> rc.RoughcutParams:
    """逐项问。回车 = 保持不变。"""
    print("逐项确认（回车 = 保持不变）：")
    for f in fields(rc.RoughcutParams):
        cur = getattr(params, f.name)
        ans = ask(f"  {PARAM_LABEL.get(f.name, f.name)} [{cur}]: ")
        if not ans:
            continue
        try:
            if isinstance(cur, bool):
                new = ans.lower() in ("1", "true", "y", "yes", "开")
            elif isinstance(cur, int):
                new = int(ans)
            elif isinstance(cur, float):
                new = float(ans)
            else:
                new = ans
        except ValueError:
            print(f"    （{ans!r} 不是合法值，保持 {cur}）")
            continue
        params = params.merged(**{f.name: new})
        sources[f.name] = "本次输入"
    return params


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 两段解析：先只认 --config，据此拿到"本次生效的默认值"，再生成完整的帮助文本。
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default=rc.DEFAULT_CONFIG_NAME)
    known, _ = pre.parse_known_args(argv)

    config_path = Path(known.config)
    base = rc.RoughcutParams.load(config_path)
    from_config = rc.RoughcutParams.config_keys(config_path)
    args = build_parser(base).parse_args(argv)
    given = vars(args)                      # SUPPRESS 之后，这里只有"用户真的传了"的项

    params = base.merged(
        pause_sec=given.get("pause"), noise_db=given.get("noise"),
        keep_margin_sec=given.get("margin"), font=given.get("font"),
        font_size=given.get("font_size"), text_color=given.get("text_color"),
        outline=given.get("outline"), margin_v=given.get("margin_v"),
        asr_model=given.get("asr_model"),
        burn_subs=False if given.get("no_burn") else None)

    # 参数来源：命令行 > 配置文件 > 默认
    sources = {f.name: ("命令行" if f.name in given else
                        "配置文件" if f.name in from_config else "默认")
               for f in fields(rc.RoughcutParams)}

    level = given.get("level", "normal")
    heartbeat = given.get("heartbeat", 5.0)
    ask_mode = "none" if given.get("yes") else given.get("ask", "key")
    dry_run = bool(given.get("dry_run"))
    preview_frame = bool(given.get("preview_frame"))
    output = given.get("output", "")
    workdir = given.get("workdir", "")

    run_id = __import__("uuid").uuid4().hex[:8]
    sink = None
    if not given.get("no_events"):
        sink = Path(given.get("events") or (ROOT / "data" / "runs" / f"{run_id}.jsonl"))
    log = RunLog(run_id=run_id, sink=sink, level=level, heartbeat_sec=heartbeat)

    src = Path(args.input)
    try:
        with log.stage("探测") as r:
            info = rc.probe(src, on_raw=log.raw)       # 前置探测，失败要大声
            r.done(f"{info['width']}×{info['height']}，{info['duration']:.2f}s")
    except rc.RoughcutError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return EXIT_ERR

    # ---------- 找停顿 + 预览（两段式）----------
    while True:
        with log.stage("找停顿") as r:
            pauses = rc.detect_pauses(src, params.noise_db, params.pause_sec, on_raw=log.raw)
            r.done(f"{len(pauses)} 段静音")
        cuts = rc.plan_cuts(pauses, params.keep_margin_sec, info["duration"])
        after = info["duration"] - rc.cut_seconds(cuts)
        print(rc.summarize(cuts, info["duration"], after, params))

        if not cuts:
            # 没找到停顿不是失败 —— 可能素材本来就没长停顿，也可能里面有背景音乐
            # （BGM 会让"静音"不存在）。如实说明并退出，**绝不产出一个空视频**。
            print(f"\n未找到停顿（阈值 {params.pause_sec}s / 门限 {params.noise_db}dB）。"
                  f"若素材里有背景音乐，本版不处理。未产出任何文件。")
            return EXIT_OK
        if dry_run:
            show_params(params, sources)      # dry-run 更要看清"这次会用哪些值"
            print("\n[dry-run] 只看剪点，未产出任何文件。")
            return EXIT_OK
        if ask_mode == "none":
            break

        show_params(params, sources)
        if ask_mode == "all":
            params = ask_all(params, sources)
            print()
            continue

        # 刻意**不**用 `sys.stdin.isatty()` 提前跳过：那样管道喂进来的答案（`echo q | ...`）
        # 会被无视，测试和脚本都没法驱动它。改成"永远问，读到 EOF 就当作放弃"——
        # 既不会挂住，也绝不会在没得到明确确认的情况下动手。
        ans = ask("\n回车=执行 / 输入新的停顿阈值(秒)=重算 / --ask all 可逐项改 / q=放弃 > ")
        if ans.lower() in ("q", "quit", "exit"):
            print("已放弃，未产出任何文件。")
            return EXIT_OK
        if not ans:
            break
        try:
            params = params.merged(pause_sec=float(ans))
            sources["pause_sec"] = "本次输入"
        except ValueError:
            print("没听懂，请输入一个数字（秒）或直接回车。")
        print()

    # ---------- 执行 ----------
    out = Path(output) if output else src.with_name(f"{src.stem}-roughcut.mp4")
    work = Path(workdir) if workdir else out.with_suffix(out.suffix + ".work")
    work.mkdir(parents=True, exist_ok=True)

    try:
        cut = work / "cut.mp4"
        with log.stage("剪裁") as r:
            rc.cut_video(src, cut, cuts, expected_sec=after, on_progress=r.progress, on_raw=log.raw)
            r.done(f"剪掉 {rc.cut_seconds(cuts):.2f}s", cut_sec=rc.cut_seconds(cuts))

        wav = work / "audio16k.wav"
        with log.stage("抽音轨"):
            rc.extract_wav(cut, wav, on_raw=log.raw)

        with log.stage("转写") as r:
            try:
                segments = asr.transcribe_segments(str(wav), on_progress=r.progress,
                                                   total_sec=after)
            except RuntimeError as e:
                raise rc.AsrMissing(str(e)) from e
            r.done(f"{len(segments)} 段字幕", segments=len(segments))

        srt = out.with_suffix(".srt")
        srt.write_text(rc.segments_to_srt(segments), encoding="utf-8")

        if preview_frame:
            frame = work / "preview.png"
            with log.stage("预览帧"):
                rc.preview_frame(cut, frame, srt, params, at=min(2.0, after / 2))
            print(f"\n[预览帧] {frame}（只渲了一帧，未做整片）")
            return EXIT_OK

        if params.burn_subs:
            with log.stage("烧字幕") as r:
                rc.burn_subtitles(cut, out, srt, params, expected_sec=after,
                                  on_progress=r.progress, on_raw=log.raw)
                r.done(str(out.name))
        else:
            out.write_bytes(cut.read_bytes())

        # 剪点落盘，便于复核与调参对照
        (work / "cuts.json").write_text(json.dumps(
            {"run_id": run_id, "cuts": cuts, "pauses": pauses, "params": params.__dict__,
             "before": info["duration"], "after": after, "segments": segments},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except rc.RoughcutError as e:
        print(f"\n[错误] {e}", file=sys.stderr)
        print(f"[报告] 失败于上述阶段；事件文件：{sink}", file=sys.stderr)
        return EXIT_ERR

    report(rc.probe(out)["duration"], info["duration"], cuts, params, sources,
           out, srt, work, sink, log)
    return EXIT_OK


def report(real_after: float, before: float, cuts, params, sources, out, srt, work,
           sink, log) -> None:
    """运行报告：每步耗时 / 生效参数 / 产物位置。路径都**核实存在**才打勾。"""
    if log.level == "quiet":
        print(f"成片 {out}  {real_after:.2f}s（原 {before:.2f}s）")
        return
    print("\n运行报告（run_id=%s）" % log.run_id)
    for stage, ms in log.stage_durations().items():
        print(f"  {stage:<8} {ms / 1000:6.2f}s")
    print(f"  {'合计':<8} {sum(log.stage_durations().values()) / 1000:6.2f}s")
    print(f"  时长     {before:.2f}s → {real_after:.2f}s（剪掉 {rc.cut_seconds(cuts):.2f}s）")
    show_params(params, sources)
    print("产物：")
    for label, p in (("成片", out), ("字幕", srt), ("剪点", work / "cuts.json"),
                     ("中间件", work), ("事件", sink)):
        if p is None:
            print(f"  {label:<6} （未启用）")
        else:
            mark = "✓" if Path(p).exists() else "✗ 不存在"
            print(f"  {label:<6} {mark} {p}")


if __name__ == "__main__":
    raise SystemExit(main())
