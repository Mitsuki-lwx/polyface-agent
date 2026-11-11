# Spec Delta

## Purpose

让图片也能当素材母体：图片没有文字 ✗，但它的**视觉描述**有 ✓。

## ADDED Requirements

### Requirement: 图片可转为素材正文

导入图片时 MUST 产出可编辑的**描述文本**，并 MUST 填入素材输入区供用户修改后再解析。

#### Scenario: 导入一张带文字的图
- **WHEN** 用户导入含文字的图片
- **THEN** 描述里 MUST 包含图上的可读文字（原样，不翻译、不改写）

#### Scenario: 模型失败
- **WHEN** 视觉调用失败
- **THEN** MUST 报出原因，MUST NOT 编造描述 ✗

### Requirement: 数据边界必须可见

导入图片会把**整张图**发给模型 ✓ —— 这一点 MUST 在 README 的数据边界表里写明，
MUST NOT 只写"音视频转写本地不上传"而不说图片 ✗。

#### Scenario: 用户读 README
- **WHEN** 用户查看数据边界表
- **THEN** 能看到「你导入的图片」在"发送到外部 LLM"那一列

### Requirement: 与文本素材同一条链路

图片转出的描述 MUST 走与粘贴文本**完全相同**的后续链路（解析 → 关键词 → 生成），
MUST NOT 为图片另开一条生成路径 ✗。

#### Scenario: 图片素材生成稿件
- **WHEN** 用图片描述解析出的素材生成
- **THEN** 关键词、检索、多平台生成行为与文本素材一致
