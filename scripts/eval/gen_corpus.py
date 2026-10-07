"""T1 · 素材生成器 —— 一次性工具，产出后**冻结**。

`docs/spec_eval.md` §5 关键决定一：素材用 LLM 合成后冻结，不抓网上的真实语料。
本脚本**只在需要换一批/扩充素材时手动跑**；跑分链路不调用它。

用法：
    python scripts/eval/gen_corpus.py --dry-run              # 只打印提示词，不调模型
    python scripts/eval/gen_corpus.py --count 10             # 生成 10 条到 eval/corpus/
    python scripts/eval/gen_corpus.py --count 3 --numbers 4  # 特征可调

产出：
    eval/corpus/<id>.json     每条素材（正文 + 特征记录 + 生成元信息）
    eval/corpus/MANIFEST.json 素材集清单（改动它 == 改测试基准）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import CORPUS_DIR, ROOT, load_env  # noqa: E402

from app import llm, usage  # noqa: E402
from app.trace import current_trace_id  # noqa: E402
from app.pipeline import generate as gen  # noqa: E402
from app.config import get_settings  # noqa: E402

import fingerprint as fp_mod  # noqa: E402

# 主题轮换 —— 让 10 条素材不至于同一个味道
TOPICS = [
    "个人成长与职业转折（辞职/转行/第一次独立做成一件事）",
    "副业与收入结构（做副业多久、赚了多少、踩了什么坑）",
    "效率工具与方法（自己怎么用某个工具/流程把事做完）",
    "行业观察（某个行业最近发生了什么变化，自己的判断）",
    "健康与运动（坚持某项运动的真实变化，含体感数据）",
    "学习与考试（备考某个证书/技能的过程与结果）",
    "内容创作（自己做账号的经历：选题、涨粉、数据）",
    "理财与消费（一笔具体的钱怎么安排的，结果如何）",
    "旅行与生活（一次具体的出行经历，含时间与花费）",
    "职场沟通（一次具体的沟通事件与复盘）",
]

SYSTEM = "你是内容素材生成器。只输出 JSON，不要任何解释、不要 markdown 围栏。"

PROMPT_TMPL = """造一份**像真实自媒体博主会喂给改写工具的原始素材**，用于内部评测。

硬性要求：
- 中文；正文 {chars_min}~{chars_max} 字
- 第一人称，口语或半正式，读起来像口播稿或长文笔记
- 至少含 {numbers} 个**具体数字**（金额/时间/数量/比例，要自然融进叙述）
- 至少含 {entities} 个**专有名词**（品牌/平台/工具/地点/机构）
- 至少含 {claims} 条**可核验的事实性断言**（有具体依据、能被查证的那种，不是主观感受）
- 不要出现"作为 AI"之类的元话语；不要 markdown 标题；不要分点编号
- 主题方向：{topic}

