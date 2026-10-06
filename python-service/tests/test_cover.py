"""M6-1 封面成图测试：场景声明 / 排版禁则 / 降级矩阵 / 真实 gimpish 渲染。

不依赖 gimpish 的用例占多数；真实渲染用例在 gimpish 不可用时自动跳过
（与 `test_ingest.py` 对 ffmpeg 的处理一致）。
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import cover
from app.pipeline.cover import CoverInvalid, CoverToolMissing

client = TestClient(app)

requires_gimpish = pytest.mark.skipif(not cover.has_gimpish(), reason="gimpish 不可用")


def _png_size(path: Path) -> tuple[int, int]:
    """从 PNG 头读尺寸（无需 Pillow）。"""
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", "不是合法 PNG"
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


# ---------------------------------------------------------------- 场景声明

def test_scene_has_version_and_canvas_per_platform():
    for platform, (w, h) in cover.PLATFORM_CANVAS.items():
        scene = cover.build_scene("标题", "副标题", platform)
        assert scene["version"] == 1
        assert scene["canvas"]["width"] == w
        assert scene["canvas"]["height"] == h
        assert scene["layers"], "至少要有背景层"


def test_scene_layer_order_background_first():
    scene = cover.build_scene("标题", "", "xhs")
    assert scene["layers"][0]["type"] == "gradient"
    assert scene["layers"][-1]["type"] == "shape"   # 强调条在最上层


def test_scene_text_layers_carry_title_lines():
    scene = cover.build_scene("同源万面", "one material", "xhs")
    texts = [l["text"]["content"] for l in scene["layers"] if l["type"] == "text"]
    assert "同源万面" in texts
    assert "one material" in texts


def test_scene_is_json_serializable_and_centered():
    scene = cover.build_scene("居中标题", "", "douyin")
    texts = [l for l in scene["layers"] if l["type"] == "text"]
    assert all(t["text"]["align"] == "center" for t in texts)
    assert all(t["text"]["x"] == scene["canvas"]["width"] // 2 for t in texts)


def test_empty_title_rejected():
    with pytest.raises(CoverInvalid):
        cover.build_scene("   ")


def test_unknown_platform_rejected():
    with pytest.raises(CoverInvalid):
        cover.build_scene("标题", platform="weibo")


# ---------------------------------------------------------------- 排版

def test_fix_punctuation_moves_forbidden_chars_off_boundaries():
    fixed = cover._fix_punctuation(["一二三四五六，", "七八九十"])
    assert fixed == ["一二三四五", "六，七八九十"], fixed


def test_wrap_never_starts_or_ends_line_with_forbidden_punctuation():
    # 字号 100 / 宽度 700 = 每行恰好 7 个全角字，第 7 字是「，」→ 裸折行必然行尾带标点。
    # 这个构造是**故意**的：早先用任意长句测，标点碰巧不落在边界，测试形同虚设
    # （变异测试注入"去掉 _fix_punctuation"后仍然全绿，才发现的）。
    text = "一二三四五六，七八九十：甲乙丙丁、戊己庚辛"
    lines = cover._wrap(text, 100, 700, 99)
    assert len(lines) > 1, "构造应产生多行"
    for line in lines:
        assert line[0] not in cover._NO_LINE_START, f"行首标点：{line}"
        assert line[-1] not in cover._NO_LINE_END, f"行尾标点：{line}"


def test_wrap_ellipsis_is_allowed_at_line_end():
    lines = cover._wrap("一二三四五六七八九十" * 6, 100, 700, 2)
    assert lines[-1].endswith("…")


def test_wrap_truncates_with_ellipsis_when_too_long():
    lines = cover._wrap("一二三四五六七八九十" * 6, 120, 900, 2)
    assert len(lines) == 2
    assert lines[-1].endswith("…")


def test_long_title_shrinks_font_size():
    _, big = cover._fit_title("短标题", 1080, 900)
    _, small = cover._fit_title("这是一个相当长的标题" * 3, 1080, 900)
    assert small < big


# ---------------------------------------------------------------- 降级矩阵

def test_missing_tool_degrades_to_needs_manual(monkeypatch):
    def _raise():
        raise CoverToolMissing(cover.HINT_GIMPISH_MISSING)

    monkeypatch.setattr(cover, "_gimpish_bin", _raise)
    result = cover.compose_cover(title="标题", platform="xhs")
    assert result["status"] == "needs_manual"
    assert "npm install -g gimpish" in result["hint"]
    assert result["path"] == ""


def test_api_missing_tool_returns_200_needs_manual(monkeypatch):
    def _raise():
        raise CoverToolMissing(cover.HINT_GIMPISH_MISSING)

    monkeypatch.setattr(cover, "_gimpish_bin", _raise)
    resp = client.post("/compose/cover", json={"title": "标题", "platform": "xhs"})
    assert resp.status_code == 200, "工具缺失是降级，不是错误"
    assert resp.json()["status"] == "needs_manual"


def test_api_empty_title_is_400():
    resp = client.post("/compose/cover", json={"title": "", "platform": "xhs"})
    assert resp.status_code == 422, "空标题由 pydantic 拦下"


def test_api_unknown_platform_is_422():
    resp = client.post("/compose/cover", json={"title": "标题", "platform": "weibo"})
    assert resp.status_code == 422


# ---------------------------------------------------------------- 真实渲染

@requires_gimpish
def test_real_render_produces_png_with_canvas_size(tmp_path):
    result = cover.compose_cover(title="同源万面", subtitle="one material, many faces",
                                 platform="xhs", out_dir=str(tmp_path), file_stem="real")
    assert result["status"] == "ok", result["hint"]
    png = Path(result["path"])
    assert png.is_file() and png.stat().st_size > 1000
    assert _png_size(png) == (1080, 1440)
    # 场景文件留在同目录，用户可用 `gimpish serve` 打开继续改
    assert Path(result["scene_path"]).is_file()


@requires_gimpish
def test_real_render_respects_platform_canvas(tmp_path):
    result = cover.compose_cover(title="抖音竖版", platform="douyin",
                                 out_dir=str(tmp_path), file_stem="dy")
    assert result["status"] == "ok", result["hint"]
    assert _png_size(Path(result["path"])) == (1080, 1920)


@requires_gimpish
def test_real_render_writes_into_configured_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYFACE_COVER_DIR", str(tmp_path / "covers"))
    result = cover.compose_cover(title="默认目录", platform="bilibili", file_stem="d")
    assert result["status"] == "ok", result["hint"]
    assert Path(result["path"]).parent.parent == (tmp_path / "covers").resolve()
