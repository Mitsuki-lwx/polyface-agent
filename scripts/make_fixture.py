"""生成粗剪用的可复现测试素材（T1）。

为什么要造素材而不是找现成的：本机**没有真实口播录像**（用户确认），
而"去停顿 + 字幕"这两件事必须对着**真的有人说话、真的有停顿**的视频才验得出来。
所以用 Windows 自带的 TTS 合成一段中文口播，并**故意插入长停顿**。

两个必须知道的坑（都是踩出来的）：
1. **PowerShell 5.1 按系统代码页（GBK）读 .ps1** —— 脚本里写中文会变乱码，
   而且 SAPI 会一本正经地把乱码念出来（转写结果里出现 "Breaktime等于1200 MS"）。
   ⇒ 中文一律写成 **UTF-8 的 .txt**，由脚本用 `[System.IO.File]::ReadAllText` 显式按 UTF-8 读。
2. 停顿要真的**听得出来**：SSML 的 `<break>` 之上，SAPI 还会自己加句末停顿，
   实测标称 1200ms 的 break 实际产生约 2.1s 静音 —— 所以别按标称值估时长，跑完用 ffprobe 量。

用法：
    python scripts/make_fixture.py [输出目录]     # 默认 outputs/roughcut-fixture
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# 台词：句号/逗号处会有自然停顿，<break> 处是"想词"的长停顿（粗剪要剪掉的就是它们）
SCRIPT_SSML = (
    "大家好，我是巳月。<break time='1200ms'/>"
    "今天聊聊怎么把一篇长文，改成五个平台的稿件。<break time='900ms'/>"
    "第一步是拆事实，第二步才是改写。<break time='2000ms'/>"
    "如果你也在做多平台，评论区聊聊。"
)

# 脚本刻意保持**纯 ASCII** —— 中文从 script.txt 按 UTF-8 读，见模块 docstring 的坑 1
PS_TEMPLATE = r"""Add-Type -AssemblyName System.Speech
$root = $PSScriptRoot
$text = [System.IO.File]::ReadAllText((Join-Path $root 'script.txt'), [System.Text.Encoding]::UTF8)
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice("Microsoft Huihui Desktop")
$s.SetOutputToWaveFile((Join-Path $root 'voice.wav'))
$ssml = "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='zh-CN'><voice name='Microsoft Huihui Desktop'>" + $text + "</voice></speak>"
$s.SpeakSsml($ssml)
$s.Dispose()
Write-Output "ok"
"""

VOICE = "Microsoft Huihui Desktop"
WIDTH, HEIGHT, FPS = 720, 1280, 25


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def probe_duration(path: Path) -> float:
    cp = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "csv=p=0", str(path)])
    return float(cp.stdout.strip() or 0)


def make_fixture(out_dir: Path) -> Path:
    """合成一段竖屏中文口播视频（含 3 处长停顿）。返回 mp4 路径。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "script.txt").write_text(SCRIPT_SSML, encoding="utf-8")
    (out_dir / "tts.ps1").write_text(PS_TEMPLATE, encoding="ascii")

    cp = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(out_dir / "tts.ps1")])
    if cp.returncode != 0 or "ok" not in cp.stdout:
        raise RuntimeError(f"TTS 合成失败：{cp.stdout[-300:]}{cp.stderr[-300:]}")

    wav = out_dir / "voice.wav"
    if not wav.is_file() or wav.stat().st_size < 1000:
        raise RuntimeError(f"TTS 没产出有效音频：{wav}")

    mp4 = out_dir / "talking.mp4"
    cp = _run(["ffmpeg", "-y", "-v", "error",
               "-f", "lavfi", "-i", f"color=c=0x1a1a2e:s={WIDTH}x{HEIGHT}:r={FPS}",
               "-i", str(wav), "-shortest",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
               str(mp4)])
    if cp.returncode != 0:
        raise RuntimeError(f"合成视频失败：{cp.stderr[-400:]}")
    return mp4


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]) if len(argv) > 1 else Path("outputs/roughcut-fixture")
    mp4 = make_fixture(out_dir)
    print(f"素材: {mp4}")
    print(f"时长: {probe_duration(mp4):.2f}s   画面: {WIDTH}x{HEIGHT}@{FPS}")
    print(f"语音: {VOICE}（台词见 {out_dir / 'script.txt'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
