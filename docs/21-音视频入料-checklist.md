# 验收清单（checklist）：音视频转文字入料（FR-51 / M3.5）

- 文档类型：checklist（事前三件套之三）
- 版本：v1.0 · 日期：2026-09-11
- 配套：`19-音视频入料-task.md`、`20-音视频入料-spec.md`
- 对应用例：UC-12（视频/音频转文字入料）
- 判定方式：每条独立判定，全部 `[x]` 方可交付

---

## A. 环境探测与降级

- [ ] A1 启动/调用时探测 ffmpeg 是否可用
- [ ] A2 ffmpeg 不可用时返回可读提示（含安装指引），不抛 500
- [ ] A3 ffmpeg 路径可由环境变量 `POLYFACE_FFMPEG` 覆盖
- [ ] A4 探测 faster-whisper 是否已安装（`is_available()`）
- [ ] A5 ASR 未安装时**不阻塞**字幕提取路径
- [ ] A6 ASR 未安装且无字幕时返回 `needs_manual` 且 hint 含可执行安装命令

## B. 媒体探测（probe）

- [ ] B1 `POST /api/ingest/probe` 对合法视频返回 200
- [ ] B2 响应含 `duration_sec`（来自 format 或 audio stream）
- [ ] B3 响应含 `has_audio`（正确识别有无音轨）
- [ ] B4 响应含 `has_subtitle` 与 `subtitle_streams`（index/codec/lang）
- [ ] B5 响应含 `recommended_mode`（subtitle / asr / manual）
- [ ] B6 无字幕+有音轨 → `recommended_mode=asr`
- [ ] B7 无字幕+无音轨 → `recommended_mode=manual`
- [ ] B8 文件不存在 → 400（不是 500）
- [ ] B9 非媒体文件（如 .txt 内容）→ 400
- [ ] B10 ffprobe 调用有超时保护（不无限挂起）

## C. 字幕提取

- [ ] C1 带字幕轨的视频可提取出非空文本
- [ ] C2 提取结果不含序号行
- [ ] C3 提取结果不含时间轴（`-->`）
- [ ] C4 提取结果不含 `<i>`/`{\an8}` 等标记
- [ ] C5 相邻重复行被去重
- [ ] C6 字幕轨为空时返回 `needs_manual` 而非空文本静默成功
- [ ] C7 `mode=subtitle` 强制走字幕路径（不走 ASR）

## D. ASR 转写（可选能力）

- [ ] D1 ASR 可用时，无字幕视频走转写并返回 `status=transcribed`
- [ ] D2 转写前先把音频降采样为 16kHz 单声道 wav
- [ ] D3 ASR 不可用时返回 `needs_manual`，且**不抛异常**
- [ ] D4 模型尺寸可由 `POLYFACE_ASR_MODEL` 覆盖（默认 small）
- [ ] D5 模型实例进程内缓存（连续两次转写不重复加载）

## E. 入料接口（Java）

- [ ] E1 `POST /api/ingest/upload` 接收 multipart 文件并落盘 `data/media/`
- [ ] E2 上传返回 `media_id` 与 `filename`
- [ ] E3 非法扩展名（如 .exe/.txt）返回 400
- [ ] E4 超过大小上限返回 413
- [ ] E5 `POST /api/ingest/transcribe` 透传 Python 结果（status/text/source/hint）
- [ ] E6 路径模式拒绝不存在文件（400）
- [ ] E7 路径模式拒绝目录（400）
- [ ] E8 `DELETE /api/ingest/media/{id}` 删除副本返回 204
- [ ] E9 扩展名白名单覆盖常见视频与音频格式

## F. 前端（SPA）

- [ ] F1 「① 素材」卡片内出现「🎬 从音视频导入」折叠区
- [ ] F2 可选择文件，也可粘贴本机路径
- [ ] F3 含合规提示（仅处理自有/授权内容 + 文件仅存本机）
- [ ] F4 探测后显示时长与是否有字幕轨
- [ ] F5 转写中按钮禁用并显示进度文案（防重复提交）
- [ ] F6 转录文本展示在**可编辑**文本域中
- [ ] F7 `needs_manual` 时明确展示 hint（安装指引/手填建议）
- [ ] F8 「确认并解析为素材」调用现有素材创建接口
- [ ] F9 成功后入料区清空且素材历史出现新条目
- [ ] F10 失败的转写不清空文件选择（可重试）

## G. 测试与回归（UC-12 验收）

- [ ] G1 Python 单元测试全绿（含新增字幕清洗/探测/降级用例）
- [ ] G2 Java 单元测试全绿（含扩展名/路径校验）
- [ ] G3 端到端：ffmpeg 造样本 → 探测 → 提字幕 → 建素材 → **走 understand 生成成功**
- [ ] G4 端到端：无字幕样本 → `needs_manual` + hint 可执行
- [ ] G5 端到端：非法扩展名与不存在路径 → 400
- [ ] G6 **无外部上传**：全流程无出网请求（仅本地 ffmpeg/ASR）
- [ ] G7 转录稿进入素材历史且 `source_kind=口播稿`、title 记原文件名
- [ ] G8 M5/FR-63 既有功能无回归
- [ ] G9 浏览器实测：选文件 → 转写 → 编辑 → 确认建素材全流程

## H. 文档与交付

- [ ] H1 `05-SRS` FR-51 状态更新为 ✅
- [ ] H2 `07-用例模型与验收标准.md` UC-12 与 M3.5 行更新
- [ ] H3 若引入新架构决策，补充 ADR 条目
- [ ] H4 `docs/22-音视频入料-交付说明.md` 产出（含验收结果 + 环境依赖说明）
- [ ] H5 代码已提交，工作区干净

---

## 验收结论

| 项 | 结果 | 备注 |
|---|---|---|
| A~H 全部勾选 | ⬜ 待执行 | |
| 自动化验收 | ⬜ 待执行 | |
| 浏览器实测 | ⬜ 待执行 | |
| 未通过项 | — | 若有，须逐条说明原因 |
