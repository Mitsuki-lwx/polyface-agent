"""图文成片（`docs/spec_b1.md` T6）：剪辑单 + 图 + BGM → MP4。

    python scripts/make_video.py plan.json -o 成片.mp4

计划文件（`plan.json`）：
```json
{
  "scenes": [
    {"seq": 1, "script": "配音台词", "subtitle": "屏幕字幕",
     "image": "/绝对路径/图.png", "duration_hint": "3s"}
  ],
  "bgm": "/绝对路径/bgm.mp3"
}
```

**三条设计要点**（都在 `docs/spec_b1.md` 里写了理由）：

1. 分镜时长由**配音实际说了多久**决定，`duration_hint` 只是提示
2. 转场是重叠的，而**配音不能重叠** —— 所以画面段要多给一个转场时长，
   否则成片越往后越比配音短，字幕全错位
3. TTS 与 librosa 都是**可选依赖**：缺了降级（无声 / 不卡点），不整体失败
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-service"))

from app.pipeline import beats as bt  # noqa: E402
from app.pipeline import checkpoint as ck  # noqa: E402
from app.pipeline import roughcut as rc  # noqa: E402
from app.pipeline import timeline as tlib  # noqa: E402
from app.pipeline import tts  # noqa: E402
from app.pipeline import tts_plugin as tp  # noqa: E402
from app.pipeline import video as V  # noqa: E402
from app.pipeline.runlog import RunLog  # noqa: E402
from roughcut import setup_output_encoding  # noqa: E402

EXIT_OK, EXIT_ERR = 0, 1

# 下面这几个值是**从用户的参考片里量出来的**，不是拍脑袋定的
# （`素材/dl.mp4`，1280×720，卡片式）：
#   底色         四角实测 (9,9,9) / (5,3,4)  → 近纯黑，不是深蓝
#   大标题高度   92px / 720 = 12.8% 画面高
#   底部字幕     y=601/720 → 距底 16%
#   转场         scene 检测 0 个 → 参考片**没有硬切**，卡片是慢速切换的
#
# libass 的脚本单位基准（PlayResY 默认值）—— MarginV 按它换算，不是像素
PLAYRES_Y = 288
# 字幕距底边占画面高度的比例。参考片实测在 5%~16% 之间浮动（不同帧不一样），
# 取中间值 —— 取 16% 会偏高（贴近参考片里最靠下的那几帧才对）。
SUBTITLE_BOTTOM_RATIO = 0.08

_STAGE_DEPS: dict[str, tuple[str, ...]] = {
    "配音": (),                       # 只取决于文本
    "画面": (),                       # 只取决于图与时长
    "拼接": (),
    "混音": ("bgm_volume_db", "bgm_fade_out"),
    "烧字幕": ("font", "font_size", "text_color", "outline", "margin_v", "burn_subs"),
}


class PlanError(ValueError):
    """计划文件不合法。**必须指出是第几个分镜** —— 一句"缺图"没法让人改。"""


def load_plan(path: Path) -> dict:
    if not path.is_file():
        raise PlanError(f"计划文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:                                   # noqa: BLE001
        raise PlanError(f"计划不是合法 JSON：{e}") from None
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise PlanError("计划里至少要有一个分镜（scenes 不能为空）")

    missing: list[int] = []
    bad_voice: list[int] = []
    for i, sc in enumerate(scenes, 1):
        if not isinstance(sc, dict):
            raise PlanError(f"第 {i} 个分镜格式不对：{sc!r}")
        if not str(sc.get("script", "")).strip() and not str(sc.get("subtitle", "")).strip():
            raise PlanError(f"第 {i} 个分镜既没有 script 也没有 subtitle —— 那它是什么？")
        img = str(sc.get("image", "")).strip()
        if not img or not Path(img).is_file():
            missing.append(i)
        own = str(sc.get("voice_file", "")).strip()
        if own and not Path(own).is_file():
            bad_voice.append(i)
    if missing:
        # **一次把缺的都说了**，别让人改一个跑一次
        raise PlanError(f"第 {missing} 个分镜没有可用的图片（image 缺失或文件不在）")
    if bad_voice:
        raise PlanError(f"第 {bad_voice} 个分镜指定的 voice_file 找不到（用户自己的配音要真实存在）")
    return data


def build_parser(base: rc.RoughcutParams) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="make_video.py",
                                description="图文成片：剪辑单 + 图 + BGM → MP4")
    p.add_argument("plan", help="计划 JSON")
    p.add_argument("-o", "--output", required=True, help="成片路径")
    p.add_argument("--workdir", default="", help="中间件目录（断点续跑靠它）")
    p.add_argument("--events", default="")
    p.add_argument("--level", default="normal", choices=("quiet", "normal", "verbose"))
    p.add_argument("--strategy", default="voice_first", choices=("voice_first", "beat_first"),
                   help="对齐策略：配音优先（默认，脚本不动）/ 卡点优先（改时长）")
    p.add_argument("--template", default="photo", choices=("photo", "dark-title"),
                   help="成片模板：photo=真照片+缓动（默认）/ dark-title=深色底+居中大标题")
    p.add_argument("--title-ratio", dest="title_ratio", type=float, default=0.128,
                   help="dark-title 的大标题占画面高度的比例（默认取自参考片实测）")
    p.add_argument("--transition", type=float, default=0.4)
    p.add_argument("--zoom", type=float, default=1.08, help="缓动强度，1.0 = 静止")
    p.add_argument("--width", type=int, default=V.DEFAULT_SIZE[0])
    p.add_argument("--height", type=int, default=V.DEFAULT_SIZE[1])
    p.add_argument("--fps", type=int, default=V.DEFAULT_FPS)
    p.add_argument("--beat-tolerance", type=float, default=bt.DEFAULT_TOLERANCE)
    p.add_argument("--bgm-volume", dest="bgm_volume_db", type=float, default=-18.0)
    p.add_argument("--bgm-fade-out", dest="bgm_fade_out", type=float, default=1.5)
    p.add_argument("--no-burn", action="store_true")
    p.add_argument("--voice", default="", help="TTS 音色（留空=自动挑中文音色）；列可用值：--list-voices")
    p.add_argument("--preset", default="", help="配音风格预设（一键选）；列可用值：--list-presets")
    p.add_argument("--plugin", default="", help="用哪个配音插件（默认 windows-sapi）；列可用值：--list-plugins")
    p.add_argument("--list-voices", action="store_true", help="列出可用音色后退出")
    p.add_argument("--list-presets", action="store_true", help="列出配音风格预设后退出")
    p.add_argument("--list-plugins", action="store_true", help="列出配音插件（所有可用方案）后退出")
    p.add_argument("--dry-run", action="store_true", help="只打印会做成什么样")
    p.add_argument("--font", default=base.font)
    p.add_argument("--font-size", dest="font_size", type=int, default=base.font_size)
    p.add_argument("--text-color", dest="text_color", default=base.text_color)
    p.add_argument("--outline", type=int, default=base.outline)
    # 不传就**按画面高度算**（见下面）：固定像素在 1280 高的竖屏上只占 3%，字幕会飘在中间
    p.add_argument("--margin-v", dest="margin_v", type=int, default=None,
                   help="字幕距底边像素（默认按画面高度的 9%% 算）")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    setup_output_encoding()
    args = build_parser(rc.RoughcutParams()).parse_args(argv)
    if args.list_plugins:
        c = tp.catalog()
        print(f"配音插件（目录 {c['dir']}）—— **所有可用方案都在这儿**：")
        for x in c["items"]:
            net = "⚠️ 要联网" if x["network"] else "本地"
            print(f"  [{x['source']:<7}] {x['id']:<16} {x['name']:<22} {net}  "
                  f"音色 {len(x['voices'])} 个")
            if x["note"]:
                print(f"              {x['note']}")
        for x in c["skipped"]:
            print(f"  [跳过] {x['id']}：{x['reason']}")
        return EXIT_OK
    if args.list_presets:
        print("配音风格预设（一键选）：")
        for x in tts.list_presets():
            try:
                r = tts.resolve_preset(x["id"])
                print(f"  {x['id']:<9} {x['name']:<14} 音色 {r['voice'] or '(引擎默认)'}"
                      f"  语速 {r['rate']:+d}  {x['note']}")
            except tts.TtsUnavailable:
                print(f"  {x['id']:<9} {x['name']:<14}（本机没有可用 TTS）")
        return EXIT_OK
    if args.list_voices:
        vs = tts.list_voices()
        print(f"可用音色（{tts.describe().get('backend') or '无 TTS'}）：")
        for v in vs:
            print(f"  {v['id']:<34} {v.get('language',''):<8} {v.get('gender','')}")
        print(f"默认（自动挑中文）：{tts.default_voice() or '（无）'}")
        return EXIT_OK
    log = RunLog(sink=Path(args.events) if args.events else None, level=args.level)

    try:
        plan = load_plan(Path(args.plan))
    except PlanError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return EXIT_ERR

    scenes = plan["scenes"]
    out = Path(args.output)
    work = Path(args.workdir) if args.workdir else out.with_suffix(out.suffix + ".work")
    # 两个目录都要先建：没有配音时没人会顺带建 work（实测漏过 —— 缺 TTS 那条路径直接失败）
    work.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    size = (args.width, args.height)
    # 字幕边距：**libass 的 MarginV 是「脚本单位」，不是像素**。
    # 实测（1280 高的画面）：MV=20 → 距底 96px、MV=40 → 185px、MV=115 → 519px，
    # 即 像素 ≈ MV × 1280/288 —— PlayResY 默认 288。
    # 所以"距底 N% 的画面" = MarginV = N% × 288，**与分辨率无关**（两边约掉了）。
    # 想要距底 9% → 26。粗剪默认的 40 换算过来是 14%，在竖屏上偏中间（实测看到过）。
    margin_v = (args.margin_v if args.margin_v is not None
                else round(PLAYRES_Y * SUBTITLE_BOTTOM_RATIO))
    params = rc.RoughcutParams().merged(
        font=args.font, font_size=args.font_size, text_color=args.text_color,
        outline=args.outline, margin_v=margin_v,
        burn_subs=False if args.no_burn else None)

    # ---- 1) 配音：拿到每个分镜的**实际时长**（这是后续一切的基础）----
    durations: list[float] = []
    voice_parts: list[Path] = []
    used: list[dict] = []        # 每个分镜实际用了哪个插件/音色（记录里要写清）
    cat = tp.catalog()
    plugins_ok = bool(cat["items"])
    tts_ok = plugins_ok          # 有插件就等于有配音能力（内置的那两个也是插件）
    rate, volume = 0, 100
    voice_used = args.voice or ""
    preset_name = ""
    if args.preset:
        try:
            pr = tts.resolve_preset(args.preset)
        except tts.TtsUnavailable as e:
            print(f"[错误] {e}", file=sys.stderr)
            return EXIT_ERR
        voice_used = voice_used or (pr["voice"] or "")
        rate, volume, preset_name = pr["rate"], pr["volume"], pr["name"]
    voice_used = voice_used or tts.default_voice() or ""
    print(f"配音插件 {len(cat['items'])} 个：{[x['id'] for x in cat['items']]}"
          + (f"（预设 {preset_name}）" if preset_name else ""))
    if cat["skipped"]:
        print(f"  跳过的插件：{[(x['id'], x['reason']) for x in cat['skipped']]}")
    if not plugins_ok:
        print("  ⚠️ 一个可用的配音插件都没有 → 降级为无声 + 字幕")
    if not tts_ok:
        print(f"  {tts.INSTALL_HINT}")
    tts_failed: list[dict] = []          # 哪些分镜的配音**失败**了（显式记录，不许静默）
    padded: set[int] = set()             # 补了静音垫片的那些镜 —— 有音频，但**没有旁白**
    for i, sc in enumerate(scenes, 1):
        wav = work / f"voice-{i:03d}.wav"
        # 逐分镜落盘：中断后重跑不用把已经配好的再合成一遍
        vstage = f"配音-{i}"
        book = ck.DoneBook(work, params, Path(sc["image"]), {vstage: ()},
                           extra={"text": str(sc.get("script") or sc.get("subtitle") or "")})
        _u = book.get(vstage)
        if _u is not None and wav.is_file():
            durations.append(float(_u["seconds"]))
            voice_parts.append(wav)
            # 续跑也要记上**用的是哪个插件/音色** —— 否则记录里这一镜是空的
            used.append({"seq": i, "plugin": _u.get("plugin"), "voice": _u.get("voice")})
            print(f"  分镜 {i}: {_u['seconds']:.2f}s（续跑，配音已在：{_u.get('plugin')}）")
            continue
        # ---- 配音：**走插件**（内置的那两个也是插件，与第三方同一条路）----
        own = str(sc.get("voice_file", "")).strip()
        if own:
            plugin, voice = "own-audio", "own"
            # **必须转成绝对路径再交给插件**：插件进程的 cwd 是**插件自己的目录**
            # （`_spawn(cwd=self.dir)`），相对路径会在那儿解析 → "没有可用的音频文件" ✗。
            # 实测踩过：同一个 plan，`image`（主程序自己解析）好好的，`voice_file` 却报找不到；
            # 用绝对路径就没事 —— 这不是用户的错，是调用方该给的保证。
            pparams = {"file": str(Path(own).expanduser().resolve())}
        else:
            plugin = str(sc.get("plugin") or args.plugin or "windows-sapi").strip()
            voice = str(sc.get("voice") or voice_used or "").strip() or None
            pparams = {}
        if not plugins_ok:
            d = _hint_seconds(sc, 3.0)          # 一个插件都没有 → 降级为无声 + 字幕
            wav = None
        else:
            try:
                with log.stage(f"配音-{i}") as r:
                    d = tp.synthesize(plugin, str(sc.get("script") or sc.get("subtitle") or ""),
                                      wav, voice=voice, rate=rate, volume=volume, params=pparams)
                    r.done(f"{d:.2f}s · {plugin}")
            except tp.PluginError as e:
                # **说清是哪个分镜、哪个插件** —— 一句"配音失败"没法排查
                print(f"  [warn] 第 {i} 个分镜配音失败（插件 {plugin}）：{e}")
                d, wav = _hint_seconds(sc, 3.0), None
                # **补一段等长静音**（2026-10-18 修）：不补的话成片比计划短，
                # 而记录仍按计划写 —— 实测踩到：某一镜 TTS 静默失败，成片少 8s，
                # 记录里 has_voice 却还是 True、skipped 还是空（见 docs/checklist_b1.md）。
                # 补静音 = 保住时长（这段没有旁白，但**记录里会明说**）。
                wav = _write_silence_wav(work / f"voice-{i:03d}.wav", float(d),
                                         like=voice_parts)
                padded.add(i)
                tts_failed.append({"seq": i, "plugin": plugin, "reason": str(e)[:200],
                                   "padded_sec": round(float(d), 3),
                                   "padded": wav is not None})
        durations.append(d)
        if wav:
            voice_parts.append(wav)
            book.put(vstage, seconds=round(d, 3), wav=str(wav), plugin=plugin, voice=voice)
        # `plugin` 为 None = **这一镜没有旁白**（失败且未补，或补了静音）。
        # 补了静音的那些：文件在、时长保住，但**旁白是没有的** —— 不能算 has_voice ✓。
        _has_narration = wav is not None and i not in padded
        used.append({"seq": i, "plugin": plugin if _has_narration else None,
                     "voice": voice if _has_narration else None})
        print(f"  分镜 {i}: {d:.2f}s" + (f"（{plugin}）" if wav else "（无配音）"))

    # ---- 2) 对齐策略 ----
    bgm = str(plan.get("bgm") or "").strip() or None
    # 对齐占比**没算的时候是 `None`（记录里是 `null`），不是 `0.0`**：
    # `0%` 看着像"完全没对齐"，其实只是没测 —— 留一个会误导人的数比留空更糟
    # （下面那句注释早就说了这个道理，只有"没节拍"这条路径没照做）。
    # 给程序看的那个标记是 `beats.available`，两者分工不同。
    bgm_offset, ratio, ratio_before = 0.0, None, None
    final = list(durations)
    if bgm and bt.is_available():
        try:
            bp = work / "bgm-beats.json"
            if bp.is_file():
                beats_list = json.loads(bp.read_text(encoding="utf-8"))
            else:
                beats_list = bt.detect(bgm)
                bp.write_text(json.dumps(beats_list), encoding="utf-8")
            bounds = [round(sum(durations[:i]), 3) for i in range(1, len(durations) + 1)]
            # "平移前"对**两种**策略都算：不然后面记的是 0%，看着像"完全对不齐"，
            # 其实只是没算 —— 记录里留一个会误导人的数比留空更糟
            ratio_before = bt.aligned_ratio(bounds, beats_list, tolerance=args.beat_tolerance)
            if args.strategy == "beat_first":
                final = bt.snap_durations(durations, beats_list,
                                          tolerance=args.beat_tolerance)
                nb = [round(sum(final[:i]), 3) for i in range(1, len(final) + 1)]
                ratio = bt.aligned_ratio(nb, beats_list, tolerance=args.beat_tolerance)
                bgm_offset = 0.0
            else:
                bgm_offset, ratio = bt.best_offset(bounds, beats_list,
                                                   tolerance=args.beat_tolerance)
                # 正偏移 = BGM 晚开始（开头静音），换算成"跳过 BGM 开头"
                if bgm_offset > 0:
                    bgm_offset = -bgm_offset
            print(f"BGM：{len(beats_list)} 个节拍 · 策略 {args.strategy} · "
                  f"偏移 {bgm_offset:+.2f}s · 对齐 {ratio_before:.0%} → {ratio:.0%}")
        except bt.BeatsUnavailable as e:
            print(f"[warn] {e}")
    elif bgm:
        print(f"[warn] {bt.INSTALL_HINT}")

    if args.strategy == "beat_first":
        durations = final
    total = round(sum(durations), 3)
    print(f"成片预计 {total:.2f}s（{len(durations)} 个分镜）")

    if args.dry_run:
        print("\n[dry-run] 未产出任何文件。")
        return EXIT_OK

    # ---- 3) 画面 ----
    seg_durs = V.segment_durations(durations, args.transition)
    segs: list[Path] = []
    for i, (sc, sd) in enumerate(zip(scenes, seg_durs), 1):
        seg = work / f"scene-{i:03d}.mp4"
        # **分镜级落盘**：跑完一个分镜就记一个，中断后只补没做完的
        # 模板：photo=真照片+缓动 / dark-title=深色底+居中大标题（用户给的参考片那种调性）
        title = _title_of(sc) if args.template == "dark-title" else ""
        sstage = f"画面-{i}"
        book = ck.DoneBook(work, params, Path(sc["image"]), {sstage: ()},
                           extra={"seconds": round(sd, 3), "zoom": args.zoom,
                                  "title": title, "template": args.template,
                                  "size": list(size), "fps": args.fps})
        _u = book.get(sstage)
        if _u is not None and seg.is_file():
            print(f"[续跑] 跳过 画面:{i}（已完成）")
        else:
            with log.stage(f"画面:{i}") as r:
                if args.template == "dark-title":
                    V.title_card(work, seg, sd, title, size=size, fps=args.fps,
                                 title_ratio=args.title_ratio, on_progress=r.progress)
                else:
                    V.ken_burns(sc["image"], seg, sd, zoom=args.zoom, size=size, fps=args.fps,
                                on_progress=r.progress)
                r.done(f"{sd:.2f}s")
            book.put(sstage, out=str(seg))
        segs.append(seg)

    # ---- 4) 字幕（成片时间轴，由**最终**分镜时长推出）----
    cursor, sub_segments = 0.0, []
    for sc, d in zip(scenes, durations):
        text = _subtitle_lines(str(sc.get("subtitle") or ""))
        if args.template == "dark-title":
            # 大标题已经占了第一行 → 底部只留其余几行（参考片就是"大标题 + 底部一行"）
            text = "\n".join(text.split("\n")[1:]).strip()
        if text:
            sub_segments.append({"start": round(cursor, 3),
                                 "end": round(cursor + d - 0.05, 3), "text": text})
        cursor += d
    srt = out.with_suffix(".srt")
    srt.write_text(rc.segments_to_srt(sub_segments), encoding="utf-8")

    # ---- 5) 拼接 → 无声成片 ----
    silent = work / "silent.mp4"
    with log.stage("拼接") as r:
        real = V.xfade_chain(segs, silent, args.transition, size=size, fps=args.fps,
                             on_progress=r.progress)
        r.done(f"{real:.2f}s")

    # ---- 6) 音轨 ----
    audio = work / "audio.m4a"
    with log.stage("混音") as r:
        if voice_parts:
            voice = work / "voice.wav"
            _concat_wavs(voice_parts, durations, voice, total)
            V.mix_audio(voice, audio, total=real, bgm=bgm, bgm_offset=bgm_offset,
                        bgm_volume_db=args.bgm_volume_db, bgm_fade_out=args.bgm_fade_out,
                        on_raw=log.raw)
        else:
            _silent_track(audio, real)
        r.done("有声" if voice_parts else "无声")

    # ---- 7) 合成（烧字幕 + 挂音轨）----
    with log.stage("烧字幕" if params.burn_subs else "合成") as r:
        V.mux(silent, audio, out, srt if params.burn_subs else None, params,
              on_progress=r.progress, on_raw=log.raw)
        r.done(str(out.name))

    # ---- 8) 初剪记录 ----
    record = {
        "version": 1,
        "strategy": args.strategy,
        "transition_sec": args.transition,
        "zoom": args.zoom,
        "margin_v": margin_v,
        "size": {"width": size[0], "height": size[1], "fps": args.fps},
        "tts": {"plugins": [x["id"] for x in cat["items"]],
                "skipped": cat["skipped"],
                "voice": voice_used, "preset": args.preset or None,
                "rate": rate, "volume": volume,
                # **哪些分镜配音失败了**（已补静音垫片保住时长）。
                # 没有这一项，用户只会看到一个"某段没声音"的成片，而记录里一切正常 ——
                # 实测就是这么被静默吞掉的 ✗。
                "failed": tts_failed},
        "beats": bt.describe(),
        "bgm": bgm,
        "bgm_offset_sec": bgm_offset,
        "aligned_ratio": ratio,
        "aligned_ratio_before_offset": ratio_before,
        "beat_tolerance_sec": args.beat_tolerance,
        "scenes": [
            {"seq": i + 1, "seconds": round(d, 3),
             "segment_seconds": round(seg_durs[i], 3),
             "image": scenes[i]["image"],
             "subtitle": str(scenes[i].get("subtitle") or ""),
             # **按实际产出判**，不是按位置比（2026-10-18 修）：
             # 原来写 `i < len(voice_parts)` —— 漏一个之后**后面全部错位**，
             # 实测某一镜 TTS 静默失败时它还是 True ✗。
             "has_voice": (used[i].get("plugin") is not None if i < len(used) else False),
             "plugin": (used[i].get("plugin") if i < len(used) else None),
             "voice": (used[i].get("voice") if i < len(used) else None)}
            for i, d in enumerate(durations)
        ],
        "total_sec": total,
        "film_sec": real,
    }
    (out.with_suffix(".plan.json")).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    # 收工：把常驻的配音插件进程关掉，免得留孤儿（Kokoro 那个进程不小）
    tp.shutdown()

    print(f"\n成片：{out}  {real:.2f}s")
    print(f"记录：{out.with_suffix('.plan.json')}")
    if abs(real - total) > 0.35:
        print(f"  ⚠️ 与预计差 {abs(real - total):.2f}s —— 超过容差", file=sys.stderr)
    return EXIT_OK


def _title_of(sc: dict) -> str:
    """dark-title 模板的大标题 = 字幕的**第一行**（剪辑单本来就是按行写的）。"""
    lines = _subtitle_lines(str(sc.get("subtitle") or sc.get("script") or "")).split("\n")
    return lines[0].strip() if lines else ""


def _subtitle_lines(text: str) -> str:
    """剪辑单里的字幕用 ` / ` 当**分行**的意思（模型就是这么写的），不是字面斜杠。

    实测踩过两次：
    1. 不转换的话成片里字幕显示成「不满足就痛苦 / 满足了就无聊 / …」；
    2. 只处理带空格的 ` / ` 还不够 —— 模型有时写成 `？/ 叔本华`（**没空格**）。

    所以用"**两侧都是非 ASCII**"来判：既盖住没空格的情况，又不会误伤 `2023/2024`。
    """
    t = str(text).strip()
    for sep in (" / ", "／", " | "):
        t = t.replace(sep, "\n")
    t = re.sub(r"(?<=[^\x00-\x7f])\s*/\s*(?=[^\x00-\x7f])", "\n", t)
    return t.strip()


def _resample_wav(src: Path, rate: int, dst: Path) -> Path:
    """把一段 wav 重采样到 `rate`。**只动采样点，不改时长、不变调。**

    失败就抛 —— 拼接阶段宁可明确失败，也不能悄悄把两段不同采样率的 PCM 拼在一起
    （那才是真的变调 + 时长错乱，实测过：1.38 倍速、高一个八度、后面还被截掉）。
    """
    code, err = rc._run_ffmpeg([rc._ffmpeg_bin("ffmpeg"), "-y", "-v", "error",
                                "-i", str(src), "-ar", str(rate), "-ac", "1",
                                "-c:a", "pcm_s16le", str(dst)])
    if code != 0 or not dst.is_file():
        raise V.VideoError(f"配音重采样失败（{src.name} → {rate}Hz）：{err[-300:]}")
    return dst


def _hint_seconds(sc: dict, default: float) -> float:
    """没有配音时用 `duration_hint` 兜底（如 "3s" / "3.5秒" / 3）。"""
    raw = str(sc.get("duration_hint") or "").strip()
    num = "".join(ch for ch in raw if ch.isdigit() or ch == ".")
    try:
        v = float(num)
        return v if v > 0.2 else default
    except ValueError:
        return default


def _concat_wavs(parts: list[Path], durations: list[float], out: Path, total: float) -> None:
    """按**分镜时长**把配音接起来 —— 每段后面补足到该分镜的时长。

    补足是必要的：`beat_first` 会把某段拉长，不补的话音轨会比画面短，后面全错位。
    """
    import wave
    # **必须用各片段自己的采样率**，不能写死。写死过的后果实测过：
    # TTS 输出 22050Hz 而这里声明 16000Hz → 配音 1.38 倍速、还高了 1.38 个八度，
    # 而且标称时长 103s（真实只有 75s）→ 后面 28 秒的配音被 -t 直接截掉。
    # 用户一听就发现了（"音轨只有 BGM 吗，不应该还有配音吗"）—— 机器量的是"有声"，人是听出来的。
    rates = []
    for q in parts:
        with wave.open(str(q), "rb") as r:
            rates.append(r.getframerate())
    # **采样率不一致就重采样，不是整片失败**（2026-10-30 改）。
    # 契约只写了"插件负责把 WAV 写到 out"，**没规定采样率** —— 所以一个
    # *完全按契约写的* 第三方插件（比如输出 16kHz）能把整片弄崩 ✗。
    # 实测：混用 SAPI(22050) + 一个 16kHz 的 Node 插件 → `[16000, 22050] 不静默拼` 直接退出。
    # 重采样**不变调、不改时长**（它是按比例换算采样点），所以守卫原本担心的"变调"并没有发生。
    # 取**出现最多**的那个；平票取**第一段**的 —— 别用 `max(set(...))`，
    # 平票时它取的是集合里任意一个（实测：两个只出现一次的，拿到的是 16000 不是 22050 ✗）。
    rate = max(dict.fromkeys(rates), key=rates.count)
    if any(x != rate for x in rates):
        parts = [_resample_wav(q, rate, out.with_name(f"{q.stem}--{rate}.wav")) for q in parts]
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        for q, d in zip(parts, durations):
            with wave.open(str(q), "rb") as r:
                frames = r.readframes(r.getnframes())
            w.writeframes(frames)
            have = len(frames) // 2 / float(rate)
            pad = max(0.0, d - have)
            if pad > 0:
                w.writeframes(b"\x00\x00" * int(rate * pad))


def _wav_rate(path: Path) -> int | None:
    """读 wav 的采样率（拿不到返回 None）。"""
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            return w.getframerate()
    except Exception:                                        # noqa: BLE001
        return None


def _write_silence_wav(path: Path, seconds: float, *, like: list[Path]) -> Path | None:
    """写一段**等长静音 wav**（配音失败时补上，保住成片时长）。

    为什么补静音而不是"跳过这一镜"：跳过会让**成片时长与计划对不上**，
    而记录仍按计划写 —— 那是"静默地少了一段"，比明确报错糟得多 ✗。
    补静音保住时长，失败则进 `tts.failed`（记录里看得见 ✓）。

    **采样率必须与已有配音一致**（实测踩到）：我第一版写死 44100，而 Windows SAPI
    出的是 22050 —— 拼接时 `_concat_wavs` 直接拒绝：「采样率不一致：[22050, 44100]
    —— 不静默拼，会变调」✓（那条守卫写得对：宁可失败，不悄悄变调）。
    所以这里**照已有配音的采样率写**；一个都没有（第 1 镜就失败）才回落到 44100。

    写不出来就返回 None（调用方照旧降级，绝不因此中断整条成片）。
    """
    rate = next((r for r in (_wav_rate(p) for p in like if p and p.is_file()) if r), 44100)
    try:
        code, err = rc._run_ffmpeg([rc._ffmpeg_bin("ffmpeg"), "-y", "-v", "error",
                                    "-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=mono",
                                    "-t", f"{max(0.05, seconds):.3f}", str(path)])
        return path if code == 0 else None
    except Exception:                                        # noqa: BLE001
        return None


def _silent_track(out: Path, total: float) -> None:
    code, err = rc._run_ffmpeg([rc._ffmpeg_bin("ffmpeg"), "-y", "-v", "error",
                                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                                "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "128k", str(out)])
    if code != 0:
        raise V.VideoError(f"生成静音轨失败：{err.strip()[:200]}")


if __name__ == "__main__":
    raise SystemExit(main())
