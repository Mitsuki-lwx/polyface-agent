"""端到端：**强制让第 2 镜配音失败**，验三件事（`docs/checklist_b1.md` 里那个缺陷的回归）：
   ① 成片时长**不再**比计划短（补了静音垫片）
   ② has_voice 按实际产出写（那镜应是 False）
   ③ tts.failed 里有它（不许静默）

用法：
    python scripts/e2e_tts_fail.py <plan.json>

**为什么要有这个脚本**：2026-10-18 实测发现，某一镜的 TTS **静默失败**时
（`voice-008.wav` 根本没产出），成片比计划短 8s，而记录里 `has_voice` 还是 `True`、
`tts.skipped` 还是 `[]` —— **错得无声** ✗。单测测不出这种**间歇性**失败，
只有"强制失败 + 跑真管线 + 逐项核对产物"才露得出来。

三个断言（就是那个缺陷的三条症状）：
    ① 成片时长**不再**比计划短（补了等长静音垫片）
    ② 那一镜 `has_voice=False`（有音频 ≠ 有旁白）
    ③ `tts.failed` 里有它（不许静默）
"""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "python-service"))

import importlib.util
spec = importlib.util.spec_from_file_location("mv", str(ROOT / "scripts" / "make_video.py"))
mv = importlib.util.module_from_spec(spec)
sys.modules["mv"] = mv
spec.loader.exec_module(mv)

real = mv.tp.synthesize
calls = {"n": 0}
def flaky(plugin, text, out, **kw):
    calls["n"] += 1
    if calls["n"] == 2:                       # 第 2 镜失败
        raise mv.tp.PluginError("模拟：SAPI 偶发失败")
    return real(plugin, text, out, **kw)
mv.tp.synthesize = flaky

plan = sys.argv[1]
outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "outputs" / "tts-fail"; outdir.mkdir(parents=True, exist_ok=True)
argv = [plan, "-o", str(outdir / "film.mp4"), "--workdir", str(outdir / "work"),
        "--level", "quiet"]
rc_ = mv.main(argv)
print("退出码：", rc_)

rec = json.loads((outdir / "film.plan.json").read_text(encoding="utf-8"))
sc = rec["scenes"]
print(f"\n记录：分镜 {len(sc)} 个  film_sec={rec['film_sec']}  total_sec={rec['total_sec']}")
print("tts.failed =", json.dumps(rec["tts"].get("failed"), ensure_ascii=False))
print(f"\nseq  seconds   has_voice  plugin")
for s in sc:
    print(f"  {s['seq']:>2}  {s['seconds']:7.3f}   {s['has_voice']!s:>9}  {s['plugin']}")

import subprocess
mp4 = outdir / "film.mp4"
d = float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",str(mp4)],
                         capture_output=True, text=True).stdout.strip())
print(f"\n成片实际 {d:.3f}s / 记录 {rec['film_sec']}s  差 {abs(d - rec['film_sec']):.3f}s")
print()
print(f"  ① 时长对得上（±0.5s）     ：{'✓' if abs(d - rec['film_sec']) <= 0.5 else '✗ 还是短了'}")
print(f"  ② 第 2 镜 has_voice=False ：{'✓' if sc[1]['has_voice'] is False else '✗ 还是 True'}")
print(f"  ③ tts.failed 里有第 2 镜  ：{'✓' if any(x['seq']==2 for x in (rec['tts'].get('failed') or [])) else '✗ 没记录'}")
