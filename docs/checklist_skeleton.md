# checklist —— 骨架式改写（段落级骨架 + 槽位回填）

对 `docs/spec_skeleton.md` 的可勾选验收。**一行一条，可独立判定。**

---

## 1. 契约与解析（T1）

- [x] `DraftSkeleton` 模型：paras 至少 1 段、每段 text 非空、facts 为正整数数组（`test_parse_skeleton_drops_garbage`）
- [x] 标准槽位 `{{n:3|286块}}` 解析出 (n=3, 表层="286块")（`test_parse_skeleton_standard_and_tolerant_forms`）
- [x] 宽容变形 `{{3|286块}}`、全角 `｛｛3|286块｝｝` 同样解析（同上）
- [x] 坏形（`{{n:}}`、`{{x|y}}`、未闭合）**不当作槽位** → 留作裸文本，由残留检查接住（`test_parse_skeleton_bad_form_left_as_text`）

## 2. 接地与机械门（T2/T3）

- [x] G1：槽位序号越界（n > 事实数）→ 硬违规（`test_g1_slot_out_of_range`）
- [x] G1：槽位序号 ∉ 所在段声明 facts → 硬违规（`test_g1_slot_not_in_para_declaration`）
- [x] G1：段声明本身含越界序号 → 硬违规，且不进 facts_used（`test_g1_declaration_out_of_range`）
- [x] G2：表层是 `facts[n].text` 子串 → 通过（`test_g2_substring_passes`）
- [x] G2：表层数值与 `facts[n].text` 数值等价（素材「两百八十六」表层「286」；「第1步」↔「第一步」）→ 通过（`test_g2_numeric_equivalence_passes`）
- [x] G2：表层为 `facts[n].text` 命中的专名 → 通过（`test_g2_proper_noun_passes`）
- [x] G2：表层数值在该条事实里**找不到**（真幻觉）→ 硬违规（`test_g2_true_hallucination_rejected`）
- [x] G3：段内裸数字的数值 ∉ 声明事实 ∪ 已验证表层 → 硬违规（反例 `199`，`test_g3_bare_number_rejected`）
- [x] G3：改写等价数字（素材「三步」→ 正文「3步」）→ **不**违规（`test_g3_equivalent_rewrite_passes`）
- [x] G3：证据为空的段不是跳过检查 —— 里面的数字全部按无依据报（`test_g3_no_declaration_means_no_numbers`）
- [x] G3：疑似未声明专名 → 软违规（warning，不拦）；证据侧抽不到专名则跳过（同 `_rule_qa` 宁可不查）（`test_g3_proper_noun_suspicion_is_soft`）
- [x] G3：`迈出这一步` 类中文序数 → **不**违规（`test_g3_chinese_ordinal_no_false_positive`）
- [x] G3：年份同口径拦截（`test_g3_year_gate`）
- [x] G3：用户模板/画像避雷走 `extra_evidence` 豁免（`test_g3_extra_evidence_exempts_user_template`）
- [x] G4：0 段 / 空标题 → 硬违规（`test_g4_no_paras_no_titles`）
- [x] 回填后正文/标题/免检字段残留任何 `{{` → 违规（正文/标题=硬；封面建议/互动语=软+尽力回填）
      （`test_parse_skeleton_bad_form_left_as_text` + `test_cover_suggestion_slots_backfilled`）
- [x] facts_used = 各段声明并集（代码计算）；模型自报字段被忽略（`test_facts_used_union_not_first_para` / `test_run_draft_ignores_model_declared_facts_used`）
- [x] 免检字段（封面建议/互动语）的槽位**尽力回填**——真模型实测把槽位写进封面建议，裸占位符不能发布（`test_cover_suggestion_slots_backfilled`）

## 3. 提示词与流程（T4/T5/T6）

- [x] 真实模式：draft 调用产出骨架 → 装配 → `DraftPayload`（实测 m01×xhs：body 无槽位残留，218.7s 全链路）
- [x] 标题里的数字走槽位，证据集合 = 全部事实清单（`test_title_slots_use_all_facts_as_evidence`）
- [x] 硬违规 → 带违规清单修复一轮；修复通过则正常继续（`test_run_draft_repairs_gate_then_raises` 前半）
- [x] 修复后仍硬违规 → 抛 `SkeletonGateError`（error 含违规明细），由 `generate()` 既有失败路径按平台记录、其余平台成果保留（同上后半；FR-70 机制复用）
- [x] mock 模式：`_mock_skeleton` 构造骨架走**同一个装配器**；body 与改前 mock **逐字节一致**
      （`test_mock_body_byte_identical_to_old` × 全语料 + `..._with_template_and_profile`）
- [x] mock 对**评测语料全集**（10 条）装配硬违规恒为零（`test_mock_gate_zero_hard_on_corpus`）
- [x] 数字截断保护：标题里每个数字 token 都是 core_message 的完整子串（`test_mock_titles_never_contain_partial_number`）
- [x] scene 名仍为 `draft`；版本号按 ADR-024 自动变化（`SCENE_BUILDERS["draft"]` 指向新构造函数，`test_promptver` 全绿）
- [x] QA 阶段（含两阶段/verifier）行为不变；`facts_used` 从此由代码产出（`test_rule_qa_*` 全部原样通过）

