"""M7-1 统一资产库端到端实测：上传 → 列表/缩略图 → 去重 → 筛选/搜索 → 删除三态 → 自动登记。

覆盖的链路：
    浏览器文件选择器上传 → `/api/assets/upload` → 内容寻址落盘 → 面板出现缩略图
    同文件再传一次 → **磁盘只留一份**（sha256 去重）
    类型筛选 / 关键词搜索
    删除语义三态：被引用 → 只删记录；最后一个引用 → 才删文件
    生成封面 → 素材库**自动**出现该图且关联到 draft（M6-1 接线）

诚实边界：视频缩略图要 ffmpeg，本脚本只断言图标与体积；`duration_sec` 本里程碑恒为 0。

用法（服务需已启动）：
    BASE=http://127.0.0.1:18080 POLYFACE_DATA_DIR=<数据目录> \
      python-service/.venv/Scripts/python.exe scripts/e2e_assets.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import websockets

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
PORT = int(os.environ.get("CDP_PORT", "9226"))
BASE = os.environ.get("BASE", "http://127.0.0.1:8080")
DATA_DIR = Path(os.environ.get("POLYFACE_DATA_DIR", "../data")).resolve()
MEDIA_DIR = DATA_DIR / "media"
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


def http_json(method, url, payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body}


def http_delete(url, timeout=60):
    req = urllib.request.Request(url, method="DELETE")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


def http_upload(path: Path, name: str, timeout=180):
    """multipart 上传（标准库手搓，避免额外依赖）。"""
    boundary = "----polyface" + uuid.uuid4().hex
    body = bytearray()
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode()
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += path.read_bytes()
    body += f"\r\n--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="name"\r\n\r\n'
    body += name.encode()
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + "/api/assets/upload", data=bytes(body),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode(errors="replace")}


def make_png(dest: Path, color: str = "red") -> Path:
    """用 ffmpeg 造一张真 PNG（不引 Pillow）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"color=c={color}:s=200x200", "-frames:v", "1", str(dest)],
                   check=True, capture_output=True)
    return dest


