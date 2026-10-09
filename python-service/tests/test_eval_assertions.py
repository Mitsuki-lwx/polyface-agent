"""T4 · 评测断言的反例测试。

规矩（沿用 `test_check_scripts.py`）：**每条断言都要有反例** ——
只测「好产物能过」等于没测。另加一个「全合规产物」正例，确认不误报。

`scripts/eval/` 不是包，用 importlib 按文件路径加载（同 test_check_scripts.py 的做法）。
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "eval"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EVAL_DIR / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


A = _load("polyface_eval_assertions", "assertions.py")


# ---------------------------------------------------------------- 夹具

MATERIAL = {
    "id": "m99",
    "raw_text": "我2023年从杭州一家电商公司辞职，月薪1万8，做了三年多。"
                "存款够撑6个月，房租加吃饭一个月4500。现在靠写作月入3万。",
}

STRUCTURED = {
    "core_message": "裸辞后靠写作翻身",
    "tone": "真诚复盘",
    "audience": "想辞职的人",
    "facts": [{"text": "2023年辞职", "type": "time"},
              {"text": "月薪1万8", "type": "number"},
              {"text": "月入3万", "type": "number"}],
}

GOOD_DRAFT = {
    "titles": ["我裸辞那年做对了什么", "月薪1万8到月入3万"],
    "body": "2023年我辞掉了月薪1万8的运营工作，那时存款只够撑6个月，"
            "每个月房租加吃饭要花4500。现在靠写作月入3万，"
            "回头看那半年是最难也最值的一段时间，想把这中间做对的事讲清楚。",
    "tags": ["职场", "副业", "写作"],
    "qa": {"passed": True, "issues": [], "warnings": []},
}


def ctx(**over) -> dict:
    """构造断言上下文；只覆盖需要改的字段。"""
    d = {"material": copy.deepcopy(MATERIAL),
         "structured": copy.deepcopy(STRUCTURED),
         "platform": "xhs",
         "draft": copy.deepcopy(GOOD_DRAFT),
         "rules": {}}
    d.update(over)
    return d


def code(res, name: str) -> bool:
    """取某条断言的通过与否。"""
    return next(r.ok for r in res if r.code.startswith(name))


# ---------------------------------------------------------------- 正例

def test_good_draft_passes_everything():
    """全合规产物必须全绿 —— 防误报，这条最要紧。"""
    res = A.check_all(ctx())
    assert all(r.ok for r in res), [r.render() for r in res if not r.ok]


def test_video_platform_good_clip_sheet_passes():
    d = copy.deepcopy(GOOD_DRAFT)
    d["clip_sheet"] = {"scenes": [{"seq": 1, "script": "开场"}, {"seq": 2, "script": "正文"}]}
    res = A.check_all(ctx(platform="douyin", draft=d))
    assert code(res, "A6")


# ---------------------------------------------------------------- A1

def test_a1_number_not_in_material_fails():
    d = copy.deepcopy(GOOD_DRAFT)
    d["body"] = "2023年我辞掉了月薪1万8的工作，现在月入9万。"
    assert not code(A.check_all(ctx(draft=d)), "A1")


def test_a1_chinese_numeral_in_material_matches_arabic_in_body():
    """**实测踩到的误报**：素材写「定价九十九」，稿件写「99」—— 那是有依据的。

    2026-10-07 首次跑分时 A1 报了 `['99']`，查下来是素材用了中文数字。
    """
    m = copy.deepcopy(MATERIAL)
    m["raw_text"] = "我搭了付费专栏，定价九十九，卖了四百多份。"
    d = copy.deepcopy(GOOD_DRAFT)
    d["body"] = ("我搭了付费专栏，定价99，卖了400多份。这段经历让我想清楚很多事，"
                 "也想把它完整地写下来，给同样在犹豫要不要迈出这一步的人看。")
    assert code(A.check_all(ctx(material=m, draft=d)), "A1")


def test_a1_idiomatic_chinese_numeral_is_not_flagged():
    """**第二类误报**：正文里的「迈出这一步」「两下搞定」不是事实数字。

    严格模式丢掉成语化的单个中文数字；素材侧仍宽松（否则素材写「三」、
    稿件写「3」会反过来误报）。
    """
    d = copy.deepcopy(GOOD_DRAFT)
    d["body"] = ("2023年我辞掉了月薪1万8的工作，那时存款只够撑6个月。"
                 "回头看，我最想说的是：迈出这一步并不难，难的是两下就想明白。")
    assert code(A.check_all(ctx(draft=d)), "A1")


def test_a1_real_fabrication_still_caught():
    """归一化之后**真幻觉照样要抓住** —— 别把断言修成永远通过。"""
    d = copy.deepcopy(GOOD_DRAFT)
    d["body"] = "2023年我月薪1万8，后来月入9万7，还清了全部欠款。"
    res = A.check_all(ctx(draft=d))
    assert not code(res, "A1")
    detail = next(r.detail for r in res if r.code.startswith("A1"))
    assert "找不到依据" in detail


def test_a1_number_from_facts_only_passes():
    """数字只要在**事实清单**里有依据即可，不必字面出现在素材原文里。"""
    d = copy.deepcopy(GOOD_DRAFT)
    d["body"] = "2023年辞职，月入3万。"          # 3万 在 facts 里
    assert code(A.check_all(ctx(draft=d)), "A1")


# ---------------------------------------------------------------- A2

def test_a2_foreign_year_fails():
    d = copy.deepcopy(GOOD_DRAFT)
    d["body"] = "2023年辞职，2021年我还在读书。"   # 2021 素材里没有
    assert not code(A.check_all(ctx(draft=d)), "A2")


# ---------------------------------------------------------------- A3

@pytest.mark.parametrize("titles,why", [
    ([], "空列表"),
    ([""], "空标题"),
    (["同一个", "同一个"], "重复"),
    (["短", "另一个标题"], "太短"),
    (["啊" * 60], "太长"),
])
def test_a3_bad_titles_fail(titles, why):
    d = copy.deepcopy(GOOD_DRAFT)
    d["titles"] = titles
    assert not code(A.check_all(ctx(draft=d)), "A3"), why


def test_a3_title_max_is_configurable():
    """阈值必须真的起作用 —— 把上限调到 1 字，原本合法的标题就该红。"""
    assert code(A.check_all(ctx()), "A3")
    assert not code(A.check_all(ctx(rules={"title_min": 100})), "A3")


# ---------------------------------------------------------------- A4

def test_a4_too_short_body_fails():
    d = copy.deepcopy(GOOD_DRAFT)
    d["body"] = "太短了"
    assert not code(A.check_all(ctx(draft=d)), "A4")


def test_a4_body_max_is_configurable():
    assert code(A.check_all(ctx()), "A4")
    assert not code(A.check_all(ctx(rules={"body_max": 5})), "A4")


# ---------------------------------------------------------------- A5

def test_a5_hash_prefixed_tag_fails():
    d = copy.deepcopy(GOOD_DRAFT)
    d["tags"] = ["#职场", "副业"]
    assert not code(A.check_all(ctx(draft=d)), "A5")


def test_a5_too_few_tags_fails():
    d = copy.deepcopy(GOOD_DRAFT)
    d["tags"] = []
    assert not code(A.check_all(ctx(draft=d)), "A5")


def test_a5_tags_min_is_configurable():
    d = copy.deepcopy(GOOD_DRAFT)
    d["tags"] = ["职场"]
    assert code(A.check_all(ctx(draft=d)), "A5")                       # 默认下限 1
    assert not code(A.check_all(ctx(draft=d, rules={"tags_min": 3})), "A5")


# ---------------------------------------------------------------- A6

def test_a6_video_platform_without_clip_sheet_fails():
    assert not code(A.check_all(ctx(platform="douyin")), "A6")


def test_a6_non_contiguous_seq_fails():
    d = copy.deepcopy(GOOD_DRAFT)
    d["clip_sheet"] = {"scenes": [{"seq": 1, "script": "a"}, {"seq": 3, "script": "b"}]}
    assert not code(A.check_all(ctx(platform="bilibili", draft=d)), "A6")


def test_a6_empty_script_fails():
    d = copy.deepcopy(GOOD_DRAFT)
    d["clip_sheet"] = {"scenes": [{"seq": 1, "script": "  "}]}
    assert not code(A.check_all(ctx(platform="douyin", draft=d)), "A6")


def test_a6_text_platform_skips():
    assert code(A.check_all(ctx(platform="zhihu")), "A6")


# ---------------------------------------------------------------- A7

def test_a7_qa_failed_fails():
    d = copy.deepcopy(GOOD_DRAFT)
    d["qa"] = {"passed": False, "issues": ["标题太夸张"], "warnings": []}
    assert not code(A.check_all(ctx(draft=d)), "A7")


def test_a7_missing_qa_fails():
    d = copy.deepcopy(GOOD_DRAFT)
    d.pop("qa")
    assert not code(A.check_all(ctx(draft=d)), "A7")


# ---------------------------------------------------------------- 纯度

def test_assertions_never_call_llm(monkeypatch):
    """**断言层不调 LLM** —— 用行为证明，不用 grep 源码。

    把 `app.llm.chat` / `chat_json` 换成会炸的东西，跑完全部断言：
    只要有人偷偷调模型，这条就红。
    """
    from app import llm

    def boom(*a, **k):
        raise AssertionError("断言层不许调 LLM")

    monkeypatch.setattr(llm, "chat", boom)
    monkeypatch.setattr(llm, "chat_json", boom)

    d = copy.deepcopy(GOOD_DRAFT)
    d["clip_sheet"] = {"scenes": [{"seq": 1, "script": "a"}]}
    res = A.check_all(ctx(platform="douyin", draft=d))
    assert all(r.ok for r in res), [r.render() for r in res if not r.ok]


def test_check_all_survives_broken_assertion(monkeypatch):
    """单条断言自身抛异常 → 记为该条不通过，**不影响其它条**。"""
    def boom(_ctx):
        raise RuntimeError("故意炸")

    monkeypatch.setattr(A, "ALL", (boom, A.a3_titles_ok))
    res = A.check_all(ctx())
    assert len(res) == 2
    assert res[0].ok is False and "故意炸" in res[0].detail
    assert res[1].ok is True


# ---------------------------------------------------------------- A5 上限（来自 platform-dna）

def test_a5_tags_max_from_platform_dna():
    """平台罚的是**堆砌**，不是太少 —— 上限必须来自 DNA，不是默认值。"""
    d = copy.deepcopy(GOOD_DRAFT)
    d["tags"] = ["a", "b", "c", "d"]
    assert code(A.check_all(ctx(draft=d, rules={"tags_max": 3})), "A5") is False
    assert code(A.check_all(ctx(draft=d, rules={"tags_max": 4})), "A5") is True


def test_a5_tags_max_zero_means_no_check():
    """0 = 该平台没给上限，不检查。"""
    d = copy.deepcopy(GOOD_DRAFT)
    d["tags"] = [f"t{i}" for i in range(20)]
    assert code(A.check_all(ctx(draft=d, rules={"tags_max": 0})), "A5") is True


# ---------------------------------------------------------------- A8 站外导流

@pytest.mark.parametrize("fragment,label", [
    (" 有问题加我微信 abcde12345", "微信号"),
    (" 联系电话 13812345678", "手机号"),
    (" 邮箱 hi@example.com", "邮箱"),
    (" 详见 https://example.com/x", "站外链接"),
    (" 见 www.example.com", "站外链接"),
])
def test_a8_catches_offsite_diversion(fragment, label):
    """platform-dna 的 `limits.banned_direction` 在 5 个平台都明文禁止站外导流。"""
    d = copy.deepcopy(GOOD_DRAFT)
    # 把内容也放进素材，避免 A1 先报（这里只验 A8）
    text = d["body"] + fragment
    d["body"] = text
    m = copy.deepcopy(MATERIAL)
    m["raw_text"] = text
    res = A.check_all(ctx(material=m, draft=d))
    a8 = next(r for r in res if r.code.startswith("A8"))
    assert not a8.ok, label
    assert label in a8.detail


def test_a8_clean_draft_passes():
    assert code(A.check_all(ctx()), "A8")


def test_a8_does_not_false_positive_on_normal_phrases():
    """「评论区聊聊」这类正常互动引导**不能**误报。"""
    d = copy.deepcopy(GOOD_DRAFT)
    d["body"] = (d["body"] + "你们会怎么选？评论区聊聊。想看更多就关注我，"
                 "下期讲讲我是怎么找到第一个客户的，也欢迎私信交流。")
    m = copy.deepcopy(MATERIAL)
    m["raw_text"] = d["body"]
    assert code(A.check_all(ctx(material=m, draft=d)), "A8")


def test_assertion_count_is_pinned():
    """断言条数钉死 —— 加/删断言时这条会红，提醒同步 checklist 与文档。"""
    assert len(A.ALL) == 9


# ---------------------------------------------------------------- A9 专名

def test_a9_invented_proper_noun_is_flagged():
    m = copy.deepcopy(MATERIAL)
    m["raw_text"] = "我最后选了 Notion，领投的是 Coatue。"
    d = copy.deepcopy(GOOD_DRAFT)
    d["body"] = d["body"] + "后来我也试过飞书。"
    res = A.check_all(ctx(material=m, draft=d))
    a9 = next(r for r in res if r.code.startswith("A9"))
    assert not a9.ok and "飞书" in a9.detail


def test_a9_known_proper_noun_passes():
    m = copy.deepcopy(MATERIAL)
    m["raw_text"] = "我最后选了 Notion，领投的是 Coatue。"
    d = copy.deepcopy(GOOD_DRAFT)
    d["body"] = d["body"] + "Notion 我一直在用。"
    assert code(A.check_all(ctx(material=m, draft=d)), "A9")


def test_a9_skips_when_material_has_no_proper_nouns():
    """抽不到就跳过 —— 不许报"检查失败"（宁可不查，不可误报）。"""
    res = A.check_all(ctx())          # 夹具素材里没有专名
    a9 = next(r for r in res if r.code.startswith("A9"))
    assert a9.ok and "跳过" in a9.detail


def test_all_has_nine_assertions():
    assert len(A.ALL) == 9
