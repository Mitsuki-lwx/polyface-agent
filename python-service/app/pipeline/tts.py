"""配音（TTS）：**可选依赖 + 降级**（`docs/spec_b1.md` 关键决定三）。

两条硬要求：

1. **没有 TTS 不是错误。** 上层应当降级成"无声 + 字幕"并给出安装指引 ——
   绝不因为缺一个可选依赖就整体失败。`asr.py` 已经立过这个先例，这里走同一条路。
2. **数据不出本机。** 所有后端都是本地引擎 —— ADR-017 与 README 的「数据边界」表
   都不需要改（这正是选本地 TTS 的全部意义）。

后端（按可用性挑，也可用 `POLYFACE_TTS` 指定）：

| 后端 | 平台 | 安装 |
|---|---|---|
| `windows_sapi` | Windows | **零安装**（系统自带，`System.Speech`） |
| `piper` | 跨平台 | 可选：`pip install piper-tts` + 下模型 |

`POLYFACE_TTS=none` 可以**强制禁用** —— 用来验"没装 TTS 也能出片"这条路径。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

INSTALL_HINT = (
    "未检测到可用的本地 TTS。成片会降级为「无声 + 字幕」。\n"
    "  · Windows：系统自带语音合成，通常**无需安装**（本机检测不到，可能是被裁剪的系统）\n"
    "  · 其它平台：pip install piper-tts，并下载一个中文模型，设置 POLYFACE_PIPER_MODEL 指向它"
)

_PS_TEMPLATE = """Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = {rate}
$synth.Volume = {volume}
{voice}
$synth.SetOutputToWaveFile('{out}')
$synth.Speak([Console]::In.ReadToEnd())
$synth.Dispose()
"""


class TtsUnavailable(RuntimeError):
    """没有可用的 TTS。**这不是错误** —— 上层应当降级，不是失败。"""


def _forced() -> str:
    """`POLYFACE_TTS`：auto（默认）/ none（强制禁用）/ windows_sapi / piper。"""
    return (os.environ.get("POLYFACE_TTS") or "auto").strip().lower()


def _piper_ok() -> bool:
    if shutil.which("piper"):
        return True
    try:
        import piper  # noqa: F401
        return True
    except Exception:                                    # noqa: BLE001
        return False


def _sapi_ok() -> bool:
    if sys.platform != "win32":
        return False
    if not shutil.which("powershell"):
        return False
    try:
        cp = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Add-Type -AssemblyName System.Speech; "
             "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices().Count"],
            capture_output=True, text=True, timeout=30)
        return cp.returncode == 0 and (cp.stdout or "").strip().isdigit() \
            and int(cp.stdout.strip()) > 0
    except Exception:                                    # noqa: BLE001
        return False


def backend() -> str | None:
    """当前会用哪个后端；没有则 None。"""
    forced = _forced()
    if forced == "none":
        return None
    if forced == "windows_sapi":
        return "windows_sapi" if _sapi_ok() else None
    if forced == "piper":
        return "piper" if _piper_ok() else None
    if _sapi_ok():
        return "windows_sapi"
    if _piper_ok():
        return "piper"
    return None


def is_available() -> bool:
    return backend() is not None


def describe() -> dict:
    """供 UI/自检：**"启用了"要有可查询证据**。"""
    b = backend()
    return {"available": b is not None, "backend": b,
            "forced": _forced(), "hint": "" if b else INSTALL_HINT}


def wav_seconds(path: str | Path) -> float:
    """WAV 时长。用标准库读 —— 不必为这点事起一个 ffprobe 进程。"""
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() / float(w.getframerate()), 3)


def synthesize(text: str, out_wav: str | Path, *, rate: int = 0,
               voice: str | None = None, volume: int = 100) -> float:
    """把一段文本合成为 WAV，返回**实际时长**（秒）。

    **时长是返回值，不是估算** —— 后续所有分镜时长都以它为准（spec 关键决定一）。

    :raises TtsUnavailable: 没有可用后端（上层应当降级，不是失败）
    """
    text = (text or "").strip()
    out = Path(out_wav)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not text:
        # 空文本 = 一个极短的静音段，免得调用方还要特判
        _write_silence(out, 0.3)
        return 0.3

    b = backend()
    if b is None:
        raise TtsUnavailable(INSTALL_HINT)
    if b == "windows_sapi":
        _sapi(text, out, rate=rate, voice=voice, volume=volume)
    else:
        _piper(text, out, voice=voice)
    return wav_seconds(out)


def _write_silence(out: Path, seconds: float) -> None:
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))


def _sapi(text: str, out: Path, *, rate: int, voice: str | None, volume: int = 100) -> None:
    voice_line = ""
    if voice:
        voice_line = f"$synth.SelectVoice('{voice}')"
    else:
        # 优先中文音色（本机实测有 Microsoft Huihui Desktop）
        voice_line = ("$zh = $synth.GetInstalledVoices() | Where-Object "
                      "{ $_.VoiceInfo.Culture.Name -like 'zh*' } | Select-Object -First 1; "
                      "if ($zh) { $synth.SelectVoice($zh.VoiceInfo.Name) }")
    script = _PS_TEMPLATE.format(rate=int(rate), voice=voice_line,
                                 volume=int(volume), out=str(out).replace("'", "''"))
    # 走 stdin 喂文本：避免命令行长度限制与引号转义地狱（中文尤其容易踩）
    cp = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                        input=text, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=300)
    if cp.returncode != 0 or not out.is_file():
        raise TtsUnavailable(f"Windows SAPI 合成失败：{(cp.stderr or cp.stdout or '')[:300]}")


def _piper(text: str, out: Path, *, voice: str | None) -> None:
    model = voice or os.environ.get("POLYFACE_PIPER_MODEL") or ""
    if not model:
        raise TtsUnavailable("piper 需要一个模型：设置 POLYFACE_PIPER_MODEL 指向 .onnx 文件")
    exe = shutil.which("piper") or "piper"
    cp = subprocess.run([exe, "--model", model, "--output_file", str(out)],
                        input=text, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=600)
    if cp.returncode != 0 or not out.is_file():
        raise TtsUnavailable(f"piper 合成失败：{(cp.stderr or '')[:300]}")


def list_voices() -> list[dict]:
    """列出可用音色（供用户挑 —— 不替他定死一个）。

    各后端能报的字段不一样：SAPI 有名字/语言/性别，piper 只有模型名。
    统一成 `{id, name, language, gender, backend}`，缺的字段留空。
    """
    b = backend()
    if b == "windows_sapi":
        try:
            cp = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Add-Type -AssemblyName System.Speech; "
                 "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices()"
                 " | ForEach-Object { $v=$_.VoiceInfo; Write-Output ($v.Name + \"|\" + "
                 "$v.Culture.Name + \"|\" + $v.Gender) }"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            out = []
            for line in (cp.stdout or "").splitlines():
                bits = [x.strip() for x in line.split("|")]
                if len(bits) == 3 and bits[0]:
                    out.append({"id": bits[0], "name": bits[0], "language": bits[1],
                                "gender": bits[2], "backend": b})
            return out
        except Exception:                                # noqa: BLE001
            return []
    if b == "piper":
        model = os.environ.get("POLYFACE_PIPER_MODEL") or ""
        return [{"id": model, "name": Path(model).name or "(未设置 POLYFACE_PIPER_MODEL)",
                 "language": "", "gender": "", "backend": b}]
    return []


def default_voice() -> str | None:
    """默认音色：优先**中文**（本产品面向中文口播），没有就交给引擎自己挑。"""
    for v in list_voices():
        if (v.get("language") or "").lower().startswith("zh"):
            return v["id"]
    return None


# ---------------- 风格预设（一键选）----------------
# ⚠️ **如实说明**：Windows 自带的中文音色**只有 Huihui 一个女声**（本机实测），
# 所以预设之间能变的只有**语速与音量**，音色本身变不了。
# 想要真正不同的音色，得靠 piper 的多套中文模型（本地、要下载），
# 或云端 TTS（那就动了 ADR-017 的数据边界）。
PRESETS: list[dict] = [
    {"id": "steady", "name": "沉稳女声", "rate": -2, "volume": 100, "pick": "zh",
     "note": "语速慢一点，适合干货/复盘类"},
    {"id": "standard", "name": "标准女声", "rate": 0, "volume": 100, "pick": "zh",
     "note": "默认"},
    {"id": "lively", "name": "轻快女声", "rate": 3, "volume": 100, "pick": "zh",
     "note": "语速快一点，适合种草/日常"},
    {"id": "male", "name": "男声（英文音色）", "rate": 0, "volume": 100, "pick": "en-male",
     "note": "⚠️ 系统没有中文男声；用英文音色读中文会读成乱码，仅作演示"},
]


def list_presets() -> list[dict]:
    """给 UI/命令行用。**带上 note** —— 用户要知道"轻快"到底变了什么。"""
    return [dict(x) for x in PRESETS]


def resolve_preset(preset_id: str) -> dict:
    """预设 → `{voice, rate, volume}`。不认识的 id **明确报错**，不静默用默认。"""
    for x in PRESETS:
        if x["id"] == preset_id:
            return {"voice": _pick_voice(x["pick"]), "rate": x["rate"],
                    "volume": x["volume"], "name": x["name"], "note": x["note"]}
    raise TtsUnavailable(
        f"不认识的配音预设：{preset_id!r}；可用：{[x['id'] for x in PRESETS]}")


def _pick_voice(kind: str) -> str | None:
    """按预设的意图挑一个**真实存在**的音色；挑不到就返回 None（交给引擎默认）。"""
    vs = list_voices()
    if kind == "zh":
        for v in vs:
            if (v.get("language") or "").lower().startswith("zh"):
                return v["id"]
        return None
    if kind == "en-male":
        for v in vs:
            if (v.get("language") or "").lower().startswith("en") and \
                    (v.get("gender") or "").lower().startswith("m"):
                return v["id"]
        return None
    return None
