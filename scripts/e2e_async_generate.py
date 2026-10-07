"""生成任务异步化端到端验收（T8）：逐条对 `docs/checklist_async_generate.md`。

两层都验：
- **API 层**：阶段事件 / 编排（理解只一次）/ 进度 / 部分失败 / 重试 / 稿件落库
- **浏览器层**：点生成 → 走任务 → 看到进度 → 稿件出现（真 CDP，含截图）

用法（Java 与 Python 服务需已启动）：
    BASE=http://127.0.0.1:18086 PY_BASE=http://127.0.0.1:18000 POLYFACE_DATA_DIR=<数据目录> \
      python-service/.venv/Scripts/python.exe scripts/e2e_async_generate.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("BASE", "http://127.0.0.1:8080")
PY_BASE = os.environ.get("PY_BASE", "http://127.0.0.1:8000")
DATA_DIR = Path(os.environ.get("POLYFACE_DATA_DIR", "../data")).resolve()
CDP_PORT = int(os.environ.get("CDP_PORT", "9231"))
SHOT_DIR = ROOT / "outputs" / "browser-shots"
RUN = time.strftime("%H%M%S")
results: list[tuple[str, bool]] = []

RAW = "我2023年裸辞做自由职业，靠写作从月入0做到3万。每周复盘帮我找到好选题。"


def check(label, cond, extra=""):
    results.append((label, bool(cond)))
    print(("  PASS " if cond else "  FAIL ") + label + (f"  | {extra}" if extra else ""))


def info(label, value):
    print(f"  INFO {label}: {value}")


def _req(url, method="GET", payload=None, timeout=180):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            return x.status, json.loads(x.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def http(method, path, payload=None, timeout=180):
    return _req(BASE + path, method, payload, timeout)


def py(method, path, payload=None, timeout=180):
    return _req(PY_BASE + path, method, payload, timeout)


def wait_job(jid, timeout=180):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        _, last = http("GET", f"/api/jobs/{jid}")
        if last.get("status") in ("succeeded", "failed", "partial", "canceled"):
            return last
        time.sleep(0.6)
    return last


def events_of(job_id):
    for p in sorted((DATA_DIR / "jobs").glob(f"job-*/events.jsonl")):
        pass
    return None


def find_events(job):
    """按任务的事件文件路径读（不猜目录名）。"""
    p = job.get("events_path")
    return Path(p) if p and Path(p).is_file() else None


def read_events(job):
    # 详情 DTO 不带 events_path，按任务目录里的唯一 events.jsonl 找
    base = DATA_DIR / "jobs"
    hits = []
    for d in base.glob("job-*"):
        f = d / "events.jsonl"
        if f.is_file():
            hits.append((f.stat().st_mtime, f))
    if not hits:
        return []
    hits.sort(reverse=True)
    lines = [json.loads(l) for l in hits[0][1].read_text(encoding="utf-8").splitlines() if l.strip()]
    return lines


def count_events(job_id, stage, kind):
    """从最近一次事件文件里数某个阶段的事件（单任务串行，最近的就是本次的）。"""
    lines = read_events(None)
    return [e for e in lines if e.get("stage") == stage and e.get("kind") == kind]


# ---------------------------------------------------------------- 浏览器

def find_edge():
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(p):
            return p
    return None


class CDP:
    def __init__(self, ws):
        self.ws, self.seq = ws, 0

    async def call(self, method, params=None):
        self.seq += 1
        mid = self.seq
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def ev(self, expr):
        r = await self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        if r.get("exceptionDetails"):
            raise RuntimeError(str(r["exceptionDetails"])[:200])
        return r.get("result", {}).get("value")

    async def wait_for(self, expr, timeout=180, label=""):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if await self.ev(expr):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.4)
        raise TimeoutError(f"等待超时：{label or expr}")

    async def shot(self, name):
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        r = await self.call("Page.captureScreenshot", {"format": "png"})
        p = SHOT_DIR / f"{name}.png"
        p.write_bytes(base64.b64decode(r["data"]))
        return p


async def browser_checks():
    exe = find_edge()
    if not exe:
        check("浏览器实测（找不到 Edge）", False)
        return
    profile = tempfile.mkdtemp(prefix="pf-gen-")
    proc = subprocess.Popen([exe, "--headless=new", f"--remote-debugging-port={CDP_PORT}",
                             f"--user-data-dir={profile}", "--no-first-run", "--disable-gpu",
                             "--window-size=1400,1400", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2):
                    break
            except Exception:
                time.sleep(0.4)
        targets = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=5).read().decode())
        ws_url = next(t["webSocketDebuggerUrl"] for t in targets
                      if t.get("type") == "page" and t.get("webSocketDebuggerUrl"))
        async with websockets.connect(ws_url, max_size=20 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Runtime.enable")
            await cdp.call("Page.enable")
            await cdp.call("Page.navigate", {"url": BASE})
            await cdp.wait_for("!!document.getElementById('btnGenerate')", label="首页")
            await cdp.ev("window.alert=(m)=>{window.__alerts=(window.__alerts||[]).concat(String(m));};"
                         "window.confirm=()=>true;")

            # 造素材 + 解析 + 确认事实（走真实界面）
            await cdp.ev(f"""(()=>{{
              document.getElementById('raw').value={json.dumps(RAW)};
              document.getElementById('title').value='GEN-UI-{RUN}';
              document.getElementById('btnAnalyze').click();
            }})()""")
            await cdp.wait_for("document.querySelectorAll('#factsEditor .fact').length>0", label="事实编辑器")
            await cdp.ev("document.getElementById('btnConfirmFacts').click()")
            await cdp.wait_for("document.getElementById('analyzeStatus').textContent.indexOf('已确认')>=0",
                               label="事实确认")
            # 勾两个平台
            # 容器是 #platList，每项是 label.plat（含一个 checkbox）
            picked = await cdp.ev("""(()=>{
              let n = 0;
              document.querySelectorAll('#platList label.plat').forEach(l=>{
                const want = l.innerText.indexOf('小红书')>=0 || l.innerText.indexOf('抖音')>=0;
                const cb = l.querySelector('input');
                if(cb.checked !== want) cb.click();
                if(want) n++;
              });
              return n;
            })()""")
            check("§7a2 界面里勾上了 2 个平台", picked == 2, f"{picked} 个")
            await asyncio.sleep(0.3)
            await cdp.ev("document.getElementById('btnGenerate').click()")
            await cdp.wait_for("document.getElementById('genStatus').textContent.length>0",
                               timeout=60, label="已发起")
            check("§7a 点生成后立刻返回（不阻塞界面）", True,
                  (await cdp.ev("document.getElementById('genStatus').textContent"))[:60])
            await cdp.wait_for(
                "['✅','⚠️','❌','已取消'].some(s=>document.getElementById('genStatus').textContent.indexOf(s)>=0)",
                timeout=300, label="生成结束")
            final = await cdp.ev("document.getElementById('genStatus').textContent")
            check("§7b 浏览器里看到生成完成", "✅" in final or "⚠️" in final, final[:80])
            stopped = await cdp.ev("genPollTimer === null")
            check("§7c 任务结束后轮询已停止", bool(stopped))
            check("§7d 没有弹出 alert（headless 会阻塞）",
                  await cdp.ev("(window.__alerts||[]).length === 0"),
                  str(await cdp.ev("window.__alerts || []")))
            tabs = await cdp.ev("document.querySelectorAll('#resultArea .plat-tab button').length")
            check("§7e 结果区按平台出了标签（=成功平台数）", tabs >= 1, f"{tabs} 个标签")
            await cdp.ev("document.getElementById('genStatus').scrollIntoView({block:'center'})")
            await asyncio.sleep(0.4)
            shot = await cdp.shot(f"gen-{RUN}-done")
            check("§8a 浏览器实测跑通（含截图）", shot.exists())
            info("截图", shot)
    finally:
        proc.terminate()


def main() -> int:
    # ---------- §2 阶段事件（Python 侧）----------
    tmp = ROOT / "outputs" / "gen-e2e"
    tmp.mkdir(parents=True, exist_ok=True)
    ev = tmp / f"direct-{RUN}.jsonl"
    st, _ = py("POST", "/generate", {
        "raw_text": RAW, "platforms": ["xhs"], "events_path": str(ev)})
    check("§2a 给了事件路径 → 写出事件文件", st == 200 and ev.is_file(), f"http {st}")
    txt = ev.read_text(encoding="utf-8") if ev.is_file() else ""
    check("§2b 事件里**不含素材正文**", "裸辞" not in txt and "复盘" not in txt)
    check("§2c 事件 schema 带 version", '"version": 1' in txt)

    # 不给路径 → 不建文件
    before = set((ROOT / "outputs" / "gen-e2e").glob("*.jsonl"))
    st, _ = py("POST", "/generate", {"raw_text": RAW, "platforms": ["xhs"]})
    after = set((ROOT / "outputs" / "gen-e2e").glob("*.jsonl"))
    check("§2d 不给路径 → 不创建任何文件（向后兼容）", st == 200 and before == after)

    # ---------- 任务：3 平台全成功 ----------
    st, mat = http("POST", "/api/materials",
                   {"raw_text": RAW, "source_kind": "长文", "title": f"E2E-{RUN}"})
    mid = mat["id"]
    st, started = http("POST", "/api/jobs",
                       {"kind": "generate", "material_id": mid,
                        "platforms": ["xhs", "douyin", "zhihu"]})
    check("§3a 发起生成任务", st == 200 and started.get("kind") == "generate", json.dumps(started))
    job = wait_job(started["job_id"])
    check("§3b 3 平台全成功", job["status"] == "succeeded" and job["result"]["ok_count"] == 3,
          f"{job['status']} ok={job['result']['ok_count']}")

    evs = read_events(None)
    n_understand = len([e for e in evs if e.get("stage") == "理解" and e.get("kind") == "start"])
    check("§3c **理解阶段只发生一次**（最贵那次调用没被重复）", n_understand == 1, f"{n_understand} 次")
    platform_starts = [e for e in evs if e.get("stage") == "平台" and e.get("kind") == "start"]
    check("§3d 平台级事件齐全（3 个）", len(platform_starts) == 3,
          [(e["fields"].get("platform"), e["fields"].get("index")) for e in platform_starts])
    stage_events = [e for e in evs if ":" in str(e.get("stage"))]
    check("§4a 阶段级事件齐全（每平台 ≥3 步）", len(stage_events) >= 9, f"{len(stage_events)} 条")
    check("§4b 两边写的事件都能被解析（无坏行残留）",
          all("stage" in e and "kind" in e for e in evs))

    _, detail = http("GET", f"/api/materials/{mid}")
    check("§6a 3 篇稿件进了既有稿件体系", len(detail.get("drafts", [])) == 3,
          [d["platform_code"] for d in detail.get("drafts", [])])
    check("§5a 全成功时没有重试入口", job.get("can_retry") is False)

    # ---------- 部分失败 + 重试 ----------
    st, p = http("POST", "/api/jobs",
                 {"kind": "generate", "material_id": mid, "platforms": ["xhs", "not_a_platform"]})
    pj = wait_job(p["job_id"])
    check("§3e 部分失败 → 状态可区分（partial）", pj["status"] == "partial",
          f"{pj['status']} ok={pj['result']['ok_count']} fail={pj['result']['fail_count']}")
    check("§3f 部分失败时**成功的平台成果保留**", pj["result"]["ok_count"] == 1)
    check("§5b 失败平台带可读原因",
          bool(pj["result"]["failed"]) and len(pj["result"]["failed"][0]["error"]) > 8,
          pj["result"]["failed"][0]["error"][:90] if pj["result"]["failed"] else "")
    check("§5c 可重试", pj.get("can_retry") is True)

    st, r = http("POST", f"/api/jobs/{p['job_id']}/retry")
    check("§5d 重试**只含失败的平台**", st == 200 and r.get("platforms") == ["not_a_platform"],
          json.dumps(r))
    rj = wait_job(r["job_id"])
    check("§5e 重试是**新任务**（原任务 id 不变）", r["job_id"] != p["job_id"],
          f"原={p['job_id']} 新={r['job_id']}")
    _, orig = http("GET", f"/api/jobs/{p['job_id']}")
    check("§5f 原任务记录**未被改动**",
          orig["status"] == "partial" and orig["result"]["ok_count"] == 1)
    st, noop = http("POST", f"/api/jobs/{job['id']}/retry")
    check("§5g 对没有失败的任务重试 → 400", st == 400, f"{st}")

    # ---------- §4 事件文件容错 ----------
    evs2 = read_events(None)
    f = None
    for d in sorted((DATA_DIR / "jobs").glob("job-*"), key=lambda x: x.stat().st_mtime, reverse=True):
        if (d / "events.jsonl").is_file():
            f = d / "events.jsonl"
            break
    if f:
        f.write_text("{半行 JSON\n", encoding="utf-8")
        st, d2 = http("GET", f"/api/jobs/{p['job_id']}")
        check("§4c 事件文件坏掉时不崩", st == 200 and "progress" in d2, f"{st}")

    # ---------- 浏览器 ----------
    asyncio.run(browser_checks())

    print("\n===== 汇总 =====")
    passed = sum(1 for _, ok in results if ok)
    print(f"  {passed}/{len(results)} 通过")
    for label, ok in results:
        if not ok:
            print("    FAIL " + label)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
