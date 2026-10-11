"""端到端：**真的改一次时间线、真的重渲染出片**（`docs/checklist_timeline.md` §7）。

不做任何"模拟编辑" —— 走完整的真实链路：
    上传素材 → 粗剪（真 ASR + 真 ffmpeg）→ 拿到时间线 → 删掉中间一段 → 保存
    → 重渲染（真 ffmpeg）→ 核对新成片

前置：Python(8000) 与 Java(8080) 已起，data 目录指向 data/e2e-timeline。

    python scripts/e2e_timeline.py
"""
from __future__ import annotations

import json
import mimetypes
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8080"
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "outputs" / "roughcut-fixture" / "talking.mp4"


def call(method: str, path: str, body=None, timeout=900):
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


def upload(path: Path, name: str):
    """multipart 上传（不引第三方库，手拼 body）。"""
    boundary = "----polyface" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    parts = []
    for field, value in (("name", name), ("tags", "端到端测试")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"\r\n\r\n{value}\r\n".encode())
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
        f"Content-Type: {ctype}\r\n\r\n".encode())
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(BASE + "/api/assets/upload", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode("utf-8"))


def probe_duration(path: Path) -> float:
    cp = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=nw=1:nk=1", str(path)],
                        capture_output=True, text=True)
    return float(cp.stdout.strip())


