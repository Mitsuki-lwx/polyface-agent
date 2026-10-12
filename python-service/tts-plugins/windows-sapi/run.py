"""内置插件：Windows SAPI（走插件接口，与第三方插件同一条路）。"""
import json
import sys
from pathlib import Path

# 让插件能 import 主程序（内置插件与主程序同仓，这是允许的；第三方插件不该依赖它）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.pipeline import tts  # noqa: E402


def main() -> int:
    req = json.loads(sys.stdin.read() or "{}")
    try:
        seconds = tts.synthesize(req.get("text") or "", req["out"],
                                 voice=req.get("voice") or None,
                                 rate=int(req.get("rate") or 0),
                                 volume=int(req.get("volume") or 100))
        print(json.dumps({"ok": True, "seconds": seconds}, ensure_ascii=False))
        return 0
    except Exception as e:                                  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
