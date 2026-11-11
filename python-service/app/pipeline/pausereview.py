"""停顿复核：模型判断"这段停顿该剪还是该留"（`openspec/changes/pause-review-cuts`）。

为什么需要它（规则补不了的洞）：

    停 2 秒有两种，**声学上完全一样**：
      · 卡壳 / 想下一句 / 犹豫   → 该剪 ✓
      · 故意留白 / 强调 / 换段   → 不能剪 ✗

    规则只能看"静音多久"，语义判断只能交出去。

纪律（与 `docs/spec_disfluency.md` 同一条，别自己发明第二套）：

1. **程序给时间，模型只投票** —— 模型返回 `{id, action, why}`，任何时间戳一律忽略 ✗。
   它看不到音频，它给的时间是猜。
2. **默认关** —— 开了会把转写文本发给用户配的 LLM（ADR-017 数据边界）。
3. **降级要出声** —— 模型挂了就退回纯规则，但必须带 `llm_skipped=True`，
   报告里看得出来"这一步没跑"（不能静默当成"模型说没问题"）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: 候选下限：比这短的停顿不值得问（规则本来也多半不剪）。
DEFAULT_MIN_SEC = 0.60
#: 一次送模型的候选上限（取最长的那几条，token 才可预测）。
DEFAULT_MAX_CANDIDATES = 80
#: 判定与剪点匹配的容差（秒）——两边都是 round 到毫秒的浮点。
MATCH_EPS = 0.02


@dataclass
class Candidate:
    """一个待判的停顿点。**时间是程序的，不是模型的**。"""

    id: int
    pause_index: int          # 回到 pauses 的下标（应用判定时要用）
    start: float
    end: float
    rule_cut: bool            # 规则原本会不会剪
    prev: str = ""            # 停顿前一句
    nxt: str = ""             # 停顿后一句
    prosody: dict | None = None   # 本地算的声学特征（见 prosody.py）；没有就不发

    @property
    def gap(self) -> float:
        return round(self.end - self.start, 3)

    def as_prompt(self) -> dict:
        out = {"id": self.id, "gap_sec": self.gap,
               "rule_would_cut": self.rule_cut,
               "prev": self.prev, "next": self.nxt}
        if self.prosody:
            out["prosody"] = self.prosody
        return out


def _prev_text(sentences: list[dict], t: float) -> str:
    """停顿**前**最后一句（结尾在停顿开始附近或更早）。"""
    best = ""
    for s in sentences or []:
        try:
            end = float(s.get("end", 0))
        except (TypeError, ValueError):
            continue
        if end <= t + MATCH_EPS * 10:
            best = (s.get("text") or "").strip()
    return best


def _next_text(sentences: list[dict], t: float) -> str:
    """停顿**后**第一句（开头在停顿结束附近或更晚）。"""
    for s in sentences or []:
        try:
            start = float(s.get("start", 0))
        except (TypeError, ValueError):
            continue
        if start >= t - MATCH_EPS * 10:
            return (s.get("text") or "").strip()
    return ""


def build_candidates(pauses: list[tuple[float, float]],
                     cuts: list[tuple[float, float]],
                     sentences: list[dict] | None = None, *,
                     margin: float = 0.15,
                     min_sec: float = DEFAULT_MIN_SEC,
                     max_candidates: int = DEFAULT_MAX_CANDIDATES,
                     total: float | None = None) -> list[Candidate]:
    """停顿 → 候选。

    - 只收时长 ≥ `min_sec` 的（短的没有判断价值）
    - 超过 `max_candidates` 时**取最长的 N 条**（最长的停顿最值得判），
      然后按时间重排、重新编号（编号是这次调用的局部 id，不跨次稳定）
    - `rule_cut`：拿 `cuts` 反查"规则在这里剪不剪" —— 让模型知道它是在**复核**，
      而不是自由发挥
    """
    pool = [(i, float(s), float(e)) for i, (s, e) in enumerate(pauses or [])
            if float(e) - float(s) >= min_sec - 1e-9]
    if total is not None:
        pool = [(i, s, min(e, float(total))) for i, s, e in pool]
        pool = [(i, s, e) for i, s, e in pool if e > s]
    if len(pool) > max(0, int(max_candidates)):
        pool = sorted(pool, key=lambda x: -(x[2] - x[1]))[:int(max_candidates)]
    pool.sort(key=lambda x: x[1])

    out: list[Candidate] = []
    for cid, (pi, s, e) in enumerate(pool):
        want = round(s + margin, 3)
        rule_cut = any(abs(cs - want) <= MATCH_EPS for cs, _ce in (cuts or []))
        out.append(Candidate(id=cid, pause_index=pi, start=s, end=e,
                             rule_cut=rule_cut,
                             prev=_prev_text(sentences or [], s),
                             nxt=_next_text(sentences or [], e)))
    return out


def attach_prosody(candidates: list[Candidate],
                   by_pause_index: dict[int, dict | None]) -> None:
    """把本地声学特征挂到候选上（就地改）。

    `by_pause_index`：`pauses` 下标 → 特征（`prosody.analyze_pauses` 的结果）。
    没有的候选保持 `None` —— prompt 里就不出现 `prosody` 键（不发假数据）。
    """
    for c in candidates:
        c.prosody = by_pause_index.get(c.pause_index)


def review_candidates(candidates: list[Candidate], *, chat=None,
                      enabled: bool = False) -> tuple[list[dict], bool]:
    """一次批量调用，返回 `(verdicts, llm_skipped)`。

    - `enabled=False` 或没有候选 → **一次都不调**（特性：不花冤枉钱）
    - 模型不可用/超时/坏 JSON → `([], True)`，调用方退回纯规则
    - 未知 id、非法 action 一律丢弃（模型幻觉不该把剪点带偏）
    """
    if not enabled or not candidates:
        return [], False

    from . import prompts

    caller = chat
    if caller is None:
        from .. import llm as _llm
        caller = _llm.chat_json

    try:
        resp = caller(prompts.build_pause_review_prompt(
            [c.as_prompt() for c in candidates]), scene="pause_review")
    except Exception as e:                                   # noqa: BLE001
        log.warning("停顿复核不可用，降级到纯规则：%s", e)
        return [], True

    raw = (resp or {}).get("verdicts") if isinstance(resp, dict) else None
    if not isinstance(raw, list):
        log.warning("停顿复核返回结构不对（缺 verdicts），降级到纯规则")
        return [], True

    known = {c.id for c in candidates}
    out: list[dict] = []
    for v in raw:
        if not isinstance(v, dict):
            continue
        try:
            cid = int(v.get("id"))
        except (TypeError, ValueError):
            continue
        if cid not in known:
            continue
        action = str(v.get("action") or "").strip().lower()
        if action not in ("cut", "keep"):
            continue
        out.append({"id": cid, "action": action,
                    "why": str(v.get("why") or "").strip()})
    return out, False


def apply_verdicts(cuts: list[tuple[float, float]],
                   candidates: list[Candidate],
                   verdicts: list[dict], *,
                   margin: float = 0.15,
                   total: float | None = None) -> tuple[list[tuple[float, float]], dict]:
    """按判定改剪点。**两个方向都生效**（只做单向等于只解决一半）。

    - `keep`（规则会剪）→ 去掉那一刀
    - `cut`（规则不剪） → 加上那一刀（仍按 `margin` 两侧回退）
    - 与既有剪点重合/无效区间 → 跳过

    返回 `(新剪点, {"dropped": n, "added": n})`。
    """
    by_id = {c.id: c for c in candidates}
    keep_ids, cut_ids = set(), set()
    for v in verdicts or []:
        c = by_id.get(v.get("id"))
        if c is None:
            continue
        (keep_ids if v.get("action") == "keep" else cut_ids).add(c.id)

    dropped = 0
    new: list[tuple[float, float]] = []
    for s, e in cuts or []:
        hit = any(abs(float(s) - round(by_id[cid].start + margin, 3)) <= MATCH_EPS
                  for cid in keep_ids)
        if hit:
            dropped += 1
            continue
        new.append((float(s), float(e)))

    added = 0
    have = {round(s, 3) for s, _e in new}
    for cid in cut_ids:
        c = by_id[cid]
        s = round(c.start + margin, 3)
        e = round(c.end - margin, 3)
        if total is not None:
            e = min(e, round(float(total), 3))
        if e <= s or s in have:
            continue
        new.append((s, e))
        have.add(s)
        added += 1

    new.sort()
    return new, {"dropped": dropped, "added": added}


def format_report(result: dict) -> str:
    """人看的报告。**没跑要说出来**，不能静默。"""
    if not result:
        return ""
    n = len(result.get("candidates") or [])
    v = result.get("verdicts") or []
    if result.get("llm_skipped"):
        return (f"[停顿复核] {n} 条候选 —— 模型复核**未执行**（降级到纯规则），"
                f"本次剪点与不勾选时一致。")
    if not n:
        return "[停顿复核] 没有符合条件的停顿，未调用模型。"
    cut = sum(1 for x in v if x.get("action") == "cut")
    keep = sum(1 for x in v if x.get("action") == "keep")
    applied = result.get("applied") or {}
    return (f"[停顿复核] {n} 条候选 → 模型判 剪 {cut} / 留 {keep}；"
            f"实际去掉 {applied.get('dropped', 0)} 刀、加上 {applied.get('added', 0)} 刀。")
