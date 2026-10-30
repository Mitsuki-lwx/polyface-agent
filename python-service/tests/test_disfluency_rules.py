"""规则层判定（`docs/tasks_disfluency.md` T2）。

每一条规则都要有**"该抓"和"不该抓"**两面 —— 只验"该抓"会放过误剪，
而误剪正话是这个功能里最严重的错误。
"""
from __future__ import annotations

from app.pipeline import disfluency as df


def seg(text, start, end, words=None):
    return {"text": text, "start": start, "end": end, "words": words or []}


def word(w, start, end):
    return {"w": w, "start": start, "end": end}


# ---------------------------------------------------------------- 完全重复


def test_exact_repeat_marks_the_second_one():
    segs = [seg("这个世界是荒谬的。", 0.0, 2.0), seg("这个世界是荒谬的。", 2.2, 4.2)]
    out = df.find_exact_repeats(segs)
    assert len(out) == 1
    assert out[0].kind == "repeat"
    # 标的是**后一句**（前一句是本来要说的）
    assert out[0].start == 2.2 and out[0].end == 4.2


def test_different_sentences_are_not_repeats():
    segs = [seg("这个世界是荒谬的。", 0.0, 2.0), seg("但我们要活下去。", 2.2, 4.2)]
    assert df.find_exact_repeats(segs) == []


def test_repeat_ignores_punctuation_differences():
    """转写标点不可靠，不能因为标点不同就漏掉。"""
    segs = [seg("我们要讲的是自由", 0.0, 2.0), seg("我们要讲的是自由。", 2.1, 4.0)]
    assert len(df.find_exact_repeats(segs)) == 1


# ---------------------------------------------------------------- 半句重起


def test_restart_marks_the_abandoned_half():
    segs = [seg("我们今天要讲的", 0.0, 1.5), seg("我们今天要讲的是自由。", 1.6, 4.0)]
    out = df.find_restarts(segs)
    assert len(out) == 1
    assert out[0].kind == "restart"
    assert out[0].start == 0.0 and out[0].end == 1.5      # 被废弃的是**前一句**


def test_restart_does_not_fire_on_short_overlap():
    """「那个」和「那个人」不是"重起" —— 短句太短，不足以判。"""
    segs = [seg("那个", 0.0, 0.5), seg("那个人来了。", 0.6, 2.0)]
    assert df.find_restarts(segs) == []


def test_restart_does_not_fire_when_not_a_prefix():
    """只是说了同一件事但结构不同 → 不属于"重起"（那是 T3/模型的事）。"""
    segs = [seg("我们今天要讲自由。", 0.0, 2.0), seg("自由是我们今天要讲的。", 2.1, 4.0)]
    assert df.find_restarts(segs) == []


def test_restart_handles_the_longer_one_first():
    segs = [seg("我们今天要讲的是自由。", 0.0, 2.5), seg("我们今天要讲的", 2.6, 4.0)]
    out = df.find_restarts(segs)
    assert len(out) == 1
    assert out[0].start == 2.6      # 短的那句（无论先后）才是被废弃的


# ---------------------------------------------------------------- 自我纠正


def test_self_correction_marks_the_negated_sentence():
    segs = [seg("这个项目做了一年。", 0.0, 2.0), seg("不对，我是说做了两年。", 2.1, 4.5)]
    out = df.find_self_corrections(segs)
    assert len(out) == 1
    assert out[0].kind == "self_correct"
    assert out[0].start == 0.0 and out[0].end == 2.0


def test_negation_mid_sentence_is_not_a_correction():
    """「这不是重点」是正话 —— 引导词必须**在句首**才算纠正。"""
    segs = [seg("前面那些都不重要。", 0.0, 2.0), seg("这不是重点，重点是执行。", 2.1, 4.5)]
    assert df.find_self_corrections(segs) == []


def test_first_sentence_correction_has_nothing_to_mark():
    """第一句就是"不对" → 前面没有被废弃的句子，不该产出空的区间。"""
    segs = [seg("不对，我是说做了两年。", 0.0, 2.0), seg("所以我们继续。", 2.1, 4.0)]
    assert df.find_self_corrections(segs) == []