def make_mp4(dest: Path) -> Path:
    """造一个极小的真 MP4（0.5s 黑帧），用来验非图片资产的渲染分支。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=black:s=160x120:d=0.5", "-pix_fmt", "yuv420p",
                    str(dest)], check=True, capture_output=True)
    return dest


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def launch_edge(exe, profile):
    return subprocess.Popen([
        exe, "--headless=new", f"--remote-debugging-port={PORT}",
        f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--window-size=1400,1400", "about:blank",
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

    work = Path(tempfile.mkdtemp(prefix="polyface-assets-"))
    # 颜色每轮不同 → 内容哈希每轮不同。否则跨轮次会命中同一 sha，
    # 上一轮留下的记录会让 G1/G2 的"删最后一条才删文件"断言失真。
    color = "0x%06x" % random.randrange(0x1000000)
    png = make_png(work / f"asset-{RUN}.png", color)
    png_copy = work / f"asset-{RUN}-copy.png"
    png_copy.write_bytes(png.read_bytes())   # **同样字节、不同文件名** → 用来验去重

    profile = tempfile.mkdtemp(prefix="polyface-assets-prof-")
    proc = launch_edge(exe, profile)
    try:
        wait_devtools()
        async with websockets.connect(page_ws_url(), max_size=20 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Runtime.enable")
            await cdp.call("Page.enable")
            await cdp.call("DOM.enable")
            print(f"===== 统一资产库端到端实测 (BASE={BASE}, RUN={RUN}) =====")

            await cdp.call("Page.navigate", {"url": BASE})
            await cdp.wait_for("!!document.getElementById('assetList')", label="首页加载")
            # headless 下 alert() 会**阻塞页面**，测试会莫名其妙卡死 —— 换成记录，顺便当诊断信息
            await cdp.ev("window.alert=(m)=>{window.__lastAlert=String(m);};"
                         "window.confirm=(m)=>{window.__lastConfirm=String(m);return true;};")
            check("A1 首页出现「📚 素材库」面板", True)

            # 与「素材历史」并列且文案能区分（docs/73 §9）
            panel = await cdp.ev("document.getElementById('assetList').closest('.card').innerText")
            check("A2 文案说明了与「素材历史」的区别",
                  "文本素材" in (panel or "") and "资产" in (panel or ""),
                  (panel or "")[:80].replace("\n", " "))

            # ---------- 上传（走真实文件选择器）----------
            # ⚠️ 必须先等 init() 把 onchange 绑上：`#assetList` 在 HTML 解析完就存在，
            # 但事件处理器是在 init() 里挂的 —— 早一步 setFileInputFiles 会静默丢掉这次上传
            # （实测表现为随机超时，最难查的那种）。
            await cdp.wait_for("typeof document.getElementById('assetFile').onchange==='function'",
                               timeout=30, label="上传处理器就绪")
            doc = await cdp.call("DOM.getDocument", {})
            node = await cdp.call("DOM.querySelector", {"nodeId": doc["root"]["nodeId"],
                                                       "selector": "#assetFile"})
            await cdp.call("DOM.setFileInputFiles", {"files": [str(png)], "nodeId": node["nodeId"]})
            try:
                await cdp.wait_for(f"document.getElementById('assetList').innerText.indexOf('asset-{RUN}.png')>=0",
                                   timeout=60, label="上传后列表出现")
                check("B1 通过文件选择器上传 → 列表出现", True)
            except TimeoutError:
                alert = await cdp.ev("window.__lastAlert || '(无 alert)'")
                listing_txt = await cdp.ev("document.getElementById('assetList').innerText")
                check("B1 通过文件选择器上传 → 列表出现", False,
                      f"alert={alert} 列表={(listing_txt or '')[:120]}")
                raise

            # 缩略图用了 loading="lazy"（素材多时不拖垮浏览器）→ **必须把那张图滚进视口**，
            # 否则离屏图片根本不加载、naturalWidth 恒为 0。
            # 注意滚的是**第一张图**而不是整个列表容器：列表长了以后容器比视口还高，
            # 滚容器只会把中间那段露出来，第一张仍在屏幕外（实测踩过）。
            await cdp.ev("(()=>{const i=document.querySelector('#assetList img');"
                         "if(i) i.scrollIntoView({block:'center'});})()")
            await asyncio.sleep(0.5)
            await cdp.wait_for("(document.querySelector('#assetList img')||{}).naturalWidth>0",
                               timeout=30, label="缩略图")
            nw = await cdp.ev("document.querySelector('#assetList img').naturalWidth")
            check("B2 图片缩略图真的渲染出来（naturalWidth>0）", nw and nw > 0, f"{nw}px")

            # ---------- 去重：同样字节再传一次 ----------
            st, dup = http_upload(png_copy, png_copy.name)
            check("C1 同字节文件再次上传成功", st == 200, f"{st}")
            st, listing = http_json("GET", BASE + "/api/assets?q=asset-" + RUN)
            shas = {i["sha256"] for i in listing["items"] if i["sha256"]}
            check("C2 两条记录指向同一个 sha256（内容寻址）", len(shas) == 1 and len(listing["items"]) >= 2,
                  f"{len(listing['items'])} 条记录 / {len(shas)} 个 sha")
            if shas:
                sha = next(iter(shas))
                blob = MEDIA_DIR / "assets" / sha[:2] / f"{sha}.png"
                same_sha_files = list((MEDIA_DIR / "assets" / sha[:2]).glob(f"{sha}.*")) if blob.parent.is_dir() else []
                check("C3 磁盘上同一内容只有一份文件", len(same_sha_files) == 1,
                      f"{[p.name for p in same_sha_files]}")

            # ---------- 筛选与搜索 ----------
            await cdp.ev("(()=>{const s=document.getElementById('assetKind');s.value='image';s.onchange();})()")
            await asyncio.sleep(0.8)
            only_img = await cdp.ev("document.getElementById('assetList').innerText")
            check("D1 类型筛选「图片」生效", "图片" in (only_img or ""), (only_img or "")[:60].replace("\n", " "))
            await cdp.ev("(()=>{const s=document.getElementById('assetKind');s.value='video';s.onchange();})()")
            await asyncio.sleep(0.8)
            no_img = await cdp.ev("document.getElementById('assetList').innerText")
            # 断言"筛选真的排除了别的类型"，而不是"视频一定为空" ——
            # 后者会被上一轮遗留的视频资产打脸（跨轮次脆弱）
            check("D2 筛选「视频」时列表里不再出现图片", "🖼" not in (no_img or ""),
                  (no_img or "")[:80].replace("\n", " | "))
            await cdp.ev("(()=>{const s=document.getElementById('assetKind');s.value='';s.onchange();})()")
            await cdp.ev(f"(()=>{{const q=document.getElementById('assetQ');q.value='asset-{RUN}';q.oninput();}})()")
            await asyncio.sleep(0.9)
            found = await cdp.ev(f"document.getElementById('assetList').innerText.indexOf('asset-{RUN}')>=0")
            check("D3 关键词搜索命中", bool(found))

            await cdp.shot(f"assets-{RUN}")

            # ---------- 非图片资产：面板要显示图标 + 体积（缩略图要 ffmpeg，本里程碑不做）----------
            mp4 = make_mp4(work / f"clip-{RUN}.mp4")
            st, v = http_upload(mp4, mp4.name)
            check("H1 视频上传成功且 kind=video", st == 200 and v.get("kind") == "video",
                  json.dumps({k: v.get(k) for k in ("kind", "mime", "size_bytes")}, ensure_ascii=False))
            await cdp.ev("(()=>{document.getElementById('assetQ').value='';"
                         "document.getElementById('assetKind').value='';})()")
            await cdp.ev("loadAssets()")
            await asyncio.sleep(1.2)
            vtxt = await cdp.ev("document.getElementById('assetList').innerText")
            check("H2 面板对视频显示图标 + 体积", "🎬" in (vtxt or "") and f"clip-{RUN}" in (vtxt or ""),
                  (vtxt or "")[:120].replace("\n", " | "))
            check("H3 视频行含类型与体积字样", "视频" in (vtxt or "") and ("KB" in (vtxt or "") or "B" in (vtxt or "")),
                  (vtxt or "")[:120].replace("\n", " | "))

            # ---------- 自动登记：生成封面 → 素材库自动出现 ----------
            await cdp.ev("""(()=>{
              document.getElementById('raw').value='我2023年裸辞做自由职业，靠写作从月入0做到3万。';
              document.getElementById('title').value='ASSET-""" + RUN + """';
              document.getElementById('btnAnalyze').click();
            })()""")
            await cdp.wait_for("document.querySelectorAll('#factsEditor .fact').length>0", label="事实编辑器")
            await cdp.ev("document.getElementById('btnConfirmFacts').click()")
            await cdp.wait_for("document.getElementById('analyzeStatus').textContent.indexOf('已确认')>=0", label="确认")
            await cdp.ev("document.getElementById('btnGenerate').click()")
            await cdp.wait_for("document.getElementById('genStatus').textContent.indexOf('完成')>=0",
                               timeout=180, label="生成")
            await cdp.wait_for("!!document.getElementById('btnCover')", label="封面按钮")
            await cdp.ev("document.getElementById('btnCover').click()")
            await cdp.wait_for("!!document.getElementById('btnOpenEditor')", timeout=180, label="封面完成")

            # 注意：资产的 name 是**草稿标题**（CoverController.deriveTitle），不是素材标题 ——
            # 所以不能按素材名搜，要按 source=generated + rel_path 前缀筛。
            st, gen = http_json("GET", BASE + "/api/assets?kind=image&limit=50")
            hit = [i for i in gen["items"]
                   if i["source"] == "generated" and i["rel_path"].startswith("covers/")]
            check("E1 生成封面 → 素材库**自动**出现该图", bool(hit),
                  json.dumps(hit[:1], ensure_ascii=False)[:150])
            if hit:
                check("E2 该资产自动关联到对应 draft",
                      any(l["owner_kind"] == "draft" for l in hit[0]["links"]),
                      json.dumps(hit[0]["links"], ensure_ascii=False))

            # ---------- 删除三态（走 API，语义在 docs/73 §6）----------
            if hit:
                aid = hit[0]["id"]
                st, d1 = http_delete(f"{BASE}/api/assets/{aid}")
                check("F1 删被 draft 引用的资产 → 只删记录，**文件保留**",
                      st == 200 and d1.get("file_removed") is False and d1.get("links_removed", 0) >= 1,
                      json.dumps(d1, ensure_ascii=False))
                st, gen2 = http_json("GET", BASE + "/api/assets?kind=image&limit=50")
                check("F2 删除后列表里不再出现",
                      not any(i["id"] == aid for i in gen2["items"]))

            # 同一 sha 的两条上传记录：删掉一条 → 文件保留；删掉最后一条 → 文件才走
            if shas:
                sha = next(iter(shas))
                st, listing = http_json("GET", BASE + "/api/assets?q=asset-" + RUN)
                ids = [i["id"] for i in listing["items"] if i["sha256"] == sha]
                if len(ids) >= 2:
                    st, r1 = http_delete(f"{BASE}/api/assets/{ids[0]}")
                    check("G1 删同 sha 的第一条 → 文件保留", r1.get("file_removed") is False,
                          json.dumps(r1, ensure_ascii=False))
                    st, r2 = http_delete(f"{BASE}/api/assets/{ids[1]}")
                    check("G2 删同 sha 的最后一条 → 文件才被删", r2.get("file_removed") is True,
                          json.dumps(r2, ensure_ascii=False))
                    blob = MEDIA_DIR / "assets" / sha[:2] / f"{sha}.png"
                    check("G3 磁盘文件确实没了", not blob.exists(), str(blob))

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
