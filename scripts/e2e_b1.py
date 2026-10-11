"""端到端：成稿 → 剪辑单 → 选图 + BGM → 成片（`docs/checklist_b1.md` §9）。

走**完整真实链路**：真 LLM 出成稿（含剪辑单）→ 真 TTS 配音 → 真 ffmpeg 出片。

前置：Python(8000) 与 Java(8080) 已起，data 目录指向 data/e2e-b1。

    python scripts/e2e_b1.py
"""
from __future__ import annotations

import json
import math
import mimetypes
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

BASE = "http://127.0.0.1:8080"
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "e2e-b1"
WORK = ROOT / ".workbuddy-ai" / "b1-e2e"

RAW = (
    "我2023年3月裸辞，从月入0做到月入3万，用了11个月。\n"
    "第一件事是算清楚自己能活几个月：房租3500、吃饭2000，手上存款8万，够撑14个月。\n"
    "第二件事是砍掉所有非必要开支，把月支出压到5500以内。\n"
    "第三件事是把写作从爱好变成产品：先接商单，单价从800涨到3000，靠的是积累案例。\n"
    "踩过的坑：一开始什么都想做，结果三个月没收入；后来只做一件事才有起色。"
)


def call(method: str, path: str, body=None, timeout=1200):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def upload(path: Path, name: str) -> dict:
    boundary = "----polyface" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    parts = []
    for f, v in (("name", name), ("tags", "端到端")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{f}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                 f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n".encode())
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(BASE + "/api/assets/upload", data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode("utf-8"))


def make_stills(n: int) -> list[Path]:
    """优先用**真照片**（`scripts/fetch_sample_media.py` 抓的）。

    合成花纹图证明不了"真素材也能处理" —— 真照片有真实的亮度分布与细节，
    缩放/缓动/编码的行为都不一样。
    """
    sample = ROOT / ".workbuddy-ai" / "sample"
    real = sorted(sample.glob("photo*.jpg"))
    if len(real) >= n:
        print(f"   用真照片（{sample}）")
        return real[:n]
    print(f"   [warn] 真照片不够（{len(real)}/{n}），回退到合成图"
          f" —— 想用真素材：python scripts/fetch_sample_media.py --photos {n}")
    WORK.mkdir(parents=True, exist_ok=True)
    srcs = ["testsrc2=s=1080x1920", "smptebars=s=1080x1920", "mandelbrot=s=1080x1920",
            "testsrc=s=1080x1920", "rgbtestsrc=s=1080x1920"]
    out = []
    for i in range(n):
        p = WORK / f"still{i}.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", srcs[i % len(srcs)], "-frames:v", "1", str(p)], check=True)
        out.append(p)
    return out


def make_bgm(interval: float = 1.0, n: int = 40) -> Path:
    """优先用**真音乐**（有真节拍、真和声）。

    点击轨规整得不像话 —— 用它测出来的对齐率没有意义。真音乐实测 137 BPM、
    间隔标准差 0.021s，是真正在测节拍检测。
    """
    sample = ROOT / ".workbuddy-ai" / "sample"
    real = sorted(sample.glob("music-*.mp3"))
    if real:
        print(f"   用真音乐：{real[0].name}")
        return real[0]
    p = WORK / "bgm.wav"
    sr = 22050
    data = bytearray()
    for _ in range(n):
        data += b"\x00\x00" * int(sr * (interval - 0.08))
        for k in range(int(sr * 0.08)):
            data += struct.pack("<h", int(12000 * math.sin(2 * math.pi * 1000 * k / sr)))
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(bytes(data))
    return p


def wait_job(job_id: int, label: str, timeout=2400):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, job = call("GET", f"/api/jobs/{job_id}")
        st = job.get("status")
        stage = (job.get("progress") or {}).get("stage", "")
        print(f"    [{time.time()-t0:6.1f}s] {st} {stage}")
        if st in ("succeeded", "failed", "partial", "canceled", "interrupted"):
            return job
        time.sleep(5)
    raise SystemExit(f"{label} 超时")


def main() -> None:
    reuse = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if reuse:
        # 复用已生成的素材 —— 生成一次要 90s+，调试后面的步骤时不必重跑
        print(f"== 1) 复用素材 #{reuse}（跳过生成）==")
        return _from_material(reuse)

    print("== 1) 建素材 + 生成成稿（真 LLM，含剪辑单）==")
    st, m = call("POST", "/api/materials",
                 {"raw_text": RAW, "source_kind": "长文", "title": "裸辞11个月复盘"})
    if st != 200:
        raise SystemExit(f"建素材失败：{m}")
    mid = m["id"]
    st, r = call("POST", "/api/jobs",
                 {"kind": "generate", "material_id": mid, "platforms": ["douyin"]})
    job = wait_job(r["job_id"], "生成")
    if job.get("status") not in ("succeeded", "partial"):
        raise SystemExit(f"生成没成功：{job.get('status')} {job.get('error')}")
    print(f"   成稿完成：{json.dumps(job.get('result'), ensure_ascii=False)[:200]}")

    return _from_material(mid)


