"""`scripts/eval/dna_rules.py` —— platform-dna → 断言阈值的桥。

**为什么值得单独测**：它是「评测集能不能当平台 DNA 实验台」的**唯一接口**。
它读错一个字段，评测集就会拿错误的阈值去判产出，而且**看起来一切正常**
（比如给知乎凭空加了个 1000 字上限、或者某平台的上限整条没生效）。

这里钉住三件事：
1. 5 个平台的关键阈值取自 DNA 的**实测值**（不是 `normalize_limits` 的默认值）
2. DNA 里写 `0` 表示**无上限**，不能掉进默认值
3. 指纹对 DNA 内容敏感 —— 改了规则要能看出来
"""
from __future__ import annotations

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


D = _load("polyface_dna_rules", "dna_rules.py")


# ---------------------------------------------------------------- 取值

@pytest.mark.parametrize("platform,key,expect", [
    ("xhs", "title_max", 20),
    ("xhs", "body_max", 1000),
    ("xhs", "tags_max", 6),
    ("douyin", "title_max", 55),
    ("douyin", "body_max", 2200),
    ("gzh", "tags_max", 3),
    ("bilibili", "title_max", 80),
    ("bilibili", "body_max", 2000),
    ("zhihu", "body_min", 100),
])
def test_values_come_from_dna(platform, key, expect):
    """阈值必须来自 DNA 的实测值，不是 `normalize_limits` 的兜底默认值。"""
    assert D.rules_for(platform)[key] == expect


def test_zhihu_zero_means_no_limit_not_the_default():
    """**实测踩到的坑**：知乎 `specs.body_chars_max: 0` 是「无上限」，
    早先的分支顺序让它掉到了 `normalize_limits` 的默认值 1000 —— 凭空给知乎加了上限。"""
    assert D.rules_for("zhihu")["body_max"] > 10000


def test_bilibili_uses_desc_chars_max():
    """B站正文上限叫 `desc_chars_max`（各平台字段名不统一）。"""
    assert D.rules_for("bilibili")["body_max"] == 2000


def test_douyin_uses_caption_chars_max():
    assert D.rules_for("douyin")["body_max"] == 2200


def test_unknown_platform_returns_empty_not_exception():
    """读不到就返回空 dict，让断言退回默认值 —— 但**不假装读到了**。"""
    assert D.rules_for("不存在的平台") == {}


def test_rules_for_all_covers_five_platforms():
    allr = D.rules_for_all()
    assert {"xhs", "douyin", "gzh", "zhihu", "bilibili"} <= set(allr)
    assert all(v for v in allr.values()), "每个平台都该有至少一条阈值"


# ---------------------------------------------------------------- 指纹

def test_fingerprint_is_stable():
    assert D.fingerprint() == D.fingerprint()


def test_fingerprint_changes_when_dna_changes(monkeypatch):
    """改了 DNA 规则，指纹必须变 —— 否则报告里「通过率变了」无法归因。"""
    from app import dna

    before = D.fingerprint()
    real = dna.load_dna

    def tampered(code):
        d = real(code)
        if code == "xhs":
            d = dict(d)
            d["specs"] = {**(d.get("specs") or {}), "title_chars_max": 19}
        return d

    monkeypatch.setattr(dna, "load_dna", tampered)
    after = D.fingerprint()
    assert after["xhs"] != before["xhs"]
    assert D.combined(after) != D.combined(before)


def test_fingerprint_ignores_non_behavioural_fields(monkeypatch):
    """`sources` / `updated_at` 变了**不算**行为变化 —— 不该动指纹。

    否则每次补个来源链接、更新个日期，报告就全标成「不可比」。
    """
    from app import dna

    before = D.fingerprint()
    real = dna.load_dna

    def touched(code):
        d = dict(real(code))
        d["updated_at"] = "2099-01-01"
        d["sources"] = [{"id": "new", "type": "official"}]
        return d

    monkeypatch.setattr(dna, "load_dna", touched)
    assert D.fingerprint() == before


# ---------------------------------------------------------------- 新鲜度

def test_staleness_reports_every_platform():
    st = D.staleness()
    assert {s["platform"] for s in st} >= {"xhs", "douyin", "gzh", "zhihu", "bilibili"}
    assert all("days" in s for s in st)
