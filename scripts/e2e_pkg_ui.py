"""发布包内验证：工作台 UI 全流程浏览器实测（FR-34 / FR-42 / FR-60 表述边界）。

与 `e2e_partial_browser.py` 的区别：那个测「部分失败保留」（FR-70），本脚本测
**发布包环境里的 UI 全流程**，重点是 **C7：刷新后历史稿的正文完整性** ——
它是 `docs/42` 修的「历史稿正文为空」缺陷的唯一端到端证据，此前只在**源码目录**验过。

用法（服务需已在运行；推荐用发布包自己的 start.sh 起）：
    python-service/.venv/Scripts/python.exe scripts/e2e_pkg_ui.py
    BASE=http://127.0.0.1:18080 python-service/.venv/Scripts/python.exe scripts/e2e_pkg_ui.py

依赖：`websockets`（在 python-service 的 venv 里）+ Edge。
"""
from __future__ import annotations

import asyncio
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
PORT = int(os.environ.get("CDP_PORT", "9223"))
BASE = os.environ.get("BASE", "http://127.0.0.1:8080")
SHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "outputs", "browser-shots")
RUN = time.strftime("%H%M%S")
EDITED_BODY = f"【人工编辑于{RUN}】这是浏览器实测改过的正文。"
results: list[tuple[str, bool]] = []


