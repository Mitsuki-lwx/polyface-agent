# Proposal

## Why

停顿复核现在**只看文字**（`pause-review-cuts`）。而"留白 vs 卡壳"最直接的证据其实是
**怎么说的**：说断就断、还是渐弱收尾；后面是起势、还是继续接上一句。

模型**收不了音频**（实测：网关对 `input_audio` 返回 400 `Invalid input`），
但声音的**数字**可以在本机算出来再发出去。

## What Changes

停顿复核的候选里增加 `prosody` 字段（本地算）：

- `rms_before` / `rms_after`：停顿前后 1.5 秒的平均能量
- `rms_slope`：停顿前末尾 0.3s 与开头 0.3s 的能量差（负=渐弱收尾，正=渐强）
- `pitch_before` / `pitch_after` / `pitch_delta`：停顿前后音高中位数与差值

**不改变**：音频不出本机（只发数字）；默认关；模型仍只投票、不给时间。

## Capabilities

### New Capabilities
- `pause-prosody`：停顿前后的声学特征（本地算，进复核 prompt）

## Impact
- Python：`app/pipeline/prosody.py`；`pausereview` 候选加字段；prompt 加说明；CLI 接线
- 成本：实测 **45 ms/候选**（40 条 1.81s）；只算候选窗口
