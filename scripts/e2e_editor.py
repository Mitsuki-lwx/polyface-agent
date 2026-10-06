"""M6-2 端到端实测：工作台内嵌编辑器（iframe）→ 真浏览器渲染 → 改动能落回 scene.json → 停进程。

覆盖的链路：
    浏览器点「在编辑器中打开」→ Java 拉起受管的 `gimpish serve`
    → 工作台用 <iframe> 内嵌它的编辑器页面 → 用它的 HTTP API 改一处
    → **回读 scene.json 校验**（因为 gimpish 的 transform 会静默忽略非法字段仍返回 ok）
    → 收起 toggle → `POST /api/editor/stop` 后端口释放 → 端口被占时降级

诚实边界：iframe 与工作台**不同源**，脚本**读不到它内部的 DOM**（同源策略）。
因此"编辑器真的渲染出来了"是在**另一个导航**里验证的，不是靠 iframe 内部断言。

两种模式：
  普通：脚本自带，覆盖 A~G
  降级（`POLYFACE_EXPECT_MANUAL=1`，需把 Java 的 gimpish 路径指到不存在处）：覆盖 H —— 
        gimpish 没装时封面走 needs_manual，但**编辑器入口仍在**，点了给安装指引

用法（服务需已启动）：
    BASE=http://127.0.0.1:18080 POLYFACE_DATA_DIR=<数据目录> POLYFACE_EDITOR_PORT=18765 \
      python-service/.venv/Scripts/python.exe scripts/e2e_editor.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
PORT = int(os.environ.get("CDP_PORT", "9225"))
BASE = os.environ.get("BASE", "http://127.0.0.1:8080")
DATA_DIR = Path(os.environ.get("POLYFACE_DATA_DIR", "../data")).resolve()
EDITOR_PORT = int(os.environ.get("POLYFACE_EDITOR_PORT", "8765"))
EXPECT_MANUAL = os.environ.get("POLYFACE_EXPECT_MANUAL") == "1"
SHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "outputs", "browser-shots")
RUN = time.strftime("%H%M%S")
results: list[tuple[str, bool]] = []


def check(label, cond, extra=""):
    results.append((label, bool(cond)))
    print(("  PASS " if cond else "  FAIL ") + label
          + (("  | " + str(extra)[:160]) if extra else ""))


def info(label, value):
    print(f"  INFO {label}: {value}")


def http_json(method, url, payload=None, timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


def editor_reachable():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{EDITOR_PORT}/api/scene", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def launch_edge(exe, profile):
    return subprocess.Popen([
        exe, "--headless=new", f"--remote-debugging-port={PORT}",
        f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--window-size=1400,1300", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_devtools(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(0.5)
    raise TimeoutError("Edge 调试端口未就绪")


def page_ws_url():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=5) as r:
        targets = json.loads(r.read().decode())
    for t in targets:
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return t["webSocketDebuggerUrl"]
    raise RuntimeError("找不到 page target")


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

    async def wait_for(self, expr, timeout=60, label=""):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if await self.ev(expr):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.3)
        raise TimeoutError(f"等待超时：{label or expr}")

    async def shot(self, name):
        os.makedirs(SHOT_DIR, exist_ok=True)
        r = await self.call("Page.captureScreenshot", {"format": "png"})
        p = os.path.join(SHOT_DIR, f"{name}.png")
        with open(p, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        return p


async def prepare_draft_with_cover(cdp):
    """造素材 → 确认事实 → 生成稿件 → 生成封面；返回封面响应（在页面里读回来的）。"""
    await cdp.call("Page.navigate", {"url": BASE})
    await cdp.wait_for("!!document.getElementById('btnAnalyze')", label="首页加载")
    await cdp.ev("""(()=>{
      document.getElementById('raw').value='我2023年裸辞做自由职业，靠写作从月入0做到3万。';
      document.getElementById('title').value='EDITOR-""" + RUN + """';
      document.getElementById('btnAnalyze').click();
    })()""")
    await cdp.wait_for("document.querySelectorAll('#factsEditor .fact').length>0", label="事实编辑器")
    await cdp.ev("document.getElementById('btnConfirmFacts').click()")
    await cdp.wait_for("document.getElementById('analyzeStatus').textContent.indexOf('已确认')>=0", label="事实确认")
    await cdp.ev("document.getElementById('btnGenerate').click()")
    await cdp.wait_for("document.getElementById('genStatus').textContent.indexOf('完成')>=0",
                       timeout=180, label="生成完成")
    await cdp.wait_for("!!document.getElementById('btnCover')", label="封面按钮")
    await cdp.ev("document.getElementById('btnCover').click()")
    await cdp.wait_for("!!document.getElementById('btnOpenEditor')", timeout=180, label="编辑器入口")


async def run_degraded(cdp):
    """gimpish 没装：封面降级，但编辑器入口仍在，点了把原因讲清楚。"""
    await prepare_draft_with_cover(cdp)
    box = await cdp.ev("document.getElementById('coverBox').innerText")
    check("H1 封面降级为 needs_manual（不是报错）", "需手动处理" in (box or ""), (box or "").strip()[:120])
    check("H2 降级时**编辑器入口仍在**（能力缺失≠功能消失）",
          await cdp.ev("!!document.getElementById('btnOpenEditor')"))

    await cdp.ev("document.getElementById('btnOpenEditor').click()")
    await cdp.wait_for("document.getElementById('editorBox').innerText.length>0", timeout=30, label="编辑器提示")
    ed = await cdp.ev("document.getElementById('editorBox').innerText")
    check("H3 点了给可执行的安装指引", "npm install -g gimpish" in (ed or ""), (ed or "").strip()[:140])
    check("H4 不显示为红色报错", "启动失败" not in (ed or ""))
    await cdp.shot(f"editor-{RUN}-degraded")


async def run_normal(cdp):
    await prepare_draft_with_cover(cdp)
    check("A1 稿件生成 + 封面生成 + 出现「在编辑器中打开」", True)

    t0 = time.time()
    await cdp.ev("document.getElementById('btnOpenEditor').click()")
    await cdp.wait_for("!!document.querySelector('#editorBox iframe') || "
                       "document.getElementById('editorBox').innerText.indexOf('需手动处理')>=0",
                       timeout=60, label="编辑器结果")
    boot = time.time() - t0

    if not await cdp.ev("!!document.querySelector('#editorBox iframe')"):
        box = await cdp.ev("document.getElementById('editorBox').innerText")
        check("B1 装了 gimpish 却未出 iframe → 显示指引", "npm install -g gimpish" in (box or ""),
              (box or "").strip()[:160])
        await cdp.shot(f"editor-{RUN}-unexpected-degraded")
        return

    src = await cdp.ev("document.querySelector('#editorBox iframe').getAttribute('src')")
    check("B1 iframe 已内嵌且指向编辑器", (src or "").startswith("http://127.0.0.1:"), src)
    check("B2 冷启动耗时在预算内（<30s）", boot < 30, f"{boot:.1f}s")
    await asyncio.sleep(2.0)
    await cdp.ev("document.querySelector('#editorBox iframe').scrollIntoView({block:'center'})")
    await asyncio.sleep(0.5)
    await cdp.shot(f"editor-{RUN}")

    st, body = http_json("GET", BASE + "/api/editor/status")
    check("C1 /api/editor/status：running 且 url 非空",
          st == 200 and body.get("running") and body.get("url"), json.dumps(body, ensure_ascii=False)[:150])
    check("C2 状态端口与配置一致", body.get("port") == EDITOR_PORT, body.get("port"))
    scene_dir = Path(body.get("scene", "") or ".")
    check("C3 scene 指向媒体目录内的封面文档目录",
          scene_dir.is_dir() and (scene_dir / "scene.json").is_file(), str(scene_dir))
    rel_dir = scene_dir.relative_to(DATA_DIR / "media").as_posix() if scene_dir.is_dir() else ""

    before = json.loads((scene_dir / "scene.json").read_text(encoding="utf-8"))
    layer = next((l for l in before["layers"] if l["type"] == "text"), None)
    if layer:
        old_x = layer["text"]["x"]
        http_json("POST", body["url"] + f"/api/layer/{layer['id']}/transform", {"dx": 30, "dy": 0})
        after = json.loads((scene_dir / "scene.json").read_text(encoding="utf-8"))
        new_x = next(l for l in after["layers"] if l["id"] == layer["id"])["text"]["x"]
        check("D1 编辑器的改动静默落回同一个 scene.json（回读校验）",
              new_x == old_x + 30, f"x {old_x} → {new_x}")
    else:
        check("D1 找到可编辑的文本层", False, "scene 里没有 text 层")

    # 收起 toggle：再点一次应移除 iframe 并把按钮文案还原
    await cdp.ev("document.getElementById('btnOpenEditor').click()")
    await cdp.wait_for("!document.querySelector('#editorBox iframe')", timeout=20, label="收起")
    label = await cdp.ev("document.getElementById('btnOpenEditor').textContent")
    check("D2 再次点击可收起（iframe 移除 + 文案还原）",
          "在编辑器中打开" in (label or ""), (label or "").strip())

    await cdp.call("Page.navigate", {"url": body["url"]})
    await cdp.wait_for("document.title.indexOf('gimpish')>=0", timeout=30, label="编辑器页面")
    await cdp.wait_for("(document.getElementById('root')||{}).childElementCount>0",
                       timeout=30, label="编辑器挂载")
    check("E1 编辑器页面在真浏览器里渲染（#root 已挂载）", True, await cdp.ev("document.title"))

    st, stop = http_json("POST", BASE + "/api/editor/stop", {})
    await asyncio.sleep(1.5)
    check("F1 /api/editor/stop 返回 stopped", st == 200 and stop.get("status") == "stopped", str(stop))
    check("F2 停后端口已释放（不再可达）", not editor_reachable())
    st, stop2 = http_json("POST", BASE + "/api/editor/stop", {})
    check("F3 stop 幂等（再停仍 200 not_running）",
          st == 200 and stop2.get("status") == "not_running", str(stop2))

    # 端口被占：真占住编辑器端口，再 open → 应**快速**降级，而不是僵住或 5xx
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", EDITOR_PORT))
        s.listen(1)
        t1 = time.time()
        try:
            st, occ = http_json("POST", BASE + "/api/editor/open", {"path": rel_dir}, timeout=90)
        finally:
            s.close()
        took = time.time() - t1
    check("G1 端口被占 → 200 + needs_manual（不是 5xx）",
          st == 200 and occ.get("status") == "needs_manual", f"{st} {json.dumps(occ, ensure_ascii=False)[:100]}")
    check("G2 端口被占时**前置探测**快速降级（<5s，不等 15s 健康预算）", took < 5, f"{took:.1f}s")
    check("G3 降级带可执行提示（点名端口）",
          str(EDITOR_PORT) in (occ.get("hint") or ""), (occ.get("hint") or "")[:120])


async def main():
    exe = find_edge()
    if not exe:
        print("找不到 Edge，无法执行浏览器实测")
        return 2
    profile = tempfile.mkdtemp(prefix="polyface-editor-")
    proc = launch_edge(exe, profile)
    try:
        wait_devtools()
        async with websockets.connect(page_ws_url(), max_size=20 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Runtime.enable")
            await cdp.call("Page.enable")
            mode = "降级（gimpish 未装）" if EXPECT_MANUAL else "正常"
            print(f"===== 内嵌编辑器端到端实测 [{mode}] (BASE={BASE}, RUN={RUN}) =====")

            if EXPECT_MANUAL:
                await run_degraded(cdp)
            else:
                await run_normal(cdp)

            print("\n===== 汇总 =====")
            passed = sum(1 for _, ok in results if ok)
            print(f"  {passed}/{len(results)} 通过")
            for label, ok in results:
                if not ok:
                    print("    FAIL " + label)
            return 0 if passed == len(results) else 1
    finally:
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
