# 验收清单（checklist）：事实确认与稿件编辑闭环

- 文档类型：checklist（事前三件套之三）
- 版本：v1.0 · 日期：2026-09-15
- 配套：`39-事实确认与稿件编辑闭环-task.md`、`40-事实确认与稿件编辑闭环-spec.md`
- 判定方式：每条独立判定，全部 `[x]` 方可交付

---

## A. 事实确认

- [x] A1 `material` 表新增 `facts_confirmed` / `facts_confirmed_at`（幂等迁移）
- [x] A2 `PUT /api/materials/{id}/facts` 可全量保存事实
- [x] A3 `confirm=true` 时置 `facts_confirmed=1` 并记录时间
- [x] A4 空 `text` 的事实被拒（400）
- [x] A5 非法 `type` 被拒（400）
- [x] A6 前端事实清单可**增**（添加一条）
- [x] A7 前端事实清单可**改**（修改文本）
- [x] A8 前端事实清单可**删**
- [x] A9 前端有「保存并确认」入口，确认后显示 ✅ 与时间

## B. 生成复用已确认事实（省一次调用）

- [x] B1 已确认时，Java 请求体**含** `confirmed_facts`
- [x] B2 未确认时，Java 请求体**不含** `confirmed_facts`（向后兼容）
- [x] B3 Python 收到 `confirmed_facts` 时**跳过 understand**
- [x] B4 跳过时记录明确日志（可断言）
- [x] B5 跳过时**不产生** understand 的 LLM 调用（usage 可验证）
- [x] B6 未传时仍走原 understand 路径
- [x] B7 使用已确认事实时，QA 的事实校验**照常执行**

## C. 稿件编辑与保存

- [x] C1 `draft` 表新增 `edited_at`（幂等迁移）
- [x] C2 `PUT /api/drafts/{id}` 可更新 titles/body/tags/interaction_line/cover_suggestion
- [x] C3 保存后 `edited_at` 非空
- [x] C4 通过该接口**无法**修改 `qa`（防绕过质检）
- [x] C5 前端正文可编辑
- [x] C6 前端标题、标签可编辑
- [x] C7 前端有「保存修改」，保存后显示「人工编辑于 …」

## D. 导出

- [x] D1 `GET /api/drafts/{id}/export?format=md` 返回 Markdown
- [x] D2 `format=txt` 返回纯文本
- [x] D3 导出内容 = **当前生效**版本（含人工编辑）
- [x] D4 响应头含正确 `Content-Type` 与 `Content-Disposition`
- [x] D5 前端提供 .md / .txt 导出按钮

## E. 历史稿完整性（修复正文为空）

- [x] E1 前端点击历史稿时**补读** `GET /api/drafts/{id}`
- [x] E2 历史稿正文**完整显示**（浏览器实测）
- [x] E3 `GET /api/drafts/{id}` 返回完整 payload + qa

## F. 兼容性与回归

- [x] F1 未确认事实时行为与改动前**完全一致**
- [x] F2 旧数据（无新列）自动迁移，不丢数据
- [x] F3 既有 API 响应只增字段、不改既有语义
- [x] F4 既有 Python 测试全绿（60 项）
- [x] F5 既有 Java 测试全绿（28 项）

## G. 端到端验证

- [x] G1 mock 全链路：建素材 → 改事实 → 确认 → 生成（断言日志含跳过 understand）
- [x] G2 **浏览器实测**：事实增删改保存
- [x] G3 **浏览器实测**：稿件编辑 + 保存 + 导出
- [x] G4 **浏览器实测**：历史稿正文完整
- [ ] G5 真实链路端到端 —— **未做**：上游额度紧张（单次 100~250s + 频繁 429）；已用 mock 覆盖全链路，真实额度节省未实测

## H. 交付

- [x] H1 `docs/42-事实确认与稿件编辑闭环-交付说明.md` 产出
- [x] H2 交付说明含**实测证据**（日志 / 接口返回 / 截图）
- [x] H3 未验证部分显式标注
- [x] H4 代码与文档已提交，工作区干净（`.env` 未入库）
- [x] H5 未越界（未顺手改超时 / 启动包 / 自动成片）

---

## 验收结论

| 项 | 结果 | 备注 |
|---|---|---|
| A~H 勾选 | ✅ 完成（G5 除外） | 2026-09-15 |
| 跳过 understand | ✅ 日志实测： | |
| 端到端 | ✅ 18/18 | |
| 浏览器实测 | ✅ 事实编辑/确认、稿件编辑/保存、历史稿完整性 | DOM 证据 + 截图 |
| 回归 | ✅ Java 39 / Python 64 全绿 | |
| **真实链路** | ❌ **未做**（G5） | 额度限制，已在交付说明 §6 标注 |
| 未通过项 | G5 | |
