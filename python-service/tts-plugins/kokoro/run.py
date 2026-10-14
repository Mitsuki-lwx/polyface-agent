"""Kokoro 插件：**完全离线**跑（不碰 HuggingFace）。

关键在 `_offline()`：`KModel.__init__` **总是**调 `hf_hub_download` 拿 config 与权重
（`model.py` 第 16、37 行），而 `KPipeline.load_voice` 也是。所以不补丁就一定会联网。

补丁的办法：把 `hf_hub_download(repo_id, filename)` 映射到**本地已下好的文件** ——
一次把 config / 权重 / 音色全解决，之后一个字节都不出网。

模型与音色放哪见 `scripts/fetch_kokoro.py`；本插件默认找
`python-service/kokoro-models/`（可用 `POLYFACE_KOKORO_DIR` 覆盖）。
"""
from __future__ import annotations

import json
import os
import sys
import wave
from pathlib import Path

# 仓库 → 本地子目录（与 fetch_kokoro.py 的布局一致）
REPO_DIR = {
    "hexgrad/Kokoro-82M": "",
    "hexgrad/Kokoro-82M-v1.1-zh": "zh",
}
# 语言码 → 用哪个仓库（Kokoro 的 lang_code：a=美英、z=中文）
LANG_REPO = {"a": "hexgrad/Kokoro-82M", "b": "hexgrad/Kokoro-82M",
             "z": "hexgrad/Kokoro-82M-v1.1-zh"}


def _models_root() -> Path:
    env = os.environ.get("POLYFACE_KOKORO_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "kokoro-models"


def _offline(root: Path) -> None:
    """把 `hf_hub_download` 换成"从本地取"，**并显式声明离线**。

    只补 `hf_hub_download` 不够 —— `huggingface_hub` / `transformers` 还会自己发请求
    探一下（实测：偶发 `temporary issue with the remote server` 让合成整个失败）。
    所以两个 OFFLINE 环境变量也要设上：**要么不碰网络，要么就明确别碰**。
    """
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    # **全局**补丁，不只补 kokoro 那两个模块：misaki 之类会自己 import `hf_hub_download`，
    # 只补 kokoro 的话它们照样联网（实测：偶发 "temporary issue with the remote server"
    # 让整个合成失败）。在 import kokoro **之前**补，才拦得住。
    import huggingface_hub
    huggingface_hub.hf_hub_download = _make_local(root)
    huggingface_hub.snapshot_download = lambda *a, **kw: str(root)

    import kokoro.model as km
    import kokoro.pipeline as kp

    local = _make_local(root)
    km.hf_hub_download = local
    kp.hf_hub_download = local


def _make_local(root: Path):
    def local(repo_id=None, filename=None, **kw):
        sub = REPO_DIR.get(repo_id)
        if sub is None:
            raise FileNotFoundError(
                f"这个仓库没有下到本地：{repo_id!r}（只支持 {list(REPO_DIR)}）")
        p = root / sub / str(filename)
        if not p.is_file():
            raise FileNotFoundError(f"本地缺文件：{p}（跑 scripts/fetch_kokoro.py）")
        return str(p)
    return local


def _find_voice(root: Path, voice: str) -> tuple[str, str, str, Path]:
    """音色名 → `(仓库, lang_code, .pt 路径)`。

    **按文件实际在哪个仓库判断，不按名字前缀猜。** 实测踩过：
    `af_maple` 是英文音色，但它在**中文仓库**的 `voices/` 里 ——
    按前缀猜会把 lang_code 猜成英文，然后去英文仓库找、找不到。
    """
    for sub, repo, lang in (("zh", "hexgrad/Kokoro-82M-v1.1-zh", "z"),
                            ("", "hexgrad/Kokoro-82M", "a")):
        p = root / sub / "voices" / f"{voice}.pt"
        if p.is_file():
            return repo, sub, lang, p
    raise FileNotFoundError(
        f"没有这个音色：{voice}（本地 {root} 里找不到 {voice}.pt）")


def main() -> int:
    req = json.loads(sys.stdin.read() or "{}")
    voice = (req.get("voice") or "zf_001").strip()
    text = (req.get("text") or "").strip()
    out = Path(req["out"])
    speed = float((req.get("params") or {}).get("speed") or 1.0)

    try:
        if not text:
            raise ValueError("没有文本")
        root = _models_root()
        _offline(root)

        import numpy as np
        from kokoro import KPipeline, KModel

        repo, sub, lang, vp = _find_voice(root, voice)

        # config 与权重都给本地路径 —— 加上 _offline 补丁，一个字节都不出网
        kmodel = KModel(repo_id=repo, config=str(root / sub / "config.json"))
        pipe = KPipeline(lang_code=lang, repo_id=repo, model=kmodel)

        # 音色也预读进去（KPipeline.load_voice 同样会走 hf_hub_download）
        import torch
        pipe.voices[voice] = torch.load(str(vp), map_location="cpu", weights_only=True)

        chunks = []
        for _, _, audio in pipe(text, voice=voice, speed=speed):
            chunks.append(audio)
        if not chunks:
            raise RuntimeError("Kokoro 没有产出音频")
        audio = np.concatenate(chunks)

        out.parent.mkdir(parents=True, exist_ok=True)
        # 用标准库写 WAV，**不引 soundfile** —— 少一个依赖就少一处会缺的东西
        # （实测：kokoro 的依赖里并没有 soundfile，装了才发现的）
        pcm = np.clip(np.asarray(audio, dtype="float32"), -1.0, 1.0)
        pcm = (pcm * 32767.0).astype("<i2").tobytes()
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)                     # Kokoro 的采样率是 24k
            w.writeframes(pcm)
        with wave.open(str(out), "rb") as w:
            seconds = round(w.getnframes() / w.getframerate(), 3)
        print(json.dumps({"ok": True, "seconds": seconds}, ensure_ascii=False))
        return 0
    except Exception as e:                             # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
