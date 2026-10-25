# tasks —— 骨架式改写（段落级骨架 + 槽位回填）

对应 spec：`docs/spec_skeleton.md`　　决策：ADR-025　　上游：`docs/76`

---

- **T1 契约**：`DraftSkeleton` pydantic 模型（paras[{facts,role,text}] + titles/tags/…）；
  槽位正则与解析（含宽容变形）
- **T2 接地规则**：槽位表层有依据的判定（子串 / `num_values` 数值等价 / `proper` 专名），
  口径与 `_rule_qa` 同源
- **T3 装配器**：`assemble_skeleton` 纯函数 —— 回填、G1~G4 机械门、
  facts_used 并集计算、软/硬违规分级、残留 `{{` 检查
- **T4 提示词**：`DRAFT_SYSTEM` 改为骨架契约说明；`build_draft_prompt` 携带事实清单
  （带序号，同现状）+ 槽位规则 + （修复轮时）违规清单；`SCENE_BUILDERS["draft"]` 指向新构造函数
- **T5 mock 同路径**：`_mock_draft` 改为构造骨架（text=事实原句）→ 走装配器；
  mock titles 若含事实清单外数字，改走槽位
- **T6 修复轮**：`run_draft` 真实模式内 —— 装配硬违规 → 带清单重调一轮 →
  仍硬违规 → 抛出显式失败（`skeleton_gate`），由 `_generate_one` 的既有失败路径接住
- **T7 单测**：解析（正/变形/坏形）、回填、G1~G4 各自的反例、修复轮、
  facts_used 并集、mock 装配不变量（body 与旧 mock 逐字节一致）；
  全量 pytest + 既有 Java 测试无退化
- **T8 变异测试**：≥2 次注入（候选：去掉 G3 裸数字拦截；facts_used 并集写成首段声明）
- **T9 真模型验证**：先 smoke（1 素材 × 5 平台，真 Key）；后全量评测
  5 平台 × 10 素材，`--baseline eval/reports/factguard-t67/report.json` 对比；
  指纹必然不同 —— 这是**有意的 A/B**，结论记录在 checklist
- **T10 人工判读**：抽 ≥3 篇新旧正文对照（同素材同平台），附于交付说明，
  **由作者判读"读起来是否还像人写的"** —— 机械断言测不出人味（`docs/76` §1 的主要风险）
- **T11 收口**：勾 checklist、交付说明、ADR-025 落 `docs/08`
