"""阶段事件底座单测：格式 / 落盘 / 分级 / 心跳 / 观测不影响业务。

对应 `docs/spec_observability.md` 的三条硬不变量：
① 观测不得改变业务 ② 格式稳定（给机器读）③ 长阶段不能看起来卡死。
"""
from __future__ import annotations

import io
import json
import time

from app.pipeline.runlog import END, FAIL, PROGRESS, START, RunLog


def _log(tmp_path, **kw) -> tuple[RunLog, io.StringIO]:
    stream = io.StringIO()
    kw.setdefault("level", "normal")
    kw.setdefault("heartbeat_sec", 0)          # 默认关心跳，避免干扰行数断言
    kw.setdefault("stream", stream)
    return RunLog(run_id="t1", **kw), stream


# ---------------------------------------------------------------- 格式（给机器读）

def test_event_is_one_json_line_with_version(tmp_path):
    log, _ = _log(tmp_path, sink=tmp_path / "e.jsonl")
    with log.stage("探测"):
        pass
    lines = (tmp_path / "e.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2                     # start + end
    for line in lines:
        ev = json.loads(line)                  # 逐行可解析，不依赖整体
        assert set(ev) == {"run_id", "stage", "kind", "ts", "elapsed_ms",
                           "message", "fields", "version"}
        assert ev["version"] == 1              # 格式版本必须带上
        assert ev["run_id"] == "t1"
    kinds = [json.loads(x)["kind"] for x in lines]
    assert kinds == [START, END]


def test_fields_and_message_are_recorded(tmp_path):
    log, _ = _log(tmp_path, sink=tmp_path / "e.jsonl")
    with log.stage("剪裁") as r:
        r.done("剪掉 7.19s", cut_sec=7.19)
    end = json.loads((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert end["message"] == "剪掉 7.19s"
    assert end["fields"]["cut_sec"] == 7.19
    assert end["elapsed_ms"] >= 0


def test_stage_durations_helper(tmp_path):
    log, _ = _log(tmp_path)
    with log.stage("a"):
        time.sleep(0.02)
    with log.stage("b"):
        pass
    d = log.stage_durations()
    assert set(d) == {"a", "b"} and d["a"] >= 15


# ---------------------------------------------------------------- 观测不得改变业务

def test_sink_disabled_creates_no_file(tmp_path):
    log, _ = _log(tmp_path, sink=None)
    with log.stage("x"):
        pass
    assert list(tmp_path.iterdir()) == []


def test_unwritable_sink_does_not_break_business(tmp_path):
    """把事件文件目录设成**不可写**（父路径是个文件）→ 业务必须照跑完。"""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    log, stream = _log(tmp_path, sink=blocker / "e.jsonl")
    ok = False
    with log.stage("探测"):
        ok = True
    assert ok, "上报失败不该中断阶段体"
    assert "[warn]" in stream.getvalue(), "失败要有告警，不能静默吞掉"
    assert log.events(), "内存里仍应留下事件（供报告用）"


def test_stage_failure_emits_fail_and_reraises(tmp_path):
    log, _ = _log(tmp_path, sink=tmp_path / "e.jsonl")
    try:
        with log.stage("剪裁"):
            raise RuntimeError("炸了")
    except RuntimeError:
        pass
    else:
        raise AssertionError("阶段体的异常必须原样抛出（观测不得吞掉业务异常）")
    ev = json.loads((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert ev["kind"] == FAIL and "炸了" in ev["message"]


# ---------------------------------------------------------------- 分级

def test_quiet_hides_stage_lines_but_keeps_events(tmp_path):
    log, stream = _log(tmp_path, sink=tmp_path / "e.jsonl", level="quiet")
    with log.stage("探测") as r:
        r.progress(50)
        r.done("done")
    out = stream.getvalue()
    assert "▶" not in out and "✓" not in out and "50%" not in out
    assert len(log.events()) >= 3, "安静档只是不打印，事件仍要记"


def test_levels_strictly_increase(tmp_path):
    counts = {}
    for level in ("quiet", "normal", "verbose"):
        log, stream = _log(tmp_path, level=level)
        with log.stage("s") as r:
            r.progress(50)
            log.raw("ffmpeg -y ...")
        counts[level] = len(stream.getvalue().splitlines())
    assert counts["quiet"] < counts["normal"] < counts["verbose"], counts


def test_raw_only_in_verbose(tmp_path):
    normal_log, normal_stream = _log(tmp_path, level="normal")
    verbose_log, verbose_stream = _log(tmp_path, level="verbose")
    normal_log.raw("cmd")
    verbose_log.raw("cmd")
    assert "cmd" not in normal_stream.getvalue()
    assert "cmd" in verbose_stream.getvalue()


# ---------------------------------------------------------------- 心跳与进度

def test_heartbeat_fires_for_slow_stage(tmp_path):
    log, stream = _log(tmp_path, sink=tmp_path / "e.jsonl", heartbeat_sec=0.05)
    with log.stage("转写"):
        time.sleep(0.22)
    beats = [e for e in log.events() if e["kind"] == PROGRESS and e["message"] == "心跳"]
    assert len(beats) >= 2, f"慢阶段该有心跳，实际 {len(beats)} 次"
    assert "还在跑" in stream.getvalue()


def test_no_heartbeat_when_disabled(tmp_path):
    log, _ = _log(tmp_path, heartbeat_sec=0)
    with log.stage("转写"):
        time.sleep(0.1)
    assert not [e for e in log.events() if e["message"] == "心跳"]


def test_progress_is_clamped_to_0_100(tmp_path):
    log, _ = _log(tmp_path)
    with log.stage("剪裁") as r:
        r.progress(150)
        r.progress(100.0)
    pcts = [e["fields"]["pct"] for e in log.events() if e["kind"] == PROGRESS]
    assert pcts[0] == 100.0, "超过 100 要夹到 100"

    log2, _ = _log(tmp_path)
    with log2.stage("剪裁") as r:
        r.progress(-5)
    pcts2 = [e["fields"]["pct"] for e in log2.events() if e["kind"] == PROGRESS]
    assert pcts2[0] == 0.0, "负数要夹到 0"


def test_progress_never_goes_backwards(tmp_path):
    """回退的进度一律丢弃 —— 否则终端上会出现进度条倒退。

    注意"夹到 100 之后再报 80"这种情形：**也应该被丢掉**（它是调用方的 bug，
    不是用户的进度真的倒退了），所以这里断言的是"单调"而不是"最后一次的值"。
    """
    log, _ = _log(tmp_path)
    with log.stage("剪裁") as r:
        r.progress(80)
        r.progress(50)
        r.progress(90)
    pcts = [e["fields"]["pct"] for e in log.events() if e["kind"] == PROGRESS]
    assert pcts == sorted(pcts), f"进度不单调：{pcts}"
    assert 50.0 not in pcts and 90.0 in pcts


def test_progress_lines_are_throttled(tmp_path):
    """进度只跨过 10 的倍数才打一行 —— 否则会刷屏。"""
    log, stream = _log(tmp_path)
    with log.stage("剪裁") as r:
        for p in range(1, 101):
            r.progress(p)
    lines = [ln for ln in stream.getvalue().splitlines() if ln.strip().startswith("剪裁 ")]
    assert len(lines) <= 12, f"100 次上报打了 {len(lines)} 行，太密"
