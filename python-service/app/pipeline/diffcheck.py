"""多平台成稿的**差异化**检查（`openspec/changes/platform-differentiation`）。

**为什么存在**：2026-11-01 第一次真实产出，三篇成稿跨平台**逐字重合 30%~33%**，
而源文自己写着「别原封不动搬运，同一段话发5个平台只会让人觉得你敷衍」✗。
产品恰好做了这句话禁止的事 —— 因为**没有任何东西在检查它**。

这里只做**可计算**的事：
  · 切句与归一（去掉空白/标点/数字后逐字比对）
  · 跨篇重合率
  · 篇幅（字数 + 跨篇"长文平台不得比短图文还短"）
  · 平台格式必备项（话题标签 / 小标题 / 结论先行）

刻意**不做**：判断"写得好不好" ✗ —— 那不可复算，只会变成又一个自我感觉良好的指标。
"""
from __future__ import annotations

import re

# 切句：中文句末标点 + 换行。分号也算一句 —— 长文里分句常被当成独立卖点。
_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+")

# 归一：去掉空白、中英标点、引号括号、破折号省略号、以及**数字**。
# 去掉数字是为了让"3个案例"和"三个案例"这类差异不被当成不同的句子 ✗，
# 同时避免"25分钟"这种出现在多处的时间碎片把重合率抬高。
_NORMALIZE = re.compile(r"[\s，。！？、：；\"'“”‘’「」『』（）()\[\]【】…—\-–_#*~·0-9]")

MIN_SENTENCE_CHARS = 12   # 太短的碎片（"合计10条"）不比，噪声大于信息


def sentences(text: str, min_len: int = MIN_SENTENCE_CHARS) -> list[str]:
    """按句切分。返回**原样**句子（保留标点），长度按去空白后的字符数过滤。"""
    out: list[str] = []
    for raw in _SENTENCE_SPLIT.split(text or ""):
        s = raw.strip()
        if len(_NORMALIZE.sub("", s)) >= min_len:
            out.append(s)
    return out


def normalize(sentence: str) -> str:
    """比较用的归一形式。"""
    return _NORMALIZE.sub("", sentence or "")


def overlap_sentences(a_text: str, b_text: str) -> list[str]:
    """`b` 中**逐字**出现在 `a` 里的句子（返回 b 侧原文）。"""
    a_set = {normalize(s) for s in sentences(a_text)}
    return [s for s in sentences(b_text) if normalize(s) in a_set]


def overlap_ratio(a_text: str, b_text: str) -> float:
    """重合句占 `b` 的比例（0~1）。`b` 没有可比句子时返回 0.0。"""
    bs = sentences(b_text)
    if not bs:
        return 0.0
    return len(overlap_sentences(a_text, b_text)) / len(bs)


def shared_by_all(texts: dict[str, str]) -> list[str]:
    """在所有文本里都**逐字**出现的句子（>1 篇才有意义）。

    这是最刺眼的那种重复：三篇共用同一句金句 —— 读者一眼就能看出来 ✗。
    """
    if len(texts) < 2:
        return []
    first_key, *rest = list(texts)
    common: list[str] = []
    for s in sentences(texts[first_key]):
        n = normalize(s)
        if all(n in {normalize(x) for x in sentences(texts[k])} for k in rest):
            common.append(s)
    return common


def pairwise_overlaps(texts: dict[str, str]) -> dict[str, float]:
    """所有两两组合的重合率，键为 `"a∩b"`。"""
    keys = list(texts)
    out: dict[str, float] = {}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            out[f"{a}∩{b}"] = round(overlap_ratio(texts[a], texts[b]), 4)
    return out


def pairwise_overlap_sentences(texts: dict[str, str]) -> dict[str, list[str]]:
    """所有两两组合的逐字重合句子清单，键为 `"a∩b"`。供质检与 UI 溯源展示。"""
    keys = list(texts)
    out: dict[str, list[str]] = {}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            out[f"{a}∩{b}"] = overlap_sentences(texts[a], texts[b])
    return out


def body_chars(text: str) -> int:
    """正文字数 = 原始字符数（含标点与换行）。

    口径必须固定，否则"篇幅窗口"会因为口径变化而在不同版本里松紧不一致 ✗。
    取原始长度是因为它和 2026-11-01 那次真实产出公布的数字（399/498/474）一致，
    历史上所有基线都按这个口径记录 ✓。
    """
    return len(text or "")


