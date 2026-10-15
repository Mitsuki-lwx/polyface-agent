"""T6 图文成片：计划校验 + 整条管线（`docs/checklist_b1.md` §3 §4 §7）。

端到端那两条会真跑 ffmpeg（小尺寸，几秒）。TTS 缺失时**应当照样通过** ——
这正是 §3 要验的降级行为。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import wave
from pathlib import Path

import pytest

from app.pipeline import roughcut as rc, tts

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str, filename: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


MV = _load("polyface_make_video", "make_video.py")

pytestmark = pytest.mark.skipif(not rc.has_ffmpeg(), reason="没有 ffmpeg")


def _img(path: Path, src: str = "testsrc2=s=640x1136") -> Path:
    rc._run_ffmpeg([rc._ffmpeg_bin("ffmpeg"), "-y", "-v", "error", "-f", "lavfi",
                    "-i", src, "-frames:v", "1", str(path)])
    return path


def _plan(tmp_path: Path, n: int = 2, bgm: bool = False) -> Path:
    scenes = []
    for i in range(n):
        scenes.append({"seq": i + 1, "script": f"第{i+1}句台词。",
                       "subtitle": f"第{i+1}条字幕",
                       "image": str(_img(tmp_path / f"i{i}.png",
                                         "testsrc2=s=640x1136" if i % 2 == 0
                                         else "smptebars=s=640x1136"))})
    plan = {"scenes": scenes}
    if bgm:
        plan["bgm"] = str(_click_track(tmp_path / "bgm.wav"))
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return p


def _click_track(path: Path, interval: float = 1.0, n: int = 30) -> Path:
    """60 BPM 的点击轨（每 1s 一下）—— 节拍稀疏一点，对齐率才有区分度。"""
    import math
    import struct
    sr = 22050
    data = bytearray()
    for _ in range(n):
        data += b"\x00\x00" * int(sr * (interval - 0.08))
        for k in range(int(sr * 0.08)):
            data += struct.pack("<h", int(12000 * math.sin(2 * math.pi * 1000 * k / sr)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(bytes(data))
    return path


# ---------------- 计划校验：必须指出是第几个分镜 ----------------

def test_rejects_missing_images_and_names_the_indices(tmp_path):
    plan = _plan(tmp_path, 3)
    data = json.loads(plan.read_text(encoding="utf-8"))
    data["scenes"][1]["image"] = str(tmp_path / "没有.png")     # 第 2 个缺图
    data["scenes"][2]["image"] = ""                             # 第 3 个也没图
    plan.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(MV.PlanError) as e:
        MV.load_plan(plan)
    msg = str(e.value)
    assert "[2, 3]" in msg, f"必须一次说清缺哪几个：{msg}"


def test_rejects_empty_scenes(tmp_path):
    p = tmp_path / "p.json"
    p.write_text('{"scenes": []}', encoding="utf-8")
    with pytest.raises(MV.PlanError, match="至少要有一个分镜"):
        MV.load_plan(p)


def test_rejects_scene_without_any_text(tmp_path):
    plan = _plan(tmp_path, 1)
    data = json.loads(plan.read_text(encoding="utf-8"))
    data["scenes"][0]["script"] = ""
    data["scenes"][0]["subtitle"] = ""
    plan.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(MV.PlanError, match="第 1 个分镜"):
        MV.load_plan(plan)


def test_rejects_bad_json(tmp_path):
    p = tmp_path / "p.json"
    p.write_text("{不是 json", encoding="utf-8")
    with pytest.raises(MV.PlanError, match="JSON"):
        MV.load_plan(p)


# ---------------- duration_hint 兜底 ----------------

def test_hint_seconds_parses_common_forms():
    assert MV._hint_seconds({"duration_hint": "3s"}, 9) == 3.0
    assert MV._hint_seconds({"duration_hint": "3.5秒"}, 9) == 3.5
    assert MV._hint_seconds({"duration_hint": "约 4 秒"}, 9) == 4.0
    assert MV._hint_seconds({"duration_hint": "很久"}, 9) == 9.0      # 解析不了 → 兜底
    assert MV._hint_seconds({}, 9) == 9.0


# ---------------- 配音拼接：按分镜时长补足 ----------------

def test_concat_wavs_pads_to_scene_durations(tmp_path):
    """`beat_first` 会把某段拉长 —— 不补的话音轨比画面短，后面全错位。

    **采样率故意用 22050**（TTS 的真实输出率），不是 16000 ——
    原先写 16000 时测试全绿，而线上是 1.38 倍速 + 尾部被截。见下一条。
    """
    def wav(p: Path, sec: float):
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            w.writeframes(b"\x00\x00" * int(22050 * sec))
        return p

    a = wav(tmp_path / "a.wav", 1.0)
    b = wav(tmp_path / "b.wav", 1.0)
    out = tmp_path / "o.wav"
    MV._concat_wavs([a, b], [2.0, 3.0], out, 5.0)      # 分镜要 2s / 3s，配音只有 1s / 1s
    assert tts.wav_seconds(out) == pytest.approx(5.0, abs=0.05)


# ---------------- 整条管线 ----------------

@pytest.mark.parametrize("strategy", ["voice_first", "beat_first"])
def test_full_pipeline_produces_film_and_record(tmp_path, strategy):
    plan = _plan(tmp_path, 3, bgm=True)
    out = tmp_path / "film.mp4"
    code = MV.main([str(plan), "-o", str(out), "--workdir", str(tmp_path / "w"),
                    "--width", "180", "--height", "320", "--fps", "12",
                    "--strategy", strategy, "--level", "quiet"])
    assert code == 0
    assert out.is_file() and rc.probe(out)["duration"] > 1.0

    rec = json.loads(out.with_suffix(".plan.json").read_text(encoding="utf-8"))
    assert rec["strategy"] == strategy
    assert len(rec["scenes"]) == 3
    # 记录里的时长必须与成片对得上（checklist §7）
    assert rec["film_sec"] == pytest.approx(rc.probe(out)["duration"], abs=0.35)
    assert rec["total_sec"] == pytest.approx(sum(s["seconds"] for s in rec["scenes"]), abs=0.05)


def test_pipeline_survives_without_tts(tmp_path, monkeypatch):
    """**一个配音插件都没有，也要出片**（无声 + 字幕）—— checklist §3 的核心。

    插件化之后，"没有 TTS"的等价说法是"插件目录里什么都没有"。
    """
    empty = tmp_path / "no-plugins"; empty.mkdir()
    monkeypatch.setenv("POLYFACE_TTS_PLUGINS", str(empty))
    monkeypatch.setenv("POLYFACE_TTS", "none")
    plan = _plan(tmp_path, 2)
    out = tmp_path / "film.mp4"
    code = MV.main([str(plan), "-o", str(out), "--workdir", str(tmp_path / "w"),
                    "--width", "180", "--height", "320", "--fps", "12", "--level", "quiet"])
    assert code == 0, "缺 TTS 不该让成片失败"
    assert out.is_file()
    rec = json.loads(out.with_suffix(".plan.json").read_text(encoding="utf-8"))
    assert rec["tts"]["plugins"] == [], "没有插件时清单应当是空的"
    assert all(not s["has_voice"] for s in rec["scenes"])
    assert all(s["seconds"] > 0 for s in rec["scenes"]), "没有配音时用 duration_hint 兜底"


def test_pipeline_survives_without_librosa(tmp_path, monkeypatch):
    """没有 librosa → 跳过卡点并**说明未做**，成片照出。"""
    monkeypatch.setenv("POLYFACE_BEATS", "none")
    plan = _plan(tmp_path, 2, bgm=True)
    out = tmp_path / "film.mp4"
    code = MV.main([str(plan), "-o", str(out), "--workdir", str(tmp_path / "w"),
                    "--width", "180", "--height", "320", "--fps", "12", "--level", "quiet"])
    assert code == 0
    rec = json.loads(out.with_suffix(".plan.json").read_text(encoding="utf-8"))
    assert rec["beats"]["available"] is False
    assert rec["beats"]["hint"]
    assert rec["aligned_ratio"] == 0.0


@pytest.mark.skipif(not tts.is_available(), reason="本机没有可用 TTS")
def test_scene_duration_comes_from_voice_not_from_hint(tmp_path):
    """**分镜时长必须等于配音的实际时长**，不是 `duration_hint`（spec 关键决定一）。

    这条是补的：原先只断言"时长 > 0"，于是把"时长改由 duration_hint 决定"这个变异
    **注入后测试全绿** —— 变异测试就是这么发现测试没咬住重点的。
    """
    import wave
    plan = _plan(tmp_path, 2)
    data = json.loads(plan.read_text(encoding="utf-8"))
    # 故意把提示值写成一个**明显不对**的数：真按它算，断言必红
    for sc in data["scenes"]:
        sc["duration_hint"] = "99s"
    plan.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "film.mp4"
    assert MV.main([str(plan), "-o", str(out), "--workdir", str(tmp_path / "w"),
                    "--width", "180", "--height", "320", "--fps", "12",
                    "--level", "quiet"]) == 0

    rec = json.loads(out.with_suffix(".plan.json").read_text(encoding="utf-8"))
    for i, sc in enumerate(rec["scenes"], 1):
        wav = tmp_path / "w" / f"voice-{i:03d}.wav"
        with wave.open(str(wav), "rb") as w:
            real = round(w.getnframes() / float(w.getframerate()), 3)
        assert sc["seconds"] == pytest.approx(real, abs=0.01), \
            f"第 {i} 个分镜的时长应当来自配音（{real}s），不是提示值（{sc['seconds']}s）"
        assert sc["seconds"] < 50, "提示值 99s 显然没有被采用"


def test_second_run_resumes_and_skips_finished_scenes(tmp_path, monkeypatch):
    """**分镜级落盘**：第二次跑同一个工作目录，已完成的分镜不该重做。

    这条是补的 —— 原先 `make_video.py` 引了 `ck` 却**从没用过 DoneBook**，
    "分镜级落盘"根本没实现，而当时没有任何测试会发现。端到端要真杀进程才能发现，
    这里用"数调用次数"把它钉死。
    """
    plan = _plan(tmp_path, 3)
    work = tmp_path / "w"
    out = tmp_path / "film.mp4"
    argv = [str(plan), "-o", str(out), "--workdir", str(work),
            "--width", "180", "--height", "320", "--fps", "12", "--level", "quiet"]
    assert MV.main(argv) == 0

    calls = {"n": 0}
    real = MV.V.ken_burns

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(MV.V, "ken_burns", counting)
    assert MV.main(argv) == 0
    assert calls["n"] == 0, f"已完成的画面段不该重做，实际调了 {calls['n']} 次"

    # 删掉一个分镜的段 → 只有它应当被重做
    (work / "scene-002.mp4").unlink()
    calls["n"] = 0
    assert MV.main(argv) == 0
    assert calls["n"] == 1, f"只该补第 2 个分镜，实际 {calls['n']} 次"


def test_concat_keeps_the_real_sample_rate(tmp_path):
    """**必须用片段自己的采样率**。

    写死成 16000 过的后果：TTS 输出 22050Hz → 配音 1.38 倍速、还高了 1.38 个八度，
    标称时长 103s（真实 75s）→ 尾部 28 秒被 `-t` 截掉。
    **机器量的是"有声"，人是听出来的** —— 这条是用户听出来之后补的。
    """
    def wav(p: Path, sec: float, rate: int):
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(b"\x00\x00" * int(rate * sec))
        return p

    a = wav(tmp_path / "a.wav", 1.0, 22050)
    b = wav(tmp_path / "b.wav", 1.0, 22050)
    out = tmp_path / "o.wav"
    MV._concat_wavs([a, b], [1.0, 1.0], out, 2.0)

    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == 22050, "不能把采样率改成别的 —— 那会变速变调"
        assert w.getnframes() / w.getframerate() == pytest.approx(2.0, abs=0.02)


def test_concat_rejects_mixed_rates(tmp_path):
    """采样率不一致时**不静默拼** —— 拼出来的必然有一个是变调的。"""
    def wav(p: Path, sec: float, rate: int):
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(b"\x00\x00" * int(rate * sec))
        return p

    a = wav(tmp_path / "a.wav", 0.5, 16000)
    b = wav(tmp_path / "b.wav", 0.5, 22050)
    with pytest.raises(Exception, match="采样率"):
        MV._concat_wavs([a, b], [0.5, 0.5], tmp_path / "o.wav", 1.0)


# ---------------- 音色选择 / 导入自己的配音 ----------------

def test_list_voices_shape():
    vs = tts.list_voices()
    for v in vs:
        assert set(v) >= {"id", "name", "language", "gender", "backend"}
        assert v["id"]
    if vs and any((x.get("language") or "").lower().startswith("zh") for x in vs):
        assert tts.default_voice() in [x["id"] for x in vs], "默认应当挑一个真实存在的音色"


def test_plan_rejects_missing_voice_file_and_names_index(tmp_path):
    """用户自己的配音要真实存在 —— 缺了要说清是第几个分镜。"""
    plan = _plan(tmp_path, 3)
    data = json.loads(plan.read_text(encoding="utf-8"))
    data["scenes"][2]["voice_file"] = str(tmp_path / "没有.wav")
    plan.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(MV.PlanError) as e:
        MV.load_plan(plan)
    assert "[3]" in str(e.value)


def test_own_voice_file_is_used_and_not_tts(tmp_path):
    """给了 `voice_file` 就用**用户的配音**，不再调 TTS。

    用一段已知时长的 wav 当"用户素材"，成片里那一镜的时长必须等于它 ——
    如果走了 TTS，时长会是那句台词的合成长度，对不上。
    """
    own = tmp_path / "my-voice.wav"
    with wave.open(str(own), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(b"\x00\x00" * int(22050 * 2.5))

    plan = _plan(tmp_path, 2)
    data = json.loads(plan.read_text(encoding="utf-8"))
    data["scenes"][1]["voice_file"] = str(own)
    plan.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "film.mp4"
    assert MV.main([str(plan), "-o", str(out), "--workdir", str(tmp_path / "w"),
                    "--width", "180", "--height", "320", "--fps", "12",
                    "--level", "quiet"]) == 0
    rec = json.loads(out.with_suffix(".plan.json").read_text(encoding="utf-8"))
    assert rec["scenes"][1]["seconds"] == pytest.approx(2.5, abs=0.02), \
        "第 2 个分镜的时长应当等于用户给的配音长度"


# ---------------- T5：插件是**主路径**，且能逐分镜混用 ----------------

def test_record_names_the_plugin_per_scene(tmp_path):
    """记录里必须能查到**每个分镜**用了哪个插件/音色。

    这是 spec 关键决定三的兑现：装插件等于执行别人的代码 ——
    那就得能回答"这一镜到底是谁合成的"。
    """
    plan = _plan(tmp_path, 2)
    out = tmp_path / "film.mp4"
    assert MV.main([str(plan), "-o", str(out), "--workdir", str(tmp_path / "w"),
                    "--width", "180", "--height", "320", "--fps", "12",
                    "--level", "quiet"]) == 0
    rec = json.loads(out.with_suffix(".plan.json").read_text(encoding="utf-8"))
    assert rec["tts"]["plugins"], "记录里要写上这次有哪些插件可用"
    for sc in rec["scenes"]:
        if sc["has_voice"]:
            assert sc["plugin"], f"第 {sc['seq']} 个分镜有配音却没记插件"


def test_scene_can_pick_a_different_source(tmp_path):
    """**逐分镜混用**：一个用 TTS、一个用自己的音频 —— 两个都生效且各自记上。"""
    own = tmp_path / "mine.wav"
    with wave.open(str(own), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(b"\x00\x00" * int(22050 * 2.0))

    plan = _plan(tmp_path, 2)
    data = json.loads(plan.read_text(encoding="utf-8"))
    data["scenes"][1]["voice_file"] = str(own)
    plan.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "film.mp4"
    assert MV.main([str(plan), "-o", str(out), "--workdir", str(tmp_path / "w"),
                    "--width", "180", "--height", "320", "--fps", "12",
                    "--level", "quiet"]) == 0
    rec = json.loads(out.with_suffix(".plan.json").read_text(encoding="utf-8"))
    assert rec["scenes"][1]["plugin"] == "own-audio", "给了 voice_file 就该走 own-audio 插件"
    assert rec["scenes"][1]["seconds"] == pytest.approx(2.0, abs=0.05)


def test_broken_plugin_does_not_kill_the_film(tmp_path, monkeypatch, capsys):
    """插件坏了 → **只影响那一镜**，其余照跑（spec 能力清单第 5 条）。"""
    import app.pipeline.tts_plugin as tp

    real = tp.synthesize

    def boom(plugin_id, *a, **kw):
        if plugin_id == "windows-sapi":
            raise tp.PluginError("插件 windows-sapi 故意炸了")
        return real(plugin_id, *a, **kw)

    monkeypatch.setattr(tp, "synthesize", boom)
    plan = _plan(tmp_path, 2)
    out = tmp_path / "film.mp4"
    code = MV.main([str(plan), "-o", str(out), "--workdir", str(tmp_path / "w"),
                    "--width", "180", "--height", "320", "--fps", "12", "--level", "quiet"])
    assert code == 0, "插件坏了不该让整片失败"
    assert out.is_file()
    err = capsys.readouterr().out
    assert "第 1 个分镜配音失败" in err and "windows-sapi" in err, \
        "要**说清是哪个分镜、哪个插件**"


def test_subtitle_slash_becomes_newline():
    """剪辑单里的字幕用 ` / ` 表示**分行**，不是字面斜杠。

    实测踩过：不转换的话成片字幕显示成「不满足就痛苦 / 满足了就无聊 / …」——
    一眼能看出不对，但**只有真的看了片子才发现**
    （接口 200、时长对、记录对、测试全绿，一串斜杠就那么挂在画面上）。
    """
    assert MV._subtitle_lines("不满足就痛苦 / 满足了就无聊 / 人生像钟摆") == \
        "不满足就痛苦\n满足了就无聊\n人生像钟摆"
    assert MV._subtitle_lines("普通一句，没有分隔符") == "普通一句，没有分隔符"
    assert MV._subtitle_lines(" 两边有空格 / 要去掉 ") == "两边有空格\n要去掉"


def test_subtitle_slash_without_spaces_too():
    """**没空格的 `/` 也要分行** —— 模型有时写成 `这个人是谁？/ 叔本华`。

    但 `2023/2024` 不能被误伤 —— 所以判据是"两侧都是非 ASCII"。
    """
    assert MV._subtitle_lines("这个人是谁？/ 叔本华 / 悲观主义哲学家") == \
        "这个人是谁？\n叔本华\n悲观主义哲学家"
    assert MV._subtitle_lines("2023/2024 的对比") == "2023/2024 的对比"
    assert MV._subtitle_lines("A/B 测试") == "A/B 测试"


def test_title_of_takes_the_first_line():
    assert MV._title_of({"subtitle": "你不是不努力 / 是掉进了叔本华的钟摆"}) == "你不是不努力"
    assert MV._title_of({"subtitle": "", "script": "只有台词"}) == "只有台词"


def test_em_width_counts_cjk_wider():
    assert MV.V._em_width("四个汉字") == pytest.approx(4.0)
    assert MV.V._em_width("abcd") == pytest.approx(2.2)
    assert MV.V._em_width("汉字\n更长的一整行文字") == pytest.approx(8.0)   # 取最长那行


def _motion(a, b) -> float:
    """两帧的平均绝对差（16×16 灰度）。**别用 md5** —— 编码噪声会让它误判（实测过）。"""
    import subprocess

    def g(p):
        return subprocess.run(["ffmpeg", "-v", "error", "-i", str(p),
                               "-vf", "scale=16:16,format=gray", "-f", "rawvideo", "-"],
                              capture_output=True).stdout
    x, y = g(a), g(b)
    return sum(abs(i - j) for i, j in zip(x, y)) / max(1, len(x))


@pytest.mark.skipif(not rc.has_ffmpeg(), reason="没有 ffmpeg")
def test_title_card_fits_and_has_motion(tmp_path):
    """深色标题卡：时长对、**文字不出画**、且画面在动（模板也要有运动）。"""
    out = tmp_path / "card.mp4"
    MV.V.title_card(tmp_path, out, 2.0, "你不是不努力\n是掉进了叔本华的钟摆",
                    size=(360, 640), fps=15)
    assert rc.probe(out, require_audio=False)["duration"] == pytest.approx(2.0, abs=0.2)
    # 字号必须按**宽度**收过 —— 8 个汉字 × 54px 会撑出 360px 的画宽（实测被切掉）
    fs_h = int(640 * 0.085)
    fs_w = int((360 * 0.86) / MV.V._em_width("你不是不努力\n是掉进了叔本华的钟摆"))
    assert fs_w < fs_h, "这个用例就该触发宽度收窄，否则测不到自适应"
    MV.V.frame_at(out, tmp_path / "f0.png", 0.0)
    MV.V.frame_at(out, tmp_path / "f1.png", 1.8)
    # 卡片动得比照片含蓄（zoom 1.03 vs 1.08 —— 文字放大太多会晕），
    # 所以阈值比照片那条低。静止基线实测 0.03，这里 0.28 —— 分离度仍然清楚。
    assert _motion(tmp_path / "f0.png", tmp_path / "f1.png") > 0.1, \
        "模板也要有运动（静止就退化成 docs/49 §5.1 打击的形态）"


def test_subtitle_margin_is_in_script_units_not_pixels():
    """**libass 的 `MarginV` 是脚本单位，不是像素** —— 这是实测出来的换算。

    1280 高的画面：MV=20 → 距底 96px、MV=40 → 185px、MV=115 → 519px，
    即 `像素 ≈ MV × 画面高/288`（PlayResY 默认 288）。

    所以"距底 N%"= `MV = N% × 288`，**与分辨率无关**（两边约掉了）。
    不知道这件事的后果：把 `margin_v` 当像素设成 115，字幕会跑到画面中间去（实测踩过）。
    """
    assert MV.PLAYRES_Y == 288
    margin = round(MV.PLAYRES_Y * MV.SUBTITLE_BOTTOM_RATIO)
    assert margin == round(288 * 0.08) == 23, "距底 8% 换算成 MarginV=23"
    # 反推：23 脚本单位在 1280 高的画面里约等于 102px —— 与实测的 96~185 区间一致
    assert 96 <= margin * 1280 / MV.PLAYRES_Y <= 185


def test_template_constants_come_from_the_reference():
    """模板的默认值**取自参考片实测**（`素材/dl.mp4`），不是拍脑袋定的。

    手搓的那版底色是深蓝 `0x0E0E14`、标题只有 8.5% —— 跟参考片不是一回事，
    **是量了才发现**。这条测试把"量的结果"钉住，免得以后被随手改回去。

    实测（参考片 1280×720）：四角底色 (9,9,9)、大标题 92px = 12.8% 画面高、
    底部字幕底边在距底 5%~16% 之间浮动。
    """
    assert MV.PLAYRES_Y == 288
    assert MV.SUBTITLE_BOTTOM_RATIO == pytest.approx(0.08), "参考片中位"
    import inspect
    sig = inspect.signature(MV.V.title_card)
    assert sig.parameters["title_ratio"].default == pytest.approx(0.128), "参考片实测 12.8%"
    assert sig.parameters["bg"].default == "0x090909", "参考片四角是 (9,9,9)，近纯黑"


def test_list_plugins_shows_every_option(tmp_path, capsys):
    """`--list-plugins` 要**把所有方案摆出来**（含 network 标记与 note）。"""
    plan = _plan(tmp_path, 1)
    assert MV.main([str(plan), "-o", str(tmp_path / "o.mp4"), "--list-plugins"]) == 0
    out = capsys.readouterr().out
    assert "windows-sapi" in out and "own-audio" in out
    assert "本地" in out or "要联网" in out, "要标出联网与否（数据边界）"
