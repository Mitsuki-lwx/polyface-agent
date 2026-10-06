"""封面成图（M6-1）：由 agent 驱动**后台图像编辑器** gimpish 渲染平台封面。

架构（见 `docs/08` ADR-019）：polyface 不再承诺"无需 Node"。图像编辑交给
[gimpish](https://github.com/jvanderberg/gimpish) 在后台运行，本模块是**适配器**：

1. 由我们**声明式**产出 gimpish 的版本化契约 `scene.json`（version=1，zod 校验）；
2. 只调用**一次** `gimpish export` 把场景光栅化为 PNG（sharp 渲染，无 GUI、无浏览器）。

不逐条调用 CLI 动词（省 4 次 node 冷启动），不引入任何 Python 新依赖。

降级策略（与 `asr.py` / `ingest.py` 一致）：gimpish 不可用 → `needs_manual` +
安装指引，**不抛 5xx**。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from ..config import get_settings

logger = logging.getLogger(__name__)

HINT_GIMPISH_MISSING = (
    "未检测到图像编辑器 gimpish。请安装 Node.js ≥ 20.19 后执行 "
    "`npm install -g gimpish`，或用环境变量 POLYFACE_GIMPISH 指向其可执行文件/入口 js。"
)

# 平台封面画布（ADR-018 的成片目标平台）
PLATFORM_CANVAS: dict[str, tuple[int, int]] = {
    "xhs": (1080, 1440),       # 小红书 3:4
    "douyin": (1080, 1920),    # 抖音 9:16
    "bilibili": (1920, 1080),  # B站 16:9
}
DEFAULT_PLATFORM = "xhs"

# 主题（深色底 + 渐变 + 强调色）。刻意不做"单图 + 大量文字"的轮播形态
# （抖音官方将其列为低质内容，见 docs/33），封面只承载标题/副标题两行信息。
THEMES: dict[str, dict[str, str]] = {
    "violet": {"grad_from": "#7c3aedff", "grad_to": "#0b0b16ff",
               "accent": "#f59e0bff", "title": "#ffffffff", "subtitle": "#c7c7d1ff"},
    "ink": {"grad_from": "#1f2937ff", "grad_to": "#05070aff",
            "accent": "#38bdf8ff", "title": "#f8fafcff", "subtitle": "#94a3b8ff"},
    "sunset": {"grad_from": "#f97316ff", "grad_to": "#4c0519ff",
               "accent": "#fde68aff", "title": "#ffffffff", "subtitle": "#fed7aaff"},
}
DEFAULT_THEME = "violet"

_EXPORT_TIMEOUT_SEC = 120
_SAFE_STEM = re.compile(r"[^0-9A-Za-z_.-]+")


class CoverToolMissing(RuntimeError):
    """gimpish 不可用。"""


class CoverInvalid(RuntimeError):
    """入参非法（标题为空、平台不支持等）。"""


# ------------------------------------------------------------------ 工具定位

def _gimpish_bin() -> str:
    """定位 gimpish：配置项 > 环境变量 POLYFACE_GIMPISH > PATH。"""
    setting = (getattr(get_settings(), "gimpish_path", "") or os.getenv("POLYFACE_GIMPISH", "")).strip()
    if setting:
        p = Path(setting)
        candidates = [p] if p.is_file() else []
        candidates += [p / name for name in ("gimpish.cmd", "gimpish", "gimpish.js")]
        for cand in candidates:
            if cand.is_file():
                return str(cand)
    found = shutil.which("gimpish")
    if not found:
        raise CoverToolMissing(HINT_GIMPISH_MISSING)
    return found


def has_gimpish() -> bool:
    try:
        _gimpish_bin()
        return True
    except CoverToolMissing:
        return False


def _invocation(binary: str) -> list[str]:
    """`.js` 入口需要显式交给 node；其余（.cmd/.exe/shell wrapper）直接执行。"""
    if binary.lower().endswith(".js"):
        node = shutil.which("node") or "node"
        return [node, binary]
    return [binary]


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


# ------------------------------------------------------------------ 排版（确定性）

def _char_width(ch: str, size: int) -> float:
    """粗略字宽：CJK/全角 ≈ 1em，ASCII ≈ 0.55em，空格/标点更窄。"""
    if ch.isspace():
        return size * 0.28
    if ord(ch) > 0x2E7F:  # CJK 及全角区
        return float(size)
    if ch.isupper():
        return size * 0.62
    return size * 0.53


def _text_width(text: str, size: int) -> float:
    return sum(_char_width(c, size) for c in text)


# 中文排版禁则：这些标点不能出现在行尾 / 行首（"…"是截断标记，允许落在行尾）
_NO_LINE_END = "，。、：；！？）」』】》—·"
_NO_LINE_START = "，。、：；！？）』】》…—·"


def _fix_punctuation(lines: list[str]) -> list[str]:
    """把落在行尾/行首的禁则标点挪到相邻行（中文折行的基本要求）。"""
    for i in range(len(lines) - 1):
        while lines[i] and lines[i][-1] in _NO_LINE_END and len(lines[i]) > 1:
            moved = lines[i][-1]
            lines[i] = lines[i][:-1]
            lines[i + 1] = moved + lines[i + 1]
        while lines[i + 1] and lines[i + 1][0] in _NO_LINE_START and len(lines[i]) > 1:
            moved = lines[i][-1]
            lines[i] = lines[i][:-1]
            lines[i + 1] = moved + lines[i + 1]
    return lines


def _wrap(text: str, size: int, max_width: float, max_lines: int) -> list[str]:
    """按测量宽度折行。ASCII 优先在空格断行，CJK 逐字断行。"""
    text = text.strip()
    if not text:
        return []
    lines: list[str] = []
    cur = ""
    for ch in text:
        if _text_width(cur + ch, size) > max_width and cur:
            cut = cur.rstrip()
            if " " in cut and ord(ch) <= 0x2E7F:
                head, _, tail = cut.rpartition(" ")
                if head:
                    lines.append(head)
                    cur = tail + ch
                    continue
            lines.append(cut)
            cur = ch
        else:
            cur += ch
    if cur.strip():
        lines.append(cur.rstrip())
    lines = _fix_punctuation(lines)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and _text_width(last + "…", size) > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def _fit_title(title: str, width: int, max_width: float) -> tuple[list[str], int]:
    """从基准字号逐级缩小，直到标题在 3 行内放得下。

    注意：测量时必须用**不截断**的折行（max_lines 足够大），否则截断后的行
    永远"放得下"，字号就永远不会缩小。
    """
    size = max(48, round(width * 0.111))          # 1080 → 120
    while size > 44:
        lines = _wrap(title, size, max_width, 99)
        if len(lines) <= 3 and all(_text_width(ln, size) <= max_width for ln in lines):
            return lines, size
        size -= 6
    return _wrap(title, 44, max_width, 3), 44


def _text_layer(layer_id: str, content: str, x: int, y: int, size: int,
                weight: str, color: str) -> dict:
    return {
        "id": layer_id, "name": layer_id, "opacity": 1, "blend": "normal", "visible": True,
        "type": "text",
        "text": {
            "content": content, "x": x, "y": y, "font": "sans-serif",
            "size": size, "weight": weight, "style": "normal", "align": "center",
            "fill": color, "stroke": None, "stroke_width": 0,
            "line_height": 1.15, "letter_spacing": 0, "rotation": 0,
        },
    }


def build_scene(title: str, subtitle: str = "", platform: str = DEFAULT_PLATFORM,
                theme: str = DEFAULT_THEME) -> dict:
    """声明式产出 gimpish scene.json（version=1）。纯函数，可单测。"""
    title = (title or "").strip()
    if not title:
        raise CoverInvalid("标题不能为空")
    if platform not in PLATFORM_CANVAS:
        raise CoverInvalid(f"不支持的平台：{platform}（可选 {'/'.join(PLATFORM_CANVAS)}）")
    palette = THEMES.get(theme) or THEMES[DEFAULT_THEME]

    width, height = PLATFORM_CANVAS[platform]
    margin = round(width * 0.083)
    max_width = width - 2 * margin

    title_lines, title_size = _fit_title(title, width, max_width)
    line_h = round(title_size * 1.18)
    subtitle_size = max(28, round(title_size * 0.36))
    subtitle_lines = _wrap(subtitle, subtitle_size, max_width, 2) if subtitle.strip() else []

    block_h = len(title_lines) * line_h
    if subtitle_lines:
        block_h += round(height * 0.035) + len(subtitle_lines) * round(subtitle_size * 1.3)
    title_top = max(round(height * 0.40) - block_h // 2, margin)

    layers: list[dict] = [
        {
            "id": "bg", "name": "bg", "opacity": 1, "blend": "normal", "visible": True,
            "type": "gradient",
            "gradient": {
                "kind": "linear", "anchor": "top-left",
                "stops": [{"at": 0, "color": palette["grad_from"]},
                          {"at": 1, "color": palette["grad_to"]}],
            },
        }
    ]

    cursor = title_top
    for i, line in enumerate(title_lines):
        layers.append(_text_layer(f"title{i + 1}", line, width // 2, cursor,
                                  title_size, "800", palette["title"]))
        cursor += line_h

    if subtitle_lines:
        cursor += round(height * 0.035)
        for i, line in enumerate(subtitle_lines):
            layers.append(_text_layer(f"sub{i + 1}", line, width // 2, cursor,
                                      subtitle_size, "700", palette["subtitle"]))
            cursor += round(subtitle_size * 1.3)

    bar_w = round(width * 0.16)
    bar_h = max(6, round(width * 0.0074))
    layers.append({
        "id": "accent", "name": "accent", "opacity": 1, "blend": "normal", "visible": True,
        "type": "shape", "shape": "rect",
        "rect": {"x": (width - bar_w) // 2, "y": cursor + round(height * 0.045),
                 "w": bar_w, "h": bar_h},
        "fill": palette["accent"], "stroke": None, "stroke_width": 0,
    })

    return {"version": 1,
            "canvas": {"width": width, "height": height, "background": palette["grad_to"]},
            "layers": layers}


# ------------------------------------------------------------------ 渲染

def _default_out_dir() -> Path:
    explicit = os.getenv("POLYFACE_COVER_DIR", "").strip()
    if explicit:
        return Path(explicit).resolve()
    base = Path(os.getenv("POLYFACE_DATA_DIR", "../data")).resolve()
    return base / "media" / "covers"


def _safe_stem(raw: str) -> str:
    stem = _SAFE_STEM.sub("-", (raw or "").strip()).strip("-._")
    return stem[:40] or "cover"


def compose_cover(*, title: str, subtitle: str = "", platform: str = DEFAULT_PLATFORM,
                  theme: str = DEFAULT_THEME, out_dir: str = "",
                  file_stem: str = "") -> dict:
    """渲染一张封面 PNG。返回统一结构（含降级），**任何工具缺失都不抛 5xx**。"""
    started = time.monotonic()
    if not title.strip():
        raise CoverInvalid("标题不能为空")
    if platform not in PLATFORM_CANVAS:
        raise CoverInvalid(f"不支持的平台：{platform}（可选 {'/'.join(PLATFORM_CANVAS)}）")

    scene = build_scene(title, subtitle, platform, theme)
    width, height = scene["canvas"]["width"], scene["canvas"]["height"]

    try:
        binary = _gimpish_bin()
    except CoverToolMissing:
        return {"status": "needs_manual", "editor": "gimpish", "hint": HINT_GIMPISH_MISSING,
                "width": width, "height": height, "path": "", "scene_path": "",
                "elapsed_ms": int((time.monotonic() - started) * 1000)}

    target_dir = Path(out_dir).resolve() if out_dir.strip() else _default_out_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    stem = _safe_stem(file_stem or f"{platform}-{title}") + "-" + uuid.uuid4().hex[:6]
    doc_dir = target_dir / stem
    doc_dir.mkdir(parents=True, exist_ok=True)
    # gimpish 的文档目录契约：目录内必须是 `scene.json`（+ 可选 assets/）
    scene_path = doc_dir / "scene.json"
    out_png = doc_dir / "cover.png"

    import json
    scene_path.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")

    cmd = _invocation(binary) + ["-C", str(doc_dir), "export", "--out", str(out_png)]
    try:
        cp = _run(cmd, _EXPORT_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return {"status": "needs_manual", "editor": "gimpish",
                "hint": f"gimpish 渲染超时（>{_EXPORT_TIMEOUT_SEC}s）", "width": width,
                "height": height, "path": "", "scene_path": str(scene_path),
                "elapsed_ms": int((time.monotonic() - started) * 1000)}

    elapsed = int((time.monotonic() - started) * 1000)
    if cp.returncode != 0 or not out_png.is_file():
        detail = (cp.stderr or cp.stdout or "").strip()[-400:]
        logger.warning("gimpish 渲染失败 rc=%s: %s", cp.returncode, detail)
        return {"status": "needs_manual", "editor": "gimpish",
                "hint": f"gimpish 渲染失败：{detail or '无输出'}", "width": width,
                "height": height, "path": "", "scene_path": str(scene_path),
                "elapsed_ms": elapsed}

    return {"status": "ok", "editor": "gimpish", "hint": "",
            "path": str(out_png), "scene_path": str(scene_path),
            "width": width, "height": height, "elapsed_ms": elapsed}
