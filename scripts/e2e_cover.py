"""封面成图（M6-1）端到端浏览器实测：真实稿件 → 点「生成封面」→ 看到图片。

覆盖的是**整条链路**：浏览器 → Java `/api/drafts/{id}/cover` → Python `/compose/cover`
→ 后台 gimpish 渲染 → `GET /api/media/**` 回图 → `<img>` 真的加载出来（naturalWidth>0）。

用法（服务需已启动；Python 侧要能定位 gimpish，见 README「生成封面需要装什么」）：
    python-service/.venv/Scripts/python.exe scripts/e2e_cover.py
    BASE=http://127.0.0.1:18080 python-service/.venv/Scripts/python.exe scripts/e2e_cover.py

依赖：`websockets`（在 python-service 的 venv 里）+ Edge。
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.request

import websockets

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
PORT = int(os.environ.get("CDP_PORT", "9224"))
BASE = os.environ.get("BASE", "http://127.0.0.1:8080")
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


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def launch_edge(exe, profile):
    return subprocess.Popen([
        exe, "--headless=new", f"--remote-debugging-port={PORT}",
        f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--window-size=1400,1200", "about:blank",
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


async def main():
    exe = find_edge()
    if not exe:
        print("找不到 Edge，无法执行浏览器实测")
        return 2
    profile = tempfile.mkdtemp(prefix="polyface-cover-")
    proc = launch_edge(exe, profile)
    try:
        wait_devtools()
        async with websockets.connect(page_ws_url(), max_size=20 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Runtime.enable")
            await cdp.call("Page.enable")
            print(f"===== 封面成图端到端实测 (BASE={BASE}, RUN={RUN}) =====")

            # ---------- 造一份真实素材并生成稿件 ----------
            await cdp.call("Page.navigate", {"url": BASE})
            await cdp.wait_for("!!document.getElementById('btnAnalyze')", label="首页加载")
            check("A1 首页加载", True)

            await cdp.ev("""(()=>{
              document.getElementById('raw').value=
                '我2023年裸辞做自由职业，靠写作从月入0做到3万。每周复盘帮我找到好选题。';
              document.getElementById('title').value='COVER-""" + RUN + """';
              document.getElementById('btnAnalyze').click();
            })()""")
            await cdp.wait_for("document.querySelectorAll('#factsEditor .fact').length>0",
                               label="事实编辑器")
            await cdp.ev("document.getElementById('btnConfirmFacts').click()")
            await cdp.wait_for("document.getElementById('analyzeStatus').textContent.indexOf('已确认')>=0",
                               label="事实确认")
            check("A2 素材解析 + 事实确认", True)

            await cdp.ev("document.getElementById('btnGenerate').click()")
            await cdp.wait_for("document.getElementById('genStatus').textContent.indexOf('完成')>=0",
                               timeout=180, label="生成完成")
            await cdp.wait_for("!!document.getElementById('btnCover')", timeout=30, label="封面按钮")
            check("A3 生成稿件并出现「生成封面」按钮", True)

            # ---------- 核心：点按钮 → 出图 ----------
            t0 = time.time()
            await cdp.ev("document.getElementById('btnCover').click()")
            await cdp.wait_for(
                "!!document.querySelector('#coverBox img') || "
                "document.getElementById('coverBox').innerText.indexOf('需手动处理')>=0",
                timeout=180, label="封面结果")
            elapsed = time.time() - t0

            box = await cdp.ev("document.getElementById('coverBox').innerText")
            has_img = await cdp.ev("!!document.querySelector('#coverBox img')")
            if has_img:
                # naturalWidth>0 才算**真的解码出来了**（仅 <img> 存在可能是死链）
                await cdp.wait_for(
                    "(document.querySelector('#coverBox img')||{}).naturalWidth>0",
                    timeout=30, label="图片解码")
                nw = await cdp.ev("document.querySelector('#coverBox img').naturalWidth")
                nh = await cdp.ev("document.querySelector('#coverBox img').naturalHeight")
                src = await cdp.ev("document.querySelector('#coverBox img').getAttribute('src')")
                check("B1 封面图真的加载出来（naturalWidth>0）", nw and nw > 0, f"{nw}x{nh}")
                check("B2 尺寸是平台画布（3:4 竖版）", (nw, nh) == (1080, 1440), f"{nw}x{nh}")
                check("B3 url 指向 /api/media/", (src or "").startswith("/api/media/"), src)
                check("B4 端到端耗时在预算内（<60s）", elapsed < 60, f"{elapsed:.1f}s")

                # 直接按 url 取字节，证明托管端给的是真 PNG（不是 404/HTML 兜底）
                with urllib.request.urlopen(BASE + src, timeout=20) as r:
                    body = r.read()
                    ctype = r.headers.get("Content-Type", "")
                check("B5 /api/media 回的是 image/png 且是真 PNG 字节",
                      ctype.startswith("image/png") and body[:8] == b"\x89PNG\r\n\x1a\n",
                      f"{ctype} {len(body)}B")
                info("coverBox", box.strip().replace("\n", " ")[:120])
            else:
                # 未安装 gimpish 时的降级路径：中性提示 + 安装指引，不是红色报错
                check("B1 未装 gimpish → 显示安装指引（降级非报错）",
                      "npm install -g gimpish" in (box or ""), box.strip()[:160])
                check("B2 降级时不显示红色错误样式",
                      "封面生成失败" not in (box or ""))

            await cdp.shot(f"cover-{RUN}")
            info("截图", os.path.join(SHOT_DIR, f"cover-{RUN}.png"))

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