# ---------------------------------------------------------------- 口头语


def test_standalone_filler_is_found():
    segs = [seg("嗯", 0.0, 0.5, [word("嗯", 0.0, 0.5)]),
            seg("我觉得这事能成", 0.9, 3.0, [word("我", 0.9, 1.2), word("觉得", 1.2, 1.6),
                                          word("这事", 1.6, 2.2), word("能成", 2.2, 3.0)])]
    out = df.find_fillers(segs)
    assert len(out) == 1
    assert out[0].kind == "filler"
    assert out[0].start == 0.0 and out[0].end == 0.5
    assert "嗯" in out[0].evidence


def test_filler_inside_a_word_is_not_found():
    """**这条最重要**：「那个人」里的「那个」是正话，剪了就是事故。"""
    segs = [seg("那个人来了", 0.0, 2.0, [word("那个", 0.0, 0.8), word("人", 0.8, 1.1),
                                        word("来了", 1.1, 2.0)])]
    assert df.find_fillers(segs) == []


def test_filler_needs_a_gap_on_at_least_one_side():
    """连读的口头语抓不到 —— 这是**刻意的取舍**（宁少剪不多剪）。"""
    segs = [seg("我就是觉得", 0.0, 2.0, [word("我", 0.0, 0.4), word("就是", 0.4, 0.9),
                                        word("觉得", 0.9, 1.6)])]
    assert df.find_fillers(segs) == []


def test_filler_gap_is_configurable():
    """调大空隙要求 → 原本抓到的就不再抓（参数确实起作用）。"""
    segs = [seg("嗯，我觉得", 0.0, 1.8, [word("嗯", 0.0, 0.4), word("我", 0.65, 0.95),
                                        word("觉得", 0.95, 1.8)])]
    assert len(df.find_fillers(segs, gap_sec=0.2)) == 1     # 右空隙 0.25s ≥ 0.2
    assert df.find_fillers(segs, gap_sec=0.3) == []         # 门槛提到 0.3 就抓不到了


def test_short_gap_is_treated_as_connected_speech():
    """0.1s 的空隙在口语里就是"连读" —— 不当成独立口头语（宁少剪）。"""
    segs = [seg("嗯我觉得", 0.0, 1.5, [word("嗯", 0.0, 0.4), word("我", 0.5, 0.8),
                                      word("觉得", 0.8, 1.5)])]
    assert df.find_fillers(segs) == []


def test_filler_wordlist_is_configurable():
    segs = [seg("哎呀", 0.0, 0.5, [word("哎呀", 0.0, 0.5)])]
    assert df.find_fillers(segs) == []                        # 默认词表里没有"哎呀"
    assert len(df.find_fillers(segs, fillers=("哎呀",))) == 1  # 加进词表就抓到


def test_segment_without_words_is_skipped_not_guessed():
    """没有词级时间戳的段 → 跳过（不猜）。T1 已经保证不会静默返回空 words。"""
    segs = [seg("嗯，我觉得", 0.0, 2.0, [])]
    assert df.find_fillers(segs) == []


# ---------------------------------------------------------------- 汇总


def test_rule_findings_sorted_and_labelled():
    segs = [seg("我们今天要讲的", 0.0, 1.5), seg("我们今天要讲的是自由。", 1.6, 4.0),
            seg("嗯", 4.5, 5.0, [word("嗯", 4.5, 5.0)])]
    out = df.rule_findings(segs)
    assert [f.start for f in out] == sorted(f.start for f in out)
    assert all(f.source == "rule" for f in out)
    assert {f.kind for f in out} == {"restart", "filler"}


def test_finding_dict_is_serializable():
    f = df.Finding(1.0, 2.0, "repeat", "因为…", extra={"original": "x"})
    d = f.as_dict()
    assert d["kind"] == "repeat" and d["source"] == "rule" and d["original"] == "x"
    import json
    json.dumps(d)          # 能落盘


# ---------------------------------------------------------------- 相似度（T3）


def test_similarity_scores_paraphrase_high():
    assert df.similarity("这事的核心是执行。", "这件事的核心在于执行") > 0.5


