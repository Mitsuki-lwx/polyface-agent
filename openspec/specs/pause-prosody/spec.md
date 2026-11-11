# pause-prosody Specification

## Purpose
给停顿复核提供"怎么说的"这一层证据：本地算声学数字，音频不出本机。

## Requirements

### Requirement: 只发数字，不发音频

声学特征 MUST 在本地计算，MUST NOT 把音频数据发送给模型。
实测网关对 `input_audio` 返回 400，这条路本来也不通。

#### Scenario: 检查请求体
- **WHEN** 停顿复核调用模型
- **THEN** 请求里 MUST 只有文本与数字，MUST NOT 有 base64 音频

### Requirement: 没有数据就不发

某条候选算不出特征（音频读不出/缺依赖/静音）→ MUST 省略 `prosody` 字段，
MUST NOT 发送 `null` 或 0 冒充。

#### Scenario: 缺 librosa
- **WHEN** 环境里没有 librosa
- **THEN** 复核照常进行，只是不带 `prosody`；MUST NOT 因此失败

### Requirement: 斜率的定义要抗噪

`rms_slope` MUST 用"末尾一段均值 − 开头一段均值"，MUST NOT 用首帧减末帧
（会被停顿前的静音带偏，实测符号都会反）。

#### Scenario: 停顿前渐弱
- **WHEN** 音量线性下降后进入停顿
- **THEN** `rms_slope` MUST 明显为负

### Requirement: 模型仍只投票

`prosody` 是材料。模型 MUST NOT 因此获得给时间戳或直接决定剪点的权力。

#### Scenario: 同一文字、不同 prosody
- **WHEN** 文字完全相同、只有 `prosody` 不同
- **THEN** 判定 SHOULD 可以不同，但输出结构仍是 `{id, action, why}`
