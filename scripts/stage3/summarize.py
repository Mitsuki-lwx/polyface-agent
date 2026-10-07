"""第三关（真实作者验证）· 判定器 + 记录表模板。

`docs/50` §3 把判定门槛**先定死**，目的就是"避免事后找理由"。但门槛写在文档里，
人填完表还是要自己算一遍 —— 那一步正是最容易手滑、也最容易自我说服的地方。
这个脚本把 §3 的门槛变成**可执行的判定**。

它**不产生证据**，只做汇总与判定。证据来自作者填的那张表。

用法：
    # 1) 生成一张空表给作者/你填
    python scripts/stage3/summarize.py --template 作者A > /tmp/作者A.json

    # 2) 填完（用任意编辑器改 JSON）后判定
    python scripts/stage3/summarize.py /tmp/作者A.json

    # 3) 需要时覆盖门槛（默认 = docs/50 §3）
    python scripts/stage3/summarize.py /tmp/作者A.json --faster-by 0.30

退出码：0 = 判定完成；2 = 输入不完整（**不猜**，缺项就报缺）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# docs/50 §3 的门槛
FASTER_BY_DEFAULT = 0.30        # polyface 至少快 30% 才算"明显少于"
GROUPS = ("control", "polyface")


def template(author: str) -> dict:
    """空记录表。字段与 `docs/50` §2.3 / §5 一一对应。"""
    def rows(material: str) -> list[dict]:
        return [{"material": material, "group": g, "minutes": None,
                 "published": None, "fact_errors": None, "notes": ""}
                for g in GROUPS]

    return {
        "_说明": "按 docs/50 §2.3 填。minutes=到「我认为可以发了」为止的分钟数（作者自己掐表）；"
                 "fact_errors=作者逐句核对出的「素材里没有的事实」条数。",
        "author": author,
        "date": "",
        "platform": "",
        "materials": [{"name": "素材1", "records": rows("素材1")},
                      {"name": "素材2", "records": rows("素材2")}],
        "answers": {
            "_说明": "docs/50 §2.4 的三问，当面问、记原话",
            "1_有没有想不到的句子": "",
            "2_下次还会用吗_最卡哪一步": "",
            "3_只能留一个功能留哪个": "",
        },
        "friction": {
            "_说明": "docs/50 §5：环境摩擦**与产品判断分开记**，失败原因要分清是哪一类",
            "install_minutes": None,
            "blockers": "",
            "first_config_difficulty": "",
            "self_diagnosable": "",
        },
    }


def _num(x, where: str, missing: list[str]):
    if x is None or x == "":
        missing.append(where)
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        missing.append(f"{where}（不是数字：{x!r}）")
        return None


def verdict(data: dict, faster_by: float) -> tuple[str, list[str], dict]:
    """按 docs/50 §3 判定。返回 (结论, 理由列表, 数字摘要)。"""
    missing: list[str] = []
    per_group: dict[str, list[float]] = {g: [] for g in GROUPS}
    fact_errors: dict[str, int] = {g: 0 for g in GROUPS}
    materials: list[str] = []

    for m in data.get("materials") or []:
        name = m.get("name") or "?"
        materials.append(name)
        seen = set()
        for r in m.get("records") or []:
            g = r.get("group")
            if g not in GROUPS:
                missing.append(f"{name} 的 group={g!r} 不认识")
                continue
            seen.add(g)
            v = _num(r.get("minutes"), f"{name}/{g}.minutes", missing)
            if v is not None:
                per_group[g].append(v)
            e = _num(r.get("fact_errors"), f"{name}/{g}.fact_errors", missing)
            if e is not None:
                fact_errors[g] += int(e)
        for g in GROUPS:
            if g not in seen:
                missing.append(f"{name} 缺 {g} 组")

    if missing:
        return "无法判定", [f"输入不完整：{x}" for x in missing], {}

    ctrl = sum(per_group["control"]) / len(per_group["control"])
    poly = sum(per_group["polyface"]) / len(per_group["polyface"])
    faster = (ctrl - poly) / ctrl if ctrl else 0.0
    summary = {
        "materials": materials,
        "control_avg_min": round(ctrl, 1),
        "polyface_avg_min": round(poly, 1),
        "faster_ratio": round(faster, 3),
        "threshold": faster_by,
        "fact_errors_control": fact_errors["control"],
        "fact_errors_polyface": fact_errors["polyface"],
    }

    reuse = str((data.get("answers") or {}).get("2_下次还会用吗_最卡哪一步") or "")
    reasons: list[str] = []

    # ❌ 方向不成立：编造事实 / 不快 / 明确说不用（且理由是产品性的）
    if fact_errors["polyface"] > 0:
        reasons.append(f"polyface 组出现 {fact_errors['polyface']} 条「素材里没有的事实」→ 按 §3 判方向不成立")
        return "方向不成立", reasons, summary
    if faster <= 0:
        reasons.append(f"polyface 平均 {poly:.1f} 分钟 **不低于** 对照组 {ctrl:.1f} 分钟 → 按 §3 判方向不成立")
        return "方向不成立", reasons, summary
    if reuse and ("不用" in reuse or "不会用" in reuse):
        reasons.append(f"作者答「下次不用」：{reuse!r} —— **需人工确认理由是不是产品性的**"
                       "（若是环境/安装问题，按 §3 不算方向不成立）")

    if faster >= faster_by:
        reasons.append(f"polyface 平均 {poly:.1f} 分钟，比对照组 {ctrl:.1f} 分钟快 {faster:.1%}"
                       f"（门槛 ≥{faster_by:.0%}）")
        reasons.append("两份素材均未出现「素材里没有的事实」")
        return "值得继续投入", reasons, summary

    reasons.append(f"只快 {faster:.1%}，未达 {faster_by:.0%} 门槛")
    return "需要修具体环节", reasons, summary


def render(data: dict, v: str, reasons: list[str], s: dict) -> str:
    L = [f"# 第三关判定 · {data.get('author') or '（未填作者）'}", ""]
    L += [f"日期：{data.get('date') or '（未填）'}　平台：{data.get('platform') or '（未填）'}", ""]
    if s:
        L += ["## 数字", "", "| 项 | 值 |", "|---|---|",
              f"| 素材 | {'、'.join(s['materials'])} |",
              f"| 对照组平均 | {s['control_avg_min']} 分钟 |",
              f"| polyface 平均 | {s['polyface_avg_min']} 分钟 |",
              f"| 快了多少 | {s['faster_ratio']:.1%}（门槛 ≥{s['threshold']:.0%}） |",
              f"| 事实错误（对照 / polyface） | {s['fact_errors_control']} / {s['fact_errors_polyface']} |", ""]
    L += [f"## 结论：**{v}**", ""]
    L += [f"- {r}" for r in reasons]
    L += ["", "> 环境摩擦与产品判断必须分开记（docs/50 §5）。"
              "装不起来/跑不起来**不算**方向不成立。", ""]
    L += ["## 作者三问（原话）", ""]
    for k, val in (data.get("answers") or {}).items():
        if k.startswith("_"):
            continue
        L.append(f"- **{k}**：{val or '（未填）'}")
    L += ["", "## 环境摩擦", ""]
    for k, val in (data.get("friction") or {}).items():
        if k.startswith("_"):
            continue
        L.append(f"- **{k}**：{val if val not in (None, '') else '（未填）'}")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="第三关判定器（docs/50 §3 的门槛）")
    ap.add_argument("records", nargs="?", help="填好的记录表 JSON")
    ap.add_argument("--template", metavar="作者名", help="输出一张空表")
    ap.add_argument("--faster-by", type=float, default=FASTER_BY_DEFAULT,
                    help=f"「明显更快」的门槛（默认 {FASTER_BY_DEFAULT}）")
    ap.add_argument("--md", action="store_true", help="输出 Markdown（默认输出 JSON 摘要）")
    args = ap.parse_args()

    if args.template:
        print(json.dumps(template(args.template), ensure_ascii=False, indent=2))
        return 0

    if not args.records:
        ap.error("要么给 --template，要么给填好的记录表路径")

    data = json.loads(Path(args.records).read_text(encoding="utf-8"))
    v, reasons, s = verdict(data, args.faster_by)

    if args.md:
        print(render(data, v, reasons, s))
    else:
        print(json.dumps({"verdict": v, "reasons": reasons, "numbers": s},
                         ensure_ascii=False, indent=2))
    return 0 if v != "无法判定" else 2


if __name__ == "__main__":
    sys.exit(main())
