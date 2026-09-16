"""长任务预算与部分失败保留 端到端验收（FR-42）。

说明：**部分失败路径**（某平台抛异常）由单元测试覆盖 ——
Python `tests/test_partial_failure.py`（4 项，monkeypatch 制造失败）与
Java `PartialFailureTest`（4 项，替身 PythonClient 返回 1 成功 + 1 失败）。
本脚本负责验证**响应契约**与**正常路径**，避免为制造失败而在生产代码里留测试钩子。

前置：Java(:8080) + Python(:8000) 已启动。
用法：python scripts/e2e_partial.py
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
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def check(label, cond, extra=""):
    results.append((label, bool(cond)))
    print(("  PASS " if cond else "  FAIL ") + label + (("  | " + str(extra)[:80]) if extra else ""))


print(f"===== 长任务预算与部分失败保留 E2E (RUN={RUN}) =====")

code, mat = call("POST", "/api/materials", {
    "raw_text": "我2023年裸辞做自由职业，靠写作从月入0做到3万。",
    "source_kind": "长文", "title": f"PARTIAL-E2E-{RUN}"})
check("1 建素材成功", code == 200 and mat.get("id"))
mid = mat["id"]

# ---------- 2. 正常路径：单平台（默认建议） ----------
t0 = time.time()
code, gen = call("POST", f"/api/materials/{mid}/generate", {"platforms": ["xhs"]})
elapsed = time.time() - t0
check("2 生成成功", code == 200 and gen.get("drafts"), f"code={code}")
check("2 响应含 ok_count", "ok_count" in gen, gen.get("ok_count"))
check("2 响应含 fail_count", "fail_count" in gen, gen.get("fail_count"))
check("2 全成功时 fail_count=0", gen.get("fail_count") == 0)
check("2 ok_count 与 drafts 数量一致", gen.get("ok_count") == len(gen.get("drafts") or []),
      f"{gen.get('ok_count')} vs {len(gen.get('drafts') or [])}")
print(f"     · 单平台耗时 {elapsed:.1f}s")

# ---------- 3. 非法平台仍应 400（提前校验，不进入生成） ----------
code, resp = call("POST", f"/api/materials/{mid}/generate", {"platforms": ["not_a_platform"]})
check("3 非法平台返回 400", code == 400, f"code={code}")

# ---------- 4. 超时预算可配（配置生效，间接验证） ----------
code, health = call("GET", "/api/materials")   # 服务可用性
check("4 服务健康（配置加载未致启动失败）", code == 200)

# ---------- 5. 部分完成后仍可重试同一平台（幂等性：重复生成不报错） ----------
code, gen2 = call("POST", f"/api/materials/{mid}/generate", {"platforms": ["xhs"]})
check("5 重复生成（模拟重试）成功", code == 200 and gen2.get("drafts"))

print()
total, passed = len(results), sum(1 for _, ok in results if ok)
print(f"===== {passed}/{total} 通过 =====")
print()
print("提示：部分失败（单平台抛错但其余保留）由单元测试覆盖：")
print("  · python tests/test_partial_failure.py（4 项）")
print("  · java   PartialFailureTest（4 项，替身 PythonClient）")
