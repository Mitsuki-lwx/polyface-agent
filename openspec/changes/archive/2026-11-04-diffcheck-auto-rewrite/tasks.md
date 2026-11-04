# Tasks

## 1. 配置与目标选择函数

- [x] 1.1 在 `app/config.py` 中添加 `diff_auto_rewrite_enabled: bool = True` 与 `diff_max_rewrite_rounds: int = 1` 配置项，并通过 `pytest tests/test_diffcheck.py` 验证配置正确挂载
- [x] 1.2 在 `app/pipeline/diffcheck.py` 中实现 `select_rewrite_candidate(platforms_order, overlaps, max_overlap)`，按平台生成顺序选出需改写的后发平台及重合冲突源，并在单测中验证选择逻辑

## 2. 定向改写反馈与生成循环闭环

- [x] 2.1 在 `app/pipeline/diffcheck.py` 中实现 `build_diff_rewrite_feedback(target_code, other_code, conflict_sentences, ratio)` 组装精准去重提示语，并在单测中验证提示语包含冲突句清单与视角分流指引
- [x] 2.2 在 `app/pipeline/generate.py` 中实现自动去重改写循环：初稿评估后若重合率超标且在配置允许轮次内，重新触发候选平台的 `_generate_one`（带针对性 feedback 与冲突 avoid 句子），并在重合降低时采纳更新
- [x] 2.3 在改写执行中通过 RunLog 发出 `平台:{code}:去重改写` 阶段事件，并在改写完成后更新 `platform_issues` 与 workdir 断点持久化单元

## 3. 单元测试与端到端验证

- [x] 3.1 编写 `tests/test_diff_rewrite.py`，覆盖：重合超标触发改写、改写达标收敛更新、改写未超标直接放行、达最大轮数安全降级保留原稿等场景
- [x] 3.2 运行全量单元测试套件，确保全部通过无任何回归
- [x] 3.3 运行真实端到端生成脚本验证真实大模型去重自愈效果

## Workflow follow-up

- 归档该变更（openspec archive diffcheck-auto-rewrite）
