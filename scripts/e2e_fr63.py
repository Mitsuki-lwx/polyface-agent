"""FR-63 示例学习 端到端验收：对照 17-示例学习模板-checklist.md 的 A~D 层。

可重复运行：自建对象名 / source_note 带 RUN 唯一标识。
前置：Java(:8080) + Python(:8000) 已启动。
用法：python scripts/e2e_fr63.py
"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"
RUN = time.strftime("%H%M%S")
results = []

SAMPLE = (
    "我以前也踩过这个坑，做了半年没什么起色。\n\n"
    "后来我发现问题不在努力，而在选题方向。\n\n"
    "第一步是先看数据反馈，找出真正有人看的角度。\n\n"
    "第二步是聚焦单一方向，不要什么都写。\n\n"
    "第三步是持续复盘迭代，每篇都做小结。\n\n"
    "你遇到过类似的情况吗？评论区聊聊。"
)
SHORT_SAMPLE = "这个方法很好用，我一直在用。"   # <50 字


def call(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            code, raw = r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read().decode("utf-8")
    try:
        parsed = json.loads(raw) if raw else None
    except Exception:
        parsed = None
    return code, parsed


def check(label, cond, extra=""):
    results.append((label, bool(cond)))
    print(("  PASS " if cond else "  FAIL ") + label + (("  | " + str(extra)[:110]) if extra else ""))


print(f"===== FR-63 端到端验收 (RUN={RUN}) =====")

print("--- A. 数据层 / 列表安全默认 ---")
code, def_list = call("GET", "/api/templates")
code, all_list = call("GET", "/api/templates?status=all")
code, draft_list = call("GET", "/api/templates?status=draft")
check("A4 默认只返回 active",
      code == 200 and all(t["status"] == "active" for t in def_list["templates"]),
      f"n={len(def_list['templates'])}")
check("A6 status=all 返回全部（含草稿）",
      len(all_list["templates"]) >= len(def_list["templates"]))
check("A5 status=draft 只返回草稿",
      all(t["status"] == "draft" for t in draft_list["templates"]),
      f"n={len(draft_list['templates'])}")
builtin_tpl = next(t for t in all_list["templates"] if t["builtin"])
check("A8 内置模板为 active", builtin_tpl["status"] == "active")

print("--- B. learn 接口 ---")
code, _ = call("POST", "/api/templates/learn", {"sample_text": SHORT_SAMPLE})
check("B4 示例过短返回 400", code == 400, f"code={code}")
code, _ = call("POST", "/api/templates/learn", {})
check("B5 缺 sample_text 返回 400", code == 400, f"code={code}")

note = f"E2E示例-{RUN}"
code, learned = call("POST", "/api/templates/learn",
                     {"sample_text": SAMPLE, "source_note": note})
check("B1 learn 返回 201", code == 201, f"code={code}")
check("B1 产物 status=draft", learned.get("status") == "draft", learned.get("status"))
check("B2 含全部拆解字段",
      all(k in learned for k in
          ("name", "voice", "opening", "structure", "closing", "tag_style", "taboo")))
check("B3 含 rationale", bool(learned.get("rationale")))
check("B6 source_note 落库", learned.get("source_note") == note)
check("B8 不存完整示例原文", SAMPLE not in json.dumps(learned, ensure_ascii=False))
check("B9 builtin=false 且 version=1",
      learned["builtin"] is False and learned["version"] == 1)
lid = learned["id"]

print("--- C. 拆解质量 ---")
check("C1 structure 非空", len(learned["structure"]) >= 1, learned["structure"])
check("C2 opening 取自示例首段", learned["opening"].startswith(SAMPLE[:8]), learned["opening"][:24])
check("C3 末段为互动句 → closing 非空", bool(learned["closing"]), learned["closing"][:24])
check("C5 structure 条数 ≤ 6", len(learned["structure"]) <= 6)
check("C6 structure 每条 ≤ 15 字",
      all(len(x) <= 15 for x in learned["structure"]), learned["structure"])

print("--- D. 草稿生命周期（UC-16 核心）---")
code, d1 = call("GET", "/api/templates")
check("D2 草稿不在默认列表（不进生成选项）",
      all(t["id"] != lid for t in d1["templates"]))
code, det = call("GET", f"/api/templates/{lid}")
check("D2' 详情仍可查草稿", code == 200 and det["status"] == "draft")

code, act = call("POST", f"/api/templates/{lid}/activate")
check("D3 activate 返回 200", code == 200, f"code={code}")
check("D3 status 变为 active", act.get("status") == "active")
code, d2 = call("GET", "/api/templates")
check("D4 启用后进入默认列表", any(t["id"] == lid for t in d2["templates"]))
code, _ = call("POST", f"/api/templates/{lid}/activate")
check("D5 重复 activate 幂等 200", code == 200, f"code={code}")
code, _ = call("POST", "/api/templates/99999/activate")
check("D6 不存在 id activate → 404", code == 404, f"code={code}")

code, mat = call("POST", "/api/materials", {
    "raw_text": "我2023年裸辞做自由职业，靠写作从月入0做到3万。每周复盘帮我找到好选题。",
    "source_kind": "长文", "title": f"FR63-E2E-{RUN}"})
mid = mat["id"]
code, gen = call("POST", f"/api/materials/{mid}/generate",
                 {"platforms": ["xhs"], "template_id": lid})
body = gen["drafts"][0]["draft"]["body"] if code == 200 else ""
check("D4' 启用后可用于生成且模板生效",
      code == 200 and learned["opening"][:10] in body, body[:40].replace("\n", "⏎"))

code, l2 = call("POST", "/api/templates/learn",
                {"sample_text": SAMPLE, "source_note": f"E2E编辑-{RUN}"})
code, upd = call("PUT", f"/api/templates/{l2['id']}", {
    "name": f"编辑后-{RUN}", "voice": "已改", "opening": "改过的开头",
    "structure": ["A"], "closing": "", "tag_style": "", "taboo": []})
check("D7 草稿可编辑且仍为 draft",
      code == 200 and upd["status"] == "draft" and upd["name"] == f"编辑后-{RUN}",
      f"code={code} status={upd.get('status')}")

code, _ = call("DELETE", f"/api/templates/{l2['id']}")
check("D8 丢弃草稿返回 204", code == 204, f"code={code}")
code, _ = call("GET", f"/api/templates/{l2['id']}")
check("D8 丢弃后不再存在", code == 404, f"code={code}")

code, _ = call("POST", f"/api/templates/{builtin_tpl['id']}/activate")
check("D10 内置模板 activate → 409", code == 409, f"code={code}")

print("--- 兼容与回归 ---")
code, nt = call("POST", "/api/templates", {"name": f"默认态-{RUN}"})
check("A7 手动新建默认 active", code == 201 and nt["status"] == "active")
code, exp = call("GET", "/api/templates/export")
check("导出只含 active 模板",
      code == 200 and len([t for t in exp["templates"] if t.get("name") == f"默认态-{RUN}"]) == 1)
code, gen2 = call("POST", f"/api/materials/{mid}/generate", {"platforms": ["xhs"]})
check("M5 回归：不传模板仍可生成", code == 200 and gen2["drafts"][0]["status"] == "qa_passed")

for tid in (lid, nt.get("id")):
    call("DELETE", f"/api/templates/{tid}")

print()
total, passed = len(results), sum(1 for _, ok in results if ok)
print(f"===== 端到端结果：{passed}/{total} 通过 =====")
fails = [l for l, ok in results if not ok]
if fails:
    print("失败项：")
    for f in fails:
        print("  -", f)
