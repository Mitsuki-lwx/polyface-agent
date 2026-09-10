"""M5 模板管理 端到端验收：对照 13-模板管理-checklist.md 的 A/B/C 层逐条断言。

可重复运行：所有自建对象名带 RUN 唯一标识，不会因历史数据产生同名冲突。
前置：Java(:8080) + Python(:8000) 已启动。
用法：python scripts/e2e_m5.py
"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"
RUN = time.strftime("%H%M%S")
results = []


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
    mark = "  PASS " if cond else "  FAIL "
    print(mark + label + (("  | " + str(extra)[:120]) if extra else ""))


print(f"===== M5 端到端验收 (RUN={RUN}) =====")

print("--- A. 数据层 / 预置 seed ---")
code, tpl = call("GET", "/api/templates")
builtin = [t for t in tpl["templates"] if t["builtin"]]
check("A8 预置模板已 seed（≥3 且 builtin）", code == 200 and len(builtin) >= 3, f"builtin={len(builtin)}")
check("B1 列表内置在前", tpl["templates"][0]["builtin"] is True)

print("--- B. 模板 CRUD ---")
NAME = f"E2E模板-{RUN}"
code, created = call("POST", "/api/templates", {
    "kind": "content", "name": NAME, "voice": "直接",
    "opening": "先说结论：", "structure": ["结论", "论据", "行动"],
    "closing": "关注我", "tag_style": "短标签", "taboo": ["不要AI味"]})
check("B3 新建返回 201 + id", code == 201 and created.get("id"), f"code={code}")
new_id = created["id"]
check("A4 新建后字段完整",
      created["name"] == NAME and created["structure"] == ["结论", "论据", "行动"]
      and created["taboo"] == ["不要AI味"])
check("A6/A9 新建默认 version=1 且 builtin=False",
      created["version"] == 1 and created["builtin"] is False)

code, _ = call("POST", "/api/templates", {"name": ""})
check("B4 空名返回 400", code == 400, f"code={code}")

code, got = call("GET", f"/api/templates/{new_id}")
check("B5 详情 200", code == 200 and got["id"] == new_id)
code, _ = call("GET", "/api/templates/99999")
check("B5 不存在返回 404", code == 404, f"code={code}")

NAME2 = f"{NAME}(改)"
code, upd = call("PUT", f"/api/templates/{new_id}", {
    "name": NAME2, "voice": "更直接", "opening": "你好",
    "structure": ["新结构"], "closing": "", "tag_style": "", "taboo": []})
check("B6 编辑 200 且 version 自增", code == 200 and upd["version"] == 2, f"v={upd.get('version')}")
check("A6 created_at 不变 / updated_at 变化",
      upd["created_at"] == created["created_at"] and upd["updated_at"] != created["updated_at"])
code, _ = call("PUT", "/api/templates/99999", {"name": "x"})
check("B7 更新不存在返回 404", code == 404, f"code={code}")

code, dup = call("POST", f"/api/templates/{new_id}/duplicate")
check("B10 复制生成新模板(builtin=0, origin_id=源)",
      code == 201 and dup["builtin"] is False and dup["origin_id"] == new_id, f"code={code}")
dup_id = dup["id"]
code, dupb = call("POST", f"/api/templates/{builtin[0]['id']}/duplicate")
check("B11 内置也可复制(builtin=0)", code == 201 and dupb["builtin"] is False)

code, _ = call("DELETE", f"/api/templates/{dup_id}")
check("B8 删除我的模板 204", code == 204, f"code={code}")
code, _ = call("DELETE", f"/api/templates/{builtin[0]['id']}")
check("B9 删除内置 409", code == 409, f"code={code}")
code, _ = call("GET", f"/api/templates/{builtin[0]['id']}")
check("B9 内置未被删除", code == 200)

print("--- B. 导出 / 导入（冲突交用户裁决）---")
code, exp = call("GET", "/api/templates/export")
check("B12 导出含版本字段", code == 200 and exp.get("polyface_templates") == 1)
check("B12 导出条目去掉本机标识(id/builtin/origin_id)",
      all(("id" not in t and "builtin" not in t and "origin_id" not in t) for t in exp["templates"]),
      f"n={len(exp['templates'])}")

# 同名冲突：未指定策略 → 409 + conflicts
payload = {"polyface_templates": 1, "exported_at": "x", "templates": [
    {"kind": "content", "name": NAME2, "voice": "导入版",
     "opening": "", "structure": [], "closing": "", "tag_style": "", "taboo": []}]}
code, conf = call("POST", "/api/templates/import", dict(payload))
check("B14 冲突未指定策略 → 409 + conflicts",
      code == 409 and conf and conf.get("conflicts") and conf["conflicts"][0]["name"] == NAME2,
      f"code={code}")

code, r = call("POST", "/api/templates/import", dict(payload, on_conflict="skip"))
check("B16 skip 跳过同名", code == 200 and r["skipped"] == 1 and r["imported"] == 0, r and {k: r[k] for k in ("imported", "skipped", "overwritten", "kept_both")})

code, r = call("POST", "/api/templates/import", dict(payload, on_conflict="overwrite"))
check("B16 overwrite 覆盖同名", code == 200 and r["overwritten"] == 1)
_, chk = call("GET", f"/api/templates/{new_id}")
check("B16 覆盖后内容更新且 version 自增", chk["voice"] == "导入版" and chk["version"] >= 2,
      f"voice={chk['voice']} v={chk['version']}")

payload2 = {"polyface_templates": 1, "exported_at": "x", "templates": [
    {"kind": "content", "name": NAME2, "voice": "并存版",
     "opening": "", "structure": [], "closing": "", "tag_style": "", "taboo": []}]}
code, r = call("POST", "/api/templates/import", dict(payload2, on_conflict="keep_both"))
check("B16 keep_both 都保留", code == 200 and r["kept_both"] == 1)
_, allT = call("GET", "/api/templates")
check("B16 并存条目名带 (导入) 后缀",
      any(n == NAME2 + "(导入)" for n in [t["name"] for t in allT["templates"]]))

NEW_NAME = f"全新导入模板-{RUN}"
payload3 = {"polyface_templates": 1, "exported_at": "x", "templates": [
    {"kind": "content", "name": NEW_NAME, "voice": "v", "opening": "",
     "structure": [], "closing": "", "tag_style": "", "taboo": []}]}
code, r = call("POST", "/api/templates/import", dict(payload3))
check("B13 无冲突导入 200", code == 200 and r["imported"] == 1)

code, _ = call("POST", "/api/templates/import", {"foo": 1})
check("B15 非法文件 400", code == 400, f"code={code}")

print("--- C. 生成接入 + 版本存档（FR-64 / UC-15）---")
UNIQ_OPEN, UNIQ_CLOSE = "【E2E独家开头】", "【E2E独家结尾】"
code, tplx = call("POST", "/api/templates", {
    "name": f"E2E独特模板-{RUN}", "voice": "测试", "opening": UNIQ_OPEN,
    "structure": ["S1", "S2"], "closing": UNIQ_CLOSE, "tag_style": "", "taboo": []})
tpl_id = tplx["id"]
code, mat = call("POST", "/api/materials", {
    "raw_text": "我2023年裸辞做自由职业，靠写作月入0到3万。每周复盘帮我找到好选题。",
    "source_kind": "长文", "title": f"M5-E2E-{RUN}"})
mid = mat["id"]

code, gen = call("POST", f"/api/materials/{mid}/generate", {
    "platforms": ["xhs", "douyin"], "template_id": tpl_id})
check("C1 传 template_id 生成成功", code == 200 and len(gen["drafts"]) == 2, f"code={code}")
body0 = gen["drafts"][0]["draft"]["body"]
check("C1 成稿体现模板开头/结尾",
      UNIQ_OPEN in body0 and UNIQ_CLOSE in body0, body0[:80].replace("\n", "⏎"))
check("C5 draft 记录 template_id + version",
      all(d.get("template_id") == tpl_id and d.get("template_version") == 1 for d in gen["drafts"]),
      [(d.get("template_id"), d.get("template_version")) for d in gen["drafts"]])

code, _ = call("POST", f"/api/materials/{mid}/generate", {"platforms": ["xhs"], "template_id": 99999})
check("C2 不存在 template_id 返回 404", code == 404, f"code={code}")

code, gen2 = call("POST", f"/api/materials/{mid}/generate", {
    "platforms": ["xhs"], "template_id": tpl_id,
    "template": {"name": "内联", "opening": "内联开头", "closing": ""}})
check("C3 template_id 优先于内联 template",
      UNIQ_OPEN in gen2["drafts"][0]["draft"]["body"]
      and "内联开头" not in gen2["drafts"][0]["draft"]["body"])

code, gen3 = call("POST", f"/api/materials/{mid}/generate", {"platforms": ["xhs"]})
check("C4 不传模板无回归（qa_passed）",
      code == 200 and gen3["drafts"][0]["status"] == "qa_passed")

call("PUT", f"/api/templates/{tpl_id}", {
    "name": f"E2E独特模板-{RUN}-v2", "voice": "v2", "opening": "新的开头：",
    "structure": [], "closing": "", "tag_style": "", "taboo": []})
code, gen4 = call("POST", f"/api/materials/{mid}/generate", {
    "platforms": ["xhs"], "template_id": tpl_id})
check("C6 模板编辑后再次生成记录新版本",
      gen4["drafts"][0]["template_version"] == 2, gen4["drafts"][0].get("template_version"))

call("DELETE", f"/api/templates/{tpl_id}")
code, hist = call("GET", f"/api/materials/{mid}")
check("C7 模板删除后历史 draft 仍保留 template_id",
      len([d for d in hist["drafts"] if d.get("template_id") == tpl_id]) > 0)

code, t1 = call("POST", "/api/templates", {"name": f"A开头-{RUN}", "opening": "AAA开头", "structure": [], "taboo": []})
code, t2 = call("POST", "/api/templates", {"name": f"B开头-{RUN}", "opening": "BBB开头", "structure": [], "taboo": []})
_, g1 = call("POST", f"/api/materials/{mid}/generate", {"platforms": ["xhs"], "template_id": t1["id"]})
_, g2 = call("POST", f"/api/materials/{mid}/generate", {"platforms": ["xhs"], "template_id": t2["id"]})
b1, b2 = g1["drafts"][0]["draft"]["body"], g2["drafts"][0]["draft"]["body"]
check("C8 不同模板产出不同开头", ("AAA开头" in b1) and ("BBB开头" in b2) and b1 != b2)

print()
total, passed = len(results), sum(1 for _, ok in results if ok)
print(f"===== 端到端结果：{passed}/{total} 通过 =====")
fails = [l for l, ok in results if not ok]
if fails:
    print("失败项：")
    for f in fails:
        print("  -", f)
