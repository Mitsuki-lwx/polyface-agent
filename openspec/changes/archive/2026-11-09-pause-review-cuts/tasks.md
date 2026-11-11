# Tasks

- [x] 1.1 新建 `app/pipeline/pausereview.py`：`build_candidates` / `review_candidates` / `apply_verdicts` / `format_report`
- [x] 1.2 `prompts.py` 新增 `build_pause_review_prompt`，登记 `SCENE_BUILDERS["pause_review"]`
- [x] 1.3 `RoughcutParams` 新增 `pause_review` / `pause_review_min_sec` / `pause_review_max`
- [x] 1.4 `scripts/roughcut.py` 新增 `--pause-review` / `--pause-review-min` / `--pause-review-max`，在"找停顿"阶段接入
- [x] 1.5 复核结果落 checkpoint（`停顿复核`），续跑不重跑
- [x] 2.1 单测：`keep` 去掉剪点 / `cut` 加上剪点 / 未知 id 忽略 / 模型异常降级 / 默认关零调用
- [x] 2.2 单测：候选上限取最长 N 条且按时间排序
- [x] 3.1 Java `RoughcutRunner` 透传三个参数（默认不传 = 关）
- [x] 3.2 界面加勾选框 + 数据边界提示
- [x] 4.1 跑 Python 全量 + Java 全量
- [x] 4.2 真实视频冒烟：开 `--pause-review` 跑一条，核对报告与剪点变化
- [x] 5.1 更新 `docs/polyface-explained.html`（难题 + 实现）
- [ ] 5.2 归档
