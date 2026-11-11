# Proposal

## Why

生成一个多平台任务要几分钟，界面只有"当前阶段 + 已完成 N 步"。
用户看不到**哪个平台已经好了**，只能等整批返回 —— 实测 draft 单次 p95 210 秒，
体感就是"点了没反应"。

拆步提速已评测否决（docs/79），所以下一步是**改交互**：已经产出的先给出来。

## What Changes

- Java 从自己写的「平台」事件里读出每个平台的状态（pending/running/done/failed）
- `GET /api/jobs/{id}` 增加 `platform_states`
- 生成面板逐平台显示：`小红书 ✅ 知乎 ⏳ 公众号 …`

不改生成逻辑，不改任务模型；数据来源是已有的事件文件。

## Capabilities

### New Capabilities
- `live-platform-progress`: 生成任务的平台级实时状态
