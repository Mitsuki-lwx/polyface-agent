# 验收清单（checklist）：模板管理完整版（FR-62 + FR-64）

- 文档类型：checklist（事前三件套之三）
- 版本：v1.0 · 日期：2026-09-10
- 配套：`11-模板管理-task.md`、`12-模板管理-spec.md`
- 对应用例：UC-14（创建/管理我的内容模板）、UC-15（生成时套用模板）
- 判定方式：每条独立判定，全部 `[x]` 方可交付

---

## A. 数据层

- [ ] A1 `template` 表随启动自动创建（缺失时）
- [ ] A2 `draft` 表在缺列时自动补 `template_id`、`template_version` 两列
- [ ] A3 对同一 DB 二次构造 Store 不抛错（ALTER 幂等）
- [ ] A4 新建模板后可立即查回，字段（name/voice/opening/structure/closing/tag_style/taboo）完整无丢失
- [ ] A5 `structure` 与 `taboo` 以 JSON 数组存取，含中文/特殊字符不损坏
- [ ] A6 编辑模板后 `version` 自增 1、`updated_at` 变化、`created_at` 不变
- [ ] A7 删除模板后 `listTemplates` 不再返回该条
- [ ] A8 预置模板首次启动自动 seed（builtin=1 且 ≥3 条）
- [ ] A9 二次启动不重复 seed（builtin 条数不增长）
- [ ] A10 预置模板内容不与用户改动冲突（用户改过的预置模板不被启动覆盖）

## B. 模板 REST API

- [ ] B1 `GET /api/templates` 返回全部模板，内置在前、我的在后
- [ ] B2 `GET /api/templates?kind=content` 按 kind 过滤生效
- [ ] B3 `POST /api/templates` 成功返回 201 且响应含新 id
- [ ] B4 `POST /api/templates` 的 name 为空时返回 400
- [ ] B5 `GET /api/templates/{id}` 存在返回 200，不存在返回 404
- [ ] B6 `PUT /api/templates/{id}` 更新成功返回 200，version 自增
- [ ] B7 `PUT /api/templates/{id}` 对不存在 id 返回 404
- [ ] B8 `DELETE /api/templates/{id}` 删除我的模板返回 204
- [ ] B9 `DELETE /api/templates/{id}` 删除内置模板返回 409，且数据未被删除
- [ ] B10 `POST /api/templates/{id}/duplicate` 生成新模板（builtin=0，origin_id=源 id）
- [ ] B11 内置模板也可被复制（复制后 builtin=0、可编辑可删除）
- [ ] B12 `GET /api/templates/export` 返回含 `polyface_templates` 版本字段的 JSON
- [ ] B13 `POST /api/templates/import` 导入合法文件后模板数量正确增加
- [ ] B14 导入同名模板不覆盖既有，新模板名称带 `(导入)` 后缀
- [ ] B15 导入格式非法（缺版本字段/非 JSON）返回 400

## C. 生成接入与版本存档（FR-64 / UC-15）

- [ ] C1 `POST /api/materials/{id}/generate` 可传 `template_id` 并生效（成稿体现模板开头/结尾）
- [ ] C2 传不存在的 `template_id` 返回 404
- [ ] C3 `template_id` 与内联 `template` 同时传时，以 `template_id` 为准
- [ ] C4 不传模板时行为与 M3 一致（仅平台 DNA），无回归
- [ ] C5 生成的 draft 记录写入所用 `template_id` 与 `template_version`
- [ ] C6 模板编辑后再次生成，draft 记录的 `template_version` 为新版本号
- [ ] C7 模板被删除后，历史 draft 的 `template_id` 仍保留（可追溯，不置空）
- [ ] C8 同素材套用不同模板，产出结构与开头明显不同

## D. 前端（SPA 工作台）

- [ ] D1 左栏出现「模板库」卡片，内置/我的分组展示
- [ ] D2 内置模板不显示删除按钮，我的模板显示删除按钮
- [ ] D3 「+ 新建模板」表单可提交并刷新列表
- [ ] D4 点「编辑」回填表单，保存后列表版本号 +1
- [ ] D5 点「复制为我的」生成副本并出现在「我的模板」组
- [ ] D6 点「删除」有二次确认，确认后从列表消失
- [ ] D7 「导出 JSON」可下载文件
- [ ] D8 「导入 JSON」选择文件后导入成功并刷新列表
- [ ] D9 生成页出现模板下拉选择器，含「不使用模板」+ 内置 + 我的
- [ ] D10 选择模板生成后，稿件卡显示 `模板：{名称} v{版本}`
- [ ] D11 模板被删除的历史稿件显示「模板已删除」而非报错
- [ ] D12 内联「临时模板」入口仍可用（向后兼容）

## E. 测试与回归

- [ ] E1 Java 单元测试全绿（含 A/B/C 层新增用例）
- [ ] E2 Python 测试全绿（17 项，无回归）
- [ ] E3 端到端链路跑通：建模板 → 生成 → 稿件带版本 → 编辑（版本+1）→ 导出 → 删除 → 导入恢复
- [ ] E4 mock 模式（无 Key）下以上全部可用
- [ ] E5 真实 LLM 模式下模板字段正确进入 brief/draft prompt payload

## F. 文档与交付

- [ ] F1 `05-SRS` FR-62 / FR-64 状态更新为 ✅
- [ ] F2 `07-用例模型与验收标准.md` 里程碑矩阵 M4 行更新
- [ ] F3 `docs/14-模板管理-交付说明.md` 产出（含验收结果表 + 架构增量）
- [ ] F4 若引入新架构决策，补充 ADR 条目
- [ ] F5 代码已提交，工作区干净

---

## 验收结论

| 项 | 结果 | 备注 |
|---|---|---|
| A~F 全部勾选 | ⬜ 待执行 | |
| 未通过项 | — | 若有，须逐条说明原因 |
