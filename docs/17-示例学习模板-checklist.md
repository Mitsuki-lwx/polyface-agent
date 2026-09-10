# 验收清单（checklist）：示例自动学习模板（FR-63）

- 文档类型：checklist（事前三件套之三）
- 版本：v1.0 · 日期：2026-09-10
- 配套：`15-示例学习模板-task.md`、`16-示例学习模板-spec.md`
- 对应用例：UC-16（示例自动学习模板：学习 + 人工确认）
- 判定方式：每条独立判定，全部 `[x]` 方可交付

---

## A. 数据层与迁移

- [ ] A1 `template` 表在缺列时自动补 `status`、`source_note`
- [ ] A2 存量模板迁移后 `status` 为 `active`（现有行为不变）
- [ ] A3 对同一 DB 二次启动不抛错（ALTER 幂等）
- [ ] A4 `GET /api/templates`（不传 status）只返回 `active`
- [ ] A5 `GET /api/templates?status=draft` 只返回草稿
- [ ] A6 `GET /api/templates?status=all` 返回全部（含草稿）
- [ ] A7 新建模板（`POST /api/templates`）默认 `status=active`
- [ ] A8 内置模板迁移后仍为 `active`，且不可删除（沿用 M5 红线）

## B. 学习接口（learn）

- [ ] B1 `POST /api/templates/learn` 合法请求返回 201，且响应 `status=draft`
- [ ] B2 返回体含拆解结果：`name/voice/opening/structure/closing/tag_style/taboo`
- [ ] B3 返回体含 `rationale`（拆解依据说明）
- [ ] B4 `sample_text` 少于 50 字返回 400，且不创建任何模板
- [ ] B5 `sample_text` 缺失返回 400
- [ ] B6 传 `source_note` 时落库，且出现在 `name` 或 `source_note` 字段
- [ ] B7 未传 `source_note` 也能成功（name 走默认命名）
- [ ] B8 拆解**不存储示例原文**（DB 中无原文残留）
- [ ] B9 学习产生的模板 `builtin=false`、`version=1`

## C. 拆解质量

- [ ] C1 mock 模式拆解出非空 `structure`（≥1 条）
- [ ] C2 mock 模式 `opening` 取自示例首段（截断 ≤60 字）
- [ ] C3 mock 模式末尾为互动句时，`closing` 非空
- [ ] C4 mock 模式 `voice` 按关键词合理判定（含兜底"通用"）
- [ ] C5 mock 模式 `structure` 条数不超过上限（6）
- [ ] C6 `structure` 每条为短语概括（≤15 字），非整句照抄
- [ ] C7 真实模式 prompt 明确要求「只归纳结构、不复述内容」
- [ ] C8 真实模式 prompt payload 含示例文本与输出 schema
- [ ] C9 LLM 返回字段缺失时有兜底（字段为空而非报错）

## D. 草稿生命周期（UC-16 核心）

- [ ] D1 草稿模板**不出现**在生成页模板选择器中
- [ ] D2 草稿模板**不出现**在 `GET /api/templates` 默认响应中
- [ ] D3 `POST /api/templates/{id}/activate` 使 `status` 变为 `active`
- [ ] D4 启用后该模板**出现**在生成页选择器中
- [ ] D5 对已 `active` 的模板再次 activate 幂等返回 200
- [ ] D6 对不存在的 id activate 返回 404
- [ ] D7 草稿可被编辑（PUT 沿用），编辑后仍为 `draft`
- [ ] D8 草稿可被丢弃（DELETE 返回 204 且从列表消失）
- [ ] D9 草稿被编辑后再启用，生成时使用编辑后的内容
- [ ] D10 内置模板 activate 返回 409（无需启用）

## E. 前端（SPA 工作台）

- [ ] E1 模板库卡片出现「✨ 从示例学习」入口
- [ ] E2 学习弹窗含示例文本输入与来源备注输入
- [ ] E3 弹窗含"仅用于归纳结构、不保存原文"的合规提示
- [ ] E4 提交学习后弹窗关闭、列表刷新、新草稿出现在「草稿（待确认）」分组
- [ ] E5 草稿项显示拆解依据（rationale）与"请核对后启用"提示
- [ ] E6 草稿项提供「编辑 / 确认启用 / 丢弃」三个操作
- [ ] E7 「确认启用」后草稿从草稿分组移入「我的模板」
- [ ] E8 「丢弃」有二次确认，确认后草稿消失
- [ ] E9 生成页选择器只列 `active` 模板（含内置 + 已启用我的模板）
- [ ] E10 模板库列表按 `?status=all` 拉取，能同时展示草稿与已启用
- [ ] E11 示例文本为空时前端阻止提交并提示

## F. 测试与回归

- [ ] F1 Python 单元测试全绿（含新增拆解用例，无回归）
- [ ] F2 Java 单元测试全绿（含 status 过滤/activate，无回归）
- [ ] F3 端到端链路：learn → 草稿不进选择器 → activate → 进选择器 → 生成生效
- [ ] F4 端到端：过短示例返回 400 且未创建模板
- [ ] F5 端到端：丢弃草稿后列表与选择器均不含该模板
- [ ] F6 mock 模式（无 Key）下全部功能可用
- [ ] F7 M5 既有功能无回归（模板 CRUD / 导入导出 / 版本存档仍正常）
- [ ] F8 浏览器实测：学习入口、确认启用、丢弃交互真实可用

## G. 文档与交付

- [ ] G1 `05-SRS` FR-63 状态更新为 ✅
- [ ] G2 `07-用例模型与验收标准.md` UC-16 里程碑更新
- [ ] G3 若引入新架构决策，补充 ADR 条目
- [ ] G4 `docs/18-示例学习模板-交付说明.md` 产出（含验收结果表 + 架构增量）
- [ ] G5 代码已提交，工作区干净

---

## 验收结论

| 项 | 结果 | 备注 |
|---|---|---|
| A~G 全部勾选 | ⬜ 待执行 | |
| 自动化验收 | ⬜ 待执行 | |
| 浏览器实测 | ⬜ 待执行 | |
| 未通过项 | — | 若有，须逐条说明原因 |
