# Proposal

## Why

素材能检索了 ✓，但**查到之后还得自己复制粘贴** ✗ —— 检索和生成之间断了一刀。

用户提的形态：生成时给一个「是否关联本地素材库」的开关 ✓。评估后**该做** ✓，但不能按字面做 ✗：

| 字面做法 | 后果 |
|---|---|
| 勾选 = 把**整个库**传给模型 | 库无限长 ✗；token 爆 ✗；无关素材污染上下文 ✗ |
| 只给"参考一下"的软指令 | 模型会把**别的素材里的事实**搬进来 ✗ → 串题/幻觉 ✓ |

## What Changes

- **检索 top-K**：用当前素材的关键词去库里取 3 条最相关历史素材 ✓
- **参考 ≠ 事实**：库里素材只用于**借鉴角度与表达**；事实仍**只能**来自本次素材 ✓（prompt 里写死）
- **跨时间去重**：把被引用素材的句子加入 `avoid` 清单 ✓（复用 `platform-differentiation` 的机制）
- **可追溯**：成稿的 QA warnings 里列出"参考了哪几条"，用户能核对 ✓
- **默认不勾** ✓：勾上才额外发送 ✗

## Capabilities

### New Capabilities
- `library-context-generation`: 生成时按需引用本地历史素材（检索、注入、隔离事实、可追溯）

## Impact
- **Java**：生成任务参数增 `use_library`；检索复用 `Store.searchMaterials`
- **Python**：`/generate` 增 `library_context`；`run_draft` 的 prompt 增参考块
- **成本**：勾选时每次多 3 条素材的 token（约 1~2k）
