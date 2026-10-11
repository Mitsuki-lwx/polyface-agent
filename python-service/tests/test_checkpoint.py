"""T1 落盘契约：原子写 + 读失败当"没完成"。"""
from __future__ import annotations

import json

import pytest

from app.pipeline import checkpoint as ck


def test_roundtrip(tmp_path):
    ck.write_unit(tmp_path, "platform", "xhs", {"title": "标题"})
    assert ck.read_unit(tmp_path, "platform", "xhs") == {"title": "标题"}
    assert ck.list_units(tmp_path, "platform") == ["xhs"]


def test_kinds_do_not_mix(tmp_path):
    ck.write_unit(tmp_path, "platform", "xhs", {"a": 1})
    ck.write_unit(tmp_path, "stage", "探测", {"b": 2})
    assert ck.list_units(tmp_path, "platform") == ["xhs"]
    assert ck.list_units(tmp_path, "stage") == ["探测"]


def test_chinese_names_ok(tmp_path):
    """粗剪阶段名是中文 —— 必须能用，且与事件里的名字一致（不另造映射）。"""
    for name in ("探测", "找停顿", "剪裁", "抽音轨", "转写", "预览帧", "烧字幕"):
        ck.write_unit(tmp_path, "stage", name, {"ok": True})
    assert set(ck.list_units(tmp_path, "stage")) == {
        "探测", "找停顿", "剪裁", "抽音轨", "转写", "预览帧", "烧字幕"}


def test_half_written_is_not_a_unit(tmp_path):
    """**本契约的核心**：写一半崩掉留下的文件，必须被当成"没完成"。

    做法：手工造一个坏 JSON（模拟进程在写临时文件时被杀）。
    """
    d = ck.done_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    (d / "platform-xhs.json").write_text('{"title": "标题", "body": "没写完', encoding="utf-8")

    assert ck.read_unit(tmp_path, "platform", "xhs") is None      # 不猜
    assert ck.list_units(tmp_path, "platform") == []             # 不算已完成


def test_crash_mid_write_leaves_target_untouched(tmp_path, monkeypatch):
    """原子写：临时文件写失败时，**目标文件保持原样**（绝不半新半旧）。"""
    ck.write_unit(tmp_path, "platform", "xhs", {"v": 1})

    real_write_text = ck.Path.write_text

    def boom(self, *a, **kw):
        if self.name.endswith(".tmp"):
            raise OSError("模拟写临时文件时被杀")
        return real_write_text(self, *a, **kw)

    monkeypatch.setattr(ck.Path, "write_text", boom)
    with pytest.raises(OSError):
        ck.write_unit(tmp_path, "platform", "xhs", {"v": 2})

    # 目标还是旧的那份，不是半成品
    assert ck.read_unit(tmp_path, "platform", "xhs") == {"v": 1}


def test_no_tmp_leftover_counted(tmp_path):
    """`.tmp` 残留不能被当成单位。"""
    ck.write_unit(tmp_path, "platform", "xhs", {"a": 1})
    (ck.done_dir(tmp_path) / "platform-douyin.json.tmp").write_text("{", encoding="utf-8")
    assert ck.list_units(tmp_path, "platform") == ["xhs"]


def test_illegal_names_rejected(tmp_path):
    #  之类是 **Windows 文件名非法字符** —— 不挡的话落盘会静默失败
    # （实测踩过：用 "画面:1" 当阶段名，done/ 一个文件都没写出来，续跑当然全不命中）
    for bad in ("../evil", "a/b", "a\\b", "", "  ", ".", "..", "x" * 65,
                "画面:1", "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b"):
        with pytest.raises(ck.UnitNameError):
            ck.write_unit(tmp_path, "platform", bad, {})


def test_write_is_real_json(tmp_path):
    """产物必须是合法 JSON（Java 侧要读）。"""
    ck.write_unit(tmp_path, "platform", "xhs", {"t": "中文", "n": 3})
    raw = ck.unit_file(tmp_path, "platform", "xhs").read_text(encoding="utf-8")
    assert json.loads(raw) == {"t": "中文", "n": 3}


def test_clear_units(tmp_path):
    ck.write_unit(tmp_path, "platform", "xhs", {"a": 1})
    ck.write_unit(tmp_path, "platform", "douyin", {"a": 2})
    ck.write_unit(tmp_path, "stage", "探测", {"a": 3})
    assert ck.clear_units(tmp_path, "platform") == 2
    assert ck.list_units(tmp_path, "platform") == []
    assert ck.list_units(tmp_path, "stage") == ["探测"]           # 没被误删
