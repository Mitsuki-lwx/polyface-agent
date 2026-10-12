"""内置插件：用户自己的音频 —— 统一转成 22050 单声道 WAV。

采样率必须统一：混用会让后面拼接时变调（实测踩过：写死 16000 而实际 22050 → 1.38 倍速）。
"""
import json
import subprocess
import sys
import wave
from pathlib import Path


def main() -> int:
    req = json.loads(sys.stdin.read() or "{}")
    src = (req.get("params") or {}).get("file") or ""
    try:
        if not src or not Path(src).is_file():
            raise FileNotFoundError(f"没有可用的音频文件：{src!r}")
        cp = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                             "-ac", "1", "-ar", "22050", str(req["out"])],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if cp.returncode != 0:
            raise RuntimeError(f"ffmpeg 转换失败：{(cp.stderr or '')[-200:]}")
        with wave.open(str(req["out"]), "rb") as w:
            seconds = round(w.getnframes() / w.getframerate(), 3)
        print(json.dumps({"ok": True, "seconds": seconds}, ensure_ascii=False))
        return 0
    except Exception as e:                                  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
