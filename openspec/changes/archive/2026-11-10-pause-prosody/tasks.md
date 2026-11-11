# Tasks

- [x] 1.1 `app/pipeline/prosody.py`：`analyze_pauses` / `summarize`
- [x] 1.2 `pausereview.Candidate.prosody` + `attach_prosody`
- [x] 1.3 prompt 增加 `prosody` 说明与判定规则
- [x] 1.4 参数 `pause_review_prosody`（默认 true）+ CLI `--no-pause-prosody`
- [x] 1.5 粗剪 CLI 接线（只算候选窗口）
- [x] 2.1 单测 8 条：渐弱/平稳/无声/读不出/下标对齐/越界/挂载/提示词
- [x] 2.2 真模型 A/B：同一文字、只有 prosody 不同 → 判定翻转
- [x] 3.1 全量测试
- [x] 4.1 更新讲解页与 STATE
- [x] 4.2 归档