只输出这个 JSON：
{{"title": "一个短标题", "raw_text": "正文", "entities": ["专有名词1", "专有名词2"], "claims": ["可核验断言1", "可核验断言2"]}}
"""


def build_prompt(topic: str, args: argparse.Namespace) -> str:
    return PROMPT_TMPL.format(chars_min=args.chars_min, chars_max=args.chars_max,
                              numbers=args.numbers, entities=args.entities,
                              claims=args.claims, topic=topic)


def gen_one(topic: str, args: argparse.Namespace) -> dict:
    """生成一条素材。**数字由代码机械统计，不采信模型自报的**（它经常数错）。"""
    trace = current_trace_id()
    data = llm.chat_json(build_prompt(topic, args), system=SYSTEM,
                         temperature=args.temperature, model=args.model or None,
                         scene="eval_corpus")
    raw = (data.get("raw_text") or "").strip()
    if not raw:
        raise ValueError("模型没返回 raw_text")

    numbers = gen._numbers_in(raw)          # 复用线上同一套数字提取，别自己造一套
    return {
        "_trace": trace,
        "title": (data.get("title") or "").strip(),
        "topic": topic,
        "raw_text": raw,
        "features": {
            "chars": len(raw),
            # 机械统计出来的，可复现
            "numbers": sorted(numbers.keys()),
            "entities": [e for e in (data.get("entities") or []) if str(e).strip()],
            "claims": [c for c in (data.get("claims") or []) if str(c).strip()],
        },
    }


def check_features(item: dict, args: argparse.Namespace) -> list[str]:
    """返回不满足的项（空列表 = 合格）。"""
    f = item["features"]
    bad: list[str] = []
    if not (args.chars_min <= f["chars"] <= args.chars_max):
        bad.append(f"字数 {f['chars']} 不在 {args.chars_min}~{args.chars_max}")
    if len(f["numbers"]) < args.numbers:
        bad.append(f"数字 {len(f['numbers'])} < {args.numbers}")
    if len(f["entities"]) < args.entities:
        bad.append(f"专有名词 {len(f['entities'])} < {args.entities}")
    if len(f["claims"]) < args.claims:
        bad.append(f"可核验断言 {len(f['claims'])} < {args.claims}")
    return bad


def models_used_since(started_utc: str) -> dict[str, int]:
    """本次运行**真正服务过请求的模型**及其调用次数。

    为什么不能只记 `.env` 里配的那个：实测本次生成时 `deepseek-v4-flash` 额度耗尽，
    自动降级到 `deepseek-v4-pro` / `glm-5.2`。只记配置值等于**写了一个没发生过的实测值**
    —— 本项目明令禁止这个（MEMORY：数字要写实测值）。
    """
    counts: dict[str, int] = {}
    for e in usage.summary(limit=500)["recent"]:
        if e.get("scene") != "eval_corpus" or not e.get("ok"):
            continue
        if str(e.get("ts") or "") < started_utc:
            continue
        m = e.get("model") or "unknown"
        counts[m] = counts.get(m, 0) + 1
    return counts


def models_for_traces(traces: set[str]) -> dict[str, list[str]]:
    """trace_id -> 该条素材**成功时**实际服务的模型列表。

    生成时若发生降级（额度耗尽/限流换模型），配置里的模型名就与实际不符；
    按 trace 归属才能知道「这条素材到底是哪个模型造的」。
    """
    out: dict[str, list[str]] = {}
    for e in usage.summary(limit=500)["recent"]:
        if e.get("scene") != "eval_corpus" or not e.get("ok"):
            continue
        t = str(e.get("trace_id") or "")
        if t not in traces:
            continue
        m = e.get("model") or "unknown"
        out.setdefault(t, [])
        if m not in out[t]:
            out[t].append(m)
    return out


def _aggregate(by_trace: dict[str, list[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for models in by_trace.values():
        for m in models:
            counts[m] = counts.get(m, 0) + 1
    return counts


def _all_eval_models() -> dict[str, int]:
    """兜底：用量日志里所有 eval_corpus 成功调用的模型分布（无 trace 时用）。"""
    counts: dict[str, int] = {}
    for e in usage.summary(limit=500)["recent"]:
        if e.get("scene") == "eval_corpus" and e.get("ok"):
            m = e.get("model") or "unknown"
            counts[m] = counts.get(m, 0) + 1
    return counts


def build_manifest(items: list[dict], args: argparse.Namespace,
                   models_used: dict[str, int] | None = None) -> dict:
    settings = get_settings()
    return {
        "_note": "素材集清单。**改动本文件等同于改动测试基准**，必须显式提交并在 PR 里说明。",
        "version": args.version,
        "count": len(items),
        "spec": {
            "chars_min": args.chars_min, "chars_max": args.chars_max,
            "min_numbers": args.numbers, "min_entities": args.entities,
            "min_claims": args.claims,
        },
        "gen": {
            "model_configured": args.model or settings.llm_model,
            "models_used": models_used or {},
            "mock": settings.llm_mock,
            "temperature": args.temperature,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "prompt_fingerprint": fp_mod.combined(),
        },
        "items": [
            {"id": it["id"], "title": it["title"], "topic": it["topic"],
             "features": {k: (len(v) if isinstance(v, list) else v)
                          for k, v in it["features"].items()}}
            for it in items
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="评测集素材生成器（一次性工具）")
    ap.add_argument("--count", type=int, default=10, help="生成条数")
    ap.add_argument("--chars-min", type=int, default=300)
    ap.add_argument("--chars-max", type=int, default=2000)
    ap.add_argument("--numbers", type=int, default=3, help="每条最少数字个数")
    ap.add_argument("--entities", type=int, default=2, help="每条最少专有名词个数")
    ap.add_argument("--claims", type=int, default=2, help="每条最少可核验断言条数")
    ap.add_argument("--temperature", type=float, default=0.9, help="要多样性，调高")
    ap.add_argument("--model", default="", help="覆盖模型（空=用 .env）")
    ap.add_argument("--version", default="v1", help="素材集版本，写进 manifest")
    ap.add_argument("--out", type=Path, default=CORPUS_DIR)
    ap.add_argument("--dry-run", action="store_true", help="只打印提示词，不调模型")
    ap.add_argument("--manifest-only", action="store_true",
                    help="只按 <out> 下已有素材重建 MANIFEST.json（不调模型）")
    ap.add_argument("--max-attempts", type=int, default=3, help="单条不合格时的重造次数")
    args = ap.parse_args()

    if args.dry_run:
        print(f"[dry-run] 会生成 {args.count} 条到 {args.out}")
        print(f"[dry-run] 提示词示例（主题 1/{len(TOPICS)}）：\n")
        print(build_prompt(TOPICS[0], args))
        return 0

    settings = get_settings()

    if args.manifest_only:
        items = []
        # ⚠️ 不能用 m*.json —— Windows 的 glob **不区分大小写**，会把 MANIFEST.json 也匹配进来
        for f in sorted(args.out.glob("m[0-9][0-9].json")):
            items.append(json.loads(f.read_text(encoding="utf-8")))
        if not items:
            print(f"[失败] {args.out} 下没有素材")
            return 1
        # 逐条 trace 已落盘，可精确归属；缺 trace 的老素材退回运行级统计
        by_trace = models_for_traces(
            {it.get("gen", {}).get("trace") or "" for it in items} - {""})
        mf = build_manifest(items, args, {})
        mf["gen"]["models_used"] = _aggregate(by_trace) or _all_eval_models()
        (args.out / "MANIFEST.json").write_text(
            json.dumps(mf, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n")
        print(f"清单已重建：{len(items)} 条 -> {args.out / 'MANIFEST.json'}")
        print(f"实际服务模型：{mf['gen']['models_used']}")
        return 0

    if settings.llm_mock:
        print("[失败] 当前是 mock 模式 —— 生成的素材会是罐头，不能用作评测基准。")
        print("       请确认 python-service/.env 里 LLM_MOCK=false 且 LLM_API_KEY 已填。")
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    started_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    items: list[dict] = []
    for i in range(args.count):
        topic = TOPICS[i % len(TOPICS)]
        for attempt in range(1, args.max_attempts + 1):
            try:
                item = gen_one(topic, args)
            except (llm.LLMError, ValueError) as e:
                print(f"  [{i + 1}/{args.count}] 第 {attempt} 次调用失败：{e}")
                continue
            bad = check_features(item, args)
            if not bad:
                break
            print(f"  [{i + 1}/{args.count}] 第 {attempt} 次不合格：{'；'.join(bad)}，重造")
        else:
            print(f"  [{i + 1}/{args.count}] {args.max_attempts} 次都没造出合格素材，跳过")
            continue

        item["id"] = f"m{i + 1:02d}"
        item["gen"] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        path = args.out / f"{item['id']}.json"
        item["gen"]["trace"] = item.pop("_trace", "") or ""   # provenance：可与用量日志对账
        path.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
        items.append(item)
        f = item["features"]
        print(f"  [{i + 1}/{args.count}] {item['id']}  {f['chars']} 字 · "
              f"数字 {len(f['numbers'])} · 实体 {len(f['entities'])} · 断言 {len(f['claims'])}")

    if not items:
        print("[失败] 一条都没生成成功")
        return 1

    # 把「真正服务过的模型」按 trace 归到每条素材上（可能降级，不能只记配置值）
    by_trace = models_for_traces({it["_trace"] for it in items if it.get("_trace")})
    for it in items:
        it["gen"]["models"] = by_trace.get(it.get("_trace") or "", [])

    manifest = build_manifest(items, args, models_used_since(started_utc))
    (args.out / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    print(f"\n完成：{len(items)} 条 -> {args.out}")
    print(f"prompt 指纹：{manifest['gen']['prompt_fingerprint']}")
    print(f"实际服务模型：{manifest['gen']['models_used'] or '(读不到用量日志)'}")
    print("下一步：人工过一眼，删掉不通顺的，然后提交（改动 manifest 等同于改测试基准）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
