"""时间线：把「剪」变成「改数据」（`docs/spec_timeline.md`）。

一句话：**保留哪些区间 + 字幕是什么**，就是这条时间线的全部。渲染细节不进这里。

三条不变量（都来自"改数据"这条路本身的坑）：

1. **`keep` 存的是「保留」区间，不是「剪掉」的区间**。语义只能有一种 ——
   写反了不会报错，只会安静地剪错。所以从粗剪的 `cuts` 转过来时必须显式转换，
   而且有测试盯着。
2. **字幕存「源视频时间轴」**。成片轴是 `keep` 的函数；存成片轴就意味着每次改
   `keep` 都要把字幕重映射一遍，而重映射正是最容易出错的环节（边界、跨段、取整），
   算错就是"字幕和嘴对不上"。存源轴则相反：改 `keep` **完全不碰字幕**，
   "字幕跟着剪点走"不是要实现的规则，而是结构上自动成立的事实。
3. **不存 ffmpeg 参数**。渲染是实现，数据格式不该被它污染。
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

VERSION = 1

# 时间比较的容差（秒）：浮点运算后判断"有没有交叠"不能直接用 ==
_EPS = 1e-6

# **与"秒值被四舍五入到 3 位小数"配套的容差**（2026-10-27 修，实测踩到）。
#
# 时间线里的秒值落盘时是 `round(x, 3)`，所以最大误差 **5e-4**。
# 而源时长来自 ffprobe、是**未舍入**的（如 `199.044844`）——
# 于是"段尾 199.045"会大于"源时长 199.044844"，差 **0.000156 秒**。
# 用 `_EPS = 1e-6` 判越界 → **整条渲染失败** ✗：
#   `第 33 段 [198.965, 199.045] 越界：源视频只有 199.045s`
# （报错里两个数看着相等，因为消息里也是 `.3f` 格式化过的 —— 更难排查 ✗）
#
# 所以边界类判断要用**与舍入精度同量级**的容差；`_EPS` 仍留给纯浮点噪声。
_ROUND_EPS = 5e-4


class TimelineError(ValueError):
    """时间线不合法。

    **必须说清是哪一段** —— 一句"格式错误"没法让人改。
    """


# ---------------- 轴换算 ----------------

def keep_seconds(keep: Sequence[Sequence[float]]) -> float:
    """成片总时长 = 保留区间之和。**唯一口径** —— 不另存一份可能对不上的"总长"。"""
    return round(sum(float(e) - float(s) for s, e in keep), 3)


def film_to_source(t_film: float, keep: Sequence[Sequence[float]],
                   prefer: str = "earlier") -> float | None:
    """成片时间轴 → 源时间轴。超出成片长度返回 None。

    剪点是瞬时的：源 10.0（第 1 段尾）与 20.0（第 2 段头）都落在成片 10.0。
    所以边界上的反推有歧义，用 `prefer` 指定取哪一侧：
    字幕**起点**要 earlier（贴左），**终点**要 later（贴右）—— 否则会短掉/多出一点点。
    """
    acc = 0.0
    for s, e in keep:
        s, e = float(s), float(e)
        length = e - s
        if t_film < acc + length - _EPS:
            return round(s + max(0.0, t_film - acc), 3)
        if t_film <= acc + length + _EPS:
            # 正好在边界上
            return round(e if prefer == "earlier" else s, 3)
        acc += length
    return None


def source_to_film(t_src: float, keep: Sequence[Sequence[float]]) -> float | None:
    """源时间轴 → 成片时间轴。落在**被剪掉**的区间里返回 None。"""
    acc = 0.0
    for s, e in keep:
        s, e = float(s), float(e)
        if s - _EPS <= t_src <= e + _EPS:
            return round(acc + max(0.0, min(t_src, e) - s), 3)
        acc += e - s
    return None


def complement(intervals: Iterable[Sequence[float]], total: float) -> list[list[float]]:
    """取补集：给"剪掉的"得到"保留的"，给"保留的"得到"剪掉的"。

    **两种用法在数学上是同一个操作**（补集的补集就是原集），所以只留这一份实现 ——
    分成两个函数必然有一天会分叉，而分叉的后果是"安静地剪错"。
    """
    out: list[list[float]] = []
    cursor = 0.0
    for s, e in sorted((float(s), float(e)) for s, e in intervals):
        if s - cursor > _EPS:
            out.append([round(cursor, 3), round(s, 3)])
        cursor = max(cursor, e)
    if float(total) - cursor > _EPS:
        out.append([round(cursor, 3), round(float(total), 3)])
    return out


def cuts_to_keep(cuts: Iterable[Sequence[float]], total: float) -> list[list[float]]:
    """**剪掉的**区间 → **保留的**区间。粗剪产出时间线时走这一步。

    语义写反不会报错，只会安静地剪错 —— 所以这个函数必须有测试。
    """
    return complement(cuts, total)


def keep_to_cuts(keep: Iterable[Sequence[float]], total: float) -> list[list[float]]:
    """**保留的**区间 → **剪掉的**区间。渲染时喂给 `cut_video`（它收的是剪掉的）。"""
    return complement(keep, total)


# ---------------- 字幕 ----------------

def segments_to_source(segments_film: Iterable[dict], keep: Sequence[Sequence[float]],
                       ) -> list[dict]:
    """粗剪的 ASR 输出（**成片轴**）→ 源轴。建时间线时用**一次**。

    段的首尾分别映射；首尾都映射不到（整条落在被剪区间里）就丢掉。
    """
    out: list[dict] = []
    for seg in segments_film:
        # 起点贴左、终点贴右：否则边界上会短掉/多出一点点
        a = film_to_source(float(seg["start"]), keep, prefer="earlier")
        b = film_to_source(float(seg["end"]), keep, prefer="later")
        if a is None or b is None or b - a <= _EPS:
            continue
        out.append({"start": a, "end": b, "text": str(seg.get("text", ""))})
    return out


def _clamp_forward(t: float, keep: Sequence[Sequence[float]]) -> float | None:
    """落在被剪区间里的时间点，向**后**夹到最近的保留区间起点。全被剪光则 None。"""
    for s, e in keep:
        s, e = float(s), float(e)
        if t < s - _EPS:
            return s
        if t <= e + _EPS:
            return t
    return None


def _clamp_backward(t: float, keep: Sequence[Sequence[float]]) -> float | None:
    """落在被剪区间里的时间点，向**前**夹到最近的保留区间终点。全被剪光则 None。"""
    last = None
    for s, e in keep:
        s, e = float(s), float(e)
        if t < s - _EPS:
            return last
        if t <= e + _EPS:
            return t
        last = e
    return last


def segments_to_film(segments_src: Iterable[dict], keep: Sequence[Sequence[float]],
                     ) -> list[dict]:
    """源轴字幕 → 成片轴。**渲染时现推** —— 这就是"字幕跟着剪点走"。

    **一个条目，不拆。** 关键认识：**成片是连续的** —— 被剪掉的停顿在成片里根本不存在，
    所以源区间 `[a, b]` 在成片里就是 `[film(a), film(b)]` 这一段连续时间。
    早先按保留区间拆开是错的：同一句话会出现两次，其中一段还可能只有几十毫秒
    （实测抓到过 28ms 的碎片）。

    整段都落在被剪区间里 → 丢掉（这正是"删掉一段，那段字幕就没了"）。
    """
    out: list[dict] = []
    for seg in segments_src:
        s, e = float(seg["start"]), float(seg["end"])
        text = str(seg.get("text", ""))
        a = _clamp_forward(s, keep)
        b = _clamp_backward(e, keep)
        if a is None or b is None:
            continue                       # 整段都在被剪区间里
        fa = source_to_film(a, keep)
        fb = source_to_film(b, keep)
        if fa is None or fb is None or fb - fa <= _EPS:
            continue
        out.append({"start": round(fa, 3), "end": round(fb, 3), "text": text})
    out.sort(key=lambda x: (x["start"], x["end"]))
    return out


# ---------------- 构造与校验 ----------------

def make(source: dict, keep: Sequence[Sequence[float]],
         segments: Iterable[dict] | None = None) -> dict:
    """组一条时间线。`source` 至少要能认出是哪个视频（给血缘用）。"""
    return {
        "version": VERSION,
        "source": dict(source),
        "keep": [[round(float(s), 3), round(float(e), 3)] for s, e in keep],
        "segments": [
            {"start": round(float(g["start"]), 3), "end": round(float(g["end"]), 3),
             "text": str(g.get("text", ""))}
            for g in (segments or [])
        ],
    }


def validate(tl: Any, source_duration: float | None = None) -> None:
    """校验。**不通过就抛 `TimelineError`，并说清是哪一段。**"""
    if not isinstance(tl, dict):
        raise TimelineError("时间线必须是一个对象")
    ver = tl.get("version")
    if ver != VERSION:
        # 不认识的版本**明确报错**，不猜 —— 按 v1 处理未知版本是错的
        raise TimelineError(f"不认识的时间线版本：{ver!r}（本程序只认 {VERSION}）")

    keep = tl.get("keep")
    if not isinstance(keep, list) or not keep:
        raise TimelineError("至少要保留一段（keep 不能为空）")

    prev_end = None
    for i, seg in enumerate(keep, 1):
        if not isinstance(seg, (list, tuple)) or len(seg) != 2:
            raise TimelineError(f"第 {i} 段格式不对：应为 [起, 止]，实际 {seg!r}")
        try:
            s, e = float(seg[0]), float(seg[1])
        except (TypeError, ValueError):
            raise TimelineError(f"第 {i} 段的起止不是数字：{seg!r}") from None
        if e - s <= _EPS:
            raise TimelineError(f"第 {i} 段 [{s}, {e}] 的起不小于止")
        if prev_end is not None and s < prev_end - _ROUND_EPS:
            raise TimelineError(
                f"第 {i} 段 [{s}, {e}] 与上一段重叠（上一段止于 {prev_end}）—— "
                f"保留区间必须升序且互不重叠")
        if source_duration is not None and e > float(source_duration) + _ROUND_EPS:
            raise TimelineError(
                f"第 {i} 段 [{s}, {e}] 越界：源视频只有 {source_duration:.3f}s")
        prev_end = e

    segs = tl.get("segments")
    if segs is not None:
        if not isinstance(segs, list):
            raise TimelineError("segments 必须是列表")
        for i, g in enumerate(segs, 1):
            if not isinstance(g, dict) or "start" not in g or "end" not in g:
                raise TimelineError(f"第 {i} 条字幕缺 start/end：{g!r}")


def parse(data: Any, source_duration: float | None = None) -> dict:
    """校验 + 归一（补齐缺省字段）。"""
    validate(data, source_duration)
    return make(data.get("source") or {}, data["keep"], data.get("segments") or [])


def keep_select_expr(keep: Sequence[Sequence[float]]) -> str:
    """保留区间 → ffmpeg `select` 表达式。**唯一一处**生成它（别处不许再拼）。"""
    return "+".join(f"between(t,{float(s)},{float(e)})" for s, e in keep) or "0"
