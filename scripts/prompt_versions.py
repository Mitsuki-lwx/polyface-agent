"""提示词版本反查（`docs/tasks_promptver.md` T5）。

没有它，`prompt_fp` 就只是"多了一列看不懂的东西" ✗。

    python scripts/prompt_versions.py --list              # 用过的版本（哈希/首次/次数）
    python scripts/prompt_versions.py --show <fp>         # 该版本的模板全文
    python scripts/prompt_versions.py --diff <fp1> <fp2>  # 两份模板的差异
    python scripts/prompt_versions.py --current           # 当前代码里各 scene 的版本号
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python-service"))

from app.pipeline import promptstore, prompts  # noqa: E402
from app.usage import usage_path  # noqa: E402


def used_versions() -> list[tuple[str, str, int]]:
    """从用量日志里统计用过的版本：`[(哈希, 首次时间, 次数)]`（按首次时间排）。"""
    p = usage_path()
    if not p.is_file():
        return []
    first: dict[str, str] = {}
    count: Counter[str] = Counter()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        fp = (e.get("prompt_fp") or "").strip()
        if not fp:
            continue
        count[fp] += 1
        ts = e.get("ts") or ""
        if fp not in first or (ts and ts < first[fp]):
            first[fp] = ts
    return sorted(((fp, first.get(fp, ""), n) for fp, n in count.items()),
                  key=lambda x: x[1])


def cmd_list() -> int:
    rows = used_versions()
    if not rows:
        print("用量日志里还没有带 prompt_fp 的记录。")
        print("（老记录没有这个字段是正常的 —— 版本化是后加的。）")
        return 0
    print(f"用过 {len(rows)} 个提示词版本：")
    print(f"  {'版本':<14} {'首次使用':<22} 次数  模板")
    for fp, ts, n in rows:
        has = "有" if promptstore.load(fp) else "**缺失**"
        print(f"  {fp:<14} {ts[:19]:<22} {n:>4}  {has}")
    return 0


def cmd_show(fp: str) -> int:
    text = promptstore.load(fp)
    if not text:
        print(f"没有 {fp} 的模板全文（可能没存下来，或这个版本不是本机产生的）。",
              file=sys.stderr)
        return 1
    print(text)
    return 0


def cmd_diff(a: str, b: str) -> int:
    ta, tb = promptstore.load(a), promptstore.load(b)
    if not ta or not tb:
        print("两个版本都要有模板全文才能比。", file=sys.stderr)
        return 1
    d = difflib.unified_diff(ta.splitlines(), tb.splitlines(),
                             fromfile=a, tofile=b, lineterm="", n=2)
    out = list(d)
    print("\n".join(out) if out else "两份模板完全相同。")
    return 0


def cmd_current() -> int:
    print("当前代码里各 scene 的版本号：")
    for scene, fp in prompts.fingerprints().items():
        print(f"  {scene:<14} {fp or '(取不到 ✗)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="提示词版本反查")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="列出用过的版本")
    g.add_argument("--current", action="store_true", help="列出当前代码里的版本号")
    g.add_argument("--show", metavar="FP", help="打印该版本的模板全文")
    g.add_argument("--diff", nargs=2, metavar=("FP1", "FP2"), help="比较两份模板")
    a = ap.parse_args(argv)
    if a.list:
        return cmd_list()
    if a.current:
        return cmd_current()
    if a.show:
        return cmd_show(a.show)
    return cmd_diff(*a.diff)


if __name__ == "__main__":
    sys.exit(main())
