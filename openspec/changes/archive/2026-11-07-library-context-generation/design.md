# Design

## Context

- 检索能力已就绪：`Store.searchMaterials(q, tags)`（`LIKE` 全文 + 关键词交集）✓
- 生成链路：Java `GenerateRunner` → `PythonClient.generate` → Python `/generate` → `generate()` → `_generate_one` → `run_draft`
- 去重机制已就绪：`diffcheck` + `avoid` 句子清单 ✓
- 事实核对已就绪：factguard（数字/年份/专名/新增断言）✓

## Decisions

### D1：检索放在 Java（库在这边），注入放在 Python（prompt 在这边）

Java 拿着 `use_library` + 当前素材的关键词 → `searchMaterials` 取 3 条 → 拼成
`library_context: [{id, title, snippet, keywords}]` 随请求下发 ✓。
Python 不碰数据库，只负责把它渲染进 prompt ✗ 不重新检索（两处检索口径必然不一致 ✗）。

### D2：每条的 `snippet` 只截 200 字

参考素材的**价值在角度**，不在全文 ✓。截断让 prompt 体积可预测，也降低"整段事实被搬走"的机会 ✓。

### D3：事实隔离靠 prompt + 质检双保险

prompt 写死"事实只能来自本次素材"；质检的 factguard 本来就会对**本次素材里没有的数字/专名**报错 ✓ ——
所以**不新增检查**，只复用 ✓。被引用素材**不进入**事实清单（`facts`）✗ 关键。

### D4：`avoid` 里放参考素材的句子

跨时间重复和跨平台重复是同一个病 ✓ → 复用同一张清单 ✓。上限沿用 `MAX_AVOID_SENTENCES`。

### D5：默认关闭 + 记录

不勾时请求字段**不出现**（不是 `false`）✓ —— 这样"未勾选 = 与改动前一致"可以被逐字段对照验证 ✓。

## Risks / Trade-offs

- [相关性检索质量] 关键词检索可能取到不相干的 → 界面上直接展示"参考了哪三条"，用户看得见就能判断 ✓
- [模型仍可能抄事实] → factguard 会抓 ✓；抓不到的语义级断言本来就有既有局限 ✗（如实记录）
- [token 成本] +1~2k/次，只在勾选时 ✗

## Migration Plan

无数据迁移。回退 = 去掉字段与 prompt 块。
