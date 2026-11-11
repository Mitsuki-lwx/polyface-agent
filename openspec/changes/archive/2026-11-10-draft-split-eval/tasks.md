# Tasks

- [x] 1.1 `prompts._draft_payload` 抽出共用材料与规则
- [x] 1.2 `build_draft_outline_prompt` / `build_draft_section_prompt` + 登记两个 scene
- [x] 1.3 `DRAFT_SECTION_SYSTEM`（只写这一段）
- [x] 1.4 `generate._split_draft`：提纲 → 段间并行 → 拼回同形 JSON
- [x] 1.5 开关 `llm_draft_split` / `llm_draft_section_workers`（默认关）
- [x] 2.1 单测 5 条：同形、乱序完成仍按段号排、空提纲不抛、两类提示词约束
- [x] 2.2 子集评测两轮（split / split2）：均劣于基线（10% vs 92%，耗时更长）
- [x] 2.3 判定：**否决**（三条预置标准全未达到，全量评测不再跑 —— 子集已经反向）
- [x] 3.1 结果落 docs/79
- [x] 3.2 归档（结论：否决，开关保持默认关）
