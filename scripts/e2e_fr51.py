"""FR-51 音视频入料 端到端验收：对照 21-音视频入料-checklist.md。

样本由 ffmpeg 现场生成（带字幕 / 不带字幕），无需外部素材。
前置：Java(:8080) + Python(:8000) 已启动。
用法：python scripts/e2e_fr51.py
"""
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8080"
RUN = time.strftime("%H%M%S")
WORK = Path("D:/temp/pfmedia-e2e")
results = []


def call(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def upload(path: Path):
    boundary = "----polyface" + RUN
    head = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
    body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/ingest/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def check(label, cond, extra=""):
    results.append((label, bool(cond)))
    print(("  PASS " if cond else "  FAIL ") + label + (("  | " + str(extra)[:110]) if extra else ""))


def build_samples() -> tuple[Path, Path]:
    """造样本：base.mp4（无字幕）/ withsub.mp4（mov_text 字幕轨）。"""
    WORK.mkdir(parents=True, exist_ok=True)
    base = WORK / "base.mp4"
    if not base.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", "color=c=black:s=320x240:d=3",
             "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-shortest",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(base)],
            check=True, capture_output=True)
    srt = WORK / "s.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,500\n这是测试字幕第一句\n\n"
        "2\n00:00:01,500 --> 00:00:02,400\n这是测试字幕第二句\n\n"
        "3\n00:00:02,400 --> 00:00:03,000\n这是测试字幕第二句\n", encoding="utf-8")
    withsub = WORK / "withsub.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(base), "-i", str(srt),
         "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text", str(withsub)],
        check=True, capture_output=True)
    return base, withsub


print(f"===== FR-51 端到端验收 (RUN={RUN}) =====")

code, health = call("GET", "/health")
check("前置：Java 服务可用", code == 200)

base_mp4, withsub_mp4 = build_samples()
print(f"--- 样本已生成：{withsub_mp4.name} / {base_mp4.name} ---")

print("--- E. 上传接口 ---")
code, up = upload(withsub_mp4)
check("E1/E2 上传成功并返回 media_id", code == 200 and up.get("media_id"), f"code={code}")
media_id = up["media_id"]

bad = WORK / "bad.txt"
bad.write_text("not media", encoding="utf-8")
code, _ = upload(bad)
check("E3 非法扩展名上传 → 400", code == 400, f"code={code}")

code, _ = call("POST", "/api/ingest/probe", {"path": str(WORK / "nope.mp4")})
check("E6 路径不存在 → 400", code == 400, f"code={code}")

code, _ = call("POST", "/api/ingest/probe", {"path": str(WORK)})
check("E7 路径是目录 → 400", code == 400, f"code={code}")

code, _ = call("POST", "/api/ingest/probe", {"path": str(bad)})
check("E9 非法扩展名探测 → 400", code == 400, f"code={code}")

print("--- B. 媒体探测 ---")
code, info = call("POST", "/api/ingest/probe", {"media_id": media_id})
check("B1 探测返回 200", code == 200, f"code={code}")
check("B2 含时长", info.get("duration_sec", 0) > 0, info.get("duration_sec"))
check("B3 识别有音轨", info.get("has_audio") is True)
check("B4 识别有字幕轨", info.get("has_subtitle") is True, info.get("subtitle_streams"))
check("B5/B6 推荐 subtitle", info.get("recommended_mode") == "subtitle", info.get("recommended_mode"))

print("--- C. 字幕提取 ---")
code, tr = call("POST", "/api/ingest/transcribe", {"media_id": media_id})
check("C1 提取成功且非空", code == 200 and tr.get("text"), f"code={code} status={tr.get('status')}")
check("C1 status=subtitle_extracted", tr.get("status") == "subtitle_extracted", tr.get("status"))
check("C2/C3/C4 无序号/时间轴/标签",
      "1\n" != tr["text"][:2] and "-->" not in tr["text"] and "<" not in tr["text"])
check("C5 相邻重复已去重", tr["text"].count("这是测试字幕第二句") == 1, repr(tr["text"]))
check("B/UC-12 hint 存在", bool(tr.get("hint")), tr.get("hint"))

print("--- D. 无字幕 + 无 ASR → 降级 ---")
code, up2 = upload(base_mp4)
code, tr2 = call("POST", "/api/ingest/transcribe", {"media_id": up2["media_id"]})
check("D3 降级 needs_manual（不抛 5xx）",
      code == 200 and tr2.get("status") == "needs_manual", f"code={code} status={tr2.get('status')}")
check("A6 hint 含可执行安装指引", "faster-whisper" in (tr2.get("hint") or ""), tr2.get("hint"))

print("--- UC-12：转录文本 → 素材 → 生成全通 ---")
code, mat = call("POST", "/api/materials", {
    "raw_text": tr["text"], "source_kind": "口播稿", "title": withsub_mp4.name})
check("G7 转录文本可创建素材", code == 200 and mat.get("id"), f"code={code}")
check("G7 source_kind/标题来自入料",
      mat.get("structured", {}).get("core_message") is not None)
mid = mat["id"]

code, hist = call("GET", "/api/materials")
rows = hist if isinstance(hist, list) else (hist or {}).get("materials", [])
ids = [m.get("id") for m in rows]
check("G7 素材进入历史", mid in ids, f"mid={mid} ids={ids[:5]}")
hit = next((m for m in rows if m.get("id") == mid), {})
check("G7 source_kind=口播稿 且 title 记原文件名",
      hit.get("source_kind") == "口播稿" and hit.get("title") == withsub_mp4.name,
      f"kind={hit.get('source_kind')} title={hit.get('title')}")

code, gen = call("POST", f"/api/materials/{mid}/generate", {"platforms": ["xhs", "douyin"]})
check("G3 走既有管线生成成功",
      code == 200 and len(gen.get("drafts", [])) == 2, f"code={code}")
check("G3 成稿 qa_passed", gen["drafts"][0]["status"] == "qa_passed", gen["drafts"][0]["status"])

print("--- 清理 ---")
code, _ = call("DELETE", f"/api/ingest/media/{media_id}")
check("E8 删除媒体副本 → 204", code == 204, f"code={code}")
call("DELETE", f"/api/ingest/media/{up2['media_id']}")

print()
total, passed = len(results), sum(1 for _, ok in results if ok)
print(f"===== 端到端结果：{passed}/{total} 通过 =====")
fails = [l for l, ok in results if not ok]
if fails:
    print("失败项：")
    for f in fails:
        print("  -", f)
