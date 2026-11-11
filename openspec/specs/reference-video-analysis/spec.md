# reference-video-analysis Specification

## Purpose
把一条**别人的**视频拆成可借鉴的结构：钩子、分段、节奏、画面类型、可抄的做法。

## Requirements

### Requirement: 分析必须基于视频本身

输出的每一条判断 MUST 来自该视频的转写或画面 ✓，MUST NOT 套用通用套路 ✗。

#### Scenario: 视频没有语音
- **WHEN** 参考视频没有说话（纯音乐/纯画面）
- **THEN** MUST 明确说明"无语音"，只做画面与节奏分析，MUST NOT 编造台词 ✗

### Requirement: 钩子要看前 3 秒

`hook` MUST 取自**开头 3 秒**的转写与画面，并 MUST 给出"它靠什么留住人"的判断。

#### Scenario: 开头是提问
- **WHEN** 前 3 秒是一句提问
- **THEN** hook.type = 提问，hook.quote 为原话

### Requirement: 结构要带时间

`structure` 每一段 MUST 带起止秒数与作用（开场/论证/举例/收尾…）。

#### Scenario: 用户想定位
- **WHEN** 分析说"第二段是举例，12.4–28.1s"
- **THEN** 用户按 12 秒跳过去确实是那个例子

### Requirement: 可执行的做法

`takeaways` MUST 是**能直接照做**的动作（"前 3 秒先抛反常识"✓），
MUST NOT 是"内容要优质"这类空话 ✗。

#### Scenario: 空话不过关
- **WHEN** 某条 takeaway 无法指导下一步动作
- **THEN** 应被重写或删除 ✗
