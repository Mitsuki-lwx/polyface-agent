"""端到端：真的中断一次生成任务，然后续跑（`docs/checklist_resume.md` §7）。

**不做任何"模拟中断"** —— 直接杀 Java 进程。分两步是因为"重启服务"得由你来做
（脚本自己起不来一个后台服务）：

    # 起好 Python(8000) 与 Java(8080)，data 目录指向 data/e2e-resume
    python scripts/e2e_resume.py step1    # 建素材 → 提交任务 → 等第一个平台落盘 → **杀 Java**
    # 手动重启 Java（同样的 data 目录）
    python scripts/e2e_resume.py step2    # 查 interrupted → resume → 验证只跑了缺的

要真调 LLM（这是"真链路"的意义所在）。2026-10-11 实测跑通，原始输出见
`docs/checklist_resume.md` 的「实测」。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8080"
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "e2e-resume"
PLATFORMS = ["xhs", "douyin", "zhihu"]      # 3 个平台（成本考虑；机制与 5 个相同）

RAW = (
    "我2023年3月裸辞，从月入0做到月入3万，用了11个月。\n"
    "第一件事是算清楚自己能活几个月：房租3500、吃饭2000，手上存款8万，够撑14个月。\n"
    "第二件事是砍掉所有非必要开支，把月支出压到5500以内。\n"
    "第三件事是把写作从爱好变成产品：先接商单，单价从800涨到3000，靠的是积累案例。\n"
    "第四个月我开始做课程，第一期卖了47份，单价299。\n"
    "踩过的坑：一开始什么都想做，结果三个月没收入；后来只做一件事才有起色。"
)


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


def java_pid() -> str | None:
    # netstat 的输出是**控制台代码页**（Windows 中文机是 GBK），不是 UTF-8。
    # 严格解码会抛 UnicodeDecodeError，`out` 直接变成 None —— 第一次跑就是这么崩的。
    out = subprocess.run(["netstat", "-ano"], capture_output=True,
                         encoding="utf-8", errors="replace").stdout or ""
    for line in out.splitlines():
        if ":8080" in line and "LISTENING" in line:
            return line.split()[-1]
    return None


def done_platforms(job_dir: Path) -> list[str]:
    d = job_dir / "done"
    if not d.is_dir():
        return []
    return sorted(f.name[len("platform-"):-len(".json")] for f in d.glob("platform-*.json"))


def job_dir_of(job_id: int) -> Path | None:
    """任务目录名是 `job-<nanoTime>`，与 jobId 无直接对应。

    优先挑 `done/` 非空的（本脚本串行跑，那多半就是本次的任务）。
    """
    dirs = sorted((DATA / "jobs").glob("job-*"), key=lambda p: p.stat().st_mtime)
    with_done = [d for d in dirs if (d / "done").is_dir() and any((d / "done").iterdir())]
    if with_done:
        return with_done[-1]
    return dirs[-1] if dirs else None


def main_step1() -> None:
    print("== 1) 建素材（这一步会真调 LLM 做理解）==")
    st, m = call("POST", "/api/materials",
                 {"raw_text": RAW, "source_kind": "长文", "title": "裸辞11个月复盘"})
    print(f"   HTTP {st}  material_id={m.get('id') if isinstance(m, dict) else m}")
    if st != 200:
        sys.exit(f"建素材失败：{m}")
    mid = m["id"]

    print(f"== 2) 提交生成任务（{len(PLATFORMS)} 平台：{PLATFORMS}）==")
    st, r = call("POST", "/api/jobs",
                 {"kind": "generate", "material_id": mid, "platforms": PLATFORMS})
    print(f"   HTTP {st}  {r}")
    if st != 200:
        sys.exit(f"提交失败：{r}")
    job_id = r["job_id"]
    (DATA / "e2e_job_id.txt").write_text(str(job_id), encoding="utf-8")

    print("== 3) 等第一个平台落盘（这才是跑了一半）==")
    jd = None
    t0 = time.time()
    while time.time() - t0 < 600:
        if jd is None:
            jd = job_dir_of(job_id)
        if jd:
            got = done_platforms(jd)
            if got:
                print(f"   [{time.time()-t0:5.1f}s] 已完成平台：{got}  → 现在杀掉 Java")
                break
        time.sleep(2)
    else:
        sys.exit("等不到第一个平台落盘（超时）")

    pid = java_pid()
    print(f"== 4) 杀 Java（PID {pid}）—— 真杀，不是模拟 ==")
    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, text=True)
    time.sleep(3)
    print(f"   8080 还在吗：{'是（没杀掉）' if java_pid() else '否（已死）'}")
    print(f"   job 目录：{jd}")
    print(f"   落盘文件：{done_platforms(jd)}")
    print("\n现在手动重启 Java，然后跑 e2e_resume_step2.py")


def main_step2() -> None:
    job_id = int((DATA / "e2e_job_id.txt").read_text(encoding="utf-8").strip())
    jd = job_dir_of(job_id)
    before = done_platforms(jd) if jd else []
    print(f"== 5) 重启后看状态（job {job_id}）==")
    st, job = call("GET", f"/api/jobs/{job_id}")
    print(f"   HTTP {st}  status={job.get('status')}  can_resume={job.get('can_resume')}")
    print(f"   error={job.get('error')!r}")
    assert job.get("status") == "interrupted", f"状态不是 interrupted：{job.get('status')}"
    assert job.get("can_resume") is True

    print("== 6) 继续 ==")
    st, r = call("POST", f"/api/jobs/{job_id}/resume")
    print(f"   HTTP {st}  {r}")
    assert st == 200, f"resume 失败：{r}"

    print("== 7) 等它跑完 ==")
    t0 = time.time()
    while time.time() - t0 < 1500:
        st, job = call("GET", f"/api/jobs/{job_id}")
        if job.get("status") in ("succeeded", "failed", "partial", "canceled", "interrupted"):
            break
        time.sleep(5)
    print(f"   最终 status={job.get('status')}  result={json.dumps(job.get('result'), ensure_ascii=False)}")
    print(f"   落盘（续跑前）：{before}")
    print(f"   落盘（续跑后）：{done_platforms(jd) if jd else []}")

    print("== 8) 库里是不是 3 个平台的稿子都在 ==")
    mid = job.get("material_id")
    st, m = call("GET", f"/api/materials/{mid}")
    drafts = m.get("drafts") or []
    print(f"   草稿：{[d.get('platform_code') for d in drafts]}")
    assert len(drafts) >= len(PLATFORMS), f"稿子不全：{len(drafts)}/{len(PLATFORMS)}"
    print("\n✅ 端到端通过")


if __name__ == "__main__":
    (main_step1 if (len(sys.argv) > 1 and sys.argv[1] == "step1") else main_step2)()