# ---------------------------------------------------------------- 平台格式

_TAG_RE = re.compile(r"#[^\s#]{1,20}")
_HEADING_RE = re.compile(r"^(#{2,4}\s+\S|\*\*[^*]+\*\*\s*$|【[^】]{2,20}】)", re.MULTILINE)
_HEDGE_STARTS = ("首先", "其实", "说起来", "话说", "最近", "这几年", "随着")


def count_tags(text: str, tags: list[str] | None = None) -> int:
    """话题标签数：正文里的 `#xxx` + 结构化 `tags` 字段（去重）。

    小红书既可能写在正文，也可能落在 `tags` 字段 —— 两边都算，不然会误报。
    """
    in_body = {m.group(0).lstrip("#") for m in _TAG_RE.finditer(text or "")}
    in_field = {t.strip().lstrip("#") for t in (tags or []) if t and t.strip()}
    return len(in_body | in_field)


def count_headings(text: str) -> int:
    """分段小标题数：markdown 标题、独占一行的加粗、或【小标题】。"""
    return len(_HEADING_RE.findall(text or ""))


# 只认"明确说明"，不认顺带一提 —— 「用 AI 写周报」不算标注 ✗
_AI_DISCLOSURE_RE = re.compile(
    r"(AI\s*(辅助|生成|参与|协助|工具)|由\s*AI|使用\s*AI\s*(辅助|协助)|部分内容由\s*AI)")


def mentions_ai_disclosure(text: str) -> bool:
    """成稿里是否**明确标注**了 AI 辅助创作。"""
    return bool(_AI_DISCLOSURE_RE.search(text or ""))


def opens_with_conclusion(text: str, max_chars: int = 60) -> bool:
    """首句是否"结论先行"（知乎体）：够短、且不以铺垫词开头。"""
    ss = sentences(text or "", min_len=1)
    first = (ss[0] if ss else (text or "").strip().split("\n")[0]).strip()
    if not first:
        return False
    return len(first) <= max_chars and not first.startswith(_HEDGE_STARTS)


_PLATFORM_ALIASES: dict[str, str] = {
    "xiaohongshu": "xhs",
    "gongzhonghao": "gzh",
    "bili": "bilibili",
}

# 平台必备项（阈值来自 spec，允许后续在 DNA 里覆盖）
PLATFORM_REQUIREMENTS: dict[str, dict] = {
    # 小红书社区公约 2.0（2026-01）明确要求 AI 辅助创作主动标明 —— 作者评审也点了这条 ✗
    "xhs": {"min_tags": 5, "require_ai_disclosure": True},
    "gzh": {"min_headings": 3},
    "zhihu": {"conclusion_first": True, "min_headings": 3},
}


def format_issues(code: str, text: str, tags: list[str] | None = None) -> list[str]:
    """返回该平台**缺了哪些必备项**（空列表 = 齐全）。消息要能直接指导修改。"""
    canon = _PLATFORM_ALIASES.get((code or "").lower(), (code or "").lower())
    req = PLATFORM_REQUIREMENTS.get(canon) or {}
    issues: list[str] = []
    if "min_tags" in req:
        n = count_tags(text, tags)
        if n < req["min_tags"]:
            issues.append(f"小红书话题标签只有 {n} 个，至少需要 {req['min_tags']} 个（正文 #话题 或 tags 字段都算）")
    if "min_headings" in req:
        n = count_headings(text)
        if n < req["min_headings"]:
            # 平台名不能写死成"公众号"——知乎现在也要小标题 ✗（实测报错过）
            _label = {"gzh": "公众号", "zhihu": "知乎"}.get(code, code)
            issues.append(f"{_label}分段小标题只有 {n} 个，至少需要 {req['min_headings']} 个")
    if req.get("require_ai_disclosure") and not mentions_ai_disclosure(text):
        issues.append("小红书未标注 AI 辅助创作（社区公约 2.0 要求）")
    if req.get("conclusion_first") and not opens_with_conclusion(text):
        issues.append("知乎首句不是结论先行（开头 60 字内没给结论，或以铺垫词开头）")
    return issues


# ---------------------------------------------------------------- 篇幅

