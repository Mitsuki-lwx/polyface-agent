"""专名抽取（`app/pipeline/proper.py`）的测试。

**为什么值得单独测**：它是「专名有依据」这条断言的**唯一输入**。
抽宽了 → 天天误报（用户会去删正确的词）；抽窄了 → 覆盖变少（可接受）。
所以这里的重点是**反例**：普通词绝不能进白名单。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pipeline import proper


# ---------------------------------------------------------------- 该抽的

@pytest.mark.parametrize("text,expect", [
    ("我最后选了 Notion", "Notion"),
    ("领投的是 Coatue 和 Sequoia", "Coatue"),
    ("零基础考 CPA", "CPA"),
    ("买了沪深300ETF联接C类", "ETF"),
    ("在 Boss直聘上投了简历", "Boss"),
    ("用 Google Maps 导航", "Google"),
    ("配速用 Forerunner 记的", "Forerunner"),
    ("规则是 T+1 到账", "T+1"),
])
def test_latin_runs_with_case_or_digit(text, expect):
    assert expect in proper.extract(text)


def test_title_marks_are_extracted():
    assert "小鹅通" in proper.extract("靠着一个叫《小鹅通》的工具")
    assert "Notion" in proper.extract("用了《Notion》")


def test_known_chinese_platforms():
    got = proper.extract("我在小红书和抖音都发过，闲鱼上也卖了")
    assert {"小红书", "抖音", "闲鱼"} <= got


def test_extra_wordlist_is_merged():
    """用户补的行业词（配置项）要能进白名单。"""
    assert "某某工具" in proper.extract("没提到它", extra={"某某工具"})


# ---------------------------------------------------------------- 不该抽的（重点）

@pytest.mark.parametrize("word", [
    "brain", "collaborator", "freelance", "second", "database", "email", "app",
])
def test_common_english_words_are_not_extracted_on_draft_side(word):
    """**实测踩到过**：稿件侧若放宽成「≥4 个字母的全小写词」，
    `brain` / `collaborator` / `freelance` / `second` 这些普通英文词会被抽进来 → 天天误报。

    注意 `strict=True`：**素材侧是宽松的**（见下一条），两边刻意不对称。"""
    assert word not in proper.extract(f"这个 {word} 的用法", strict=True)


def test_material_side_is_loose():
    """**素材侧必须宽松**：素材写小写 `database`、稿件写大写 `Database` 是同一个词。

    素材侧若也严格，`database` 就进不了白名单，而稿件的 `Database` 会被抽出来 → 误报
    （实测：m03/xhs 报「素材里没有的专名：Database」）。
    """
    assert "database" in proper.extract("它那个 database 视图很直观")              # 素材侧
    assert "Database" in proper.extract("用 Database 视图", strict=True)          # 稿件侧
    # 两边都抽到 → 比对时大小写不敏感 → 不报


def test_quoted_ordinary_words_are_not_extracted():
    """**引号内刻意不抽** —— 引号里也可能是被强调的普通词。

    这是 spec 实现时改的（`docs/spec_factguard.md` §5）：
    放弃这部分覆盖，换零噪声。
    """
    assert proper.extract('这件事说白了就是"副业"而已') == set()


def test_chinese_org_names_are_not_extracted():
    """中文机构名需要分词与 NER，**刻意不抽**（宁可不查，不可误报）。"""
    got = proper.extract("我在北京的一家互联网公司做运营，后来去了杭州")
    assert got == set()


def test_empty_text():
    assert proper.extract("") == set()
    assert proper.extract(None) == set()          # type: ignore[arg-type]


# ---------------------------------------------------------------- 真实素材

def test_real_corpus_extraction_has_no_ordinary_words():
    """拿 10 条真实冻结素材跑一遍，人工过一眼的机械化版本：
    结果里不许出现已知的普通英文词。"""
    corpus = Path(__file__).resolve().parents[2] / "eval" / "corpus"
    files = sorted(corpus.glob("m[0-9][0-9].json"))
    if not files:
        pytest.skip("素材集不在（eval/corpus/）")
    bad = set()
    for f in files:
        names = proper.extract(json.loads(f.read_text(encoding="utf-8"))["raw_text"])
        bad |= {n for n in names if n.lower() in proper._COMMON_LATIN}
    assert not bad, f"白名单里混进了常见词：{sorted(bad)}"


# ---------------------------------------------------------------- 通用缩写（实测教训）

@pytest.mark.parametrize("word", [
    "AI", "AIGC", "LLM", "SOP", "KPI", "OKR", "SEO", "PPT", "API", "UI",
])
def test_common_acronyms_are_not_extracted(word):
    """**实测踩到过**：不排掉通用缩写，A9 会在 **19/50 格**上误报，而且全是同一个词 `AI` ——
    因为平台 DNA 明文要求「AI 辅助创作需标明」，草稿**合法地**写了「AI」，
    而素材里当然不会有。

    一个机械断言要有自己的**误报预算**；这一条最初严重超支。
    """
    assert word not in proper.extract(f"这段提到 {word} 的内容")


def test_real_proper_acronyms_still_extracted():
    """反例守卫：真专名（CPA / ETF）不能被通用缩写词表误伤。"""
    got = proper.extract("我考了 CPA，买了沪深300ETF")
    assert {"CPA", "ETF"} <= got
