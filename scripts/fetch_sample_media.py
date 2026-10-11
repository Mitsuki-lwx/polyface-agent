"""抓一份**真素材**给端到端用（`docs/checklist_b1.md` §9）。

为什么要真素材：合成的 `testsrc2` 花纹图与生成的点击轨**证明不了"真素材也能处理"** ——
尤其节拍检测：点击轨规整得不像话，用它测出来的对齐率没有意义。

素材**不进仓库**（体积 + 许可），但本脚本可复现。抓到哪里由 `--out` 决定（默认
`.workbuddy-ai/sample/`，在 .gitignore 里）。

    python scripts/fetch_sample_media.py --photos 9

## 来源与许可（都是可自由使用的）

| 来源 | 内容 | 许可 |
|---|---|---|
| [picsum.photos](https://picsum.photos) | 真实照片（取自 Unsplash） | Unsplash License：可自由使用 |
| [SoundHelix](https://www.soundhelix.com) | **算法生成**的音乐（真节拍、真和声） | 站点明示可自由用于测试 |
| [Big Buck Bunny](https://peach.blender.org) | 真视频（Blender 开源电影） | CC-BY 3.0，Blender Foundation |

⚠️ 这些只是**测试素材**。真要用到成片里，许可与署名由使用者自己负责 ——
本工具不内置任何素材（`docs/spec_b1.md` 关键决定四）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PHOTO_URL = "https://picsum.photos/seed/polyface{n}/1080/1920"
MUSIC_URLS = {
    "music-1.mp3": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
    "music-2.mp3": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3",
}
VIDEO_URLS = {
    # Blender 开源电影（CC-BY 3.0）—— 有真实画面运动与音频
    "bbb-10s.mp4": "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/"
                   "Big_Buck_Bunny_360_10s_1MB.mp4",
}

UA = {"User-Agent": "polyface-e2e/1.0 (+local test fixture fetch)"}


def fetch(url: str, dest: Path, *, timeout: int = 180) -> bool:
    if dest.is_file() and dest.stat().st_size > 1024:
        print(f"  已有 {dest.name}（{dest.stat().st_size // 1024}KB）")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if len(data) < 1024:
            print(f"  ✗ {dest.name}：只拿到 {len(data)} 字节")
            return False
        dest.write_bytes(data)
        print(f"  ✓ {dest.name}  {len(data) // 1024}KB")
        return True
    except Exception as e:                                   # noqa: BLE001
        print(f"  ✗ {dest.name}：{e}")
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fetch_sample_media.py")
    ap.add_argument("--out", default=str(ROOT / ".workbuddy-ai" / "sample"))
    ap.add_argument("--photos", type=int, default=9)
    ap.add_argument("--music", type=int, default=1)
    ap.add_argument("--video", action="store_true", help="也抓一个真视频（CC-BY）")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"抓到哪里：{out}")

    ok = 0
    print(f"照片（{args.photos} 张，真实照片）")
    for i in range(1, args.photos + 1):
        ok += fetch(PHOTO_URL.format(n=i), out / f"photo{i:02d}.jpg")

    print(f"音乐（{args.music} 首，算法生成但有真节拍）")
    for name, url in list(MUSIC_URLS.items())[: args.music]:
        ok += fetch(url, out / name)

    if args.video:
        print("视频（Blender 开源电影，CC-BY 3.0）")
        for name, url in VIDEO_URLS.items():
            ok += fetch(url, out / name)

    # 抓完核一遍：真能当素材用才算数
    print("\n核对（ffprobe）")
    for f in sorted(out.iterdir()):
        if f.suffix.lower() not in (".jpg", ".png", ".mp3", ".wav", ".mp4"):
            continue
        cp = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "stream=codec_type,width,height", "-show_entries", "format=duration",
                             "-of", "default=nw=1", str(f)], capture_output=True, text=True)
        info = " ".join(x for x in cp.stdout.split() if x)
        print(f"  {f.name:16} {info[:90]}")

    print(f"\n完成 {ok} 个文件 → {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
