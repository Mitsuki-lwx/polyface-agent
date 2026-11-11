"""视觉描述（`openspec/changes/video-frame-understanding`）。

**为什么不复用 `llm.chat_json`**：那个客户端面向**纯文本 JSON** ✗。视觉要走
OpenAI 兼容的 `content` 数组（`[{type: text}, {type: image_url}]`）✓ —— 是另一个请求形态。
但**配置复用同一份**（base_url / key / 超时），不新造一套 ✗。

⚠️ 实测（2026-11-08）：`api.cline.bot` 的返回体把标准结构套了一层 `{"data": {...}}` ✗
—— 这里两种形态都认 ✓，否则本地跑通、换网关就炸 ✓。
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

# 三问覆盖"能不能用"，而不是"美不美" ✗（后者不可判定）
DESCRIBE_PROMPT = (
    "这是一段视频里的一帧。用不超过 40 个字回答三件事，一行一条，不要客套：\n"
    "1) 画面里是什么（人物/物体/场景，具体一点）\n"
    "2) 适合放在视频的什么位置（开场/讲解/演示/收尾/不适合）\n"
    "3) 画面里有没有可读的文字，有就原样抄 10 字以内，没有写“无文字”"
)


class VisionError(RuntimeError):
    """视觉调用失败（网络/鉴权/模型不支持图片）。"""


def describe_frame(frame_path: str | Path, prompt: str = DESCRIBE_PROMPT,
                   timeout: float = 90.0, max_tokens: int = 1000) -> str:
    """把一帧送进模型，返回描述文本。失败抛 `VisionError`（调用方决定是降级还是中断）。"""
    path = Path(frame_path)
    if not path.is_file():
        raise VisionError(f"帧文件不存在：{path}")
    data = base64.b64encode(path.read_bytes()).decode()

    s = get_settings()
    body = {
        "model": s.llm_model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{data}"}},
        ]}],
        # ⚠️ 必须给足：这是**推理型**模型，max_tokens 太小会被思考过程吃光，
        # 正文直接返回空、网关报 "empty response content" ✗（实测 300 全空，1000 正常）
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    url = f"{s.llm_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {s.llm_api_key}", "Content-Type": "application/json",
               # 缺这个头时带图请求返回空 ✗（Cline 网关行为，见 docs.cline.bot/getting-started/clinepass）
               "X-Response-Format": "openai"}
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=timeout)
    except httpx.HTTPError as e:
        raise VisionError(f"请求失败：{type(e).__name__}: {str(e)[:160]}") from None

    if resp.status_code != 200:
        raise VisionError(f"HTTP {resp.status_code}: {(resp.text or '')[:200]}")

    try:
        payload = resp.json()
    except json.JSONDecodeError:
        raise VisionError(f"返回不是 JSON：{(resp.text or '')[:160]}") from None

    # 网关可能套一层 data ✓（cline 就是），两种都认
    node = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    try:
        text = node["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise VisionError(f"返回结构不认识：{json.dumps(payload, ensure_ascii=False)[:200]}") from None
    return (text or "").strip()


def describe_frames(frames: list[dict], prompt: str = DESCRIBE_PROMPT,
                    workers: int = 6) -> list[dict]:
    """**并行**描述（实测：6 帧串行 167s → 并行 17s ✓，10 倍）。

    这是纯 I/O 等待（每帧一次 HTTP），线程池正好用得上 ✓。
    **单帧失败不中断整批**：那一帧描述留空并带 error ✓（帧本身不丢 ✓）。
    """
    if not frames:
        return []
    if workers <= 1 or len(frames) == 1:
        return [_describe_one(f, prompt) for f in frames]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(workers, len(frames))) as ex:
        return list(ex.map(lambda f: _describe_one(f, prompt), frames))


def _describe_one(f: dict, prompt: str) -> dict:
    item = {"t_sec": f.get("t_sec"), "path": f.get("path"), "desc": "", "error": None}
    try:
        item["desc"] = describe_frame(f["path"], prompt)
    except VisionError as first:
        # 两类都值得重试一次：空正文（思考吃光预算）与网关/网络抖动
        retryable = ("empty response content" in str(first)
                     or "disconnected" in str(first).lower()
                     or "timed out" in str(first).lower()
                     or "请求失败" in str(first))
        if retryable:
            try:
                item["desc"] = describe_frame(f["path"], prompt, max_tokens=3000)
            except VisionError as e:
                item["error"] = str(e)[:200]
        else:
            item["error"] = str(first)[:200]
        if item["error"]:
            logger.warning("帧 %s 描述失败：%s", f.get("path"), item["error"])
    return item
