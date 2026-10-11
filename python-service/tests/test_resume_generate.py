"""T2 逐平台落盘 + 续跑只跑缺的（生成侧）。

对应 `docs/spec_resume.md` §3 能力 1/4/5、`docs/checklist_resume.md` §4 §5。
核心不变量：**落盘粒度 = 续跑粒度**。
"""
from __future__ import annotations

import pytest

from app.pipeline import checkpoint as ck
from app.pipeline import generate as G
from app.schemas_gen import GenerateRequest

SAMPLE = "我2023年裸辞做自由职业，靠写作从月入0做到3万。"


@pytest.fixture
def count_calls(monkeypatch):
    """记录真正跑了的平台（落盘之后"谁被调了"是续跑是否生效的唯一证据）。"""
    calls: list[str] = []
    real = G._generate_one

    def fake(code, *args, **kwargs):
        calls.append(code)
        return real(code, *args, **kwargs)

    monkeypatch.setattr(G, "_generate_one", fake)
    return calls


def _req(tmp_path, platforms):
    return GenerateRequest(raw_text=SAMPLE, platforms=list(platforms),
                           events_path=str(tmp_path / "events.jsonl"))


def test_persists_each_platform_as_it_finishes(tmp_path, count_calls):
    """一个平台一完成就落盘 —— **不等整批返回**。"""
    _, drafts, _, _ = G.generate(_req(tmp_path, ["xhs", "douyin"]))

    assert sorted(ck.list_units(tmp_path, "platform")) == ["douyin", "xhs"]
    # 落盘内容 = 返回的稿子（不是摘要、不是空壳）
    for d in drafts:
        payload = ck.read_unit(tmp_path, "platform", d.platform_code)
        assert payload["draft"] == d.draft.model_dump()


def test_resume_only_runs_missing_platforms(tmp_path, count_calls):
    """已完成 1 个平台 → 续跑**只跑缺的那个**。"""
    G.generate(_req(tmp_path, ["xhs"]))
    count_calls.clear()

    _, drafts, _, failures = G.generate(_req(tmp_path, ["xhs", "douyin"]))

    assert count_calls == ["douyin"], f"只该跑 douyin，实际跑了 {count_calls}"
    assert failures == []
    assert [d.platform_code for d in drafts] == ["xhs", "douyin"], "按请求顺序返回完整集合"


def test_all_done_makes_no_calls(tmp_path, count_calls):
    """全部已完成 → 一次调用都不发（不白花钱）。"""
    G.generate(_req(tmp_path, ["xhs", "douyin"]))
    count_calls.clear()

    _, drafts, _, _ = G.generate(_req(tmp_path, ["xhs", "douyin"]))

    assert count_calls == [], "全已完成时不该有任何调用"
    assert len(drafts) == 2


def test_corrupt_done_file_reruns_that_platform(tmp_path, count_calls):
    """落盘读不出 → 当没完成，**重跑该平台**（不猜、不跳过）。"""
    G.generate(_req(tmp_path, ["xhs", "douyin"]))
    (ck.done_dir(tmp_path) / "platform-xhs.json").write_text('{"draft": ', encoding="utf-8")
    count_calls.clear()

    _, drafts, _, _ = G.generate(_req(tmp_path, ["xhs", "douyin"]))

    assert count_calls == ["xhs"], "坏落盘的那个平台必须重跑"
    assert [d.platform_code for d in drafts] == ["xhs", "douyin"]


def test_no_events_path_means_no_persistence(tmp_path, count_calls):
    """CLI 直跑（无 events_path）不落盘、也不续跑 —— 两种用法行为一致。"""
    req = GenerateRequest(raw_text=SAMPLE, platforms=["xhs"])
    G.generate(req)

    assert ck.list_units(tmp_path, "platform") == []


def test_failed_platform_is_not_persisted(tmp_path, monkeypatch):
    """失败平台的格子不落盘 —— 否则下次续跑会拿它当"已完成"。"""
    real = G._generate_one

    def fake(code, *args, **kwargs):
        if code == "douyin":
            raise RuntimeError("模拟失败")
        return real(code, *args, **kwargs)

    monkeypatch.setattr(G, "_generate_one", fake)
    _, _, _, failures = G.generate(_req(tmp_path, ["xhs", "douyin"]))

    assert len(failures) == 1
    assert ck.list_units(tmp_path, "platform") == ["xhs"], "失败平台不该被记为已完成"


def test_resume_is_logged_in_events(tmp_path, count_calls):
    """续跑要在事件流里留痕（"跳过了哪些、跑了哪些"可查）。"""
    G.generate(_req(tmp_path, ["xhs"]))
    _, _, _, _ = G.generate(_req(tmp_path, ["xhs", "douyin"]))

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert any('"续跑"' in ln and "douyin" in ln for ln in lines), "事件流里应有续跑记录"