def body_window(code: str, dna: dict | None = None) -> tuple[int, int] | None:
    """取该平台的正文篇幅窗口 `(min, max)`；DNA 没配就返回 `None`（不检查）。"""
    dna = dna or {}
    w = dna.get("body_chars") or {}
    lo, hi = w.get("min"), w.get("max")
    if not isinstance(lo, int) or not isinstance(hi, int) or lo <= 0 or hi < lo:
        return None
    return lo, hi


def length_issue(code: str, text: str, dna: dict | None = None) -> str | None:
    """窗口检查：超出/不足都报，消息里带实际字数。"""
    w = body_window(code, dna)
    if w is None:
        return None
    lo, hi = w
    n = body_chars(text)
    if n < lo:
        return f"{code} 正文只有 {n} 字，低于窗口下限 {lo}"
    if n > hi:
        return f"{code} 正文 {n} 字，超过窗口上限 {hi}"
    return None


def inversion_issue(long_code: str, long_chars: int, short_code: str, short_chars: int,
                    ratio: float = 1.5) -> str | None:
    """跨篇"篇幅倒挂"：长文平台的字数不得比短图文还少（至少 `ratio` 倍）。"""
    if short_chars <= 0:
        return None
    if long_chars < short_chars * ratio:
        return (f"篇幅倒挂：{long_code} {long_chars} 字 < {short_code} {short_chars} 字 × {ratio}"
                f"（长文平台应当更长）")
    return None


# ---------------------------------------------------------------- 整批

def evaluate(drafts: dict[str, str], codes: dict[str, list[str]] | None = None,
             max_overlap: float = 0.10, length_ratio: float = 1.5,
             required_overlap: float | None = None,
             source_text: str | None = None) -> dict:
    """对一批成稿做**整批**评估，返回可直接放进响应的 dict。

    `drafts`: `{platform_code: body_text}`
    `codes`: `{platform_code: [tag, ...]}`（可选，用于标签计数）
    """
    if required_overlap is not None:
        length_ratio = required_overlap
    codes = codes or {}
    # 素材原句排除：模型保留作者自己的金句不该算"偷懒" ✗
    # ⚠️ 必须用**子串**判定，不能逐字相等：
    # 素材是「记住：内容复用不是偷懒，…」，稿子里常被剪成「内容复用不是偷懒，…」✗
    # 逐字相等会漏掉这种"掐头去尾复用素材金句"的情形（2026-11-08 实测踩到）。
    _src_blob = normalize(source_text or "")

    def _own(text: str) -> str:
        if not _src_blob:
            return text
        return "\n".join(x for x in sentences(text) if normalize(x) not in _src_blob)

    own = {k: _own(v) for k, v in drafts.items()}
    excluded = sum(len(sentences(v)) - len(sentences(own[k])) for k, v in drafts.items())
    result: dict = {
        "overlap": pairwise_overlaps(own),
        "overlap_sentences": pairwise_overlap_sentences(own),
        "shared_by_all": shared_by_all(own),
        "excluded_from_source": excluded,
        "length": {k: body_chars(v) for k, v in drafts.items()},
        "issues": [],
        "platform_issues": {k: [] for k in drafts},
        "max_overlap": max_overlap,
    }

    for k, ratio in result["overlap"].items():
        if ratio > max_overlap:
            ov_sents = result.get("overlap_sentences", {}).get(k, [])
            ex = f"（如: 「{ov_sents[0][:24]}」）" if ov_sents else ""
            msg = f"跨平台逐字重合过高：{k} = {ratio:.0%}（上限 {max_overlap:.0%}）{ex}"
            result["issues"].append(msg)
            parts = k.split("∩")
            for p in parts:
                if p in result["platform_issues"]:
                    result["platform_issues"][p].append(msg)

    if result["shared_by_all"]:
        msg = (
            "有句子在多篇中同时出现："
            + "；".join(s[:28] for s in result["shared_by_all"][:3])
        )
        result["issues"].append(msg)
        for p in result["platform_issues"]:
            result["platform_issues"][p].append(msg)

    # 篇幅倒挂检查（openspec: platform-differentiation §1.3）：
    # 长文平台（公众号等）篇幅不应低于短图文（小红书/抖音等）× length_ratio
    long_code = next((k for k in drafts if _PLATFORM_ALIASES.get(k.lower(), k.lower()) == "gzh"), None)
    if long_code:
        long_chars = result["length"].get(long_code, 0)
        for short_code in drafts:
            if _PLATFORM_ALIASES.get(short_code.lower(), short_code.lower()) in ("xhs", "douyin"):
                inv = inversion_issue(long_code, long_chars, short_code,
                                      result["length"].get(short_code, 0),
                                      ratio=length_ratio)
                if inv:
                    result["issues"].append(inv)
                    if long_code in result["platform_issues"]:
                        result["platform_issues"][long_code].append(inv)
                    if short_code in result["platform_issues"]:
                        result["platform_issues"][short_code].append(inv)

    for code, text in drafts.items():
        canon = _PLATFORM_ALIASES.get((code or "").lower(), (code or "").lower())
        try:
            dna = __import__("app.dna", fromlist=["load_dna"]).load_dna(canon)
        except Exception:  # noqa: BLE001 —— DNA 读不到就不做篇幅检查，不阻断
            dna = None
        issue = length_issue(code, text, dna)
        if issue:
            result["issues"].append(issue)
            if code in result["platform_issues"]:
                result["platform_issues"][code].append(issue)
        for fi in format_issues(code, text, codes.get(code)):
            result["issues"].append(fi)
            if code in result["platform_issues"]:
                result["platform_issues"][code].append(fi)

    return result


