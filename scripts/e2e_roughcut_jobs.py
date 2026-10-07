"""粗剪任务化端到端验收（T9）：逐条对 `docs/checklist_roughcut_jobs.md`。

两层都验：
- **API 层**：任务模型 / 执行 / 进度 / 取消 / 产物入素材库
- **浏览器层**：素材库发起 → 看到进度 → 完成 → 产物出现（真 CDP，含截图）

用法（Java 与 Python 服务需已启动）：
    BASE=http://127.0.0.1:18085 POLYFACE_DATA_DIR=<数据目录> \
      python-service/.venv/Scripts/python.exe scripts/e2e_roughcut_jobs.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / "python-service" / ".venv" / "Scripts" / "python.exe")
BASE = os.environ.get("BASE", "http://127.0.0.1:8080")
DATA_DIR = Path(os.environ.get("POLYFACE_DATA_DIR", "../data")).resolve()
MEDIA_DIR = DATA_DIR / "media"
CDP_PORT = int(os.environ.get("CDP_PORT", "9230"))
SHOT_DIR = ROOT / "outputs" / "browser-shots"
RUN = time.strftime("%H%M%S")
WORK = ROOT / "outputs" / "roughcut-jobs-e2e"
results: list[tuple[str, bool]] = []


def check(label, cond, extra=""):
    results.append((label, bool(cond)))
    print(("  PASS " if cond else "  FAIL ") + label + (f"  | {extra}" if extra else ""))


def info(label, value):
    print(f"  INFO {label}: {value}")


def http(method, path, payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def upload(path: Path, name: str):
    b = "----x" + uuid.uuid4().hex
    body = bytearray()
    body += f"--{b}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode()
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += path.read_bytes()
    body += f"\r\n--{b}\r\n".encode()
    body += b'Content-Disposition: form-data; name="name"\r\n\r\n'
    body += name.encode()
    body += f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(BASE + "/api/assets/upload", data=bytes(body),
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())


def wait_job(job_id: int, timeout=180):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        _, last = http("GET", f"/api/jobs/{job_id}")
        if last.get("status") in ("succeeded", "failed", "canceled"):
            return last
        time.sleep(1.0)
    return last


def make_png(dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=navy:s=120x120", "-frames:v", "1", str(dst)],
                   check=True, capture_output=True)
    return dst


# ---------------------------------------------------------------- 浏览器侧

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

    async def wait_for(self, expr, timeout=90, label=""):
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


async def browser_checks(asset_id: int):
    exe = find_edge()
    if not exe:
        check("浏览器实测（找不到 Edge）", False)
        return
    profile = tempfile.mkdtemp(prefix="pf-jobs-")
    proc = subprocess.Popen([exe, "--headless=new", f"--remote-debugging-port={CDP_PORT}",
                             f"--user-data-dir={profile}", "--no-first-run", "--disable-gpu",
                             "--window-size=1400,1500", "about:blank"],
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
            await cdp.wait_for("!!document.getElementById('assetList')", label="首页")
            # headless 下 alert() 会阻塞页面（本轮踩过）→ 换记录桩
            await cdp.ev("window.alert=(m)=>{window.__alerts=(window.__alerts||[]).concat(String(m));};"
                         "window.confirm=()=>true;")
            await cdp.wait_for("document.querySelectorAll('#assetList .side-item').length>0",
                               label="素材列表")
            await cdp.ev("loadAssets()")
            await asyncio.sleep(1.0)

            cut_btns = await cdp.ev("document.querySelectorAll('#assetList [data-cut]').length")
            check("§7a 素材库里视频行有「粗剪」按钮", cut_btns >= 1, f"{cut_btns} 个")
            non_video_cut = await cdp.ev(
                "Array.from(document.querySelectorAll('#assetList .side-item'))"
                ".filter(el=>el.innerText.indexOf('图片')>=0)"
                ".some(el=>!!el.querySelector('[data-cut]'))")
            check("§7b 非视频资产**没有**粗剪按钮", not non_video_cut)

            # 点**原始上传的那个视频**的按钮 —— 第一条往往是刚生成的成片，
            # 拿它再剪会"找不到停顿"（已经剪过了），那是另一条分支
            clicked = await cdp.ev("""(()=>{
              const rows = Array.from(document.querySelectorAll('#assetList .side-item'));
              const row = rows.find(el => el.innerText.indexOf('talking') >= 0 && el.querySelector('[data-cut]'));
              if(!row) return false;
              row.querySelector('[data-cut]').click();
              return true;
            })()""")
            check("§7b2 能定位到原始视频并点开粗剪", bool(clicked))
            await cdp.wait_for("document.getElementById('rcForm').style.display !== 'none'",
                               label="参数弹层")
            # ⚠️ 必须等**字段渲染出来**再断言：表单容器可见 ≠ 字段已填。
            # 而且 `[].every(...)` 恒为 true —— 不等的话这条会**空转通过**
            # （本轮就踩了：§7c 绿着，紧接着的点击却找不到元素）。
            await cdp.wait_for("document.querySelectorAll('#rcParams [data-p]').length >= 4",
                               timeout=30, label="参数项")
            n_fields = await cdp.ev("document.querySelectorAll('#rcParams [data-p]').length")
            filled = await cdp.ev("Array.from(document.querySelectorAll('#rcParams [data-p]'))"
                                  ".every(el=>String(el.value).length > 0 || el.type === 'checkbox')")
            check("§7c 参数弹层显示生效的默认值", bool(filled) and n_fields >= 4,
                  f"{n_fields} 项：" + str(await cdp.ev("document.getElementById('rcParams').innerText.replace(/\\n/g,' ')")))
            await cdp.ev("document.querySelector('#rcParams [data-p=pause_sec]').value='1.2'")
            await cdp.ev("document.getElementById('rcStart').click()")
            await cdp.wait_for("document.getElementById('rcActive').innerText.indexOf('任务 #')>=0",
                               timeout=60, label="任务面板")
            await asyncio.sleep(2.0)
            await cdp.shot(f"jobs-{RUN}-running")
            # ⚠️ 必须等**状态标签**，不能等"完成"二字：面板里还有一行
            # "已完成：探测 → 找停顿"，它会让人误判任务已结束（本轮就假通过了一次）。
            await cdp.wait_for("document.getElementById('rcActive').innerText.indexOf('✅ 完成')>=0",
                               timeout=300, label="任务完成")
            final_text = await cdp.ev("document.getElementById('rcActive').innerText")
            check("§7d 浏览器里看到任务完成（状态标签为完成）",
                  "✅ 完成" in final_text and "进行中" not in final_text,
                  final_text[:100].replace("\n", " "))
            # 轮询必须停：直接看句柄，比"数请求次数"更硬
            stopped = await cdp.ev("rcPollTimer === null")
            check("§7e 任务结束后轮询已停止", bool(stopped))
            check("§7f 没有弹出 alert（headless 会阻塞）",
                  await cdp.ev("(window.__alerts||[]).length === 0"),
                  str(await cdp.ev("window.__alerts || []")))
            await cdp.ev("document.getElementById('rcActive').scrollIntoView({block:'center'})")
            await asyncio.sleep(0.5)
            shot = await cdp.shot(f"jobs-{RUN}-done")
            info("截图", shot)
            check("§8a 浏览器实测跑通（含截图）", shot.exists())
    finally:
        proc.terminate()


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    fixture = WORK / "talking.mp4"
    subprocess.run([PY, str(ROOT / "scripts" / "make_fixture.py"), str(WORK)],
                   check=True, capture_output=True)
    video = upload(fixture, "talking.mp4")
    img = upload(make_png(WORK / "pic.png"), "pic.png")
    info("资产", f"video#{video['id']} / image#{img['id']}")

    # ---------- §3 执行 ----------
    st, bad = http("POST", "/api/jobs", {"kind": "roughcut", "asset_id": 999999})
    check("§3a 不存在的资产 → 404", st == 404, f"{st}")
    st, bad2 = http("POST", "/api/jobs", {"kind": "roughcut", "asset_id": img["id"]})
    check("§3b 非视频资产 → 400", st == 400, f"{st} {bad2.get('detail','')}")
    st, bad3 = http("POST", "/api/jobs", {"kind": "unknown", "asset_id": video["id"]})
    check("§3c 未知任务类型 → 400", st == 400)

    # ---------- §2/§4/§6 一次真跑 ----------
    st, started = http("POST", "/api/jobs",
                       {"kind": "roughcut", "asset_id": video["id"], "params": {"pause_sec": 1.0}})
    jid = started["job_id"]
    check("§2a 发起返回 job_id 且状态为排队/运行",
          st == 200 and started.get("status") in ("queued", "running"), json.dumps(started))

    saw_stage = set()
    saw_pct = False
    for _ in range(90):
        _, d = http("GET", f"/api/jobs/{jid}")
        p = d.get("progress", {})
        if p.get("stage"):
            saw_stage.add(p["stage"])
        if isinstance(p.get("pct"), (int, float)):
            saw_pct = True
            pct_stage = p.get("pct_stage")
        if d.get("status") in ("succeeded", "failed", "canceled"):
            break
        time.sleep(1.0)
    job = d
    check("§3d 任务真的跑完（退出码被正确解读）", job["status"] == "succeeded",
          f"{job['status']} {job.get('error','')[:80]}")
    # ⚠️ 不要断言"轮询期间看到 ≥3 个阶段"：探测/找停顿/抽音轨都不到 1 秒，
    # 1 秒轮询抓不到它们是**正常**的，那样写是脆的（本轮就假红了一次）。
    # 拆成两条确定性断言：
    #   §4a 轮询期间**确实看到过**阶段（证明"实时可见"这条链路通）
    #   §4c 最终记录里**阶段齐全**（证明事件被完整读到）
    check("§4a 轮询期间能实时看到阶段推进", len(saw_stage) >= 1, sorted(saw_stage))
    check("§4b 能报出百分比（来自事件文件）", saw_pct)
    done = job["progress"]["done_stages"]
    check("§4c 最终记录里阶段齐全（≥5）", len(done) >= 5, done)
    check("§4d 轮询看到的阶段都出现在最终记录里",
          saw_stage.issubset(set(done)), f"看到={sorted(saw_stage)} 记录={done}")

    outs = job.get("outputs", {})
    check("§6a 产物 url 齐全（成片/字幕/剪点）",
          all(k in outs for k in ("video_url", "srt_url", "cuts_url")), list(outs))
    for k, u in outs.items():
        with urllib.request.urlopen(BASE + u, timeout=30) as r:
            check(f"§6b {k} 真能取回字节", r.status == 200 and len(r.read()) > 0)

    _, assets = http("GET", "/api/assets?limit=20")
    gen = [a for a in assets["items"] if a["source"] == "generated"]
    check("§6c 产物自动进素材库（≥3 条）", len(gen) >= 3, [a["name"] for a in gen])
    linked = [a for a in gen if any(l["owner_kind"] == "asset" and l["owner_id"] == video["id"]
                                    for l in a["links"])]
    check("§6d 成片关联到源视频", bool(linked), json.dumps(linked[:1], ensure_ascii=False)[:120])

    # ---------- §3e 失败任务：退出码非 0 → 必须记失败且原因可读 ----------
    # 造一个"扩展名合法但内容不是视频"的文件：CLI 会在探测阶段失败并退出非 0。
    # 没有这条用例的话，"退出码被正确解读"根本无从验证（变异也抓不住）。
    bogus = WORK / "bogus.mp4"
    bogus.write_bytes(b"this is definitely not a video\n" * 20)
    bad_asset = upload(bogus, "bogus.mp4")
    st, started_bad = http("POST", "/api/jobs",
                           {"kind": "roughcut", "asset_id": bad_asset["id"]})
    bad_job = wait_job(started_bad["job_id"], timeout=90)
    check("§3e 坏输入 → 任务失败（退出码非 0 被识别）", bad_job["status"] == "failed",
          f"{bad_job['status']} {bad_job.get('error','')[:100]}")
    check("§3f 失败原因可读（不是一句'失败'）",
          bool(bad_job.get("error")) and len(bad_job["error"]) > 8,
          (bad_job.get("error") or "")[:120].replace("\n", " "))

    # ---------- §4 事件文件容错 ----------
    evs = sorted(MEDIA_DIR.glob("roughcut/job-*/events.jsonl"))
    if evs:
        evs[-1].write_text("{半行 JSON\n", encoding="utf-8")   # 故意弄坏
        st, d2 = http("GET", f"/api/jobs/{jid}")
        check("§4d 事件文件坏掉时不崩（进度未知即可）", st == 200 and "progress" in d2, f"{st}")
        evs[-1].unlink()
        st, d3 = http("GET", f"/api/jobs/{jid}")
        check("§4e 事件文件缺失时不崩", st == 200 and "progress" in d3, f"{st}")
    else:
        check("§4d 事件文件坏掉时不崩", False, "找不到事件文件")

    # ---------- §5 取消 ----------
    _, started2 = http("POST", "/api/jobs", {"kind": "roughcut", "asset_id": video["id"]})
    jid2 = started2["job_id"]
    time.sleep(2.5)
    _, h1 = http("GET", "/api/jobs/health")
    running_before = h1.get("running", [])
    _, c1 = http("POST", f"/api/jobs/{jid2}/cancel")
    check("§5a 取消返回 canceled", c1.get("canceled") is True, json.dumps(c1))
    time.sleep(1.5)
    _, h2 = http("GET", "/api/jobs/health")
    check("§5b 取消后**没有进程残留**", h2.get("running", []) == [] or jid2 not in h2.get("running", []),
          f"前={running_before} 后={h2.get('running')}")
    _, c2 = http("POST", f"/api/jobs/{jid2}/cancel")
    check("§5c 重复取消幂等（200）", c2.get("status") in ("canceled", "failed", "succeeded"),
          json.dumps(c2))
    _, c3 = http("POST", f"/api/jobs/{jid}/cancel")
    check("§5d 取消已结束的任务 → 200 且状态不变",
          c3.get("canceled") is False and c3.get("status") == "succeeded", json.dumps(c3))

    # ---------- §2 列表 ----------
    _, lst = http("GET", "/api/jobs?limit=20")
    ids = [j["id"] for j in lst["items"]]
    check("§2b 任务列表含刚跑的任务且新的在前", ids and ids[0] >= jid, ids[:5])

    # ---------- 浏览器 ----------
    asyncio.run(browser_checks(video["id"]))

    print("\n===== 汇总 =====")
    passed = sum(1 for _, ok in results if ok)
    print(f"  {passed}/{len(results)} 通过")
    for label, ok in results:
        if not ok:
            print("    FAIL " + label)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
