"""真实 LLM 链路端到端验收（补 docs/38 §5 / docs/46 §6 / docs/48 §6 长期挂着的缺口）。

前置：
  1) Python(:8000) 以真实模式启动（LLM_MOCK=false 且 .env 配好 Key）
  2) Java(:8080) 已启动
  3) 上游额度可用 —— 建议先跑额度探针，否则本脚本会长时间等待或被 429 拒绝

用法：
  python scripts/e2e_real_llm.py [python服务日志路径]
  python scripts/e2e_real_llm.py D:/temp/py-real.log

诚实边界：
  - 本脚本**不做**"事实是否编造"的自动判定（程序化判不准，会变成假检查），
    只把成稿原文打出来供人读；判定留给 `docs/50` 的真实作者试验。
  - 上游额度紧时可能在任意一步 429，脚本会明确区分"额度问题"与"功能问题"。
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"
PY = "http://127.0.0.1:8000"
PY_LOG = sys.argv[1] if len(sys.argv) > 1 else "D:/temp/py-real.log"
RUN = time.strftime("%H%M%S")
results: list[tuple[str, bool]] = []
quota_blocked = False


def call(method, path, body=None, timeout=600, base=BASE):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def check(label, cond, extra=""):
    results.append((label, bool(cond)))
    print(("  PASS " if cond else "  FAIL ") + label + (("  | " + str(extra)[:110]) if extra else ""))


def note(label, value):
    print(f"  INFO {label}: {value}")


def read_log():
    try:
        return open(PY_LOG, encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


def looks_like_rate_limit(payload) -> bool:
    s = str(payload)
    return "429" in s or "rate_limit" in s or "tpm/rpm" in s


print(f"===== 真实 LLM 链路 E2E (RUN={RUN}) =====")

# ---------- 0. 必须确认双方都是真实模式（否则这个 E2E 毫无意义） ----------
print("\n-- 0 模式确认 --")
code, pyh = call("GET", "/health", base=PY, timeout=20)
check("0.1 Python /health 可达", code == 200, f"code={code}")
if isinstance(pyh, dict):
    check("0.2 Python 处于**真实模式**（mock=false）", pyh.get("mock") is False, f"mock={pyh.get('mock')}")
    note("Python 模型/链", f"{pyh.get('model')} ← {pyh.get('model_chain')}")
    note("Langfuse 可用", pyh.get("langfuse"))
else:
    check("0.2 Python 处于真实模式", False, str(pyh)[:80])

code, jh = call("GET", "/health", base=BASE, timeout=20)
check("0.3 Java 可达且非 mock", code == 200 and (jh or {}).get("mock") is False,
      f"code={code} mock={(jh or {}).get('mock') if isinstance(jh, dict) else '?'}")

# ---------- 1. 真实素材理解（1 次真实调用） ----------
print("\n-- 1 真实素材理解 --")
t0 = time.time()
code, mat = call("POST", "/api/materials", {
    "raw_text": "我2023年裸辞做自由职业，靠写作从月入0做到3万。每周复盘帮我找到好选题。",
    "source_kind": "长文", "title": f"REAL-E2E-{RUN}"})
dt = time.time() - t0
if code == 200 and isinstance(mat, dict):
    mid = mat["id"]
    structured = mat.get("structured") or {}
    facts = structured.get("facts") or []
    check("1.1 建素材成功", bool(mid), f"id={mid} 耗时={dt:.1f}s")
    check("1.2 非 mock 产出", mat.get("used_mock") is False, f"used_mock={mat.get('used_mock')}")
    check("1.3 抽到事实", len(facts) > 0, f"count={len(facts)}")
    note("core_message", str(structured.get("core_message"))[:80])
    for f in facts:
        note(f"  fact[{f.get('type')}]", str(f.get("text"))[:80])
    print(f"  → 单次真实理解耗时 {dt:.1f}s")
else:
    quota_blocked = looks_like_rate_limit(mat)
    check("1.1 建素材成功", False, f"code={code} {str(mat)[:100]}")
    mid = None

if mid is None:
    print("\n!! 第 1 步失败，无法继续。" + ("  判定：**上游额度/限流问题**，不是功能问题。"
          if quota_blocked else "  判定：非额度问题，需排查。"))
    sys.exit(2 if quota_blocked else 1)

# ---------- 2. 事实确认（真实模式下也须成立） ----------
print("\n-- 2 事实确认闭环（真实模式）--")
code, r = call("PUT", f"/api/materials/{mid}/facts", {
    "core_message": structured.get("core_message") or "裸辞做自由职业",
    "tone": structured.get("tone") or "真诚分享",
    "audience": structured.get("audience") or "自由职业者",
    "facts": [{"type": f.get("type", "story"), "text": f.get("text")} for f in facts]
             or [{"type": "data", "text": "2023年裸辞，写作月入从0到3万"}],
    "confirm": True})
check("2.1 保存并确认事实", code == 200 and isinstance(r, dict) and r.get("facts_confirmed"),
      f"code={code} count={r.get('facts_count') if isinstance(r, dict) else str(r)[:60]}")

# ---------- 3. 真实生成（brief→draft→qa，跳过重复理解） ----------
print("\n-- 3 真实生成（小红书 1 平台）--")
log_before = read_log()
t0 = time.time()
code, gen = call("POST", f"/api/materials/{mid}/generate", {"platforms": ["xhs"]})
dt = time.time() - t0
if code == 200 and isinstance(gen, dict):
    drafts = gen.get("drafts") or []
    check("3.1 生成成功", len(drafts) > 0, f"code={code} 耗时={dt:.1f}s drafts={len(drafts)}")
    note("总耗时", f"{dt:.1f}s")
    note("trace_id", gen.get("trace_id"))
    note("failures", gen.get("failures"))
    d = drafts[0] if drafts else {}
    payload = d.get("draft") or {}
    qa = d.get("qa") or {}
    check("3.2 产出非 mock", gen.get("used_mock") is False, f"used_mock={gen.get('used_mock')}")
    check("3.3 正文非空", bool((payload.get("body") or "").strip()), f"len={len(payload.get('body') or '')}")
    check("3.4 有标题", bool(payload.get("titles")), f"{payload.get('titles')}")
    check("3.5 有 QA 报告", bool(qa), f"passed={qa.get('passed')} issues={len(qa.get('issues') or [])}")
    note("标签", payload.get("tags"))
    print("  ── 成稿原文（请人工读一遍：是否有素材里没有的事实？）──")
    print("  " + (payload.get("body") or "(空)").replace("\n", "\n  "))
    if qa.get("issues"):
        for i in qa["issues"]:
            note("  QA issue", str(i)[:100])
    if qa.get("warnings"):
        for w in qa["warnings"]:
            note("  QA warn", str(w)[:100])
else:
    quota_blocked = looks_like_rate_limit(gen)
    check("3.1 生成成功", False, f"code={code} {str(gen)[:110]}")
    dt and note("等待耗时", f"{dt:.1f}s")

# ---------- 4. 日志证据：跳过重复理解 + 真实调用 ----------
print("\n-- 4 日志证据 --")
log_after = read_log()
delta = log_after[len(log_before):] if len(log_after) > len(log_before) else log_after
check("4.1 日志含「跳过 understand」（已确认事实被复用）",
      "skipping" in delta.lower() or "confirmed facts" in delta.lower(),
      (re.findall(r"(?i)using user-confirmed[^\n]*", delta) or [""])[0][:100])
calls = re.findall(r"(?i)(scene|scene=)[\"']?(\w+)", delta) or re.findall(r"(?i)scene['\"]?\s*[:=]\s*['\"]?(\w+)", delta)
note("本次日志里的调用线索", str(calls)[:140] or "(未匹配到 scene 标记，仅作参考)")
check("4.2 未退回 mock（日志无 mock 回退告警）",
      "回退 mock" not in delta and "fallback to mock" not in delta.lower())

# ---------- 汇总 ----------
print("\n===== 汇总 =====")
passed = sum(1 for _, ok in results if ok)
print(f"  {passed}/{len(results)} 通过")
for label, ok in results:
    if not ok:
        print("    FAIL " + label)
if quota_blocked:
    print("\n  ⚠️ 存在上游额度/限流导致的失败 —— 请明确记录为「未验证」，不要记为功能缺陷。")
sys.exit(0 if passed == len(results) else 1)
