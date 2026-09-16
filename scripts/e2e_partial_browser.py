"""长任务预算与部分失败保留 —— 浏览器实测（Edge / CDP）。

为什么自己写 CDP 驱动：本机为 Windows，内置浏览器自动化能力不支持 Windows，
沿用项目既有做法（docs/38、docs/42 的 Edge/CDP 实测）。

验证目标（docs/45 checklist E1~E6）：
  E1 默认只勾选单平台
  E2 勾选 >1 时提示预计耗时
  E3 生成后显示「成功 X / 失败 Y」
  E4 失败平台与原因可见
  E5 「重试失败平台」只重跑失败平台
  E6 超时/异常场景有可读提示

说明：E3~E5 需要一个「有失败」的响应。后端部分失败路径已由单元测试覆盖
（Python 4 项 + Java 4 项），此处**不往生产代码里塞测试钩子**，改为在页面内
替换 `window.j` 返回构造响应 —— 这样走的是**真实的 renderFailures / 按钮绑定**代码路径。

前置：Java(:8080) + Python(:8000) 已启动。
用法：python scripts/e2e_partial_browser.py
"""
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
PORT = 9222
BASE = "http://127.0.0.1:8080"
SHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs", "browser-shots")

results = []


def check(label, cond, extra=""):
    results.append((label, bool(cond)))
    print(("  PASS " if cond else "  FAIL ") + label + (("  | " + str(extra)[:110]) if extra else ""))


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.seq = 0

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

    async def ev(self, expr, await_promise=False):
        r = await self.call("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": await_promise,
        })
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("text") or str(r["exceptionDetails"]))
        return r.get("result", {}).get("value")

    async def wait_for(self, expr, timeout=30, label=""):
        """轮询 JS 表达式直到为真。"""
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
        path = os.path.join(SHOT_DIR, name)
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        return path


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def launch_edge(exe, profile):
    return subprocess.Popen([
        exe, "--headless=new", f"--remote-debugging-port={PORT}",
        f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--window-size=1400,1000", "about:blank",
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


async def main():
    exe = find_edge()
    if not exe:
        print("找不到 Edge，无法执行浏览器实测")
        return 2
    profile = tempfile.mkdtemp(prefix="polyface-cdp-")
    proc = launch_edge(exe, profile)
    try:
        wait_devtools()
        async with websockets.connect(page_ws_url(), max_size=20 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Runtime.enable")
            await cdp.call("Page.enable")

            print(f"===== 部分失败保留 浏览器实测 (Edge/CDP) =====")

            await cdp.call("Page.navigate", {"url": BASE})
            await cdp.wait_for("!!document.getElementById('btnGenerate')", label="页面加载")
            await cdp.wait_for("document.querySelectorAll('#platList input').length>0", label="平台列表渲染")
            await cdp.wait_for("document.getElementById('modeBadge').textContent.indexOf('检测中')<0",
                               label="运行模式徽章")
            mode = await cdp.ev("document.getElementById('modeBadge').textContent")
            print(f"     · 运行模式徽章：{mode.strip()}")

            # ---------- E1 默认只勾选单平台 ----------
            checked = await cdp.ev("document.querySelectorAll('#platList input:checked').length")
            total = await cdp.ev("document.querySelectorAll('#platList input').length")
            first = await cdp.ev(
                "(()=>{const i=document.querySelector('#platList input');"
                "return {value:i.value, checked:i.checked};})()")
            check("E1 默认只勾选 1 个平台", checked == 1, f"勾选 {checked} / 共 {total}")
            check("E1 默认勾选的是首个平台", first.get("checked") and first.get("value") == "xhs",
                  f"{first}")
            # 已知小瑕疵：程序化 checked 不触发 change，label 拿不到 .checked 类（无高亮）
            has_cls = await cdp.ev(
                "document.querySelector('#platList label').classList.contains('checked')")
            check("E1b 默认勾选的平台有高亮样式", has_cls,
                  "程序化 checked 已同步 label 的 .checked 类")

            # ---------- 走真实链路建素材 ----------
            await cdp.ev("""(()=>{
              document.getElementById('raw').value='我2023年裸辞做自由职业，靠写作从月入0做到3万。';
              document.getElementById('title').value='BROWSER-E2E';
              document.getElementById('btnAnalyze').click();
              return true;})()""")
            await cdp.wait_for("!document.getElementById('btnGenerate').disabled",
                               timeout=90, label="素材解析完成")
            mid = await cdp.ev("currentMaterialId")
            check("建素材（真实链路）成功", bool(mid), f"materialId={mid}")

            # ---------- 真实生成：单平台 ----------
            await cdp.ev("document.getElementById('btnGenerate').click()")
            await cdp.wait_for(
                "document.getElementById('genStatus').textContent.indexOf('完成')>=0",
                timeout=180, label="生成完成")
            st = await cdp.ev("document.getElementById('genStatus').textContent")
            check("E3 真实生成显示成功计数", "成功" in st, st.strip())

            # ---------- E2 多选提示预计耗时 ----------
            await cdp.ev("""(()=>{
              const ins=document.querySelectorAll('#platList input');
              ins.forEach((i,idx)=>{i.checked = idx<2; i.dispatchEvent(new Event('change'));});
              return true;})()""")
            n_checked = await cdp.ev("document.querySelectorAll('#platList input:checked').length")
            check("E2 可多选（2 个平台）", n_checked == 2, f"勾选 {n_checked}")

            # 让后端立刻返回失败，避免真的等两个平台：替换 window.j 构造「1 成功 + 1 失败」
            await cdp.ev("""(()=>{
              window.__lastBody = null;
              const real = window.j;
              window.__realJ = real;
              window.j = async function(method, url, body){
                if(url.indexOf('/generate')>=0 && body && body.platforms && body.platforms.length>1){
                  window.__lastBody = JSON.parse(JSON.stringify(body));
                  await new Promise(r=>setTimeout(r,1500));
                  return { drafts:[{id:1,platform_code:'xhs',platform_name:'小红书',titles:['t'],body:'b',tags:[]}],
                           used_mock:true, ok_count:1, fail_count:1,
                           failures:[{platform:'douyin', error:'LLMError: 模拟上游失败（浏览器实测）'}],
                           trace_id:null };
                }
                return real(method, url, body);
              };
              return true;})()""")

            await cdp.ev("document.getElementById('btnGenerate').click()")
            await cdp.wait_for(
                "document.getElementById('genStatus').textContent.indexOf('1~4 分钟')>=0",
                timeout=20, label="多选耗时提示")
            hint = await cdp.ev("document.getElementById('genStatus').textContent")
            check("E2 多选时提示预计耗时", "2 个平台" in hint and "1~4 分钟" in hint, hint.strip())
            await cdp.wait_for(
                "document.getElementById('genStatus').textContent.indexOf('部分完成')>=0",
                timeout=60, label="部分完成提示")
            st2 = await cdp.ev("document.getElementById('genStatus').textContent")
            check("E3 部分完成显示「成功 X / 失败 Y」",
                  "成功 1" in st2 and "失败 1" in st2, st2.strip())
            during = await cdp.ev(
                "(()=>{const b=window.__lastBody;return b?b.platforms.join(','):null;})()")
            check("E2 多选请求确实带上 2 个平台", during and len(during.split(',')) == 2, during)

            # ---------- E4 失败平台与原因可见 ----------
            fail_vis = await cdp.ev(
                "getComputedStyle(document.getElementById('failLine')).display")
            fail_txt = await cdp.ev("document.getElementById('failLine').textContent")
            check("E4 失败区域可见", fail_vis != "none", f"display={fail_vis}")
            check("E4 显示失败平台名", "douyin" in fail_txt, fail_txt.strip()[:90])
            check("E4 显示失败原因（含异常类型）", "LLMError" in fail_txt, fail_txt.strip()[:90])

            # ---------- E5 重试失败平台 ----------
            has_btn = await cdp.ev("!!document.getElementById('btnRetryFail')")
            check("E5 存在「重试失败平台」按钮", has_btn)
            await cdp.ev("window.__retryBody=null;(()=>{const real=window.j;"
                         "window.j=async function(m,u,b){if(u.indexOf('/generate')>=0)"
                         "window.__retryBody=JSON.parse(JSON.stringify(b));return real(m,u,b);};})()")
            await cdp.ev("document.getElementById('btnRetryFail').click()")
            await cdp.wait_for("window.__retryBody !== null", timeout=60, label="重试请求发出")
            retry_plats = await cdp.ev("window.__retryBody && window.__retryBody.platforms.join(',')")
            check("E5 重试只带失败平台（douyin）", retry_plats == "douyin", f"platforms={retry_plats}")
            # 等重试那一轮真正结束（按钮在生成中会被禁用，避免重复提交）
            await cdp.wait_for(
                "document.getElementById('genStatus').textContent.indexOf('生成中')<0",
                timeout=120, label="重试生成完成")
            await cdp.wait_for("!document.getElementById('btnGenerate').disabled",
                               timeout=30, label="按钮恢复可用")

            # ---------- E6 异常/超时提示可读 ----------
            await cdp.ev("""(()=>{
              window.j = async function(m,u,b){
                if(u.indexOf('/generate')>=0)
                  throw new Error('生成超时（已等待 240s）：上游限流时单平台可能需数分钟。请稍后重试，或减少平台数量后重试。');
                return window.__realJ(m,u,b);
              };return true;})()""")
            await cdp.ev("document.getElementById('btnGenerate').click()")
            diag = await cdp.ev("""(()=>({
                disabled: document.getElementById('btnGenerate').disabled,
                status: document.getElementById('genStatus').textContent,
                jPatched: String(window.j).indexOf('生成超时')>=0,
                hasRealJ: typeof window.__realJ === 'function',
                mid: (typeof currentMaterialId!=='undefined')?currentMaterialId:null,
            }))()""")
            print(f"     · E6 诊断：{diag}")
            try:
                await cdp.wait_for(
                    "document.getElementById('genStatus').textContent.indexOf('❌')>=0",
                    timeout=20, label="异常提示")
            except TimeoutError:
                pass
            st3 = await cdp.ev("document.getElementById('genStatus').textContent")
            check("E6 超时/异常有可读提示（非裸 502）",
                  "生成超时" in st3 and "重试" in st3, st3.strip()[:110])

            shot = await cdp.shot("partial-failure-frontend.png")
            print(f"     · 截图：{os.path.relpath(shot, os.path.join(os.path.dirname(__file__), '..'))}")

            print()
            passed = sum(1 for _, ok in results if ok)
            print(f"===== {passed}/{len(results)} 通过 =====")
            return 0 if passed == len(results) else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
