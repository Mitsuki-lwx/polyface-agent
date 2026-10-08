"""T6~T10 · 评测集跑分器 —— 素材 × 平台矩阵，跑真实生成，出通过率。

**它测什么**：同一批冻结素材下，产出的**性质**有没有变差。
**它不测什么**：产品有没有用（那是第三关 `docs/50`）。这句话会印在报告抬头。

用法：
    python scripts/eval/run_eval.py --dry-run                 # 只算成本，不发请求
    python scripts/eval/run_eval.py                           # 全矩阵（5 平台 × 10 条）
    python scripts/eval/run_eval.py --platforms xhs --limit 2
    python scripts/eval/run_eval.py --baseline eval/reports/<ts>/report.json

设计要点（对应 docs/spec_eval.md）：
- **执行单位是「一条素材」**：一次 `generate()` 带全部目标平台 —— 与产品真实路径一致
  （理解阶段只跑一次），也省调用。单条素材整体失败时，它的所有格子记「调用失败」。
- **每跑完一条素材立即落盘**：中断不丢（长跑几小时，429 是常态）。
- **失败归因分离**：`调用失败`（异常/超时/限流）与 `断言失败` 分开统计，绝不混为一谈。
- **不复制管线逻辑**：直接调 `app.pipeline.generate.generate` —— 线上那条路。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import CORPUS_DIR, REPORTS_DIR, ROOT  # noqa: E402,F401

import assertions as A  # noqa: E402
import dna_rules as dna_rules_mod  # noqa: E402
import fingerprint as fp_mod  # noqa: E402

from app import llm  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.pipeline.generate import generate  # noqa: E402
from app.schemas_gen import GenerateRequest  # noqa: E402

DEFAULT_PLATFORMS = ["xhs", "douyin", "gzh", "zhihu", "bilibili"]
ALL_PLATFORMS = DEFAULT_PLATFORMS + ["x", "instagram", "facebook", "youtube"]

# 报告抬头必须印的边界声明（spec §5 关键决定一）
SCOPE_NOTE = ("本报告只说明**同一批输入下产出有没有变差**；"
              "它**不能**说明产品有没有用（那是第三关 docs/50 的事）。")


# ---------------------------------------------------------------- 素材

def load_corpus(corpus_dir: Path) -> tuple[dict, list[dict]]:
    mf_path = corpus_dir / "MANIFEST.json"
    if not mf_path.exists():
        raise SystemExit(f"[失败] 找不到 {mf_path} —— 先跑 gen_corpus.py")
    manifest = json.loads(mf_path.read_text(encoding="utf-8"))
    # ⚠️ 不能用 m*.json：Windows 的 glob 不区分大小写，会把 MANIFEST.json 也匹配进来
    files = sorted(corpus_dir.glob("m[0-9][0-9].json"))
    items = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    if not items:
        raise SystemExit(f"[失败] {corpus_dir} 下没有素材文件")

    # 清单与文件必须一一对应 —— 少一条是静默漏测，宁可炸
    in_manifest = {it["id"] for it in manifest.get("items", [])}
    on_disk = {it["id"] for it in items}
    if in_manifest != on_disk:
        raise SystemExit(
            f"[失败] 清单与素材文件不一致 —— 清单有 {sorted(in_manifest)}，"
            f"磁盘有 {sorted(on_disk)}。改素材必须同步改 MANIFEST.json（等同改测试基准）")
    return manifest, items


def flatten_draft(pd) -> dict:
    """`PlatformDraft` → 断言层要的扁平视图（DraftPayload 字段 + qa）。"""
    d = pd.model_dump() if hasattr(pd, "model_dump") else dict(pd)
    payload = d.get("draft") or {}
    out = dict(payload)
    out["qa"] = d.get("qa") or {}
    out["platform_code"] = d.get("platform_code") or ""
    return out


# ---------------------------------------------------------------- 跑一条素材

def run_one(item: dict, platforms: list[str], timeout: int) -> dict:
    """跑一条素材的全部平台。返回 {platform: {...}} + `_meta`。

    超时用**看门狗线程**：到点把这条素材标为超时失败。
    ⚠️ 局限：Python 杀不掉正在跑的线程，被放弃的那次调用可能仍在后台跑完
    （已写进 docs/tasks_eval.md 的诚实边界）。
    """
    req = GenerateRequest(raw_text=item["raw_text"], title=item.get("title") or None,
                          source_kind="口播稿", platforms=list(platforms))
    box: dict = {}

    def work():
        try:
            box["result"] = generate(req)
        except BaseException as e:  # noqa: BLE001 — 一律记为调用失败
            box["error"] = f"{type(e).__name__}: {str(e)[:300]}"
            box["tb"] = traceback.format_exc()[-800:]

    t0 = time.time()
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout if timeout > 0 else None)
    elapsed = round(time.time() - t0, 1)

    if th.is_alive():
        return {"_meta": {"status": "timeout", "elapsed_sec": elapsed,
                          "error": f"超过 {timeout}s 未返回（后台可能仍在跑）"}}

    if "error" in box:
        return {"_meta": {"status": "call_failed", "elapsed_sec": elapsed,
                          "error": box["error"], "traceback": box.get("tb", "")}}

    structured, drafts, used_mock, failures = box["result"]
    fail_by_platform = {f.get("platform"): f.get("error") for f in (failures or [])}
    out: dict = {"_meta": {"status": "ok", "elapsed_sec": elapsed,
                           "used_mock": bool(used_mock)}}
    for pd in drafts:
        code = pd.platform_code
        out[code] = {"draft": flatten_draft(pd)}
    for code, err in fail_by_platform.items():
        out[code] = {"call_failed": err}
    out["_structured"] = structured
    return out


def assert_material(item: dict, raw: dict, platforms: list[str],
                    rules_by_platform: dict[str, dict] | None = None) -> dict:
    """对一条素材的每个平台跑断言。返回 {platform: {ok, results[], reason}}。

    `rules_by_platform` 来自 `platform-dna`（每平台一套阈值）+ 用户 `--rules` 覆盖。
    """
    structured = raw.get("_structured")
    rbp = rules_by_platform or {}
    out: dict = {}
    for code in platforms:
        cell = raw.get(code)
        if not cell:
            out[code] = {"ok": False, "kind": "call_failed",
                         "reason": "这一格没有产物（未返回也未报错）", "assertions": []}
            continue
        if "call_failed" in cell:
            out[code] = {"ok": False, "kind": "call_failed",
                         "reason": cell["call_failed"], "assertions": []}
            continue
        ctx = A.make_ctx(item, structured, code, cell["draft"], rbp.get(code) or {})
        res = A.check_all(ctx)
        failed = [r for r in res if not r.ok]
        out[code] = {"ok": not failed, "kind": "assert" if failed else "pass",
                     "reason": "；".join(r.detail for r in failed) or "",
                     "assertions": [{"code": r.code, "ok": r.ok, "detail": r.detail}
                                    for r in res]}
    return out


# ---------------------------------------------------------------- 报告

def write_report(out_dir: Path, manifest: dict, cells: dict, platforms: list[str],
                 items: list[dict], elapsed: float, baseline_path: Path | None,
                 rules_by_platform: dict[str, dict] | None = None) -> dict:
    fp = fp_mod.fingerprint()
    dna_fp = dna_rules_mod.fingerprint()
    total = len(items) * len(platforms)
    n_ok = sum(1 for mid in cells for c in platforms if cells[mid].get(c, {}).get("ok"))
    n_assert_fail = sum(1 for mid in cells for c in platforms
                        if cells[mid].get(c, {}).get("kind") == "assert")
    n_call_fail = sum(1 for mid in cells for c in platforms
                      if cells[mid].get(c, {}).get("kind") == "call_failed")
    n_missing = total - n_ok - n_assert_fail - n_call_fail

    report = {
        "_scope_note": SCOPE_NOTE,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "corpus": {"version": manifest.get("version"),
                   "count": len(items),
                   "prompt_fingerprint": manifest.get("gen", {}).get("prompt_fingerprint")},
        "prompt_fingerprint": fp,
        "prompt_fingerprint_combined": fp_mod.combined(fp),
        # DNA 指纹：没有它，「通过率变了」分不清是 prompt 改了、DNA 改了、还是模型换了
        "dna_fingerprint": dna_fp,
        "dna_fingerprint_combined": dna_rules_mod.combined(dna_fp),
        "rules_by_platform": {p: (rules_by_platform or {}).get(p, {}) for p in platforms},
        "model_configured": get_settings().llm_model,
        "mock": llm.is_mock(),
        "platforms": platforms,
        "totals": {"cells": total, "passed": n_ok, "assert_failed": n_assert_fail,
                   "call_failed": n_call_fail, "missing": n_missing,
                   "pass_rate": round(n_ok / total, 4) if total else 0.0},
        "by_platform": _by_platform(cells, platforms),
        "elapsed_sec": round(elapsed, 1),
        "cells": {mid: cells[mid] for mid in cells},
    }

    if baseline_path:
        report["diff"] = diff_baseline(report, baseline_path)

    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    (out_dir / "report.md").write_text(render_md(report, items), encoding="utf-8", newline="\n")
    return report


def _by_platform(cells: dict, platforms: list[str]) -> dict:
    """按平台切片 —— 改了某平台的 DNA，就要能只看那个平台的通过率。"""
    out: dict[str, dict] = {}
    for code in platforms:
        ok = af = cf = tot = 0
        for mid, res in cells.items():
            cell = res.get(code) or {}
            if not cell:
                continue
            tot += 1
            if cell.get("ok"):
                ok += 1
            elif cell.get("kind") == "call_failed":
                cf += 1
            else:
                af += 1
        out[code] = {"cells": tot, "passed": ok, "assert_failed": af,
                     "call_failed": cf,
                     "pass_rate": round(ok / tot, 4) if tot else 0.0}
    return out


def diff_baseline(report: dict, baseline_path: Path) -> dict:
    """与基线对比。**prompt 指纹不同 → 标为不可比**，不静默比较。"""
    base = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    same_fp = (base.get("prompt_fingerprint_combined")
               == report.get("prompt_fingerprint_combined"))
    same_model = base.get("model_configured") == report.get("model_configured")
    # DNA 变了也不能直接比 —— 改了平台规则，通过率变化本来就不是"退化"
    same_dna = (base.get("dna_fingerprint_combined")
                == report.get("dna_fingerprint_combined"))
    comparable = same_fp and same_model and same_dna

    regressed, improved, fixed = [], [], []
    for mid, cells in report["cells"].items():
        b = (base.get("cells") or {}).get(mid) or {}
        for code, cell in cells.items():
            was = (b.get(code) or {}).get("ok")
            now = cell.get("ok")
            if was is None:
                continue
            if was and not now:
                regressed.append(f"{mid}/{code}")
            elif not was and now:
                improved.append(f"{mid}/{code}")
            elif not was and not now and cell.get("kind") == "pass":
                fixed.append(f"{mid}/{code}")
    why = ""
    if not comparable:
        if not same_fp:
            why = "prompt 指纹不同"
        elif not same_model:
            why = "模型不同"
        else:
            why = "platform-dna 指纹不同（改了平台规则）"
    return {"baseline": str(baseline_path), "comparable": comparable,
            "same_prompt_fingerprint": same_fp, "same_model": same_model,
            "same_dna_fingerprint": same_dna, "reason": why,
            "regressed": sorted(regressed), "improved": sorted(improved)}


def render_md(r: dict, items: list[dict]) -> str:
    t = r["totals"]
    L = [
        "# 生成管线评测报告", "",
        f"> **{r['_scope_note']}**", "",
        f"- 时间：{r['ts']}　　耗时：{r['elapsed_sec']}s",
        f"- 素材集：{r['corpus']['version']}（{r['corpus']['count']} 条）"
        f"　prompt 指纹：`{r['prompt_fingerprint_combined']}`",
        f"- 模型（配置）：{r['model_configured']}　mock={r['mock']}",
        f"- 平台：{', '.join(r['platforms'])}", "",
        "## 通过率", "",
        f"| 指标 | 值 |", "|---|---|",
        f"| 格子总数 | {t['cells']} |",
        f"| **通过** | {t['passed']} |",
        f"| 断言失败 | {t['assert_failed']} |",
        f"| 调用失败（超时/限流等，**非**质量信号） | {t['call_failed']} |",
        f"| 缺失 | {t['missing']} |",
        f"| **通过率** | **{t['pass_rate']:.1%}** |", "",
    ]
    if r.get("by_platform"):
        L += ["## 分平台", "", "| 平台 | 通过 / 总数 | 通过率 | 断言失败 | 调用失败 |", "|---|---|---|---|---|"]
        for code, v in r["by_platform"].items():
            L.append(f"| {code} | {v['passed']} / {v['cells']} | {v['pass_rate']:.0%} "
                     f"| {v['assert_failed']} | {v['call_failed']} |")
        L.append("")
    if "diff" in r:
        d = r["diff"]
        L += ["## 与基线对比", "",
              f"- 基线：`{d['baseline']}`　可比：**{d['comparable']}**"
              + (f"（不可比原因：{d['reason']}）" if not d["comparable"] else ""),
              f"- 退化 {len(d['regressed'])} 个：{d['regressed'][:10]}",
              f"- 改善 {len(d['improved'])} 个：{d['improved'][:10]}", ""]
    L += ["## 逐格明细", "", "| 素材 | 平台 | 结果 | 说明 |", "|---|---|---|---|"]
    title_of = {it["id"]: it.get("title", "")[:18] for it in items}
    for mid in sorted(r["cells"]):
        for code in r["platforms"]:
            c = r["cells"][mid].get(code) or {}
            mark = "✅" if c.get("ok") else ("⛔" if c.get("kind") == "call_failed" else "❌")
            reason = (c.get("reason") or "")[:80].replace("|", "/")
            L.append(f"| {mid} {title_of.get(mid, '')} | {code} | {mark} | {reason} |")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- 主流程

def auto_timeout(n_platforms: int) -> int:
    """按平台数推算单条素材的墙钟上限。

    为什么需要它：实测**单平台**端到端 101~250s（`docs/46`），5 平台轻松超过 10 分钟。
    写死一个 600s 的默认值会把正常的长跑**误判成超时** —— 整条素材的格子全废，
    污染基线（这个坑我在启动全量跑时踩到过）。

    400s/平台 + 600s 底：够宽，又不至于在真挂住时无限等。
    """
    return max(600, 400 * max(1, n_platforms))


def estimate(items: list[dict], platforms: list[str]) -> dict:
    """粗算成本：每平台约 3~4 次 LLM 调用（brief/draft/qa + 可能的 clip），
    理解阶段每条素材 1 次。"""
    n_mat, n_plat = len(items), len(platforms)
    per_platform = 3 + (1 if any(p in A.VIDEO_NATIVE for p in platforms) else 0)
    calls = n_mat * (1 + n_plat * per_platform)
    return {"materials": n_mat, "platforms": n_plat, "cells": n_mat * n_plat,
            "approx_llm_calls": calls}


def main() -> int:
    ap = argparse.ArgumentParser(description="评测集跑分器（真实生成）")
    ap.add_argument("--corpus", type=Path, default=CORPUS_DIR)
    ap.add_argument("--out", type=Path, default=None, help="默认 eval/reports/<时间戳>/")
    ap.add_argument("--limit", type=int, default=0, help="只用前 N 条素材（0=全部）")
    ap.add_argument("--platforms", default=",".join(DEFAULT_PLATFORMS))
    ap.add_argument("--no-reuse", action="store_true", help="忽略已有结果，全部重跑")
    ap.add_argument("--timeout", type=int, default=0,
                    help="单条素材墙钟上限（秒）。0=自动（按平台数算），-1=不限")
    ap.add_argument("--jobs", type=int, default=1, help="并行跑几条素材（默认 1=串行）")
    ap.add_argument("--rules", default="", help="断言阈值 JSON 文件（覆盖默认值）")
    ap.add_argument("--baseline", type=Path, default=None, help="基线 report.json")
    ap.add_argument("--dry-run", action="store_true", help="只算成本，不发请求")
    args = ap.parse_args()

    manifest, items = load_corpus(args.corpus)
    if args.limit > 0:
        items = items[:args.limit]
    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    bad = [p for p in platforms if p not in ALL_PLATFORMS]
    if bad:
        raise SystemExit(f"[失败] 未知平台 {bad}；可用：{ALL_PLATFORMS}")

    override = json.loads(Path(args.rules).read_text(encoding="utf-8")) if args.rules else {}
    # 每平台一套阈值：platform-dna 为准，--rules 是**全局覆盖**（调试用）
    rules_by_platform: dict[str, dict] = {}
    for code in platforms:
        merged = dict(dna_rules_mod.rules_for(code))
        merged.update(override)
        rules_by_platform[code] = merged

    if args.dry_run:
        est = estimate(items, platforms)
        eff = args.timeout if args.timeout != 0 else auto_timeout(len(platforms))
        print(json.dumps({**est, "rules_by_platform": rules_by_platform,
                          "per_material_timeout_sec": eff if eff > 0 else "不限",
                          "dry_run": True, "no_llm_request_sent": True},
                         ensure_ascii=False, indent=2))
        return 0

    if llm.is_mock():
        print("[警告] 当前是 mock 模式 —— 通过率**不代表真实质量**，只是流程自检。")

    out_dir = args.out or (REPORTS_DIR / time.strftime("%Y%m%d-%H%M%S"))
    cells_dir = out_dir / "cells"
    cells_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    cells: dict[str, dict] = {}
    todo: list[dict] = []
    for it in items:
        f = cells_dir / f"{it['id']}.json"
        if f.exists() and not args.no_reuse:
            cells[it["id"]] = json.loads(f.read_text(encoding="utf-8"))
            print(f"  [跳过] {it['id']} 已有结果（--no-reuse 可强制重跑）")
        else:
            todo.append(it)

    timeout = args.timeout if args.timeout != 0 else auto_timeout(len(platforms))
    if timeout < 0:
        timeout = 0
    print(f"单条素材超时上限：{timeout or '不限'}s")

    def process(it: dict) -> tuple[str, dict]:
        raw = run_one(it, platforms, timeout)
        if raw["_meta"]["status"] != "ok":
            res = {p: {"ok": False, "kind": "call_failed",
                       "reason": raw["_meta"].get("error", "调用失败"), "assertions": []}
                   for p in platforms}
        else:
            res = assert_material(it, raw, platforms, rules_by_platform)
        res["_meta"] = raw["_meta"]
        # 存下产物与理解结果：否则报告说"数字 99 没依据"却看不到稿子、也无法离线复算
        # （实测踩到过 —— 改一条断言就得重跑几小时，有了这个就能离线重算）
        res["_drafts"] = {p: (raw.get(p) or {}) for p in platforms}
        res["_structured"] = raw.get("_structured")
        return it["id"], res

    if args.jobs > 1 and len(todo) > 1:
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            for mid, res in ex.map(process, todo):
                cells[mid] = res
                (cells_dir / f"{mid}.json").write_text(
                    json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
                print(f"  [完成] {mid} {res['_meta']['status']} {res['_meta']['elapsed_sec']}s")
                try:
                    write_report(out_dir, manifest, cells, platforms, items,
                                 time.time() - t0, args.baseline, rules_by_platform)
                except Exception as e:  # noqa: BLE001
                    print(f"  [警告] 中途刷新报告失败（不影响跑分）：{e}")
    else:
        for it in todo:
            mid, res = process(it)
            cells[mid] = res
            (cells_dir / f"{mid}.json").write_text(
                json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8", newline="\n")
            n_ok = sum(1 for p in platforms if res.get(p, {}).get("ok"))
            print(f"  [完成] {mid} {res['_meta']['status']} "
                  f"{n_ok}/{len(platforms)} 通过 {res['_meta']['elapsed_sec']}s")
            # 每跑完一条就刷新报告：整轮可能几小时，中途被中断（超时/断电/429）
            # 时不能一个报告都没有 —— 那等于白跑。
            try:
                write_report(out_dir, manifest, cells, platforms, items,
                             time.time() - t0, args.baseline, rules_by_platform)
            except Exception as e:  # noqa: BLE001 — 刷新失败不该中断跑分
                print(f"  [警告] 中途刷新报告失败（不影响跑分）：{e}")

    report = write_report(out_dir, manifest, cells, platforms, items,
                          time.time() - t0, args.baseline, rules_by_platform)
    t = report["totals"]
    print(f"\n通过率 {t['pass_rate']:.1%}（{t['passed']}/{t['cells']}）"
          f"　断言失败 {t['assert_failed']}　调用失败 {t['call_failed']}")
    print(f"报告：{out_dir / 'report.md'}")
    print(f"      {out_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
