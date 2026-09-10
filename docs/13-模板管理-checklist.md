# 验收清单（checklist）：模板管理完整版（FR-62 + FR-64）

- 文档类型：checklist（事前三件套之三）
- 版本：v1.0 · 日期：2026-09-10
- 配套：`11-模板管理-task.md`、`12-模板管理-spec.md`
- 对应用例：UC-14（创建/管理我的内容模板）、UC-15（生成时套用模板）
- 判定方式：每条独立判定，全部 `[x]` 方可交付

---

## A. 数据层

- [x] A1 `template` 表随启动自动创建（缺失时）
- [x] A2 `draft` 表在缺列时自动补 `template_id`、`template_version` 两列
- [x] A3 对同一 DB 二次构造 Store 不抛错（ALTER 幂等）
- [x] A4 新建模板后可立即查回，字段（name/voice/opening/structure/closing/tag_style/taboo）完整无丢失
- [x] A5 `structure` 与 `taboo` 以 JSON 数组存取，含中文/特殊字符不损坏
- [x] A6 编辑模板后 `version` 自增 1、`updated_at` 变化、`created_at` 不变
- [x] A7 删除模板后 `listTemplates` 不再返回该条
- [x] A8 预置模板首次启动自动 seed（builtin=1 且 ≥3 条）
- [x] A9 二次启动不重复 seed（builtin 条数不增长）
- [x] A10 预置模板内容不与用户改动冲突（用户改过的预置模板不被启动覆盖）

## B. 模板 REST API

- [x] B1 `GET /api/templates` 返回全部模板，内置在前、我的在后
- [x] B2 `GET /api/templates?kind=content` 按 kind 过滤生效
- [x] B3 `POST /api/templates` 成功返回 201 且响应含新 id
- [x] B4 `POST /api/templates` 的 name 为空时返回 400
- [x] B5 `GET /api/templates/{id}` 存在返回 200，不存在返回 404
- [x] B6 `PUT /api/templates/{id}` 更新成功返回 200，version 自增
- [x] B7 `PUT /api/templates/{id}` 对不存在 id 返回 404
- [x] B8 `DELETE /api/templates/{id}` 删除我的模板返回 204
- [x] B9 `DELETE /api/templates/{id}` 删除内置模板返回 409，且数据未被删除
- [x] B10 `POST /api/templates/{id}/duplicate` 生成新模板（builtin=0，origin_id=源 id）
- [x] B11 内置模板也可被复制（复制后 builtin=0、可编辑可删除）
- [x] B12 `GET /api/templates/export` 返回含 `polyface_templates` 版本字段的 JSON
- [x] B13 `POST /api/templates/import` 导入合法且无冲突的文件后，模板数量正确增加并返回 200
- [x] B14 导入存在同名且**未指定策略**时返回 409，body 含 `conflicts` 清单（name + existing_id）
- [x] B15 导入格式非法（缺版本字段/非 JSON）返回 400
- [x] B16 `on_conflict=skip` 跳过同名（既有内容不变）；`overwrite` 用导入内容覆盖同名；`keep_both` 新增带 `(导入)` 后缀的副本

## C. 生成接入与版本存档（FR-64 / UC-15）

- [x] C1 `POST /api/materials/{id}/generate` 可传 `template_id` 并生效（成稿体现模板开头/结尾）
- [x] C2 传不存在的 `template_id` 返回 404
- [x] C3 `template_id` 与内联 `template` 同时传时，以 `template_id` 为准
- [x] C4 不传模板时行为与 M3 一致（仅平台 DNA），无回归
- [x] C5 生成的 draft 记录写入所用 `template_id` 与 `template_version`
- [x] C6 模板编辑后再次生成，draft 记录的 `template_version` 为新版本号
- [x] C7 模板被删除后，历史 draft 的 `template_id` 仍保留（可追溯，不置空）
- [x] C8 同素材套用不同模板，产出结构与开头明显不同

## D. 前端（SPA 工作台）

- [x] D1 左栏出现「模板库」卡片，内置/我的分组展示
- [x] D2 内置模板不显示删除按钮，我的模板显示删除按钮
- [x] D3 「+ 新建模板」表单可提交并刷新列表
- [x] D4 点「编辑」回填表单，保存后列表版本号 +1
- [x] D5 点「复制为我的」生成副本并出现在「我的模板」组
- [x] D6 点「删除」有二次确认，确认后从列表消失
- [x] D7 「导出 JSON」可下载文件
- [x] D8 「导入 JSON」选择文件后导入成功并刷新列表
- [x] D9 生成页出现模板下拉选择器，含「不使用模板」+ 内置 + 我的
- [x] D10 选择模板生成后，稿件卡显示 `模板：{名称} v{版本}`
- [x] D11 模板被删除的历史稿件显示「模板已删除」而非报错
- [x] D12 内联「临时模板」入口仍可用（向后兼容）
- [x] D13 导入遇同名冲突时弹窗展示冲突清单，提供「覆盖 / 跳过 / 都保留」三选项；用户选择后按所选策略重新提交并导入成功