## 4. 回归与变异（T7/T8）

- [x] Python 全量 pytest：**925 passed, 4 skipped**（改前 918+7 失败 → 7 个绑定旧契约的测试已按新契约重写）
- [x] Java 无改动（`git status` 仅 Python/docs 变更；Java 测试既有全绿不受影响）
- [x] 既有 e2e（mock 模式，走新骨架路径）：`e2e_async_generate` **28/28**（API + 真浏览器，E2E_EXIT=0）；
      `e2e_partial` **全 PASS**（FR-70 响应契约，E2E_EXIT=0）
- [x] 变异 1：废掉 G3 裸数字拦截 → **3 个测试变红**（还原复绿）
- [x] 变异 2：facts_used 并集只取首段 → **2 个测试变红**（还原复绿）

## 5. 真模型验证（T9/T10）

- [x] smoke：m01×xhs 真 Key 直跑 `generate()` 全链通（骨架→装配→QA→定向重写，218.7s，
      QA 抓出 2 条真·新增断言 —— 门与 QA 都在实际干活）
- [x] 全量评测 5×10（`eval/reports/skeleton-v1`，2026-10-26 03:4x 完成）——
      **结果喜忧参半，如实记录**：

| 指标 | 旧 t67 | 新 skeleton-v1 | 判定 |
|---|---|---|---|
| 总通过率 | 31/50 = **62%** | 25/50 = **50%** | ✗ 下降 12 点 |
| **A1 数字有依据失败** | 9 | **0** | ✓ **机制性清零（本任务核心目标）** |
| A2 年份失败 | 0 | 0 | ✓ |
| A9 专名失败 | 2 | **0** | ✓ |
| A7 质量门失败 | 13 | **21** | ✗ 恶化 |
| skeleton_gate 显式失败 | — | 4 格（模型两轮修不好门→显式失败，FR-70 语义生效） | ✓ 机制生效 |
| 耗时 | 37min | **185min** | ✗ ×5（修复/重写轮频繁：draft 均调 ~4.7 次/平台） |
| 分平台 | xhs7/dy6/gzh4/zh6/bili8 | xhs**8**/dy**8**/gzh3/zh**1**/bili5 | xhs/dy 升；**zhihu 崩**（6→1）、bili 降 |

- [x] A7 恶化的**形态归因**（21 条失败理由逐条归类）：结尾/建议清单类 4、
      新增因果/总结类 7、其它新增断言 9、超出声明字面 1 ——
      全是「方法论/结论式」越界推断（"这件事让我意识到…""路径的边界是…"），
      即 docs/76 §0 的核心病。**段落级骨架没治住它，反而更多**：
      ① 挤出效应（数字被锁死 → 模型转向加非数字的"干货感"内容）；
      ② QA 拿到**代码计算的 facts_used** 后「超出声明」判定从空转变真咬
      （旧基线模型自报全量，判定基本不触发）—— 这部分是**检出变诚实**，不是产物变差。
      两者占比无法精确分离（诚实边界）。
- [x] ≥3 篇新旧正文对照：`docs/77-骨架改写-新旧对照样本.md`（5 组）
      —— **作者人工判读"是否还像人写的"未做**，等判读（见下）
- [ ] **作者人工判读**：读 `docs/77` 后给出"更好/持平/变差"结论（可以是"变差"）

## 6. 诚实边界（验收时如实填）

- [x] 段内非数字类新增断言（"漏交了"类、方法论总结类）**机制未消除，仍靠 QA 拦** ——
      spec §7 预测了"仍靠 QA"，**没预测到会变多**（A7 13→21）；实测 QA 确实拦到了，
      但重写轮修不动它（重写后仍判红 → 该格失败）
- [x] 总通过率下降 12 点、耗时 ×5 —— 本版**不达验收的"预期下降"项**，
      机制目标（A1/A9 清零、门显式失败）全部达成、头条指标恶化，取舍摆在桌面上
- [ ] 改写质量（人味）最终判定权在作者（docs/77 待判读）；机械断言不构成质量证明
- [x] 修复轮触发率高（draft 均调 ~4.7 次/平台）—— 模型（deepseek-v4.1-flash）
      对槽位契约的服从度有限；4 格两轮修不好直接显式失败
- [x] zhihu 1/10 的崩塌未单独归因（可能含单素材噪声，未深挖）
- [x] 未做：句级骨架 / 标题段落绑定 / tags 槽位化 / NLI（spec §7）
- [x] 本轮评测运行时 Langfuse 不可达（WSL 未起）→ 该轮无 trace（报告结论不依赖）；
      `usage_path()` 默认 `../data` **相对 CWD** —— 从仓库根启动的运行把用量写到了
      仓库外一层 `D:\media works move\data\`（既有缺陷，与本次改动无关，待修）；
      Errno 22 偶发记录失败同源（换目录首写时目录未建）
