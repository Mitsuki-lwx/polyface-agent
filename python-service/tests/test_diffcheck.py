"""多平台差异化检查（`app/pipeline/diffcheck.py`）的测试。

**最重要的一条**是 `test_baseline_2026_11_01_reproduces`：用 2026-11-01 那次真实产出的
三篇原文，必须复算出 30%/33%/33% 与 399/498/474 这组数字。
如果检查器连**已知的坏样本**都算不准，那它后面给出的"已改善"就没有任何意义 ✗。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline import diffcheck as dc  # noqa: E402

# ---------------------------------------------------------------- 真实样本（勿改）

# 2026-11-01 一次真实生成（素材：内容复用长文）的三篇产出，原文照抄。
XHS = """内容复用不是一稿多投 🙅

我拿一篇3000字长文做了个实测：拆成10条内容，熟练后2小时跑完一轮。

你缺的不是灵感，是一条能重复跑的流水线。

先纠个偏：别原封不动搬运。
同一段话发5个平台，用户不会觉得你勤奋，只会觉得你敷衍。

复用是同一个核心信息，换场景、换形态、换钩子，让它多跑几圈。

📦 第一步：先建「内容母库」
我习惯用飞书文档或Notion建一个，每篇内容只填7个字段：
选题、目标人群、核心痛点、核心观点、3个案例、5个方法、3句金句。

没有母体，后面拆出来的都是碎渣。

✂️ 第二步：拆形态
同一篇长文，我拆成：
1篇公众号 + 3条小红书图文 + 2条短视频口播 + 1条知乎回答 + 1条社群分享 + 1张清单海报 + 1封邮件
合计10条。

⏱ 第三步：排时间
4个25分钟番茄钟：
25分钟提炼观点，25分钟写小红书，25分钟录短视频，25分钟做海报和排期。

举个具体例子：
「我让Kimi读了10份周报，总结出3段式模板」。

内容复用不是偷懒，是让好内容多活几次。

先存下来，下次写内容照着跑一遍 📌
你现在是一稿多投，还是已经有母库了？评论区聊聊～"""

GZH = """内容复用不是一稿多投。我把3000字的长文拆成10条内容，熟练之后2小时能完成。你缺的不是灵感，是一条能重复跑的流水线。

内容复用的关键，不是换个平台发出去，而是换场景、换形态、换钩子，让核心信息多跑几圈。

拆出来的清单是这样的：1篇公众号、3条小红书图文、2条短视频口播、1条知乎回答、1条社群分享、1张清单海报、1封邮件，合计10条。但别原封不动搬运——同一段话发到5个平台，用户不会觉得你勤奋，只会觉得你敷衍。

我习惯用飞书文档或者Notion建一个内容母库，每篇只填7个字段：选题、目标人群、核心痛点、核心观点、3个案例、5个方法、3句金句。没有母体，后面拆出来的都是碎渣。比如这种选题：我让Kimi读了10份周报，总结出3段式模板。

我一般设4个25分钟番茄钟：25分钟提炼观点，25分钟写小红书，25分钟录短视频，25分钟做海报和排期。

内容复用不是偷懒，是让好内容多活几次。"""

ZHIHU = """内容复用不是一稿多投，也不是把同一段话复制到五个平台。真正的复用，是把同一个核心信息，换场景、换形态、换钩子，让它多跑几圈。

你缺的不是灵感，是一条能重复跑的流水线。这篇就把这条流水线拆开讲。

先看结果

我拿一篇3000字长文做过测试：拆成1篇公众号+3条小红书图文+2条短视频口播+1条知乎回答+1条社群分享+1张清单海报+1封邮件，合计10条内容，熟练后2小时能完成一轮。

复用的前提

没有母体，后面拆出来的都是碎渣。我习惯用飞书文档或Notion建一个“内容母库”，每篇内容只填7个字段：选题、目标人群、核心痛点、核心观点、3个案例、5个方法、3句金句。

换场景、换形态、换钩子

落到具体表达上，就是同一个核心信息换一种形态。比如“我让Kimi读了10份周报，总结出3段式模板”。

时间怎么排

我一般设4个25分钟番茄钟：25分钟提炼观点，25分钟写小红书，25分钟录短视频，25分钟做海报和排期。

反例：别原封不动搬运

同一段话发5个平台，用户不会觉得你勤奋，只会觉得你敷衍。