## E. 测试与回归

- [x] E1 Java 单元测试全绿（含 A/B/C 层新增用例）
- [x] E2 Python 测试全绿（17 项，无回归）
- [x] E3 端到端链路跑通：建模板 → 生成 → 稿件带版本 → 编辑（版本+1）→ 导出 → 删除 → 导入恢复
- [x] E4 mock 模式（无 Key）下以上全部可用
- [x] E5 真实 LLM 模式下模板字段正确进入 brief/draft prompt payload

## F. 文档与交付

- [x] F1 `05-SRS` FR-62 / FR-64 状态更新为 ✅
- [x] F2 `07-用例模型与验收标准.md` 里程碑矩阵 M4 行更新
- [x] F3 `docs/14-模板管理-交付说明.md` 产出（含验收结果表 + 架构增量）
- [x] F4 若引入新架构决策，补充 ADR 条目
- [x] F5 代码已提交，工作区干净

---

## 验收结论

| 项 | 结果 | 备注 |
|---|---|---|
| A~F 全部勾选 | ✅ 已勾选 | 2026-09-10 |
| 自动化验收（A/B/C 层） | ✅ 35/35 | `scripts/e2e_m5.py`，可重复运行 |
| 浏览器实测（D 层 13 项） | ✅ 13/13 | headless Edge 152 + CDP 驱动真实浏览器 |
| 单元测试 | ✅ Java 11 / Python 18 | M5 新增 Java 5 + Python 1 |
| 未通过项 | 无 | — |

### 验证方式与证据

**A/B/C 层**：`scripts/e2e_m5.py` 对运行中的服务发起真实 HTTP 调用并逐条断言，35 项全部通过。

**D 层（浏览器实测，2026-09-10）**：用 headless Edge + CDP 驱动真实浏览器加载 `http://127.0.0.1:8080`，逐条操作并读取真实 DOM 状态：

| 项 | 实测结果 |
|---|---|
| D1 模板库卡片 | 「⑤ 模板库 5 个（内置 3 / 我的 2）」正常渲染 |
| D2 删除按钮差异 | 内置模板仅「编辑 / 复制为我的」；仅我的模板出现「删除」 |
| D3 新建模板 | 填表保存后 4→5 个，「我的」1→2 |
| D4 编辑回填 + 版本 | 打开 #5 回填 name/opening/structure/taboo 全部正确；改名保存后 v1→v2 |
| D5 复制为我的 | 复制内置模板两次，「我的」2→4，重名自动加 `(2)` 后缀 |
| D6 删除 + 二次确认 | 捕获到 `confirm` 调用；确认后 7→6，删除生效 |
| D7 导出 JSON | `GET /api/templates/export` 200，含 `polyface_templates:1`；条目仅 8 个业务字段，无本机标识 |
| D8 导入 JSON | 导入 1 条 → 5→6 个；列表与生成页下拉同步出现新模板 |
| D9 生成页选择器 | 选项 = 不使用模板 + 内置 3 + 我的 N，均带 `v{版本}` |
| D10 稿件卡模板标记 | 头部显示「🧩 模板：UI验证模板 v1」 |
| D11 模板已删除 | 模板缺失时显示「🧩 模板已删除（#4 v1）」而非报错 |
| D12 内联临时模板 | 折叠区「临时模板（不保存，仅本次生成）」+ 4 个输入框均在位 |
| D13 导入冲突弹窗 | 弹窗显示「测试冲突模板（已存在 #99 · 内置）」+ 三策略按钮 `skip / overwrite / keep_both` |

> **D8 说明**：文件读取路径用标准 `File` + `DataTransfer` 注入 `#tplImportFile` 并派发 `change` 模拟（headless 无法弹出系统文件选择框）；导入接口、冲突分支与 UI 刷新均已独立验证。

**E5（真实 LLM）**：环境未配置 LLM Key，未发起真实上游调用；已用 `test_template_carried_into_brief_and_draft_prompts` 验证模板字段确实进入 brief/draft 的 prompt payload。

### 本轮浏览器验证额外发现并已修复

- **运行模式徽章显示错误**：Java `/health` 未返回 `mock` 字段，导致前端恒显示「真实 LLM」。
  已修复（`HealthController` 合并 Python `/health` 的 `mock`/`model`，Python 不可达时按离线处理）；实测徽章显示「离线演示模式 (mock)」。
