"""事实确认与稿件编辑闭环 端到端验收（FR-34 / FR-42）。

前置：Java(:8080) + Python(:8000) 已启动。
用法：python scripts/e2e_facts_edit.py
"""
import json
import re
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"
PY_LOG = "D:/temp/py-f.log"          # Python 服务日志（用于断言跳过 understand）
RUN = time.strftime("%H%M%S")
results = []


def call(method, path, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            text = r.read().decode("utf-8")
            return r.status, (text if raw else json.loads(text or "null")), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), {}


def check(label, cond, extra=""):
    results.append((label, bool(cond)))
    print(("  PASS " if cond else "  FAIL ") + label + (("  | " + str(extra)[:90]) if extra else ""))


print(f"===== 事实确认与稿件编辑闭环 E2E (RUN={RUN}) =====")

# ---------- 1. 建素材 ----------
code, mat, _ = call("POST", "/api/materials", {
    "raw_text": "我2023年裸辞做自由职业，靠写作从月入0做到3万。每周复盘帮我找到好选题。",
    "source_kind": "长文", "title": f"FACTS-E2E-{RUN}"})
check("1 建素材成功", code == 200 and mat.get("id"), f"code={code}")
mid = mat["id"]
check("1 新素材默认未确认", mat.get("facts_confirmed") is False)

# ---------- 2. 事实可编辑并确认 ----------
code, r, _ = call("PUT", f"/api/materials/{mid}/facts", {
    "core_message": "裸辞做自由职业，写作月入0到3万",
    "tone": "理性干货", "audience": "自由职业者",
    "facts": [{"type": "data", "text": "2023年裸辞，写作月入从0到3万"},
              {"type": "story", "text": "每周复盘帮助找到好选题"}],
    "confirm": True})
check("2 保存并确认事实", code == 200 and r.get("facts_confirmed"), f"count={r.get('facts_count') if isinstance(r, dict) else r}")

code, r400, _ = call("PUT", f"/api/materials/{mid}/facts",
                     {"facts": [{"type": "data", "text": "   "}], "confirm": True})
check("2 空 text 被拒（400）", code == 400, f"code={code}")

# ---------- 3. 生成（应跳过重复理解） ----------
log_before = ""
try:
    log_before = open(PY_LOG, encoding="utf-8", errors="replace").read()
except Exception:
    pass

code, gen, _ = call("POST", f"/api/materials/{mid}/generate", {"platforms": ["xhs"]})
check("3 生成成功", code == 200 and gen.get("drafts"), f"code={code}")

time.sleep(1.5)
try:
    log_after = open(PY_LOG, encoding="utf-8", errors="replace").read()
except Exception:
    log_after = ""
new_log = log_after[len(log_before):]
check("3 日志含「跳过理解」", "skipping understand" in new_log,
      [l for l in new_log.splitlines() if "skipping understand" in l][:1])

# ---------- 4. 稿件编辑 ----------
draft_id = (gen.get("drafts") or [{}])[0].get("id")
code, ed, _ = call("PUT", f"/api/drafts/{draft_id}", {
    "titles": ["人工改的标题"], "body": "人工改后的正文内容。", "tags": ["测试", "闭环"]})
check("4 保存稿件修改", code == 200 and ed.get("edited_at"), f"code={code}")
check("4 edited_at 已记录", bool(ed.get("edited_at")))
check("4 正文已更新", ed.get("draft", {}).get("body") == "人工改后的正文内容。")

# ---------- 5. 导出 ----------
code, md, headers = call("GET", f"/api/drafts/{draft_id}/export?format=md", raw=True)
check("5 导出 md 成功", code == 200 and "人工改的标题" in md, f"len={len(md) if isinstance(md,str) else 0}")
check("5 md 含正文与标签", "人工改后的正文内容。" in md and "#测试" in md)
check("5 响应头为附件下载", "attachment" in (headers.get("Content-Disposition") or ""))

code, txt, _ = call("GET", f"/api/drafts/{draft_id}/export?format=txt", raw=True)
check("5 导出 txt 成功", code == 200 and "人工改后的正文内容。" in txt)
check("5 txt 不含标签", "#测试" not in txt)

# ---------- 6. 读单稿（历史稿补齐所依赖） ----------
code, one, _ = call("GET", f"/api/drafts/{draft_id}")
check("6 读单稿含完整正文", code == 200 and one.get("draft", {}).get("body") == "人工改后的正文内容。")
check("6 读单稿含 qa", code == 200 and one.get("qa") is not None)

# ---------- 7. 列表接口（前端据此补读） ----------
code, detail, _ = call("GET", f"/api/materials/{mid}")
check("7 素材详情含草稿列表", code == 200 and detail.get("drafts"))
check("7 素材详情含已确认事实", detail.get("facts_confirmed") is True)

print()
total, passed = len(results), sum(1 for _, ok in results if ok)
print(f"===== {passed}/{total} 通过 =====")
