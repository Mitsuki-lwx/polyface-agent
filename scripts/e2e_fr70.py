"""FR-70/71/72 端到端验收：LLM 加固 + trace 贯穿 + 用量 + Langfuse 观测。

前置：Java(:8080) + Python(:8000) + Langfuse(:3000) 均已启动。
用法：python scripts/e2e_fr70.py
"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"
RUN = time.strftime("%H%M%S")
results = []


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.loads(r.read().decode() or "null"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), {}


def check(label, cond, extra=""):
    results.append((label, bool(cond)))
    print(("  PASS " if cond else "  FAIL ") + label + (("  | " + str(extra)[:100]) if extra else ""))


print(f"===== FR-70/71/72 端到端验收 (RUN={RUN}) =====")

# ---------- 1. 建素材 ----------
code, mat, _ = call("POST", "/api/materials", {
    "raw_text": "我2023年裸辞做自由职业，靠写作从月入0做到3万。每周复盘帮我找到好选题。",
    "source_kind": "长文", "title": f"FR70-E2E-{RUN}"})
check("1 建素材成功", code == 200 and mat.get("id"), f"code={code}")
mid = mat["id"]

# ---------- 2. 生成（真实 LLM，单平台） ----------
t0 = time.time()
code, gen, headers = call("POST", f"/api/materials/{mid}/generate", {"platforms": ["xhs"]})
elapsed = time.time() - t0
check("2 生成成功", code == 200 and gen.get("drafts"), f"code={code}")
check("2 响应含 trace_id（FR-71）", bool(gen.get("trace_id")), gen.get("trace_id"))
check("2 响应头含 X-Trace-Id", bool(headers.get("X-Trace-Id")), headers.get("X-Trace-Id"))
check("2 trace_id 前后一致",
      gen.get("trace_id") == headers.get("X-Trace-Id"),
      f"{gen.get('trace_id')} vs {headers.get('X-Trace-Id')}")
tid = gen.get("trace_id", "")
print(f"     · 生成耗时 {elapsed:.0f}s，trace={tid}")

# ---------- 3. 用量落库（FR-72） ----------
code, usage, _ = call("GET", "/api/usage?limit=20")
check("3 /api/usage 可用", code == 200 and "total_calls" in usage, f"code={code}")
check("3 有调用记录", usage.get("total_calls", 0) > 0, f"total={usage.get('total_calls')}")
check("3 记录含本 trace", any(r.get("trace_id") == tid for r in usage.get("recent", [])),
      f"trace={tid[:12]}…")
check("3 按场景分组", bool(usage.get("by_scene")), usage.get("by_scene"))
check("3 不含 prompt 正文（脱敏）",
      not any(k in json.dumps(usage, ensure_ascii=False)
              for k in ("裸辞", "自由职业", "月入")), "无正文泄漏")

print()
total, passed = len(results), sum(1 for _, ok in results if ok)
print(f"===== Java/Python 链路：{passed}/{total} 通过 =====")
print(f"TRACE_ID={tid}")