def wait_job(job_id: int, timeout=1800, label=""):
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
    if not FIXTURE.is_file():
        raise SystemExit(f"没有测试素材：{FIXTURE}（先跑 scripts/make_fixture.py）")

    print("== 1) 上传素材 ==")
    a = upload(FIXTURE, "端到端素材")
    print(f"   资产 #{a['id']} {a.get('name')} kind={a.get('kind')}")
    asset_id = a["id"]

    print("== 2) 粗剪（真 ASR + 真 ffmpeg）==")
    st, r = call("POST", "/api/jobs", {"kind": "roughcut", "asset_id": asset_id})
    print(f"   HTTP {st} {r}")
    job = wait_job(r["job_id"], label="粗剪")
    if job.get("status") != "succeeded":
        raise SystemExit(f"粗剪没成功：{job.get('status')} {job.get('error')}")

    print("== 3) 找到刚产出的时间线 ==")
    _, tl_list = call("GET", "/api/timelines")
    items = tl_list.get("items") or []
    if not items:
        raise SystemExit("粗剪跑完了却没有时间线资产")
    tl_id = items[0]["asset_id"]
    _, tl = call("GET", f"/api/timelines/{tl_id}")
    print(f"   时间线资产 #{tl_id}  源时长 {tl['source'].get('duration')}s")
    keep0 = tl["timeline"]["keep"]
    segs0 = tl["timeline"]["segments"]
    print(f"   保留 {len(keep0)} 段：{keep0}")
    print(f"   字幕 {len(segs0)} 条")
    if len(keep0) < 3:
        raise SystemExit(f"只切出 {len(keep0)} 段，测不了'删中间一段'（素材停顿太少）")

    src_dur = float(tl["source"]["duration"])
    keep_src = sum(e - s for s, e in keep0)
    out_before = Path(ROOT / "data" / "e2e-timeline" / "media"
                      / "roughcut") if False else None   # 旧成片路径下面从产物里拿

    # 旧成片的时长（用于对照）
    _, assets = call("GET", "/api/assets?kind=video")
    olds = [x for x in (assets.get("items") or []) if "粗剪成片" in (x.get("name") or "")]
    old_mp4 = ROOT / "data" / "e2e-timeline" / "media" / olds[0]["rel_path"] if olds else None
    old_dur = probe_duration(old_mp4) if old_mp4 and old_mp4.is_file() else None
    print(f"   粗剪成片：{old_mp4}  时长 {old_dur}")

    print("== 4) 删掉一段（专挑带字幕的那段 —— 否则验不出「字幕跟着剪点走」）==")
    def fully_inside(s, e):
        """**完全**落在 [s,e] 里的字幕 —— 只有这些才**必须**消失。

        跨越边界的那些本来就该留下（它们在成片里只是变短了）—— 断言写宽了会误报。
        """
        return [g for g in segs0
                if g["start"] >= s - 1e-6 and g["end"] <= e + 1e-6]
    counts = [(len(fully_inside(s, e)), i) for i, (s, e) in enumerate(keep0)]
    counts.sort(reverse=True)
    victim_idx = counts[0][1] if counts[0][0] > 0 else len(keep0) // 2
    victim = keep0[victim_idx]
    victim_segs = fully_inside(victim[0], victim[1])
    print(f"   完全落在这一段里的字幕有 {len(victim_segs)} 条：{[g['text'] for g in victim_segs]}")
    keep1 = [list(k) for i, k in enumerate(keep0) if i != victim_idx]
    new_total = sum(e - s for s, e in keep1)
    print(f"   删的是第 {victim_idx+1} 段 {victim}（{victim[1]-victim[0]:.2f}s）")
    print(f"   预计新时长：{keep_src:.2f}s → {new_total:.2f}s")

    payload = {"timeline": {"version": tl["timeline"]["version"],
                            "source": tl["timeline"]["source"],
                            "keep": keep1, "segments": segs0}}
    st, r = call("PUT", f"/api/timelines/{tl_id}", payload)
    print(f"   保存 HTTP {st}")
    if st != 200:
        raise SystemExit(f"保存失败：{r}")
    print(f"   保存后后端算的时长：{sum(e-s for s,e in r['timeline']['keep']):.2f}s")

    print("== 5) 重渲染（真 ffmpeg）==")
    st, r = call("POST", "/api/jobs", {"kind": "render_timeline", "asset_id": tl_id})
    print(f"   HTTP {st} {r}")
    rjob = wait_job(r["job_id"], label="渲染")
    if rjob.get("status") != "succeeded":
        raise SystemExit(f"渲染没成功：{rjob.get('status')} {rjob.get('error')}")

    print("== 6) 核对新成片 ==")
    _, assets = call("GET", "/api/assets?kind=video")
    news = [x for x in (assets.get("items") or []) if "按时间线渲染" in (x.get("name") or "")]
    if not news:
        raise SystemExit("渲染成功了但素材库里没有新成片")
    new_mp4 = ROOT / "data" / "e2e-timeline" / "media" / news[0]["rel_path"]
    new_dur = probe_duration(new_mp4)
    print(f"   新成片：{new_mp4}")
    print(f"   时长 {new_dur:.2f}s（预计 {new_total:.2f}s，容差 0.2s）")
    ok = abs(new_dur - new_total) <= 0.2
    print(f"   {'✓' if ok else '✗'} 时长对得上")
    if not ok:
        print(f"   ⚠️ 差了 {abs(new_dur - new_total):.2f}s")

    # 字幕：**落在被删区间里的必须消失**，其余必须还在（不能整批丢）
    srt = new_mp4.with_suffix(".srt")
    srt_text = srt.read_text(encoding="utf-8") if srt.is_file() else ""
    n_after = srt_text.count("-->")
    print(f"   字幕文件：{srt.name}  条目数 {n_after}（删之前 {len(segs0)}）")
    lost = [g["text"].strip() for g in victim_segs if g["text"].strip()]
    bad = 0
    for text in lost:
        n = srt_text.count(text)
        if n == 0:
            print(f"   ✓ 被删段落里的字幕「{text}」已消失")
        else:
            bad += 1
            print(f"   ✗ 被删段落里的字幕「{text}」还在（出现 {n} 次）—— 没跟着剪点走")
    kept_texts = [g["text"].strip() for g in segs0 if g not in victim_segs and g["text"].strip()]
    for text in kept_texts:
        n = srt_text.count(text)
        if n == 1:
            print(f"   ✓ 保留的字幕「{text}」恰好出现 1 次")
        else:
            bad += 1
            print(f"   ✗ 保留的字幕「{text}」出现 {n} 次（应当恰好 1 次）")
    print(f"   {'✓ 字幕与剪点一致' if bad == 0 else f'✗ 有 {bad} 处不一致'}")
    print(f"   新字幕全文：\n{srt_text.strip()}")

    print("\n结论：")
    print(f"  - 源 {src_dur:.2f}s → 粗剪 {old_dur}s → 删一段后重渲染 {new_dur:.2f}s")
    print(f"  - 时长变化与预计{'一致' if ok else '**不一致**'}")
    print("  （字幕是否错位需人工看片核对；见 docs/checklist_timeline.md §7）")


if __name__ == "__main__":
    main()
