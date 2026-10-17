"""口播去冗余：判定"哪几段是在重复/口误/口头语"（`docs/spec_disfluency.md`）。

分两层，本模块是**第一层（规则打底）**：

- 规则层：快、确定、零成本、可解释。只抓**高置信**的（完全重复、独立口头语、半句重起）。
- 模型层（`disfluency_llm`）：只复核规则层**拿不准的相邻句对**（见 spec 关键决定三）。

**本模块不做剪点** —— 它只产出"疑似区间 + 理由"，剪点由 `merge_cuts` 统一合并，
最终由人确认（spec 关键决定二：模型/规则只判定，不决定）。

一条判定（Finding）长这样：
`{start, end, kind, evidence, source, confidence}`
- `kind`：`repeat`（说两遍）/ `restart`（半句重起）/ `filler`（口头语）/ `self_correct`（说错重说）
- `source`：`rule` / `llm`
- `confidence`：`high`（规则确定）/ `low`（规则不确定，留给模型复核）
- `evidence`：人能看懂的理由（相似度数值、命中的词、前后句原文）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 默认值
# 这些是"第一次跑不用查文档"的起点，**不是写死的值** ——
# 每一项都能从配置文件/命令行到达（见 `docs/spec_disfluency.md` §3 第 4 条）。

#: 口头语词表。**独立成词**才算（"那个人"里的"那个"不算）。
DEFAULT_FILLERS: tuple[str, ...] = (
    "嗯", "啊", "呃", "唉", "哦", "诶",
    "那个", "这个", "那个啥", "这个那个",
    "就是", "然后", "其实", "反正", "怎么说", "怎么讲",
)

#: 口头语左右"多大空隙"算它是独立说出来的（秒）。见 `find_fillers` 的说明。
DEFAULT_FILLER_GAP_SEC = 0.12

#: 半句重起：短句至少这么多字才算"半句"（太短的多半是正常口语）。
DEFAULT_MIN_RESTART_CHARS = 4

#: 相似度阈值（T3 用）。高于它 = 疑似重复，交给模型复核。
DEFAULT_SIMILARITY = 0.72

_PUNCT = re.compile(r"[\s，。、！？；：“”‘’（）()\[\]【】,.!?;:\"'`~·—…\-]+")


def normalize(text: str) -> str:
    """去标点/空白，用于"是不是同一句"的比较。"""
    return _PUNCT.sub("", text or "")


@dataclass
class Finding:
    """一条判定。`evidence` 是给人看的，不是给程序看的。"""
    start: float
    end: float
    kind: str
    evidence: str
    source: str = "rule"
    confidence: str = "high"
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = {"start": round(self.start, 3), "end": round(self.end, 3), "kind": self.kind,
             "evidence": self.evidence, "source": self.source, "confidence": self.confidence}
        d.update(self.extra)
        return d


# ---------------------------------------------------------------- 相似度（T3）


def similarity(a: str, b: str) -> float:
    """两句有多像：字符级与词级取**高**的那个（[0,1]）。

    为什么两种都算：中文口语里"换个说法重说"可能字全变但词一样
    （"这事的核心是执行" / "核心在于执行这件事"），也可能字很像但切词不同。
    取高值 = 宁可多标一点（标出来是**低置信**，还要过模型那一关），不放过真重复。
    """
    import difflib

    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    char_ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    # 词级：按 2-gram 切（中文没有空格，2-gram 是常用近似）
    ga = {na[i:i + 2] for i in range(len(na) - 1)} or {na}
    gb = {nb[i:i + 2] for i in range(len(nb) - 1)} or {nb}
    word_ratio = len(ga & gb) / len(ga | gb)
    return max(char_ratio, word_ratio)


def find_similar_repeats(segments: list[dict], threshold: float = DEFAULT_SIMILARITY,
                         *, skip: set[tuple[int, int]] | None = None) -> list[Finding]:
    """相邻两句**像到一定程度** → 标出后一句，但只给 **low** 置信（留给模型复核）。

    `skip`：已经被规则层确定抓到的句对下标（完全重复、半句重起）——
    不重复标，免得报告里同一段出现两次、理由还不一样。
    """
    skip = skip or set()
    out: list[Finding] = []
    for i in range(1, len(segments)):
        if (i - 1, i) in skip:
            continue
        a, b = segments[i - 1].get("text", ""), segments[i].get("text", "")
        if normalize(a) == normalize(b):
            continue                     # 完全相同是 T2 的活（高置信）
        ratio = similarity(a, b)
        if ratio < threshold:
            continue
        out.append(Finding(
            start=float(segments[i]["start"]), end=float(segments[i]["end"]),
            kind="repeat", source="rule", confidence="low",
            evidence=f"与上一句相似度 {ratio:.2f}（阈值 {threshold:.2f}），"
                     f"疑似同一件事换个说法，待复核：\n"
                     f"  上一句：{a.strip()}\n  这一句：{b.strip()}",
            extra={"ratio": round(ratio, 3), "original": a.strip()}))
    return out


# 切句的分隔符：句末标点 **加上逗号**。
#
# 为什么要含逗号（实测）：ASR 在说话人**停顿**的地方写逗号 ——
# "今天我要讲的是,今天我要讲的是三个方法。" 正是半句重起的现场，
# 只按句末标点切，这两半会被当成一句话，重起规则就永远看不到它。
# 而且 ASR 输出的逗号可能是**半角**（实测拿到的是 `,`），两种都要认。
_SENT_END = re.compile(r"[。！？；!?;，,]")


def split_sentences(segments: list[dict]) -> list[dict]:
    """把 ASR 的**段**切成**句子** —— 这是所有配对规则的前提。

    **为什么必须有这一步**（实测踩到的）：ASR 的段是"按停顿切的"，不是"按句子切的"。
    真实输出里一个段装着两句话是常态：

        [1] 「这个方法我用了很久。这个方法我用了很久。」

    而"重复句""半句重起"这些规则看的是**相邻两句** —— 段不切开，
    这类冗余全在段内部，规则一条都抓不到（实测：5 类埋点只命中 1 类）。

    切句用**词级时间戳里的标点**（T1 的产出）：句子起止时间 = 首词起、末词止。
    没有 words 的段原样返回（交给调用方跳过，不猜）。
    """
    out: list[dict] = []
    for seg in segments:
        words = seg.get("words") or []
        if not words:
            out.append(seg)
            continue
        cur: list[dict] = []
        for w in words:
            cur.append(w)
            if _SENT_END.search(w.get("w") or ""):
                out.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                            "text": "".join(x.get("w") or "" for x in cur),
                            "words": list(cur)})
                cur = []
        if cur:
            out.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                        "text": "".join(x.get("w") or "" for x in cur),
                        "words": list(cur)})
    return out


# ---------------------------------------------------------------- 口头语（T2）


def find_fillers(segments: list[dict], fillers=DEFAULT_FILLERS,
                 gap_sec: float = DEFAULT_FILLER_GAP_SEC) -> list[Finding]:
    """找独立说出来的口头语。

    **"独立"怎么判**（这条是本函数唯一的难点）：

    只看词表会把"那个人""那个年代"里的"那个"也剪掉 —— 那是**正话**，剪了就是事故。
    所以要求口头语**与两侧邻居都有空隙**（取 min 而不是 max）。

    依据是口头语的性质：它是"想词时的填充"，说出来时前后本来就是断开的
    （"嗯……我觉得"）；而"那个人"是连读的，词与词之间没有空隙。

    **实测踩过的坑**：一开始取 max（两侧只要有一侧空就行），结果段首的词
    `left_gap = inf` → "那个人来了"里的"那个"被判成独立口头语。取 min 修掉了它。

    代价：**连读的口头语抓不到**（"我就是觉得"里的"就是"）。
    这是刻意的取舍 —— spec 关键决定四：宁少剪，不多剪。

    依赖词级时间戳（T1）。没有 words 的段会被跳过（不是猜）。
    """
    out: list[Finding] = []
    for seg in segments:
        words = seg.get("words") or []
        for i, w in enumerate(words):
            # **词级时间戳的 token 带标点**（实测拿到的是 `那个,`）——
            # 不归一化就永远匹配不上词表里的 `那个`（实测漏了一整类）。
            token = normalize(w.get("w") or "")
            if token not in fillers:
                continue
            prev_w = words[i - 1] if i > 0 else None
            next_w = words[i + 1] if i + 1 < len(words) else None
            left_gap = (w["start"] - prev_w["end"]) if prev_w else float("inf")
            right_gap = (next_w["start"] - w["end"]) if next_w else float("inf")
            if min(left_gap, right_gap) < gap_sec:
                continue          # 有一侧连读 → 多半是词的一部分（"那个人"），不剪
            out.append(Finding(
                start=float(w["start"]), end=float(w["end"]), kind="filler",
                evidence=f"口头语「{token}」独立出现（左空隙 {left_gap:.2f}s / 右空隙 {right_gap:.2f}s）",
                extra={"word": token}))
    return out


# ---------------------------------------------------------------- 完全重复（T2）


def find_exact_repeats(segments: list[dict]) -> list[Finding]:
    """相邻两句**一模一样** → 标出**后一句**（前一句是用户本来要说的）。

    完全一样是最硬的信号，不需要模型。
    """
    out: list[Finding] = []
    for i in range(1, len(segments)):
        a, b = normalize(segments[i - 1].get("text")), normalize(segments[i].get("text"))
        if a and a == b:
            out.append(Finding(
                start=float(segments[i]["start"]), end=float(segments[i]["end"]),
                kind="repeat",
                evidence=f"与上一句完全相同，后一句是重说：「{segments[i].get('text','').strip()}」",
                extra={"original": segments[i - 1].get("text", "").strip()}))
    return out


# ---------------------------------------------------------------- 半句重起（T2）


def find_restarts(segments: list[dict],
                  min_chars: int = DEFAULT_MIN_RESTART_CHARS) -> list[Finding]:
    """说了一半停下、重新起头 → 标出**前一句**（那句是被废弃的）。

    判据：相邻两句归一化后，**短的是长的前缀**（"我们今天要讲的" / "我们今天要讲的是"），
    且短句不短于 `min_chars`（太短的多半是正常口语，比如"那个"）。

    为什么要求"前缀"而不是"相似"：前缀是**确定**的结构信号
    （起头重说必然从同一串字开始）；相似度低的那种留给 T3/模型。
    """
    out: list[Finding] = []
    for i in range(1, len(segments)):
        a, b = normalize(segments[i - 1].get("text")), normalize(segments[i].get("text"))
        if not a or not b:
            continue
        short, long_ = (a, b) if len(a) <= len(b) else (b, a)
        if len(short) < min_chars or not long_.startswith(short) or short == long_:
            continue
        idx = i - 1 if len(a) <= len(b) else i      # 被废弃的是短的那句
        out.append(Finding(
            start=float(segments[idx]["start"]), end=float(segments[idx]["end"]),
            kind="restart",
            evidence=f"起头又重说：先说「{segments[idx].get('text','').strip()}」，"
                     f"紧接着完整说了「{segments[idx + 1].get('text','').strip() if idx + 1 < len(segments) else ''}」"))
    return out


# ---------------------------------------------------------------- 自我纠正（T2）


#: 自我纠正的**引导词**：命中它，说明"前面那句是说错了"。
SELF_CORRECT_CUES: tuple[str, ...] = (
    "不对", "不是", "说错了", "重来", "重新说", "抱歉", "我再说一遍", "我是说", "应该是",
)


def find_self_corrections(segments: list[dict],
                          cues=SELF_CORRECT_CUES) -> list[Finding]:
    """说错了当场纠正 → 标出**前一句**（被否定的那句）。

    判据：某句**以引导词开头**（"不对，我是说……"），且它前面还有一句 ——
    前面那句是被废弃的。要求引导词在**句首**，是因为句中出现"不是"太常见
    （"这不是重点"是正话，不是纠正）。
    """
    out: list[Finding] = []
    for i in range(1, len(segments)):
        text = normalize(segments[i].get("text"))
        cue = next((c for c in cues if text.startswith(normalize(c))), None)
        if not cue:
            continue
        prev = segments[i - 1]
        # **上一句本身就是个纠正信号** → 它没有"废弃内容"，不该被剪。
        # 实测踩到：切句后「不对,」自成一个句子，紧接着「我是说前年的。」
        # 又命中引导词 → 于是把"不对"剪掉了。而"不对"是**纠正的标记**，
        # 剪了它，观众就不知道后面那句是在纠正什么。
        if normalize(prev.get("text")) in {normalize(c) for c in cues}:
            continue
        out.append(Finding(
            start=float(prev["start"]), end=float(prev["end"]), kind="self_correct",
            evidence=f"紧接着出现了纠正信号「{cue}」，所以这句是废弃的："
                     f"「{prev.get('text','').strip()}」"))
    return out


# ---------------------------------------------------------------- 汇总（T2）


def rule_findings(segments: list[dict], *, fillers=DEFAULT_FILLERS,
                  filler_gap_sec: float = DEFAULT_FILLER_GAP_SEC,
                  min_restart_chars: int = DEFAULT_MIN_RESTART_CHARS,
                  similarity: float = DEFAULT_SIMILARITY) -> list[Finding]:
    """跑完规则层：高置信的三类 + 相似度（低置信，待模型复核）。

    相似度那一类要**避开**已被高置信抓到的句对 —— 否则报告里同一段出现两次、
    理由还不一样，用户没法复核。
    """
    high: list[Finding] = []
    high += find_exact_repeats(segments)
    high += find_restarts(segments, min_chars=min_restart_chars)
    high += find_self_corrections(segments)
    high += find_fillers(segments, fillers=fillers, gap_sec=filler_gap_sec)

    taken = {(i - 1, i) for i in range(1, len(segments))
             if normalize(segments[i - 1].get("text")) == normalize(segments[i].get("text"))
             and normalize(segments[i].get("text"))}
    taken |= {(i - 1, i) for i in range(1, len(segments))
              if any(f.kind == "restart" and abs(f.start - segments[i - 1]["start"]) < 1e-6
                     for f in high)}
    taken |= {(i - 1, i) for i in range(1, len(segments))
              if any(f.kind == "self_correct" and abs(f.start - segments[i - 1]["start"]) < 1e-6
                     for f in high)}

    out = high + find_similar_repeats(segments, threshold=similarity, skip=taken)
    return sorted(out, key=lambda f: (f.start, f.kind))


# ---------------------------------------------------------------- 模型复核（T4）


def review_with_llm(segments: list[dict], findings: list[Finding], *,
                    chat=None, enabled: bool = True) -> tuple[list[Finding], bool]:
    """让模型复核**低置信**的句对。返回 `(判定列表, llm_skipped)`。

    - 输入**只有可疑句对**（spec 关键决定三）：不是全文，成本可预测。
    - 模型**只回答判断**，不返回时间戳（spec 关键决定二）：
      时间戳一律由程序从原句对取 —— 模型看不到音频，它给的时间不可信。
    - 模型不可用/超时/返回坏 JSON → **降级**：返回空列表 + `llm_skipped=True`。
      不静默 —— 报告里要能看出"这一步没跑"（checklist §3）。
    """
    low = [f for f in findings if f.confidence == "low" and f.source == "rule"]
    if not enabled or not low:
        return [], False

    # 从 finding 反查它对应哪一对相邻句（按 start 对上后一句）
    pairs, index_map = [], {}
    for f in low:
        idx = next((i for i in range(1, len(segments))
                    if abs(float(segments[i]["start"]) - f.start) < 1e-6), None)
        if idx is None:
            continue
        index_map[len(pairs)] = (idx, f)
        pairs.append({"index": len(pairs),
                      "prev": (segments[idx - 1].get("text") or "").strip(),
                      "cur": (segments[idx].get("text") or "").strip()})
    if not pairs:
        return [], False

    from . import prompts
    caller = chat
    if caller is None:
        from .. import llm as _llm
        caller = _llm.chat_json

    try:
        resp = caller(prompts.build_disfluency_prompt(pairs), scene="disfluency")
    except Exception as e:                                    # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("去冗余模型复核不可用，降级到纯规则：%s", e)
        return [], True

    verdicts = (resp or {}).get("verdicts")
    if not isinstance(verdicts, list):
        return [], True

    out: list[Finding] = []
    for v in verdicts:
        try:
            i = int(v.get("index"))
        except (TypeError, ValueError):
            continue
        if i not in index_map or not v.get("same"):
            continue
        idx, base = index_map[i]
        # **时间戳取自原句对**，不取模型给的（模型根本没给，也不该给）
        out.append(Finding(
            start=float(segments[idx]["start"]), end=float(segments[idx]["end"]),
            kind="repeat", source="llm", confidence="high",
            evidence=f"模型复核：这两句是同一件事说两遍 —— {v.get('why') or '（模型未给理由）'}",
            extra={"original": base.extra.get("original", ""),
                   "ratio": base.extra.get("ratio")}))
    return out, False


# ---------------------------------------------------------------- 编排（T7）


#: 类别 → 开关字段名。四处都靠它，避免"加了类别忘了开关"。
KIND_SWITCH = {
    "filler": "dedup_filler",
    "repeat": "dedup_repeat",
    "restart": "dedup_restart",
    "self_correct": "dedup_self_correct",
}

KIND_LABEL = {"filler": "口头语", "repeat": "重复句", "restart": "半句废弃",
              "self_correct": "自我纠正"}


def analyze(segments: list[dict], params, *, chat=None) -> dict:
    """跑完整个判定：规则 → （可选）模型复核 → 按开关过滤 → 汇总。

    返回可直接落盘/上报的结构：
    `{findings, by_kind, total_sec, llm_skipped, llm_reviewed, params_used}`
    """
    if not getattr(params, "dedup_enabled", True):
        return {"findings": [], "by_kind": {}, "total_sec": 0.0,
                "llm_skipped": False, "llm_reviewed": False,
                "note": "去冗余已关闭（只做停顿）"}

    # **先切句**：ASR 的段是按停顿切的，一句话常横跨多句（见 split_sentences）
    sentences = split_sentences(segments)
    rule = rule_findings(
        sentences,
        fillers=tuple(getattr(params, "dedup_fillers", DEFAULT_FILLERS)),
        filler_gap_sec=getattr(params, "dedup_filler_gap_sec", DEFAULT_FILLER_GAP_SEC),
        min_restart_chars=getattr(params, "dedup_min_restart_chars", DEFAULT_MIN_RESTART_CHARS),
        similarity=getattr(params, "dedup_similarity", DEFAULT_SIMILARITY))

    llm, skipped = review_with_llm(
        sentences, rule, chat=chat, enabled=bool(getattr(params, "dedup_review", False)))
    # 模型确认过的那些，把规则层对应那条 low 置信的去掉（同一段不要出现两次）
    confirmed = {(round(f.start, 3), round(f.end, 3)) for f in llm}
    all_f = [f for f in rule if (round(f.start, 3), round(f.end, 3)) not in confirmed] + llm

    # 按开关过滤 —— **四类各自独立**
    all_f = [f for f in all_f
             if getattr(params, KIND_SWITCH.get(f.kind, ""), True)]
    all_f.sort(key=lambda f: (f.start, f.kind))

    by_kind: dict[str, int] = {}
    for f in all_f:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
    return {
        "findings": [f.as_dict() for f in all_f],
        "by_kind": by_kind,
        "total_sec": round(sum(f.end - f.start for f in all_f), 3),
        "llm_skipped": skipped,
        "llm_reviewed": bool(getattr(params, "dedup_review", False)),
        "params_used": {
            "similarity": getattr(params, "dedup_similarity", DEFAULT_SIMILARITY),
            "filler_gap_sec": getattr(params, "dedup_filler_gap_sec", DEFAULT_FILLER_GAP_SEC),
            "min_cut_sec": getattr(params, "dedup_min_cut_sec", 0.2),
            "min_restart_chars": getattr(params, "dedup_min_restart_chars",
                                         DEFAULT_MIN_RESTART_CHARS),
            "fillers": list(getattr(params, "dedup_fillers", DEFAULT_FILLERS)),
            "enabled_kinds": [k for k, sw in KIND_SWITCH.items()
                              if getattr(params, sw, True)],
        },
    }


def to_cut_intervals(result: dict) -> list[tuple[float, float]]:
    """判定结果 → 剪点区间（交给 `roughcut.merge_cuts` 去和停顿合并）。"""
    return [(f["start"], f["end"]) for f in result.get("findings", [])]


def format_report(result: dict, *, merged_sec: float | None = None,
                  before_sec: float | None = None) -> str:
    """给人看的报告：**逐条列出剪了哪几段、为什么、来源**（spec §3 第 2 条）。"""
    lines = ["=== 去冗余判定 ==="]
    if result.get("note"):
        return "\n".join(lines + [result["note"]])
    if not result.get("findings"):
        lines.append("没有发现冗余（这很正常 —— 干净的素材本来就该判出 0 条）")
    for f in result["findings"]:
        src = "模型" if f["source"] == "llm" else "规则"
        conf = "" if f["confidence"] == "high" else f"（{f['confidence']} 置信）"
        lines.append(f"  [{f['start']:>7.2f} – {f['end']:>7.2f}s] "
                     f"{KIND_LABEL.get(f['kind'], f['kind'])} · 来源{src}{conf}")
        for ev in f["evidence"].split("\n"):
            lines.append(f"      {ev}")
    counts = "、".join(f"{KIND_LABEL.get(k, k)} {v} 段" for k, v in result["by_kind"].items())
    lines.append(f"合计：{counts or '0 段'}，共 {result['total_sec']}s")
    if result.get("llm_reviewed") and result.get("llm_skipped"):
        lines.append("⚠️ 模型复核**没有跑成**（降级到纯规则）—— 换措辞的重说可能漏掉")
    if merged_sec is not None and before_sec:
        lines.append(f"剪后时长：{before_sec:.1f}s → {before_sec - merged_sec:.1f}s")
    return "\n".join(lines)