def _from_material(mid: int) -> None:
    """从素材出发：取成稿的剪辑单 → 上传图/BGM → 成片 → 核对。"""
    _, mat = call("GET", f"/api/materials/{mid}")
    summary = next(d for d in mat["drafts"] if d["platform_code"] == "douyin")
    # 草稿**列表**只给摘要，剪辑单要取详情（实测踩过：直接读 summary 的 payload 是 None）
    _, draft = call("GET", f"/api/drafts/{summary['id']}")
    scenes = ((draft.get("draft") or {}).get("clip_sheet") or {}).get("scenes") or []   # 详情里叫 draft，不是 payload
    if not scenes:
        raise SystemExit("成稿里没有剪辑单（douyin 应当是视频类平台）")
    print(f"   剪辑单 {len(scenes)} 个分镜：")
    for s in scenes:
        print(f"     {s['seq']}. {s.get('subtitle') or s.get('script')}")

    print("== 2) 上传每个分镜的图 + 一首 BGM ==")
    stills = make_stills(len(scenes))
    img_ids = [upload(p, f"分镜{i+1}配图")["id"] for i, p in enumerate(stills)]
    bgm_id = upload(make_bgm(), "测试 BGM")["id"]
    print(f"   图 {img_ids}  BGM #{bgm_id}")

    print("== 3) 先缺一张图 —— 看它是否**指出是第几个** ==")
    st, r = call("POST", "/api/jobs", {"kind": "make_video", "material_id": mid,
                                       "platform": "douyin",
                                       "images": img_ids[:-1]})
    print(f"   HTTP {st}  {r}")
    assert st == 400, "缺图应当被拒"
    print(f"   ✓ 拒绝了，理由：{r.get('detail')}")

    print("== 4) 正式成片（voice_first，默认策略）==")
    st, r = call("POST", "/api/jobs", {"kind": "make_video", "material_id": mid,
                                       "platform": "douyin",
                                       "images": img_ids, "bgm_asset_id": bgm_id})
    if st != 200:
        raise SystemExit(f"发起失败：{r}")
    job = wait_job(r["job_id"], "成片")
    if job.get("status") != "succeeded":
        raise SystemExit(f"成片没成功：{job.get('status')} {job.get('error')}")

    print("== 5) 核对成片与初剪记录 ==")
    _, assets = call("GET", "/api/assets?kind=video")
    films = [a for a in (assets.get("items") or []) if "图文成片" in (a.get("name") or "")]
    if not films:
        raise SystemExit("成片成功了但素材库里没有产物")
    film = DATA / "media" / films[0]["rel_path"]
    rec_path = film.with_suffix(".plan.json")
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=nw=1:nk=1", str(film)],
                               capture_output=True, text=True).stdout.strip())
    print(f"   成片 {film.name}  {dur:.2f}s")
    print(f"   记录：策略 {rec['strategy']} · 分镜 {len(rec['scenes'])} 个 · "
          f"对齐 {rec.get('aligned_ratio_before_offset', 0):.0%} → {rec['aligned_ratio']:.0%}")
    print(f"   时长：记录 {rec['film_sec']:.2f}s / 实际 {dur:.2f}s")
    assert abs(rec["film_sec"] - dur) <= 0.35, "记录与成片时长对不上"
    assert rec["tts"]["available"], "本机应当有 TTS（Windows 自带）"

    print("== 6) 切 beat_first 再跑一次 —— 对齐率应当提高 ==")
    st, r = call("POST", "/api/jobs", {"kind": "make_video", "material_id": mid,
                                       "platform": "douyin",
                                       "images": img_ids, "bgm_asset_id": bgm_id,
                                       "params": {"strategy": "beat_first"}})
    job2 = wait_job(r["job_id"], "成片(beat_first)")
    _, assets = call("GET", "/api/assets?kind=video")
    films2 = [a for a in (assets.get("items") or []) if "图文成片" in (a.get("name") or "")]
    rec2 = json.loads((DATA / "media" / films2[0]["rel_path"]).with_suffix(".plan.json")
                      .read_text(encoding="utf-8"))
    print(f"   voice_first：{rec['aligned_ratio_before_offset']:.0%} → {rec['aligned_ratio']:.0%}"
          f"   分镜 {[s['seconds'] for s in rec['scenes']]}")
    print(f"   beat_first ：{rec2['aligned_ratio']:.0%}"
          f"   分镜 {[s['seconds'] for s in rec2['scenes']]}")
    assert rec2["aligned_ratio"] >= rec["aligned_ratio"] - 1e-9, "卡点优先不该更差"
    print("\n✅ 端到端通过")


if __name__ == "__main__":
    main()
