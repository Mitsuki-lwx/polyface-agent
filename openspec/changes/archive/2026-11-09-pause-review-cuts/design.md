# Design

## 关键决定

### 一、程序给候选，模型只投票

候选 = 停顿本身（`pauses`，已经在 `_find_pauses` 里拿到）。模型返回
`{id, action: cut|keep, why}`，**不给时间**。

理由：模型看不到音频，任何它算出来的时间戳都是猜。候选边界是程序的，
所以"决定哪一段"和"这一段是几秒"天然分离。

### 二、候选要带上下文，但**只带两句**

每条候选附带：停顿秒数 + 规则会不会剪 + 前一句 + 后一句。

- 不带全文：成本可预测、不会整体跑偏（与 `disfluency` 同一条纪律）
- 带 `rule_cut`：让模型知道"规则已经判了"，它只需回答"同不同意"，减少自由发挥

### 三、双向生效

| 判定 | 效果 |
|---|---|
| `keep` 且规则会剪 | **去掉**这一刀（留白被保护下来）|
| `cut` 且规则不会剪 | **加上**这一刀（犹豫被剪掉，候选下限 0.6s）|

两个方向都要，否则只解决一半问题。

### 四、默认关 + 降级出声

- `pause_review=False`：默认一次模型都不调（转写文本出本机 = 数据边界）
- 模型异常/坏 JSON → `verdicts=[]`、`llm_skipped=True`，行为退回纯规则；
  报告和 checkpoint 里都留着这个标记 —— **不静默**（checklist 会查）

### 五、候选上限

`max_candidates=80`，超出时**取最长的 80 条**（最长的停顿最值得判），
然后按时间重新排序、编号。上限是为了让一次调用的 token 可预测。

## 数据流

```
pauses (VAD/silencedetect)
   │  + sentences (df.split_sentences, 词级时间戳)
   ▼
build_candidates ──► 一次 chat_json(scene="pause_review")
   │                        │
   │                        ▼
   │                  verdicts [{id, action, why}]
   ▼                        │
plan_cuts(规则)             │
   └────────► apply_verdicts ◄┘
                     │
                     ▼
              merge_cuts → ffmpeg
```

## 失败与边界

| 情况 | 行为 |
|---|---|
| 没有转写（未装 faster-whisper）| 明确报错 `AsrMissing`，不静默跳过整类判定 |
| 候选为 0 | 一次都不调（不花冤枉钱）|
| 模型返回未知 id | 忽略该条，不崩 |
| 模型说 cut 但该停顿 < 0.6s | 不在候选里，模型根本看不到 |
| 剪完区间 ≤ 0（margin 吃掉）| 丢弃 |
