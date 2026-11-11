# Proposal

## Why

现在能读懂**自己的**视频 ✓（转写 + 关键帧 + 场景切换），但看不了**别人的** ✗。
做内容的人高频需求是「这条爆了，它为什么爆」—— 想学结构、节奏、钩子 ✓。

现有能力其实已经够拼出来 ✓：转写（说了什么）✓ + 关键帧与描述（画面）✓ +
场景切换（节奏）✓。缺的是**把它们拼成一份可读的分析** ✗。

## What Changes

`POST /analyze-reference`（Python）+ `POST /api/reference/analyze`（Java 代理）：
输入一条参考视频路径，输出结构化分析 JSON：

- **hook**：前 3 秒做了什么（钩子类型 + 原话）
- **structure**：分成哪几段、每段起止、这段的作用
- **pace**：语速（字/分）、镜头切换次数、段数
- **visual**：画面类型（口播/演示/素材拼接）来自关键帧描述
- **takeaways**：3 条"你能直接抄的做法"

## Capabilities

### New Capabilities
- `reference-video-analysis`: 外部视频 → 结构/节奏/钩子分析

## Impact
- Python：新 pipeline 模块（复用 transcribe / frames / scene_times）
- Java：一个只读代理端点
- 成本：1 次转写 + N 次视觉 + 1 次分析（约 30~60 秒 / 条）