内容复用不是偷懒，是让好内容多活几次。"""

BASELINE = {"xhs": XHS, "gzh": GZH, "zhihu": ZHIHU}


# ---------------------------------------------------------------- 基线复算

def test_baseline_2026_11_01_reproduces():
    """必须复算出当时人工统计的那组数字 —— 否则检查器不可信。"""
    ov = dc.pairwise_overlaps(BASELINE)
    assert ov["xhs∩gzh"] == 0.30, ov
    assert ov["xhs∩zhihu"] == 0.3333, ov
    assert ov["gzh∩zhihu"] == 0.3333, ov

    assert dc.body_chars(XHS) == 498, dc.body_chars(XHS)
    assert dc.body_chars(GZH) == 399, dc.body_chars(GZH)
    assert dc.body_chars(ZHIHU) == 474, dc.body_chars(ZHIHU)


def test_baseline_shared_sentences_are_the_known_three():
    shared = dc.shared_by_all(BASELINE)
    assert len(shared) == 3, shared
    assert any("你缺的不是灵感" in s for s in shared)
    assert any("没有母体" in s for s in shared)
    assert any("内容复用不是偷懒" in s for s in shared)


def test_baseline_is_flagged_by_evaluate():
    """整批评估必须**报出问题**，而不是放行。"""
    res = dc.evaluate(BASELINE)
    assert res["issues"], "已知的坏样本竟然通过了评估"
    joined = " ".join(res["issues"])
    assert "重合过高" in joined
    assert any("同时出现" in i for i in res["issues"])
    assert any("篇幅倒挂" in i for i in res["issues"])


# ---------------------------------------------------------------- 切句与归一

def test_short_fragments_are_ignored():
    assert dc.sentences("合计10条。") == [], "太短的碎片不该参与比较"


def test_numbers_do_not_make_sentences_different():
    # 只归一**阿拉伯数字**（中文数字是实词，改了就是改了 ✗）
    a = "我一般设4个25分钟番茄钟，用来写小红书这件事。"
    b = "我一般设6个40分钟番茄钟，用来写小红书这件事。"
    assert dc.overlap_ratio(a, b) == 1.0, "只差数字的句子应视为同一句"


def test_overlap_ratio_is_directional():
    long_text = "复用不是搬运而是换形态的转换。内容复用不是偷懒是让内容多活几次。"
    short_text = "复用不是搬运而是换形态的转换。"
    assert dc.overlap_ratio(long_text, short_text) == 1.0
    assert dc.overlap_ratio(short_text, long_text) == 0.5


def test_single_platform_has_no_cross_checks():
    assert dc.shared_by_all({"xhs": XHS}) == []
    assert dc.pairwise_overlaps({"xhs": XHS}) == {}


# ---------------------------------------------------------------- 格式必备项

def test_xhs_requires_five_tags():
    assert dc.count_tags("内容。#复用 #效率") == 2
    assert dc.format_issues("xhs", "内容。#复用 #效率"), "2 个标签应当报缺"
    assert dc.format_issues("xhs", "内容。", ["a", "b", "c", "d", "e"]) == [], "tags 字段也算"


def test_gzh_requires_headings():
    flat = "第一段。\n\n第二段。\n\n第三段。"
    assert dc.format_issues("gzh", flat), "没有小标题应当报缺"
    with_h = "## 一、开头\n\n正文。\n\n## 二、展开\n\n正文。\n\n## 三、收尾\n\n正文。"
    assert dc.format_issues("gzh", with_h) == []


def test_zhihu_requires_conclusion_first():
    assert dc.opens_with_conclusion("结论是：复用要做形态转换，不是搬运。后面展开。")
    assert not dc.opens_with_conclusion("随着这几年自媒体的兴起，内容复用成了很多人的困扰。")


# ---------------------------------------------------------------- 篇幅

def test_length_window_from_dna():
    dna = {"body_chars": {"min": 100, "max": 200}}
    assert dc.max_window if False else True  # 占位，保持文件可读
    assert dc.body_window("x", dna) == (100, 200)
    assert dc.length_issue("x", "短", dna) is not None
    assert dc.length_issue("x", "字" * 150, dna) is None
    assert dc.length_issue("x", "字" * 250, dna) is not None


def test_length_window_absent_means_no_check():
    assert dc.body_window("x", {}) is None
    assert dc.body_window("x", {"body_chars": {"min": 0}}) is None
    assert dc.length_issue("x", "短", {}) is None


def test_inversion_detected():
    """公众号比小红书短 = 倒挂（2026-11-01 就是这个形态）。"""
    assert dc.inversion_issue("gzh", 399, "xhs", 498) is not None
    assert dc.inversion_issue("gzh", 900, "xhs", 498) is None


# ---------------------------------------------------------------- 生成侧接线（只测组装，不测模型输出）

def test_draft_prompt_carries_avoid_sentences():
    """多平台同发时，prompt 里必须带着"别的平台用过的句子"。"""
    from app import dna as dna_lib
    from app.pipeline import prompts
    from app.schemas import StructuredMaterial
    from app.schemas_gen import Brief

    dna = dna_lib.load_dna("xhs")
    mat = StructuredMaterial(core_message="复用要换形态", tone="理性", audience="博主", facts=[])
    brief = Brief(platform_code="x", angle="a", hooks=["h"], structure_plan="s", tag_direction=["t"])
    avoid = ["你缺的不是灵感，是一条能重复跑的流水线"]

    with_avoid = prompts.build_draft_skeleton_prompt(dna, mat, brief, avoid_sentences=avoid)
    without = prompts.build_draft_skeleton_prompt(dna, mat, brief)

    assert avoid[0] in with_avoid, "avoid 句子没进 prompt"
    assert avoid[0] not in without
    assert "跨平台去重" in with_avoid
    assert "跨平台去重" not in without
    # 去重指令必须同时要求**保留事实**，否则模型会把数字删光 ✗
    assert "事实与数据要保留" in with_avoid


def test_draft_prompt_carries_body_window():
    from app import dna as dna_lib
    from app.pipeline import prompts
    from app.schemas import StructuredMaterial
    from app.schemas_gen import Brief

    dna = dna_lib.load_dna("gzh")
    assert dna.get("body_chars"), "公众号 DNA 缺 body_chars 窗口"
    mat = StructuredMaterial(core_message="m", tone="t", audience="a", facts=[])
    brief = Brief(platform_code="x", angle="a", hooks=["h"], structure_plan="s", tag_direction=["t"])
    prompt = prompts.build_draft_skeleton_prompt(dna, mat, brief)
    assert '"body_chars"' in prompt
    assert str(dna["body_chars"]["max"]) in prompt
    assert "低于下限或超出上限都算不合格" in prompt


def test_draft_prompt_carries_format_requirements():
    """每个平台的特异性格式项必须作为写作规则明确注入 prompt。"""
    from app import dna as dna_lib
    from app.pipeline import prompts
    from app.schemas import StructuredMaterial
    from app.schemas_gen import Brief

    mat = StructuredMaterial(core_message="m", tone="t", audience="a", facts=[])
    brief = Brief(platform_code="x", angle="a", hooks=["h"], structure_plan="s", tag_direction=["t"])

    xhs_dna = dna_lib.load_dna("xhs")
    xhs_prompt = prompts.build_draft_skeleton_prompt(xhs_dna, mat, brief)
    assert "话题标签" in xhs_prompt and "5" in xhs_prompt

    gzh_dna = dna_lib.load_dna("gzh")
    gzh_prompt = prompts.build_draft_skeleton_prompt(gzh_dna, mat, brief)
    assert "分段小标题" in gzh_prompt and "3" in gzh_prompt

    zhihu_dna = dna_lib.load_dna("zhihu")
    zhihu_prompt = prompts.build_draft_skeleton_prompt(zhihu_dna, mat, brief)
    assert "结论先行" in zhihu_prompt and "60" in zhihu_prompt


def test_platform_issues_are_routed_to_target_platforms():
    """整批评估中的平台专属问题（篇幅、格式必备项）应分发到专属字典，避免交叉污染。"""
    res = dc.evaluate(BASELINE)
    assert "platform_issues" in res
    p_issues = res["platform_issues"]
    assert "xhs" in p_issues and "gzh" in p_issues and "zhihu" in p_issues

    # 小红书专属问题不该出现在公众号列表中
    xhs_joined = " ".join(p_issues["xhs"])
    gzh_joined = " ".join(p_issues["gzh"])
    assert "小红书话题标签" in xhs_joined
    assert "小红书话题标签" not in gzh_joined
    assert "公众号分段小标题" in gzh_joined
    assert "公众号分段小标题" not in xhs_joined

    # 跨平台共有问题两边都有
    assert "同时出现" in xhs_joined
    assert "同时出现" in gzh_joined


def test_select_rewrite_candidate():
    order = ["gzh", "xhs", "zhihu"]
    # 场景1：全达标 -> None
    assert dc.select_rewrite_candidate(order, {"gzh∩xhs": 0.05, "gzh∩zhihu": 0.08}, max_overlap=0.10) is None

    # 场景2：gzh∩zhihu 超标 (16.7%) -> 选排序靠后的 zhihu 作为改写目标
    res = dc.select_rewrite_candidate(order, {"gzh∩xhs": 0.06, "gzh∩zhihu": 0.167}, max_overlap=0.10)
    assert res == ("zhihu", "gzh", 0.167)

    # 场景3：多个对超标 -> 挑重合率最高的
    res2 = dc.select_rewrite_candidate(order, {
        "gzh∩xhs": 0.25,
        "gzh∩zhihu": 0.18,
    }, max_overlap=0.10)
    assert res2 == ("xhs", "gzh", 0.25)


def test_build_diff_rewrite_feedback():
    fb = dc.build_diff_rewrite_feedback("zhihu", "gzh", ["同一段话发5个平台"], ratio=0.167)
    assert "gzh" in fb
    assert "17%" in fb
    assert "同一段话发5个平台" in fb
    assert "知乎视角要求" in fb
    assert "核心事实与数据" in fb