def check(label, cond, extra=""):
    results.append((label, bool(cond)))
    print(("  PASS " if cond else "  FAIL ") + label
          + (("  | " + str(extra)[:120]) if extra else ""))


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
        "--disable-gpu", "--window-size=1400,1100", "about:blank",
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
        r = await self.call("Runtime.evaluate",
                            {"expression": expr, "returnByValue": True})
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
        import base64
        p = os.path.join(SHOT_DIR, f"{name}.png")
        with open(p, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        return p


async def main():
    exe = find_edge()
    if not exe:
        print("找不到 Edge，无法执行浏览器实测")
        return 2
    profile = tempfile.mkdtemp(prefix="polyface-pkgui-")
    proc = launch_edge(exe, profile)
    try:
        wait_devtools()
        async with websockets.connect(page_ws_url(), max_size=20 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Runtime.enable")
            await cdp.call("Page.enable")
            print(f"===== 发布包内 UI 全流程实测 (BASE={BASE}, RUN={RUN}) =====")

            # ---------- C1 首页 ----------
            await cdp.call("Page.navigate", {"url": BASE})
            await cdp.wait_for("!!document.getElementById('btnAnalyze')", label="首页加载")
            ok = await cdp.ev("!!(document.getElementById('raw') && document.getElementById('btnAnalyze'))")
            check("C1 首页加载且关键元素存在", ok)

            # ---------- D1/D2 表述边界（docs/38 的前端改动是否在包内生效）----------
            disclaim = await cdp.ev("document.body.innerText.indexOf('不代表事实无误')>=0")
            info("免责说明", "存在" if disclaim else "未出现（生成后才渲染，稍后复查）")

            # ---------- C2 解析 -> 事实编辑器 ----------
            await cdp.ev("""(()=>{
              document.getElementById('raw').value='我2023年裸辞做自由职业，靠写作从月入0做到3万。每周复盘帮我找到好选题。';
              document.getElementById('title').value='PKG-UI-""" + RUN + """';
              document.getElementById('btnAnalyze').click();
            })()""")
            await cdp.wait_for("document.querySelectorAll('#factsEditor .fact').length>0",
                               label="事实编辑器出现")
            rows = await cdp.ev("document.querySelectorAll('#factsEditor .fact').length")
            check("C2 解析后出现事实编辑器且行数≥1", rows >= 1, f"rows={rows}")

            # ---------- C3 编辑 + 添加 ----------
            await cdp.ev("""(()=>{
              const t=document.querySelector('#factsEditor .fText');
              t.value='人工修改后的事实文本'; t.dispatchEvent(new Event('input',{bubbles:true}));
              document.getElementById('btnAddFact').click();
            })()""")
            await asyncio.sleep(0.4)
            rows2 = await cdp.ev("document.querySelectorAll('#factsEditor .fact').length")
            check("C3 编辑一条 + 添加一条 → 行数 +1", rows2 == rows + 1, f"{rows} → {rows2}")

            # ---------- C4 保存并确认 ----------
            await cdp.ev("""(()=>{
              const ts=document.querySelectorAll('#factsEditor .fText');
              ts[ts.length-1].value='新添加的一条事实';
              ts[ts.length-1].dispatchEvent(new Event('input',{bubbles:true}));
              document.getElementById('btnConfirmFacts').click();
            })()""")
            await cdp.wait_for("document.getElementById('analyzeStatus').textContent.indexOf('已确认')>=0",
                               label="事实确认")
            st = await cdp.ev("document.getElementById('analyzeStatus').textContent")
            check("C4 「保存并确认」→ 状态含「已确认」", "已确认" in st, st.strip()[:60])
            fstate = await cdp.ev("(document.getElementById('factsState')||{}).textContent||''")
            check("C8a 事实确认状态已渲染", "已确认" in fstate, fstate.strip()[:60])

            # ---------- C5 生成 ----------
            await cdp.ev("document.getElementById('btnGenerate').click()")
            await cdp.wait_for("document.getElementById('genStatus').textContent.indexOf('完成')>=0",
                               timeout=180, label="生成完成")
            gs = await cdp.ev("document.getElementById('genStatus').textContent")
            check("C5 生成完成", "完成" in gs, gs.strip()[:70])
            await cdp.wait_for("!!document.getElementById('edBody')", timeout=30, label="成稿面板")
            body0 = await cdp.ev("document.getElementById('edBody').value")
            check("C5 成稿正文非空", bool((body0 or "").strip()), f"len={len(body0 or '')}")

            # ---------- D1/D2/D3 表述边界 ----------
            badge = await cdp.ev("(document.querySelector('.qa-badge')||{}).textContent||''")
            check("D1 徽标为「未发现阻断问题」", "未发现阻断问题" in badge, badge.strip()[:60])
            disclaim2 = await cdp.ev("document.body.innerText.indexOf('不代表事实无误')>=0")
            check("D2 免责说明存在", disclaim2)
            tr = await cdp.ev("(document.getElementById('traceLine')||{}).textContent||''")
            check("D3 mock 下 traceLine 不误报失败调用", "存在失败调用" not in tr, tr.strip()[:70])
            info("traceLine", tr.strip()[:100])

            # ---------- C6 编辑 + 保存 ----------
            await cdp.ev("""(()=>{
              const b=document.getElementById('edBody');
              b.value='""" + EDITED_BODY + """';
              b.dispatchEvent(new Event('input',{bubbles:true}));
              const t=document.querySelector('.edTitle'); if(t) t.value='包内实测标题';
              document.getElementById('edTags').value='包内实测,闭环';
              document.getElementById('btnSaveDraft').click();
            })()""")
            await cdp.wait_for("document.getElementById('edState').textContent.indexOf('已保存')>=0",
                               label="保存完成")
            es = await cdp.ev("document.getElementById('edState').textContent")
            check("C6 修改并保存 → 状态含「已保存」", "已保存" in es, es.strip()[:60])
            await cdp.shot(f"pkg-ui-{RUN}")

            # ---------- C7 刷新后历史稿正文完整性（核心）----------
            await cdp.call("Page.navigate", {"url": BASE})
            await cdp.wait_for("document.querySelectorAll('#history .side-item').length>0",
                               timeout=60, label="历史列表")
            await cdp.ev("document.querySelector('#history .side-item').click()")
            await cdp.wait_for("!!document.getElementById('edBody')", timeout=60, label="历史稿正文")
            await asyncio.sleep(1.0)
            body_after = await cdp.ev("document.getElementById('edBody').value")
            check("C7 刷新后历史稿正文 = 编辑后内容（核心）",
                  (body_after or "").strip() == EDITED_BODY,
                  f"len={len(body_after or '')} 前30字={(body_after or '')[:30]!r}")
            fstate2 = await cdp.ev("(document.getElementById('factsState')||{}).textContent||''")
            rows3 = await cdp.ev("document.querySelectorAll('#factsEditor .fact').length")
            check("C8 刷新后事实确认状态与条数恢复",
                  "已确认" in fstate2 and rows3 == rows2, f"state={fstate2.strip()[:24]} rows={rows3}")
            await cdp.shot(f"pkg-ui-{RUN}-after-reload")

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
