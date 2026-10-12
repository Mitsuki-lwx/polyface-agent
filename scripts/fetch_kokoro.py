"""抓 Kokoro 的模型与音色（**在你自己的机器上跑** —— 沙箱里连不上 HuggingFace）。

    python scripts/fetch_kokoro.py

抓到哪里：默认 `python-service/kokoro-models/`（已在 .gitignore 里）。
抓完 `python-service/app/pipeline/tts.py` 的 kokoro 后端会自动认这个目录，
**不经过 huggingface_hub**（源码里 `if voice in self.voices: return` 这一行让预置音色生效）。

## 要下什么（我从 `kokoro` 包的源码里挖出来的，不是猜的）

`kokoro/model.py` 里写着两个仓库各自要哪个模型文件：

    'hexgrad/Kokoro-82M':          'kokoro-v1_0.pth'      ← 通用（含中文 v1.0 音色）
    'hexgrad/Kokoro-82M-v1.1-zh':  'kokoro-v1_1-zh.pth'   ← 中文 v1.1（音色更多更好）

`kokoro/pipeline.py` 里音色是**一个音色一个文件**：

    hf_hub_download(repo_id, filename=f'voices/{voice}.pt')

所以要下的就是：每个仓库的 `config.json` + 模型 `.pth` + `voices/*.pt`。

## 许可

Kokoro-82M 是 **Apache-2.0**（PyPI 上 kokoro 0.9.4 的元数据里写明）。
模型卡在 HuggingFace 上 —— 下之前扫一眼它的模型卡确认。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "python-service" / "kokoro-models"

# 仓库 → 模型文件名（取自 kokoro/model.py 的 MODEL_FILE 映射）
REPOS = {
    "v1.0": {"repo": "hexgrad/Kokoro-82M", "model": "kokoro-v1_0.pth", "dir": ""},
    "v1.1-zh": {"repo": "hexgrad/Kokoro-82M-v1.1-zh", "model": "kokoro-v1_1-zh.pth", "dir": "zh"},
}

# 想要哪几个音色就写哪几个；留空 = 尝试下该仓库 voices/ 下的常见音色
ZH_VOICES = ["zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi",
             "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang"]
EN_VOICES = ["af_bella", "af_heart", "af_nicole", "am_michael", "am_puck", "bf_emma", "bm_george"]

UA = {"User-Agent": "polyface/1.0 (+local model fetch)"}


def url(repo: str, path: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/main/{path}"


def fetch(u: str, dest: Path, timeout: int = 300) -> bool:
    if dest.is_file() and dest.stat().st_size > 1024:
        print(f"    已有 {dest.name}（{dest.stat().st_size // 1024}KB）")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(u, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if len(data) < 512:
            print(f"    ✗ {dest.name}：只拿到 {len(data)} 字节")
            return False
        dest.write_bytes(data)
        print(f"    ✓ {dest.name}  {len(data) // 1024}KB")
        return True
    except Exception as e:                                   # noqa: BLE001
        print(f"    ✗ {dest.name}：{e}")
        print(f"      手动下：{u}")
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fetch_kokoro.py")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--only", choices=list(REPOS), default=None,
                    help="只抓某个仓库（默认两个都抓）")
    ap.add_argument("--no-voices", action="store_true", help="只抓模型，不抓音色")
    args = ap.parse_args(argv)

    out = Path(args.out)
    print(f"抓到哪里：{out}")
    print("（沙箱里连不上 HuggingFace —— 请在**你自己的机器**上跑这个脚本）\n")

    ok = 0
    todo = {k: v for k, v in REPOS.items() if args.only in (None, k)}
    for tag, meta in todo.items():
        base = out / meta["dir"] if meta["dir"] else out
        repo = meta["repo"]
        print(f"[{tag}] {repo}")
        ok += fetch(url(repo, "config.json"), base / "config.json")
        ok += fetch(url(repo, meta["model"]), base / meta["model"])
        if not args.no_voices:
            names = ZH_VOICES if "zh" in tag else EN_VOICES
            for v in names:
                fetch(url(repo, f"voices/{v}.pt"), base / "voices" / f"{v}.pt")

    manifest = {"repos": {k: {"repo": v["repo"], "model": v["model"], "dir": v["dir"]}
                          for k, v in REPOS.items()}, "out": str(out)}
    (out / "fetch-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成 {ok} 个文件 → {out}")
    print("然后告诉程序这个目录：环境变量 POLYFACE_KOKORO_DIR 指过去（或就用默认路径）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