def test_similarity_scores_unrelated_low():
    assert df.similarity("这个项目做了一年。", "今天天气不错适合出门。") < 0.3


def test_paraphrased_repeat_is_flagged_as_low_confidence():
    """换个说法重说 → 标出来，但只给 low（留给模型复核，不直接剪）。"""
    segs = [seg("这件事情的核心是执行。", 0.0, 2.5), seg("这件事的核心就在于执行。", 2.6, 5.0)]
    out = df.find_similar_repeats(segs)
    assert len(out) == 1
    assert out[0].confidence == "low"
    assert out[0].source == "rule"
    assert "相似度" in out[0].evidence
    assert out[0].start == 2.6      # 标后一句


def test_unrelated_sentences_are_not_flagged():
    segs = [seg("这个项目做了一年。", 0.0, 2.0), seg("今天天气不错适合出门。", 2.1, 4.5)]
    assert df.find_similar_repeats(segs) == []


def test_similarity_threshold_is_configurable():
    segs = [seg("这件事情的核心是执行。", 0.0, 2.5), seg("这件事的核心就在于执行。", 2.6, 5.0)]
    assert len(df.find_similar_repeats(segs, threshold=0.5)) == 1
    assert df.find_similar_repeats(segs, threshold=0.99) == []


def test_exact_repeats_are_not_also_flagged_as_similar():
    """完全相同的句子由高置信那条负责，**不能**在报告里出现两次、理由还不一样。"""
    segs = [seg("我们要讲的是自由。", 0.0, 2.0), seg("我们要讲的是自由。", 2.1, 4.0)]
    out = df.rule_findings(segs)
    kinds = [f.kind for f in out]
    assert kinds.count("repeat") == 1
    assert len([f for f in out if f.kind == "repeat"]) == 1
    assert out[0].confidence == "high"


def test_rule_findings_marks_confidence_levels():
    segs = [seg("我们今天要讲的", 0.0, 1.5), seg("我们今天要讲的是自由。", 1.6, 4.0),
            seg("这件事的核心是执行。", 4.5, 7.0), seg("这件事的核心就在于执行。", 7.1, 9.5)]
    out = df.rule_findings(segs)
    conf = {f.kind: f.confidence for f in out}
    assert conf["restart"] == "high"
    assert conf["repeat"] == "low"


# ---------------------------------------------------------------- 切句（实测回归）


def real_asr_segment():
    """**真实 ASR 输出**的形状：一个段里装着两句话（按停顿切的，不是按句子切的）。

    实测（2026-10-17，SAPI 合成音）：5 类埋点只命中 1 类，就是因为这个 ——
    重复句、半句重起全在**段内部**，配对规则看不到。
    """
    return [{
        "start": 8.50, "end": 13.74,
        "text": "这个方法我用了很久。这个方法我用了很久。",
        "words": [word("这个", 8.50, 9.08), word("方", 9.08, 9.34), word("法", 9.34, 9.58),
                  word("我", 9.58, 9.82), word("用", 9.82, 10.02), word("了", 10.02, 10.16),
                  word("很", 10.16, 10.38), word("久。", 10.38, 10.60),
                  word("这个", 11.84, 12.20), word("方", 12.20, 12.44), word("法", 12.44, 12.68),
                  word("我", 12.68, 12.94), word("用", 12.94, 13.12), word("了", 13.12, 13.28),
                  word("很", 13.28, 13.50), word("久。", 13.50, 13.74)],
    }]


def test_split_sentences_breaks_a_segment_into_sentences():
    out = df.split_sentences(real_asr_segment())
    assert len(out) == 2
    assert out[0]["text"] == "这个方法我用了很久。"
    assert out[1]["text"] == "这个方法我用了很久。"
    # 时间取自**首词起、末词止**
    assert out[0]["start"] == 8.50 and out[0]["end"] == 10.60
    assert out[1]["start"] == 11.84 and out[1]["end"] == 13.74


def test_repeat_inside_a_segment_is_found_after_splitting():
    """**实测回归**：切句之前这条抓不到（重复句在段内部）。"""
    segs = real_asr_segment()
    assert df.find_exact_repeats(segs) == []            # 不切句 → 看不见
    out = df.find_exact_repeats(df.split_sentences(segs))
    assert len(out) == 1 and out[0].start == 11.84      # 标后一句


