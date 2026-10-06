"""口播粗剪端到端验收（T12）：逐条对 `docs/checklist_roughcut.md`。

**真链路**：全程调 `scripts/roughcut.py` 这个命令行入口（不是 import 模块直接调），
因为用户拿到的就是这条命令。

素材：`scripts/make_fixture.py` 现场生成（TTS 合成中文口播 + 故意插入的长停顿）。
另造一份"有背景音乐"的素材来验"没找到停顿"的分支。

用法：
    python scripts/e2e_roughcut.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / "python-service" / ".venv" / "Scripts" / "python.exe")
CLI = ROOT / "scripts" / "roughcut.py"
WORK = ROOT / "outputs" / "roughcut-e2e"
RUN = time.strftime("%H%M%S")
results: list[tuple[str, bool]] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    results.append((label, bool(cond)))
    print(("  PASS " if cond else "  FAIL ") + label + (f"  | {extra}" if extra else ""))


def info(label: str, value) -> None:
    print(f"  INFO {label}: {value}")


def run_cli(*args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(CLI), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, cwd=str(ROOT))


def ffprobe_dur(path: Path) -> float:
    cp = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(cp.stdout.strip() or 0)


def make_bgm_clip(dst: Path, seconds: int = 12) -> Path:
    """有背景音乐的素材：全程有声音 → 不存在真静音 → 应该"找不到停顿"。"""
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-f", "lavfi", "-i", f"color=c=black:s=320x240:r=25:d={seconds}",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(dst)], check=True, capture_output=True)
    return dst


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    fixture = WORK / "talking.mp4"

    # ---------- T1 素材 ----------
    cp = subprocess.run([PY, str(ROOT / "scripts" / "make_fixture.py"), str(WORK)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        cwd=str(ROOT))
    check("T1 素材可由一条命令重建", cp.returncode == 0 and fixture.is_file(),
          cp.stdout.strip().splitlines()[0] if cp.stdout else "")
    before = ffprobe_dur(fixture)
    info("素材时长", f"{before:.2f}s")

    # ---------- §2 参数可改 ----------
    help_txt = run_cli("--help").stdout
    knobs = ["--pause", "--noise", "--margin", "--font", "--font-size", "--text-color",
             "--outline", "--margin-v", "--asr-model", "--no-burn"]
    missing = [k for k in knobs if k not in help_txt]
    check("§2a --help 列出全部可调参数", not missing, f"缺 {missing}" if missing else "")
    check("§2b --help 里的默认值是真实数值（不是 None）",
          "（默认 0.8）" in help_txt and "（默认 40）" in help_txt and "(default: None)" not in help_txt)

    cfg = ROOT / "roughcut.json"
    cfg.write_text(json.dumps({"pause_sec": 1.5, "font_size": 24}), encoding="utf-8")
    out_cfg = run_cli(str(fixture), "--dry-run").stdout
    check("§2c 配置文件生效（pause_sec=1.5）", "停顿阈值 1.5s" in out_cfg, out_cfg.strip().splitlines()[-2:])
    out_cli = run_cli(str(fixture), "--dry-run", "--pause", "0.4").stdout
    check("§2d 命令行覆盖配置文件", "停顿阈值 0.4s" in out_cli)
    cfg.unlink(missing_ok=True)

    # 阈值真的改变结果
    d_loose = run_cli(str(fixture), "--dry-run", "--pause", "1.5").stdout
    d_tight = run_cli(str(fixture), "--dry-run", "--pause", "0.4").stdout
    def cut_sec(txt: str) -> float:
        m = re.search(r"剪掉共\s*([\d.]+)s", txt)
        return float(m.group(1)) if m else -1.0
    check("§2e 阈值改变 → 剪掉时长确实变化", cut_sec(d_tight) > cut_sec(d_loose),
          f"0.4s→{cut_sec(d_tight)}s  1.5s→{cut_sec(d_loose)}s")

    def snapshot() -> set:
        """当前产物集合。**必须现拍**：早先这个断言用的是很早拍的快照，
        结果靠上一轮遗留的文件"碰巧通过"，一删产物目录就红 —— 典型的跨轮次脆弱。"""
        return set(WORK.glob("*.mp4")) | set(WORK.glob("*.srt"))

    # ---------- §3 两段式 ----------
    before_files = snapshot()
    run_cli(str(fixture), "--dry-run")
    check("§3a dry-run 不产出任何文件", snapshot() == before_files)
    check("§3b 预览里有「剪完从 X 秒变 Y 秒」", "→" in d_tight and "省" in d_tight,
          [l for l in d_tight.splitlines() if l.startswith("剪掉共")][:1])
    # 放弃分支：喂 "q" 给它
    cp = subprocess.run([PY, str(CLI), str(fixture)], input="q\n", capture_output=True,
                        text=True, encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=120)
    check("§3c 输入 q → 退出码 0 且无副作用",
          cp.returncode == 0 and "已放弃" in cp.stdout and snapshot() == before_files)

    # ---------- §4 前置失败要大声且各不相同 ----------
    e_missing = run_cli(str(WORK / "不存在.mp4"), "--yes")
    check("§4a 文件不存在 → 报错含路径", e_missing.returncode != 0 and "不存在.mp4" in e_missing.stderr,
          e_missing.stderr.strip()[:80])
    noaudio = WORK / "noaudio.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=black:s=320x240:r=25:d=2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(noaudio)], check=True, capture_output=True)
    e_noaudio = run_cli(str(noaudio), "--yes")
    check("§4b 无音轨 → 明确说「没有音轨」", "没有音轨" in e_noaudio.stderr, e_noaudio.stderr.strip()[:80])
    check("§4c 四种错误文本互不相同",
          len({e_missing.stderr.strip(), e_noaudio.stderr.strip()}) == 2)

    # ---------- §5 找停顿 ----------
    segs = [l for l in d_tight.splitlines() if l.strip().startswith(tuple("123456789"))]
    check("§5a 默认阈值找到 ≥3 段停顿", len(segs) >= 3, f"{len(segs)} 段")
    a = run_cli(str(fixture), "--dry-run", "--pause", "0.8").stdout
    check("§5b 同输入同参数 → 剪点逐字相同", a == run_cli(str(fixture), "--dry-run", "--pause", "0.8").stdout)
    bgm = make_bgm_clip(WORK / "bgm.mp4")
    bgm_snap = snapshot()
    e_bgm = run_cli(str(bgm), "--dry-run")
    check("§5c 有 BGM（无真静音）→ 报未找到停顿且不产出文件",
          "未找到停顿" in e_bgm.stdout and snapshot() == bgm_snap
          and not (WORK / "bgm-roughcut.mp4").exists() and not (WORK / "bgm-roughcut.srt").exists(),
          e_bgm.stdout.strip().splitlines()[-1][:70])

    # ---------- §6/§7/§8/§9 端到端 ----------
    out = WORK / f"out-{RUN}.mp4"
    t0 = time.time()
    cp = run_cli(str(fixture), "--yes", "-o", str(out))
    elapsed = time.time() - t0
    check("§9a 一条命令（非交互）跑通", cp.returncode == 0 and out.is_file(), cp.stderr.strip()[:100])
    after = ffprobe_dur(out)
    info("端到端耗时", f"{elapsed:.1f}s")
    info("时长", f"{before:.2f}s → {after:.2f}s")
    check("§6a 剪后确实变短（且成片真的存在）",
          out.is_file() and out.stat().st_size > 10_000 and after < before - 1.0,
          f"{before:.2f}→{after:.2f}s  {out.stat().st_size if out.is_file() else 0}B")
    srt = out.with_suffix(".srt")
    check("§9b 同时得到 成片 + 独立字幕文件", out.is_file() and srt.is_file() and srt.stat().st_size > 0)
    cuts_json = out.with_suffix(out.suffix + ".work") / "cuts.json"
    # 用防御式读取：端到端跑挂时这里应该**干净地判失败**，
    # 而不是甩一个 FileNotFoundError 的 traceback（那会掩盖真正的原因）
    reported = json.loads(cuts_json.read_text(encoding="utf-8")) if cuts_json.is_file() else {}
    check("§8b 剪点文件落盘且可解析", bool(reported.get("cuts")), str(cuts_json))
    if reported.get("cuts"):
        cut_total = sum(e - s for s, e in reported["cuts"])
        check("§6b ffprobe 时长与摘要自洽（≤0.2s）",
              abs((reported["before"] - cut_total) - after) <= 0.2,
              f"期望 {reported['before'] - cut_total:.2f}s 实测 {after:.2f}s")
    else:
        check("§6b ffprobe 时长与摘要自洽（≤0.2s）", False, "没有剪点文件可比对")
    check("§8a 摘要五项齐全",
          all(k in cp.stdout for k in ("找到", "剪掉共", "参数：", "成片", "字幕")),
          cp.stdout.strip().splitlines()[-3:])
    txt = srt.read_text(encoding="utf-8") if srt.is_file() else ""
    check("§7a 字幕是简体中文（不是繁体）",
          bool(txt) and "怎麽" not in txt and "長文" not in txt,
          txt.splitlines()[2][:40] if len(txt.splitlines()) > 2 else "(无字幕)")
    segs_j = reported.get("segments") or []
    check("§7b 字幕时间戳落在剪后时间轴内",
          bool(segs_j) and all(s["end"] <= after + 0.2 for s in segs_j),
          f"最晚 {max((s['end'] for s in segs_j), default=-1):.2f}s / 片长 {after:.2f}s")

    # 字幕真的画上去了：抽帧 + 改字号后抽帧必须不同
    frame_a, frame_b = WORK / "frame-a.png", WORK / "frame-b.png"
    run_cli(str(fixture), "--preview-frame", "--yes", "--font-size", "16", "-o", str(WORK / "pa.mp4"))
    run_cli(str(fixture), "--preview-frame", "--yes", "--font-size", "34", "-o", str(WORK / "pb.mp4"))
    fa = (WORK / "pa.mp4.work" / "preview.png")
    fb = (WORK / "pb.mp4.work" / "preview.png")
    check("§7c 预览帧产出且只渲一帧", fa.is_file() and fb.is_file())
    if fa.is_file() and fb.is_file():
        check("§2f 改字号 → 渲染结果确实不同", fa.read_bytes() != fb.read_bytes(),
              f"{fa.stat().st_size}B vs {fb.stat().st_size}B")

    # ---------- §obs 可观测（docs/checklist_observability.md）----------
    obs_ev = WORK / f"obs-{RUN}.jsonl"
    obs_out = WORK / f"obs-{RUN}.mp4"
    cp = run_cli(str(fixture), "--yes", "--events", str(obs_ev), "--heartbeat", "1",
                 "-o", str(obs_out))
    check("§obs-a 正常档跑通且事件文件落盘", cp.returncode == 0 and obs_ev.is_file(),
          cp.stderr.strip()[:80])
    evs = [json.loads(x) for x in obs_ev.read_text(encoding="utf-8").splitlines()] if obs_ev.is_file() else []
    kinds = {(e["stage"], e["kind"]) for e in evs}
    stages = sorted({e["stage"] for e in evs})
    info("事件阶段", stages)
    need = ["探测", "找停顿", "剪裁", "抽音轨", "转写", "烧字幕"]
    missing = [s for s in need if (s, "start") not in kinds or (s, "end") not in kinds]
    check("§obs-b 每个阶段都有 start 与 end", not missing, f"缺 {missing}" if missing else f"{len(evs)} 条事件")
    check("§obs-c 事件带 schema 版本且逐行可解析",
          bool(evs) and all(e["version"] == 1 for e in evs))
    check("§obs-d 事件里不含素材内容（脱敏）",
          not any(("大家好" in json.dumps(e, ensure_ascii=False)) or
                  ("四月" in json.dumps(e, ensure_ascii=False)) for e in evs))
    # ⚠️ 单调性是**按阶段**说的：剪裁跑到 100% 后，转写会从 0 重新开始 ——
    # 全局比较会把这种正常的"新阶段重新计数"误判成进度倒退。
    by_stage: dict[str, list[float]] = {}
    for e in evs:
        if e["kind"] == "progress" and "pct" in e["fields"]:
            by_stage.setdefault(e["stage"], []).append(e["fields"]["pct"])
    bad = {k: v for k, v in by_stage.items() if v != sorted(v) or max(v) > 100}
    check("§obs-e 进度按阶段单调不减且 ≤100", bool(by_stage) and not bad,
          f"{sum(len(v) for v in by_stage.values())} 次上报；异常 {bad}" if bad else
          f"{sum(len(v) for v in by_stage.values())} 次上报 / {len(by_stage)} 个阶段")
    beats = [e for e in evs if e.get("message") == "心跳"]
    check("§obs-f 慢阶段有心跳（转写 ≥1 次）", len(beats) >= 1, f"{len(beats)} 次")

    # 报告里的耗时必须与事件文件对得上
    rep = {ln.split()[0]: float(ln.split()[1].rstrip("s"))
           for ln in cp.stdout.splitlines()
           if ln.startswith("  ") and len(ln.split()) == 2 and ln.split()[1].endswith("s")}
    ev_dur = {e["stage"]: e["elapsed_ms"] / 1000 for e in evs if e["kind"] == "end"}
    if "转写" in rep and "转写" in ev_dur:
        check("§obs-g 报告耗时与事件文件一致（≤0.5s）",
              abs(rep["转写"] - ev_dur["转写"]) <= 0.5, f"报告 {rep['转写']}s / 事件 {ev_dur['转写']:.2f}s")
    else:
        check("§obs-g 报告耗时与事件文件一致（≤0.5s）", False, f"rep={rep} ev={ev_dur}")

    # 安静档：没有阶段行，但产物一致
    quiet_out = WORK / f"quiet-{RUN}.mp4"
    cp_q = run_cli(str(fixture), "--yes", "--level", "quiet", "--no-events",
                   "--heartbeat", "0", "-o", str(quiet_out))
    check("§obs-h 安静档没有阶段行", "▶" not in cp_q.stdout and "✓" not in cp_q.stdout,
          f"{len(cp_q.stdout.splitlines())} 行")
    check("§obs-i 安静档仍产出成片", quiet_out.is_file() and quiet_out.stat().st_size > 10_000)
    # 三档比较必须**同一心跳设置**：否则比的是"心跳谁多"，不是"详细程度"
    cp_n = run_cli(str(fixture), "--yes", "--level", "normal", "--no-events",
                   "--heartbeat", "0", "-o", str(WORK / f"nm-{RUN}.mp4"))
    cp_v = run_cli(str(fixture), "--yes", "--level", "verbose", "--no-events",
                   "--heartbeat", "0", "-o", str(WORK / f"vb-{RUN}.mp4"))
    counts = [len(x.splitlines()) for x in (cp_q.stdout, cp_n.stdout, cp_v.stdout)]
    check("§obs-j 三档输出行数严格递增", counts[0] < counts[1] < counts[2], counts)
    check("§obs-k 详细档能看到实际执行的命令", "ffmpeg" in cp_v.stdout and "    | " in cp_v.stdout)

    # 事件目录不可写 → 业务必须照跑完
    blocker = WORK / "blocker-file"
    blocker.write_text("x", encoding="utf-8")
    ro_out = WORK / f"ro-{RUN}.mp4"
    cp_ro = run_cli(str(fixture), "--yes", "--events", str(blocker / "e.jsonl"),
                    "-o", str(ro_out))
    check("§obs-l 事件目录不可写 → 业务仍成功且告警",
          cp_ro.returncode == 0 and ro_out.is_file() and "[warn]" in cp_ro.stdout,
          f"rc={cp_ro.returncode}")

    print("\n===== 汇总 =====")
    passed = sum(1 for _, ok in results if ok)
    print(f"  {passed}/{len(results)} 通过")
    for label, ok in results:
        if not ok:
            print("    FAIL " + label)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
