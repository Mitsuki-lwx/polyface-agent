"""口播粗剪：一个视频进 → 剪掉长停顿 + 烧字幕 → 一个视频出（T11）。

默认**两段式**：先把"打算怎么剪"摆给你看，你确认（或改了参数重算）之后才真的动手。
理由：剪点是否合适是**你的判断**，不是程序的。默认值只负责"第一次跑不用查文档"。

用法：
    python scripts/roughcut.py 输入.mp4                       # 预览 → 确认 → 出片
    python scripts/roughcut.py 输入.mp4 --pause 1.2            # 改停顿阈值
    python scripts/roughcut.py 输入.mp4 -o 成片.mp4 --yes       # 不问，直接跑（脚本用）
    python scripts/roughcut.py 输入.mp4 --dry-run              # 只看剪点，不产出文件
    python scripts/roughcut.py 输入.mp4 --preview-frame        # 只渲一帧看字幕样式

可调参数全部见 `--help`，或写进 `roughcut.json`（改一次管很久），命令行可临时覆盖。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-service"))

from app.pipeline import asr, roughcut as rc  # noqa: E402

EXIT_OK, EXIT_ERR = 0, 1


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

    g = p.add_argument_group("行为")
    g.add_argument("--config", default=rc.DEFAULT_CONFIG_NAME, help="配置文件路径（不存在则用内置默认）")
    g.add_argument("--yes", action="store_true", default=S, help="不问，直接执行（非交互）")
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 两段解析：先只认 --config，据此拿到"本次生效的默认值"，再生成完整的帮助文本。
    # 这样 `--help` 里显示的默认值就是**真的**默认值（可能来自配置文件），而不是一串 None。
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default=rc.DEFAULT_CONFIG_NAME)
    known, _ = pre.parse_known_args(argv)

    base = rc.RoughcutParams.load(Path(known.config))
    args = build_parser(base).parse_args(argv)
    given = vars(args)                      # SUPPRESS 之后，这里只有"用户真的传了"的项

    params = base.merged(
        pause_sec=given.get("pause"), noise_db=given.get("noise"),
        keep_margin_sec=given.get("margin"), font=given.get("font"),
        font_size=given.get("font_size"), text_color=given.get("text_color"),
        outline=given.get("outline"), margin_v=given.get("margin_v"),
        asr_model=given.get("asr_model"),
        burn_subs=False if given.get("no_burn") else None)

    yes = bool(given.get("yes"))
    dry_run = bool(given.get("dry_run"))
    preview_frame = bool(given.get("preview_frame"))
    output = given.get("output", "")
    workdir = given.get("workdir", "")

    src = Path(args.input)
    try:
        info = rc.probe(src)                       # T2：前置探测，失败要大声
    except rc.RoughcutError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return EXIT_ERR

    # ---------- 找停顿 + 预览（T3/T5）----------
    while True:
        pauses = rc.detect_pauses(src, params.noise_db, params.pause_sec)
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
            print("\n[dry-run] 只看剪点，未产出任何文件。")
            return EXIT_OK
        if yes:
            break

        # 刻意**不**用 `sys.stdin.isatty()` 提前跳过：那样管道喂进来的答案（`echo q | ...`）
        # 会被无视，测试和脚本都没法驱动它。改成"永远问，读到 EOF 就当作放弃"——
        # 既不会挂住，也绝不会在没得到明确确认的情况下动手。
        ans = ask("\n回车=执行 / 输入新的停顿阈值(秒)=重算 / q=放弃 > ")
        if ans.lower() in ("q", "quit", "exit"):
            print("已放弃，未产出任何文件。")
            return EXIT_OK
        if not ans:
            break
        try:
            params = params.merged(pause_sec=float(ans))
        except ValueError:
            print("没听懂，请输入一个数字（秒）或直接回车。")
        print()

    # ---------- 执行 ----------
    out = Path(output) if output else src.with_name(f"{src.stem}-roughcut.mp4")
    work = Path(workdir) if workdir else out.with_suffix(out.suffix + ".work")
    work.mkdir(parents=True, exist_ok=True)

    try:
        cut = work / "cut.mp4"
        rc.cut_video(src, cut, cuts)

        wav = work / "audio16k.wav"
        rc.extract_wav(cut, wav)
        try:
            segments = asr.transcribe_segments(str(wav))
        except RuntimeError as e:
            raise rc.AsrMissing(str(e)) from e

        srt = out.with_suffix(".srt")
        srt.write_text(rc.segments_to_srt(segments), encoding="utf-8")

        if preview_frame:
            frame = work / "preview.png"
            rc.preview_frame(cut, frame, srt, params, at=min(2.0, after / 2))
            print(f"[预览帧] {frame}（只渲了一帧，未做整片）")
            return EXIT_OK

        if params.burn_subs:
            rc.burn_subtitles(cut, out, srt, params)
        else:
            rc.cut_video(src, out, cuts)   # 不烧字幕就直接用剪后视频当输出

        # T10：剪点落盘，便于复核与调参对照
        (work / "cuts.json").write_text(json.dumps(
            {"cuts": cuts, "pauses": pauses, "params": params.__dict__,
             "before": info["duration"], "after": after,
             "segments": segments}, ensure_ascii=False, indent=2), encoding="utf-8")
    except rc.RoughcutError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return EXIT_ERR

    real = rc.probe(out)["duration"]
    print(f"\n成片   {out}  {real:.2f}s（原 {info['duration']:.2f}s）")
    print(f"字幕   {srt}（{len(segments)} 段）")
    print(f"中间件 {work}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
