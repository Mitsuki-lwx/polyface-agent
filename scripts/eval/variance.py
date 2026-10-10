"""多次跑分的**方差分析** —— 判断"差异是否有意义"。

**为什么需要**：`Quantifying Variance in Evaluation Benchmarks`（arXiv 2406.10229）
说评测基准被广泛用来做决策，却几乎没人量化方差 —— 而方差恰恰决定"性能差异是否显著"。

我们实测踩过：同一份代码、同一批素材，二值通过率在 **38%~78%** 之间跳。
在那之前，我拿单次采样报过好几轮"提升/退化"，**全是噪声**。

用法（先用同一个 `--out` 跑多次，每次换目录，再用本脚本对比）：

    for i in 1 2 3; do
      python scripts/eval/run_eval.py --out eval/reports/v$i
    done
    python scripts/eval/variance.py eval/reports/v1 eval/reports/v2 eval/reports/v3

它回答三个问题：
  1. 每个格子**翻面**几次（同一输入、同一代码，结论稳不稳）
  2. 两个指标的**波动幅度**各是多少
  3. 哪条断言最不稳（噪声源在哪）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import ROOT  # noqa: E402,F401


def load(run_dir: Path) -> dict:
    p = run_dir / "report.json"
    if not p.exists():
        raise SystemExit(f"[失败] {p} 不存在 —— 先跑 run_eval.py")
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="多次跑分的方差分析")
    ap.add_argument("runs", nargs="+", help="至少两个 report 目录")
    args = ap.parse_args()
    if len(args.runs) < 2:
        raise SystemExit("[失败] 至少给两个 run 目录才能比方差")

    dirs = [Path(r) for r in args.runs]
    reports = [load(d) for d in dirs]
    platforms = reports[0].get("platforms") or []
    n = len(reports)

    print(f"对比 {n} 次跑分：")
    for d, r in zip(dirs, reports):
        t = r["totals"]
        sr = t.get("support_rate")
        sr_s = f"{sr:.2%}" if sr is not None else "-"
        print(f"  {str(d):32s} 二值 {t['pass_rate']:6.1%}   支持率 {sr_s}")

    # ---- 每个格子的翻面次数
    all_mids = sorted({m for r in reports for m in (r.get("cells") or {})})
    flips: list[tuple[str, str, int]] = []
    for mid in all_mids:
        for code in platforms:
            oks = []
            for r in reports:
                cell = (r.get("cells") or {}).get(mid, {}).get(code)
                if cell is None:
                    continue
                oks.append(bool(cell.get("ok")))
            if len(oks) >= 2 and len(set(oks)) > 1:
                flips.append((mid, code, sum(oks)))

    total_cells = len(all_mids) * len(platforms)
    print()
    print(f"格子翻面：{len(flips)}/{total_cells} "
          f"（同一输入、同一代码，结论不一致）")
    for mid, code, passed in flips[:12]:
        print(f"    {mid}/{code:9s} {passed}/{n} 次通过")
    if len(flips) > 12:
        print(f"    … 另有 {len(flips) - 12} 个")

    # ---- 指标波动
    br = [r["totals"]["pass_rate"] for r in reports]
    sr = [r["totals"].get("support_rate") for r in reports]
    sr_ok = [x for x in sr if x is not None]
    print()
    print(f"指标波动幅度：")
    print(f"  二值通过率  {min(br):.1%} ~ {max(br):.1%}   （跨度 {max(br) - min(br):.1%}）")
    if sr_ok:
        print(f"  原子支持率  {min(sr_ok):.2%} ~ {max(sr_ok):.2%}   "
              f"（跨度 {max(sr_ok) - min(sr_ok):.2%}）")

    # ---- 哪条断言最不稳
    unstable: dict[str, int] = {}
    for mid in all_mids:
        for code in platforms:
            verdicts: dict[str, set[bool]] = {}
            for r in reports:
                cell = (r.get("cells") or {}).get(mid, {}).get(code)
                for a in (cell or {}).get("assertions") or []:
                    verdicts.setdefault(a["code"], set()).add(bool(a["ok"]))
            for k, v in verdicts.items():
                if len(v) > 1:
                    unstable[k] = unstable.get(k, 0) + 1
    if unstable:
        print()
        print("最不稳的断言（同一输入、结论在多次跑之间变）：")
        for k, v in sorted(unstable.items(), key=lambda x: -x[1]):
            print(f"    {k:20s} 变了 {v} 次")

    # ---- 结论
    print()
    if sr_ok and max(br) - min(br) > 0.05:
        print("结论：**二值通过率不可用于跨版本比较**（本次跨度 "
              f"{max(br) - min(br):.0%}）；支持率明显更稳，跨版本请以它为准。")
    else:
        print("结论：本次各指标跨度都不大，但仍建议以支持率为主、二值为辅。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
