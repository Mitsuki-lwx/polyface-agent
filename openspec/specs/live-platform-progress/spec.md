# live-platform-progress Specification

## Purpose
生成任务进行中，界面能逐平台显示状态，不必等整批结束。

## Requirements

### Requirement: 状态来自编排者的事件

平台状态 MUST 从 `events.jsonl` 的「平台」事件推导，MUST NOT 让 Python 反向汇报平台边界。

#### Scenario: 一个平台开始
- **WHEN** Java 写了一条「平台/start」
- **THEN** 该平台状态为 running

### Requirement: 顺序稳定

`platform_states` MUST 按请求里的平台顺序返回。

#### Scenario: 先跑完的不是第一个
- **WHEN** 第二个平台先完成
- **THEN** 数组顺序仍是请求顺序，状态各自正确

### Requirement: 容错

事件文件缺失、半行 JSON、未知 kind MUST NOT 抛错；未知状态按 pending。

#### Scenario: 读到半行
- **WHEN** 最后一行写到一半
- **THEN** 跳过该行，其余状态照常