# ---------------------------------------------------------------- 改写目标选择与反馈 (openspec: diffcheck-auto-rewrite)

def select_rewrite_candidate(
    platforms_order: list[str],
    overlaps: dict[str, float],
    max_overlap: float = 0.10,
) -> tuple[str, str, float] | None:
    """从超标的平台对中选出应当发起去重改写的后发平台。

    返回 `(target_code, conflicting_with_code, ratio)`，若无超标则返回 None。
    规则：
      1. 找出所有 `ratio > max_overlap` 的冲突对；
      2. 每一对中，在 `platforms_order` 里出现较晚的作为改写目标（先生成的为主稿/锚点，后生成的做分化）；
      3. 多对超标时，优先选择重合率最高的对。
    """
    candidates = []
    order_map = {c: i for i, c in enumerate(platforms_order)}

    for pair_key, ratio in (overlaps or {}).items():
        if ratio <= max_overlap:
            continue
        parts = pair_key.split("∩")
        if len(parts) != 2:
            continue
        p1, p2 = parts[0].strip(), parts[1].strip()
        idx1 = order_map.get(p1, -1)
        idx2 = order_map.get(p2, -1)
        if idx1 >= idx2:
            target, other = p1, p2
        else:
            target, other = p2, p1
        candidates.append((ratio, target, other))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    highest_ratio, target, other = candidates[0]
    return target, other, highest_ratio


def build_diff_rewrite_feedback(
    target_code: str,
    other_code: str,
    conflict_sentences: list[str] | None = None,
    ratio: float = 0.0,
) -> str:
    """构建针对冲突后发平台的定向去重改写提示词。"""
    sentences = conflict_sentences or []
    lines = [
        f"【跨平台去重定向改写】本稿与平台「{other_code}」逐字重合过高（{ratio:.0%} > 允许上限）。"
    ]
    if sentences:
        lines.append("以下句子被判定为与另一平台严重重合，严禁整句照搬，必须彻底换词、换句式、从本平台专属视角重构：")
        for s in sentences[:6]:
            lines.append(f"- 「{s}」")
    else:
        lines.append("两平台存在过多相同表达，请更换句式切入角度，严禁套用相同句型。")

    canon = _PLATFORM_ALIASES.get((target_code or "").lower(), (target_code or "").lower())
    if canon in ("xhs", "xiaohongshu"):
        lines.append("小红书视角要求：突出痛点清单、踩坑避雷、快节奏分点卡片，多用场景化短句与 emoji。")
    elif canon == "zhihu":
        lines.append("知乎视角要求：结论先行，聚焦底层逻辑推导与利弊深思，避免直接使用口语金句作为论据。")
    elif canon in ("gzh", "gongzhonghao"):
        lines.append("公众号视角要求：分段小标题展开全景实操，承载完整方法步骤与案例细节。")

    lines.append("【硬性要求】素材中的核心事实与数据（数字、专名、步骤逻辑）必须保留，变的是语言表层与论述结构。")
    return "\n".join(lines)
