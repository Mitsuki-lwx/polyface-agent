"""T1 时间线契约：轴换算 + 校验 + 字幕规则。

对应 `docs/checklist_timeline.md` §3 §4 §6。
最要紧的两条：**保留/剪掉的语义翻转**（写反不会报错，只会安静地剪错）、
**字幕跟着剪点走**（错位是观众一眼能看出来的）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pipeline import timeline as tl


# ---------------- 语义翻转（本模块最容易写错的地方）----------------

def test_cuts_to_keep_is_really_kept_not_cut():
    """`cuts` 是"剪掉"的，`keep` 是"保留"的 —— 必须真的转过来。"""
    keep = tl.cuts_to_keep([[10.0, 20.0], [30.0, 40.0]], total=100.0)
    assert keep == [[0.0, 10.0], [20.0, 30.0], [40.0, 100.0]]


def test_cuts_to_keep_covers_whole_source():
    """保留区间之和 == 源时长 - 剪掉的和。"""
    keep = tl.cuts_to_keep([[10.0, 20.0], [30.0, 40.0]], total=100.0)
    assert tl.keep_seconds(keep) == pytest.approx(80.0)


def test_cuts_to_keep_at_edges():
    assert tl.cuts_to_keep([[0.0, 5.0]], total=10.0) == [[5.0, 10.0]]
    assert tl.cuts_to_keep([[8.0, 10.0]], total=10.0) == [[0.0, 8.0]]
    assert tl.cuts_to_keep([], total=10.0) == [[0.0, 10.0]]


# ---------------- 轴换算 ----------------

def test_roundtrip_film_source():
    keep = [[0.0, 10.0], [20.0, 30.0]]
    for t_src in (0.0, 5.0, 9.5, 20.5, 25.0, 30.0):
        f = tl.source_to_film(t_src, keep)
        assert f is not None
        assert tl.film_to_source(f, keep) == pytest.approx(t_src, abs=1e-3)


def test_boundary_is_ambiguous_by_construction():
    """剪点是瞬时的：源 10.0（第 1 段尾）与 20.0（第 2 段头）都落在成片 10.0。

    所以反推回源轴只能给一个 —— 这不是 bug，是"剪掉了一段"的必然结果。
    写下来是为了以后有人看见它时不会误以为是错。
    """
    keep = [[0.0, 10.0], [20.0, 30.0]]
    assert tl.source_to_film(10.0, keep) == tl.source_to_film(20.0, keep) == 10.0
    assert tl.film_to_source(10.0, keep) == 10.0


def test_source_in_cut_region_has_no_film_time():
    keep = [[0.0, 10.0], [20.0, 30.0]]
    assert tl.source_to_film(15.0, keep) is None      # 15s 被剪掉了
    assert tl.film_to_source(999.0, keep) is None     # 超出成片长度


# ---------------- 校验：必须说清是哪一段 ----------------

def _base():
    return tl.make({"asset_id": 1, "path": "a.mp4"}, [[0.0, 10.0]], [])


def test_validate_ok():
    tl.validate(_base(), source_duration=10.0)


def test_rejects_unknown_version():
    bad = _base()
    bad["version"] = 99
    with pytest.raises(tl.TimelineError, match="版本"):
        tl.validate(bad)


def test_rejects_empty_keep():
    bad = _base()
    bad["keep"] = []
    with pytest.raises(tl.TimelineError, match="至少"):
        tl.validate(bad)


def test_rejects_overlap_and_names_the_segment():
    bad = _base()
    bad["keep"] = [[0.0, 10.0], [5.0, 15.0]]
    with pytest.raises(tl.TimelineError) as e:
        tl.validate(bad)
    assert "第 2 段" in str(e.value), "必须指出是哪一段"
    assert "重叠" in str(e.value)


def test_rejects_start_not_before_end():
    bad = _base()
    bad["keep"] = [[5.0, 5.0]]
    with pytest.raises(tl.TimelineError, match="第 1 段"):
        tl.validate(bad)


def test_rejects_out_of_range_and_names_the_segment():
    bad = _base()
    bad["keep"] = [[0.0, 10.0], [12.0, 20.0]]
    with pytest.raises(tl.TimelineError) as e:
        tl.validate(bad, source_duration=15.0)
    assert "第 2 段" in str(e.value)
    assert "越界" in str(e.value) and "15.000" in str(e.value), "要给出实际时长"


def test_rejects_non_numeric():
    bad = _base()
    bad["keep"] = [["甲", 10.0]]
    with pytest.raises(tl.TimelineError, match="不是数字"):
        tl.validate(bad)


def test_parse_normalises():
    out = tl.parse({"version": 1, "keep": [[0, 10]], "segments": None,
                    "source": {"asset_id": 3}}, source_duration=10)
    assert out["keep"] == [[0.0, 10.0]]
    assert out["segments"] == []
    assert out["source"] == {"asset_id": 3}


# ---------------- 字幕：源轴 ↔ 成片轴（"跟着剪点走"）----------------

def test_segments_follow_cuts_without_drift():
    """**本模块最容易错的地方**：删掉中间一段，字幕不能错位。"""
    keep = [[0.0, 10.0], [20.0, 30.0]]          # 10~20 被剪掉
    src = [{"start": 2.0, "end": 4.0, "text": "第一句"},
           {"start": 12.0, "end": 14.0, "text": "在剪掉的区间里"},
           {"start": 25.0, "end": 27.0, "text": "第三句"}]
    film = tl.segments_to_film(src, keep)

    assert [g["text"] for g in film] == ["第一句", "第三句"]
    assert film[0]["start"] == pytest.approx(2.0)
    # 25s（源）→ 10 + 5 = 15s（成片）—— 不是 25
    assert film[1]["start"] == pytest.approx(15.0)


def test_segment_straddling_a_cut_stays_one_entry():
    """**成片是连续的** —— 跨界的段在成片里就是一段连续时间，**不拆**。

    早先按保留区间拆开是错的：同一句话出现两次，其中一段还可能只有几十毫秒
    （端到端实测抓到过 28ms 的碎片）。
    """
    keep = [[0.0, 10.0], [20.0, 30.0]]
    src = [{"start": 8.0, "end": 25.0, "text": "跨了边界"}]
    film = tl.segments_to_film(src, keep)

    assert len(film) == 1, f"不该拆成多条：{film}"
    # 8s（源）→ 成片 8s；25s（源）→ 成片 15s。中间被剪掉的 10s 停顿在成片里不存在
    assert (film[0]["start"], film[0]["end"]) == (8.0, 15.0)


def test_film_to_source_for_building_timeline():
    """建时间线时把 ASR 的成片轴字幕映射回源轴（只做一次）。"""
    keep = [[0.0, 10.0], [20.0, 30.0]]
    film = [{"start": 2.0, "end": 4.0, "text": "第一句"},
            {"start": 12.0, "end": 14.0, "text": "第二句"}]
    src = tl.segments_to_source(film, keep)

    assert src[0] == {"start": 2.0, "end": 4.0, "text": "第一句"}
    # 成片 12s → 源 22s
    assert src[1]["start"] == pytest.approx(22.0)


def test_edit_keep_does_not_touch_segments():
    """**结构上的保证**：改 keep 完全不碰 segments —— 所以不可能错位。"""
    src = [{"start": 2.0, "end": 4.0, "text": "甲"},
           {"start": 25.0, "end": 27.0, "text": "乙"}]
    a = tl.segments_to_film(src, [[0.0, 10.0], [20.0, 30.0]])
    b = tl.segments_to_film(src, [[0.0, 10.0], [22.0, 30.0]])   # 只改了第二段起点

    assert [g["text"] for g in a] == [g["text"] for g in b]
    assert a[0] == b[0], "改第二段不该影响第一段的字幕"


# ---------------- select 表达式 ----------------

def test_keep_select_expr():
    assert tl.keep_select_expr([[0.0, 10.0], [20.0, 30.0]]) == \
        "between(t,0.0,10.0)+between(t,20.0,30.0)"
    assert tl.keep_select_expr([]) == "0"


# ---------------- T2：粗剪产出的时间线 ----------------

def _load_roughcut():
    import importlib.util
    import sys as _sys
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    spec = importlib.util.spec_from_file_location("polyface_rc_timeline", scripts / "roughcut.py")
    mod = importlib.util.module_from_spec(spec)
    _sys.modules["polyface_rc_timeline"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_roughcut_writes_timeline_whose_segments_roundtrip(tmp_path, monkeypatch):
    """**核心不变量**：粗剪把 ASR 的成片轴字幕映射回源轴存下来；
    再映射回成片轴必须**等于原始 ASR 输出** —— 否则就是"字幕会错位"。

    同时验 `keep` 真的是"保留"（成片时长 == keep 之和）。
    """
    from app.pipeline import asr, roughcut as rc
    R = _load_roughcut()

    segs = [{"start": 0.0, "end": 1.5, "text": "第一句"},
            {"start": 1.5, "end": 3.0, "text": "第二句"}]
    monkeypatch.setattr(rc, "probe", lambda p, **kw: {"width": 1, "height": 1, "duration": 10.0})
    monkeypatch.setattr(rc, "detect_pauses", lambda *a, **kw: [(4.0, 6.0)])
    monkeypatch.setattr(rc, "plan_cuts", lambda pauses, margin, total: [(4.0, 6.0)])
    monkeypatch.setattr(rc, "cut_video", lambda src, dst, cuts, **kw: Path(dst).write_bytes(b"c"))
    monkeypatch.setattr(rc, "extract_wav", lambda src, dst, **kw: Path(dst).write_bytes(b"w"))
    monkeypatch.setattr(asr, "transcribe_segments", lambda path, **kw: list(segs))
    # 去冗余阶段用的是**词级**入口（它在剪之前跑，读的是原始音频）
    monkeypatch.setattr(asr, "transcribe_words",
                        lambda path, **kw: [dict(x, words=[]) for x in segs])
    monkeypatch.setattr(rc, "burn_subtitles",
                        lambda cut, out, srt, params, **kw: Path(out).write_bytes(b"o"))

    src = tmp_path / "口播.mp4"
    src.write_bytes(b"fake")
    out = tmp_path / "成片.mp4"
    rc_code = R.main([str(src), "--yes", "--workdir", str(tmp_path / "w"), "-o", str(out),
                      "--events", str(tmp_path / "e.jsonl"), "--level", "quiet"])
    assert rc_code == 0

    tline = json.loads(out.with_suffix(".timeline.json").read_text(encoding="utf-8"))
    assert tline["version"] == 1
    tl.validate(tline, source_duration=10.0)

    # keep 是"保留"：10s 里剪掉 [4,6] → 保留 [0,4] 与 [6,10]
    assert tline["keep"] == [[0.0, 4.0], [6.0, 10.0]]
    assert tl.keep_seconds(tline["keep"]) == 8.0

    # 往返：源轴 → 成片轴 == 原始 ASR 输出
    back = tl.segments_to_film(tline["segments"], tline["keep"])
    assert back == segs, f"往返不一致（会错位）：{back} != {segs}"


def test_timeline_has_no_ffmpeg_params(tmp_path):
    """数据格式不该被渲染实现污染（checklist §3）。"""
    import json as _json
    tline = tl.make({"path": "a.mp4", "duration": 10.0}, [[0.0, 10.0]], [])
    blob = _json.dumps(tline, ensure_ascii=False)
    for banned in ("ffmpeg", "-vf", "crf", "preset", "codec"):
        assert banned not in blob


# ---------------- T5：校验端点（**唯一一份校验实现**）----------------

def test_validate_endpoint_accepts_and_normalises():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)

    r = c.post("/timeline/validate", json={
        "timeline": {"version": 1, "source": {"path": "a.mp4"},
                     "keep": [[0, 10]], "segments": []},
        "source_duration": 10.0})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["normalized"]["keep"] == [[0.0, 10.0]]


def test_validate_endpoint_rejects_with_segment_number():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)

    r = c.post("/timeline/validate", json={
        "timeline": {"version": 1, "source": {}, "keep": [[0, 10], [5, 15]], "segments": []}})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "第 2 段" in detail and "重叠" in detail, f"必须指出是哪一段，实际：{detail}"


def test_validate_endpoint_rejects_out_of_range():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)

    r = c.post("/timeline/validate", json={
        "timeline": {"version": 1, "source": {}, "keep": [[0, 10], [12, 20]], "segments": []},
        "source_duration": 15.0})
    assert r.status_code == 400
    assert "越界" in r.json()["detail"]
