# tasks —— 带 BGM 的口播也能剪

对应 spec：`docs/spec_bgm.md`　　验收：`docs/checklist_bgm.md`

---

## T1　VAD 语音边界 → 停顿（主路径）

- **影响文件**：`python-service/app/pipeline/bgm.py`（新）、`python-service/tests/test_bgm_vad.py`（新）
- **依赖**：无
- **做什么**：给一段 wav，用**语音边界**算出停顿区间：
  - 优先复用**已有的转写段**（去冗余阶段已经转写过原文 → 不重复转写）
  - 没有转写时，调 `asr.transcribe_segments` 拿段边界
  - 相邻段之间的空隙 ≥ `min_pause` → 一个停顿
- **关键**：判据是"**没有语音**"，不是"音量低" —— 所以与 BGM 无关
- **参考**：`disfluency.speech_bounds_from_segments`（已在做"段边界 → 时刻"）
- **验收**：同一段音频，**加 BGM / 不加 BGM 两条**给出**同样**的停顿区间

---

## T2　BGM 检测

- **影响文件**：`bgm.py`、`tests/test_bgm_vad.py`
- **依赖**：T1
- **做什么**：判断素材里有没有 BGM：
  - 在"VAD 说没人说话"的窗口里测音量 → 仍然不低 ⇒ **有 BGM**
  - 返回 `{has_bgm, bgm_level_db, speech_windows, quiet_windows}`
- **为什么**：不检测的话，用户看到"0 段停顿"只会以为工具坏了（本 spec 的由来）
- **验收**：真实带 BGM 素材 → `has_bgm=True`；纯口播 → `False`

---

## T3　降噪兜底路径

- **影响文件**：`bgm.py`、`tests/test_bgm_vad.py`
- **依赖**：T1
- **做什么**：VAD 不可用时（缺 ASR / 模型失败）退回 `ffmpeg arnndn/afftdn` 降噪 + `silencedetect`；
  返回**用了哪条路**（`source: "vad" | "denoise"`）
- **关键**：降级要**说明**，不静默 —— 两条路的剪点可能不同
- **参考**：`roughcut.detect_pauses`（现有 silencedetect 实现）
- **验收**：monkeypatch 掉 ASR → 走降噪路径且 `source=="denoise"`；报告里能看到

---

## T4　抽伴奏片段（从无人声窗口）

- **影响文件**：`bgm.py`、`tests/test_bgm_cut.py`（新）
- **依赖**：T2
- **做什么**：从"只有 BGM、没人声"的窗口里截一段伴奏：
  - 选**最长**的那个窗口（相位/音色与原曲天然一致，因为就是同一条素材）
  - 短于 `min_loop_sec` 的窗口不用（太短的循环听得出周期）
  - **一个窗口都没有 → 明确失败**，不硬凑
- **参考**：`roughcut.cut_video` / `extract_wav` 的 ffmpeg 用法
- **验收**：构造"有人声段 + 无人声段"的音频 → 抽出的片段确实来自无人声段；全程有人声 → 报错

---

## T5　铺伴奏（循环 + 交叉淡化 + 混音）

- **影响文件**：`bgm.py`、`scripts/roughcut.py`
- **依赖**：T4
- **做什么**：
  - 伴奏片段**循环**到与剪后视频等长（`ffmpeg -stream_loop`）
  - 接缝处**交叉淡化**（长度可调）—— 生硬拼接会有"咔"声
  - 与剪后的人声轨**混音**（人声/伴奏电平可调）
- **参考**：`roughcut.burn_subtitles`（它已经在用 ffmpeg 滤镜串，照那个形状）
- **验收**：产物时长 = 剪后视频时长；接缝处无爆音（波形峰值不突变）；电平可调

---

## T6　参数外露

- **影响文件**：`roughcut.py` 的 `RoughcutParams`、`config.py`
- **依赖**：T1、T3、T4、T5
- **做什么**：新增（走现有三层优先级）：
  - `bgm_aware`（总开关，关掉 = 回到纯 silencedetect）
  - `vad_min_pause_sec`（多长的语音空隙算停顿）
  - `bgm_min_loop_sec`（循环段最小长度）
  - `bgm_xfade_sec`（交叉淡化长度）
  - `bgm_voice_db` / `bgm_bed_db`（混音电平）
- **验收**：改任一值 → 行为确实变；不填 → 默认能跑

---

## T7　接入两段式（方案里说清 BGM 怎么处理）

- **影响文件**：`scripts/roughcut.py`
- **依赖**：T2、T5、T6
- **做什么**：方案里除了剪点，还要给出：
  - **有没有 BGM**、用的是哪条判据（VAD / 降噪）
  - BGM 会**怎么处理**（重建 + 循环 N 次 + 循环段来自 `a.bb–c.dd`）
  - **如实写明代价**：BGM 变成循环、不是原编曲
  - 剪后时长（人声剪短了，伴奏铺到等长）
- **验收**：方案里能看到上面四项；关掉 `bgm_aware` → 回到旧行为

---

## T8　端到端验证（真素材）

- **影响文件**：无（记录写进 `docs/checklist_bgm.md`）
- **依赖**：T7
- **做什么**：用**带 BGM 的真实口播素材**（就是那条 64.76s 的）跑：
  发起 → 看方案（含 BGM 说明）→ 确认 → 出成片。逐条核对：
  - 剪点对不对（对着波形/转写核）
  - BGM 是不是连续（听 + 看频谱）
  - 人声有没有被剪掉半个字
- **验收**：原始输出贴进 checklist；**剪错的、BGM 接缝听得出的一律列出来**（报忧不报喜）

---

## T9　文档与决策落档

- **影响文件**：`docs/08`（新 ADR）、`docs/spec_roughcut.md`（补"BGM 场景"）、`README.md`
- **依赖**：T8
- **做什么**：ADR 记录"为什么走重建而不是人声分离"（含 torch 体积这个关键权衡）；
  roughcut spec 补一节；README 的 FAQ 补"带 BGM 的素材会怎样"
- **验收**：`grep` 确认文档与实现一致