def test_restart_inside_a_segment_is_found_after_splitting():
    segs = [{"start": 2.90, "end": 7.44,
             "text": "今天我要讲的是,今天我要讲的是三个方法。",
             "words": [word("今天", 2.90, 3.14), word("我要", 3.14, 3.48), word("讲", 3.48, 3.84),
                       word("的是,", 3.84, 4.26),
                       word("今天", 4.82, 5.34), word("我要", 5.34, 5.68), word("讲", 5.68, 6.04),
                       word("的是", 6.04, 6.42), word("三", 6.42, 6.78), word("个", 6.78, 6.96),
                       word("方", 6.96, 7.16), word("法。", 7.16, 7.44)]}]
    out = df.find_restarts(df.split_sentences(segs))
    assert len(out) == 1
    assert out[0].start == 2.90 and out[0].end == 4.26   # 被废弃的前半句


def test_filler_token_with_trailing_punctuation_is_matched():
    """**实测回归**：词级时间戳给的是 `那个,`，词表里是 `那个` —— 不去标点就永远匹配不上。"""
    segs = [seg("第三个方法,那个,其实很重要。", 21.76, 26.04,
                [word("第三", 21.76, 22.06), word("个", 22.06, 22.32), word("方", 22.32, 22.52),
                 word("法,", 22.52, 22.84),
                 word("那个,", 23.62, 23.96),          # ← 带逗号
                 word("其实", 24.88, 25.18), word("很", 25.18, 25.66), word("重要。", 25.66, 26.04)])]
    out = df.find_fillers(segs)
    assert len(out) == 1
    assert out[0].start == 23.62 and out[0].end == 23.96


def test_split_sentences_leaves_wordless_segments_alone():
    """没有词级时间戳的段原样返回（交给调用方跳过，不猜）。"""
    segs = [seg("没有词", 0.0, 1.0, [])]
    assert df.split_sentences(segs) == segs


def test_cue_only_sentence_is_not_marked_as_abandoned():
    """**实测回归**：切句后「不对,」自成一个句子，紧接着「我是说前年的。」
    又命中引导词 —— 于是把"不对"剪掉了。但"不对"是**纠正的标记**，剪了它，
    观众就不知道后面那句在纠正什么。
    """
    segs = [seg("这个数据是去年的。", 0.0, 2.0), seg("不对,", 2.1, 2.4),
            seg("我是说前年的。", 2.5, 4.0)]
    out = df.find_self_corrections(segs)
    assert len(out) == 1
    assert out[0].start == 0.0                 # 只标"去年"那句
    assert all(f.start != 2.1 for f in out), "纠正信号本身不该被剪"


# ---------------------------------------------------------------- 同一段只报一次

def test_overlapping_findings_report_one_span_once():
    """**同一段时间只报一次**，高置信的赢。

    一段可能同时"像上一句"（`repeat`，标在**后**一句）和"是下一句的半句重起"
    （`restart`，标在**前**一句）—— 两者覆盖的是**同一段时间**。
    实测（2026-10-30）：报告里同一段出现两遍、理由还不一样 ✗，
    而 `rule_findings` 的注释本来就写着"免得报告里同一段出现两次…用户没法复核"。
    """
    segs = [
        seg("今天聊聊怎么把长文改成五个平台的稿件。", 0.0, 2.0),
        seg("今天我们聊的是怎么把长文改成五个平台。", 3.0, 5.0),
        seg("今天我们聊的是怎么把长文改成五个平台的稿件。", 5.5, 8.0),
    ]
    found = df.rule_findings(segs)

    spans = [(round(f.start, 3), round(f.end, 3)) for f in found]
    assert len(spans) == len(set(spans)), f"同一段报了多次：{[(f.start, f.end, f.kind) for f in found]}"
    # 留下的是**高置信**的那个（restart 的理由比"疑似重复"具体）
    assert [f.kind for f in found] == ["restart"]
    assert found[0].confidence == "high"
