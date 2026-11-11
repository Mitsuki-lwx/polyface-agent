# Proposal

> ⚠️ **补记**：这个能力是**先实现后补文档**的 ✗（当时当成 `video-frame-understanding` 的尾巴做的）。
> 事实按要求如实记录：代码当时已经在跑，本文档是为了让它进 specs，不是事前规划。

## Why

素材的母体一直是**文本** ✓（模型只能读语言）。所以「图片」此前只能进**素材库**当资产 ✗，
进不了「① 素材」✓ —— 用户提了这一点：**拍了张图想写点什么，无路可走** ✗。

而视觉已经验证可用 ✓（同一个模型能准确描述画面、读出图上文字 ✓）。

## What Changes

- **`POST /describe-image`**：图片 → 120 字内描述（含可读文字），作为素材正文 ✓
- **界面分流**：「从音视频 / 图片导入」按扩展名判断 —— 图片直接描述填进素材框 ✓
- **数据边界**：README 明确写"导入图片会整张发给模型" ✓

## Capabilities

### New Capabilities
- `image-as-material-source`: 图片经视觉描述成为文本素材

## Impact

- Python：`/describe-image`（复用 `vision.py`）
- Java：`/api/describe-image` 代理
- 前端：导入按钮按类型分流
- **成本**：每张图一次视觉调用（约 3~4s，Pass 通道免费 ✓）
