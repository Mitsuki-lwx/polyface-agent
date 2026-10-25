"""骨架装配器（Extract-then-select）的单元与不变量测试。

对应 `docs/spec_skeleton.md` §3/§4 与 `docs/checklist_skeleton.md` §1/§2。

三层：
1. 纯函数层：槽位解析 / 接地（G2） / 机械门（G1~G4）逐条的正反例；
2. mock 不变量：mock 骨架对**评测语料全集**装配硬违规恒为零；
   body 与改版前 `_mock_draft` 的行结构逐字节一致（golden = 旧算法内联）；
3. 边界：数字截断保护（`_clip_numsafe`）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.pipeline import generate as gen
from app.pipeline.numberish import numbers_in
from app.pipeline.skeleton import (
    DraftSkeleton,
    SkeletonPara,
    assemble_skeleton,
    parse_skeleton,
    surface_grounded,
)
from app.schemas import AnalyzeRequest, Fact, StructuredMaterial
from app.pipeline.understand import _mock_understand
from app.schemas_gen import Brief

CORPUS = Path(__file__).resolve().parents[2] / "eval" / "corpus"


def _facts(*texts: str) -> list[Fact]:
    return [Fact(text=t, type="data") for t in texts]


def _sk(titles=("标题",), paras=(), tags=("t",)) -> DraftSkeleton:
    return DraftSkeleton(
        titles=list(titles),
        paras=[SkeletonPara(facts=list(f), role="body", text=t) for f, t in paras],
        tags=list(tags),
    )


# ================================================================ 槽位解析

def test_parse_skeleton_standard_and_tolerant_forms():
    """标准 `{{n:3|x}}` 与宽容变形 `{{3|x}}` / 全角 `｛｛3|x｝｝` 都要能解析。"""
    facts = _facts("要交286块。", "另一条。")
    sk = _sk(paras=[({1}, "交了{{n:1|286块}}")])
    res = assemble_skeleton(sk, facts)
    assert res.hard == [] and "286块" in res.draft.body

    sk = _sk(paras=[({1}, "交了{{1|286块}}")])            # 省略 n:
    res = assemble_skeleton(sk, facts)
    assert res.hard == [] and "286块" in res.draft.body

    sk = _sk(paras=[({1}, "交了｛｛n：1｜286块｝｝")])       # 全角
    res = assemble_skeleton(sk, facts)
    assert res.hard == [] and "286块" in res.draft.body


def test_parse_skeleton_bad_form_left_as_text():
    """坏形（`{{n:}}` / `{{x|y}}` / 未闭合）不当槽位 —— 留在文本里由残留检查接住。"""
    facts = _facts("要交286块。")
    sk = _sk(paras=[({1}, "交了{{n:}}和{{x|y}}还有{{n:1|286块}")])
    res = assemble_skeleton(sk, facts)
    assert any("残留" in h for h in res.hard)


def test_parse_skeleton_drops_garbage():
    """非 dict 的段、空段、非正整数声明：能丢的丢，不猜。"""
    sk = parse_skeleton({
        "titles": [" 标题 ", ""],
        "paras": ["bad", {"facts": [0, "2"], "text": "正文"}, {"facts": [], "text": "  "}],
        "tags": ["#干货", ""],
    })
    assert sk.titles == ["标题"]
    assert len(sk.paras) == 1 and sk.paras[0].facts == [2]
    assert sk.tags == ["干货"]


# ================================================================ G1 引用合法

def test_g1_slot_out_of_range():
    facts = _facts("只有一条。")
    sk = _sk(paras=[({1}, "引了{{n:9|什么}}")])
    res = assemble_skeleton(sk, facts)
    assert any("不存在的事实序号" in h and "n=9" in h for h in res.hard)


def test_g1_slot_not_in_para_declaration():
    facts = _facts("第一条。", "第二条。")
    sk = _sk(paras=[({1}, "引用了{{n:2|第二条}}")])
    res = assemble_skeleton(sk, facts)
    assert any("不在该段声明" in h for h in res.hard)


def test_g1_declaration_out_of_range():
    facts = _facts("只有一条。")
    sk = _sk(paras=[({1, 7}, "正文")])
    res = assemble_skeleton(sk, facts)
    assert any("声明了不存在的事实序号" in h and "7" in h for h in res.hard)
    assert res.draft.facts_used == [1]           # 越界的 7 不进 facts_used


# ================================================================ G2 表层接地

def test_g2_substring_passes():
    assert surface_grounded("286块", "要交286块。")


def test_g2_numeric_equivalence_passes():
    """素材「两百八十六」表层「286」—— 数值等价（zhnum 口径）。"""
    assert surface_grounded("286", "要交两百八十六元。")
    assert surface_grounded("第一步", "第1步先做这件事。")     # 中文/阿拉伯序数等价


def test_g2_proper_noun_passes():
    assert surface_grounded("Boss直聘", "在Boss直聘上投简历。")


def test_g2_true_hallucination_rejected():
    """真幻觉：表层值在该条事实里不存在 → 硬违规。"""
    facts = _facts("卡里就剩两万三。")
    sk = _sk(paras=[({1}, "亏了{{n:1|199}}")])
    res = assemble_skeleton(sk, facts)
    assert any("找不到依据" in h and "199" in h for h in res.hard)


# ================================================================ G3 裸数字/年份门

def test_g3_bare_number_rejected():
    facts = _facts("卡里就剩两万三。", "投了五十份简历。")
    sk = _sk(paras=[({1}, "后来亏到只剩199。")])
    res = assemble_skeleton(sk, facts)
    assert any("声明依据之外的数字" in h and "199" in h for h in res.hard)


def test_g3_equivalent_rewrite_passes():
    """素材「三步」→ 正文「3步」（声明事实含「三步」）：改写不是编造，必须放行。"""
    facts = _facts("我总结出三步方法。")
    sk = _sk(paras=[({1}, "按3步来走就行。")])
    res = assemble_skeleton(sk, facts)
    assert res.hard == []


def test_g3_chinese_ordinal_no_false_positive():
    """「迈出这一步」类中文序数不能误报（稿件侧严格口径）。"""
    facts = _facts("我辞职了。")
    sk = _sk(paras=[({1}, "迈出这一步之后，一切都变了。")])
    res = assemble_skeleton(sk, facts)
    assert res.hard == []


def test_g3_year_gate():
    facts = _facts("2023年我辞职了。")
    sk = _sk(paras=[({1}, "2021年我辞职了。")])
    res = assemble_skeleton(sk, facts)
    assert any("年份" in h and "2021" in h for h in res.hard)


def test_g3_no_declaration_means_no_numbers():
    """没声明任何事实的段：证据为空 → 里面任何数字都算无依据（不是跳过检查）。"""
    facts = _facts("某条事实。")
    sk = _sk(paras=[((), "开头寒暄，第2期分享。")])
    res = assemble_skeleton(sk, facts)
    assert any("声明依据之外的数字" in h for h in res.hard)


def test_g3_proper_noun_suspicion_is_soft():
    """证据里有可抽取专名时，正文疑似未声明专名 → 软违规（不拦，交 QA）。
    证据侧抽不到任何专名则整个跳过 —— 同 `_rule_qa` 的「宁可不查，不可误报」。
    """
    facts = _facts("我一直用Obsidian记录。")
    sk = _sk(paras=[({1}, "后来换成了Notion。")])
    res = assemble_skeleton(sk, facts)
    assert res.hard == []
    assert any("专名" in s and "Notion" in s for s in res.soft)

    # 证据里没有任何专名 → 白名单为空 → 跳过（不误报）
    res2 = assemble_skeleton(_sk(paras=[({1}, "后来换成了Notion。")]), _facts("我用了某个工具。"))
    assert res2.hard == [] and res2.soft == []


def test_g3_extra_evidence_exempts_user_template():
    """用户模板/画像避雷是用户自己写的 —— 豁免数字门（不是模型编造）。"""
    facts = _facts("某条事实。")
    sk = _sk(paras=[((), "大家好，第3期干货来了。")])
    res = assemble_skeleton(sk, facts, extra_evidence=["大家好，第3期干货来了。"])
    assert res.hard == []


# ================================================================ G4 结构 / 装配

def test_g4_no_paras_no_titles():
    res = assemble_skeleton(DraftSkeleton(), _facts("事实"))
    assert any("没有" in h and "段" in h for h in res.hard)
    assert any("标题" in h for h in res.hard)


def test_body_and_facts_used_assembly():
    """段落按数组顺序以空行拼接；facts_used = 各段声明并集（代码计算）。"""
    facts = _facts("事实一：两万三。", "事实二：五十份。", "事实三：2023年。")
    sk = _sk(
        titles=["标题"],
        paras=[({1, 3}, "段A：{{n:1|两万三}}（{{n:3|2023年}}）"),
               ({2}, "段B：{{n:2|五十份}}")],
    )
    res = assemble_skeleton(sk, facts)
    assert res.hard == []
    assert res.draft.body == "段A：两万三（2023年）\n\n段B：五十份"
    assert res.draft.facts_used == [1, 2, 3]


def test_facts_used_union_not_first_para():
    """变异锚点：并集写成「首段声明」必须被这条抓住。"""
    facts = _facts("一", "二", "三")
    sk = _sk(paras=[({1}, "只用第一条"), ({2, 3}, "用了二三")])
    res = assemble_skeleton(sk, facts)
    assert res.draft.facts_used == [1, 2, 3]


def test_title_slots_use_all_facts_as_evidence():
    """标题的槽位证据 = 全部事实（不绑定段）。"""
    facts = _facts("两万三。", "五十份。")
    sk = _sk(titles=["卡里只剩{{n:1|两万三}}"], paras=[({2}, "正文{{n:2|五十份}}")])
    res = assemble_skeleton(sk, facts)
    assert res.hard == []
    assert res.draft.titles == ["卡里只剩两万三"]


def test_title_bare_number_rejected():
    facts = _facts("两万三。")
    sk = _sk(titles=["月入3000怎么活"], paras=[({1}, "正文")])
    res = assemble_skeleton(sk, facts)
    assert any("标题" in h and "3000" in h for h in res.hard)


# ================================================================ mock 不变量

def _corpus_materials() -> list[StructuredMaterial]:
    """评测语料全集 → mock 理解 —— mock 骨架的零违规要用真实规模的素材验。"""
    out = []
    for p in sorted(CORPUS.glob("m*.json")):
        if p.name == "MANIFEST.json":
            continue
        raw = json.loads(p.read_text(encoding="utf-8"))["raw_text"]
        out.append(_mock_understand(AnalyzeRequest(raw_text=raw, source_kind="general",
                                                   title=None)))
    assert len(out) >= 10
    return out


def test_cover_suggestion_slots_backfilled():
    """免检字段也要回填槽位（真模型实测：封面建议里会写槽位，裸占位符不能发布）。"""
    facts = _facts("卡里就剩两万三。", "另一条。")
    sk = parse_skeleton({
        "titles": ["标题"],
        "paras": [{"facts": [1], "text": "正文{{n:1|两万三}}"}],
        "cover_suggestion": "卡里{{n:1|两万三}}的逆袭",
        "interaction_line": "评论区聊聊{{n:9|幻觉}}",
    })
    res = assemble_skeleton(sk, facts)
    assert res.hard == []
    assert res.draft.cover_suggestion == "卡里两万三的逆袭"          # 合法槽位回填
    assert "{{" not in res.draft.interaction_line                   # 未接地也替换，不留占位符
    assert res.draft.interaction_line.endswith("幻觉")
    assert any("互动语" in s and "接地" in s for s in res.soft)      # 但要留软提醒


def test_mock_gate_zero_hard_on_corpus():
    """**语料级不变量**：mock 骨架对全部评测语料装配，硬违规恒为零。

    mock 与真实模式走同一个装配器 —— mock 出硬违规 = mock 造骨架或装配器有 bug。
    """
    for mat in _corpus_materials():
        brief = Brief(platform_code="xhs", angle="a", hooks=["h"],
                      structure_plan="s", tag_direction=["干货"])
        sk, extra = gen._mock_skeleton("xhs", {"code": "xhs", "name": "小红书"},
                                       mat, brief)
        res = assemble_skeleton(sk, mat.facts, extra_evidence=extra)
        assert res.hard == [], f"{mat.core_message[:20]}… → {res.hard}"


def _old_mock_body(mat, template=None, creator_profile=None) -> str:
    """改版前 `_mock_draft` 的 body 拼装算法（内联 golden，防止悄悄变形）。"""
    facts = mat.facts[:5]
    lines = []
    if template and template.opening:
        lines.append(template.opening)
    else:
        lines.append(f"先说结论：{gen._clip(mat.core_message, 80)}")
    lines.append("")
    for f in facts:
        marker = {"data": "📊", "story": "📖", "opinion": "💡"}.get(f.type, "·")
        lines.append(f"{marker} {f.text}")
    if creator_profile and creator_profile.get("avoid"):
        lines.append("")
        lines.append(f"⚠️ {creator_profile['avoid']}")
    if template and template.closing:
        lines.append("")
        lines.append(template.closing)
    else:
        lines += ["", "觉得有用就收藏，下次需要直接翻出来看～"]
    return "\n".join(lines)


def test_mock_body_byte_identical_to_old():
    """段落结构（段内 \\n + 段间 \\n\\n）与旧行结构同构 —— 逐字节一致。

    数字截断保护（_clip_numsafe）只在切点落在数字中间时偏离旧算法，
    本用例挑的语料都不触发那一刀（偏离由下一条单独钉）。
    """
    for mat in _corpus_materials():
        if _clip_cuts_number(mat.core_message, 80) or _clip_cuts_number(mat.core_message, 18) \
                or _clip_cuts_number(mat.core_message, 12):
            continue
        brief = Brief(platform_code="xhs", angle="a", hooks=["h"],
                      structure_plan="s", tag_direction=["干货"])
        draft, soft = gen.run_draft("xhs", {"code": "xhs", "name": "小红书"}, mat, brief)
        assert draft.body == _old_mock_body(mat), mat.core_message[:30]
        assert soft == []


def test_mock_body_byte_identical_with_template_and_profile():
    from app.schemas_gen import UserTemplate
    mat = _corpus_materials()[0]
    tpl = UserTemplate(name="tpl", voice="v", opening="大家好我是老张。",
                       structure=["先说结论"], closing="关注老张。")
    brief = Brief(platform_code="xhs", angle="a", hooks=["h"],
                  structure_plan="s", tag_direction=["干货"])
    draft, _ = gen.run_draft("xhs", {"code": "xhs", "name": "小红书"}, mat, brief,
                             template=tpl,
                             creator_profile={"avoid": "不要AI味", "domain": "自由职业"})
    assert draft.body == _old_mock_body(mat, tpl, {"avoid": "不要AI味"})
    assert "大家好我是老张。" in draft.body and "⚠️ 不要AI味" in draft.body


def _clip_cuts_number(text: str, n: int) -> bool:
    """旧 `_clip` 在 n 处切下去会不会把数字切成两半。"""
    t = text.strip()
    return len(t) > n and t[n - 1].isdigit() and bool(re.search(r"\d$", t[: n - 1]))


def test_mock_titles_never_contain_partial_number():
    """数字截断保护：标题里的每个数字 token 都必须是 core_message 里的完整子串。"""
    for mat in _corpus_materials():
        for title in (gen._clip_numsafe(mat.core_message, 18),
                      gen._clip_numsafe(mat.core_message, 12) + "｜真实经验"):
            for tok in numbers_in(title).values():
                assert tok in mat.core_message, f"{tok!r} 不是完整数字（{title!r}）"
            assert not re.search(r"\d…", title), f"省略号紧贴数字（{title!r}）"
