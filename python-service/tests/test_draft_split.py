"""骨架拆步（`llm_draft_split`，docs/79）。

只测**形状与顺序**：拆步的产出必须和不拆时同形，否则下游闸门/装配会全线出错。
真实质量差异由评测集判（`scripts/eval/run_eval.py`），不在这里断言。
"""

from types import SimpleNamespace

import pytest

from app.pipeline import generate, prompts


DNA = {"code": "xhs", "name": "小红书", "structure_template": ["hook", "body", "cta"],
       "limits": {}, "specs": {"title_chars_max": 20}, "body_chars": {"min": 200}}


def _mat():
    return SimpleNamespace(facts=[SimpleNamespace(text="要交286块"),
                                  SimpleNamespace(text="每月固定支出6500元")],
                           text="素材正文")


def _brief():
    return SimpleNamespace(angle="省钱", hooks=["反常识"], structure_plan=["hook", "body"],
                           tag_direction="省钱")


def test_split_assembles_same_shape(monkeypatch):
    calls = []

    def fake(prompt, **kw):
        calls.append(kw.get("scene"))
        assert isinstance(prompt, str) and prompt.lstrip().startswith("{")
        if kw.get("scene") == "draft_outline":
            return {"titles": ["标题"], "tags": ["#省钱"], "cover_suggestion": "封面",
                    "interaction_line": "你怎么看？", "rationale": "因为",
                    "outline": [{"role": "hook", "facts": [1], "point": "先抛数字"},
                                {"role": "cta", "facts": [], "point": "收尾"}]}
        body = prompt
        return {"text": "写了 {{n:1|286块}}" if "1/2" in body else "结尾"}

    monkeypatch.setattr(generate.llm, "chat_json", fake)
    data = generate._split_draft(DNA, _mat(), _brief(), None, None, None, None,
                                 avoid=None, library_context=None, image_notes=None,
                                 model=None, workers=2)

    assert data["titles"] == ["标题"]
    assert [p["role"] for p in data["paras"]] == ["hook", "cta"]
    assert data["paras"][0]["facts"] == [1]
    assert data["paras"][0]["text"].startswith("写了")
    assert calls[0] == "draft_outline"
    assert calls.count("draft_section") == 2


def test_split_keeps_paragraph_order_even_if_workers_finish_out_of_order(monkeypatch):
    def fake(prompt, **kw):
        if kw.get("scene") == "draft_outline":
            return {"outline": [{"role": "body", "facts": [1], "point": f"第{i}段"}
                                for i in range(1, 6)]}
        # 段落号越大睡得越短 → 完成顺序与下标相反
        n = int(prompt.split("只写第 ")[1].split("/")[0])
        import time
        time.sleep(0.01 * (6 - n))
        return {"text": f"段{n}"}

    monkeypatch.setattr(generate.llm, "chat_json", fake)
    data = generate._split_draft(DNA, _mat(), _brief(), None, None, None, None,
                                 avoid=None, library_context=None, image_notes=None,
                                 model=None, workers=5)
    assert [p["text"] for p in data["paras"]] == [f"段{i}" for i in range(1, 6)]


def test_empty_outline_returns_empty_paras_not_raise(monkeypatch):
    monkeypatch.setattr(generate.llm, "chat_json",
                        lambda *a, **k: {"titles": [], "outline": []})
    data = generate._split_draft(DNA, _mat(), _brief(), None, None, None, None,
                                 avoid=None, library_context=None, image_notes=None,
                                 model=None, workers=2)
    assert data["paras"] == []


def test_outline_prompt_asks_for_outline_not_body():
    p = prompts.build_draft_outline_prompt(DNA, _mat(), _brief())
    assert '"outline"' in p
    assert "只输出提纲" in p
    # 骨架那种 paras 字段不该出现在提纲里
    assert '"paras"' not in p


def test_section_prompt_only_carries_its_own_facts():
    p = prompts.build_draft_section_prompt(
        DNA, _mat(), _brief(), {"role": "body", "facts": [2], "point": "说支出"}, 1, 3)
    assert "只写第 1/3 段" in p
    assert "每月固定支出6500元" in p
    # 声明之外的 facts 不应单独摆出来（模型不该在这段用到它）
    assert '"n": 1' not in p.split('"section"')[1].split('"point"')[0]
