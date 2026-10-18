# 早期 checklist 怎么收口（2026-10-17 调研）

## 现状

`docs/` 下有 **6 份从未勾过任何一项**的 checklist，共 **248 项**：

| checklist | 未勾 |
|---|---|
| `checklist_b1.md` | 43 |
| `checklist_eval.md` | 56 |
| `checklist_factguard.md` | 43 |
| `checklist_resume.md` | 34 |
| `checklist_timeline.md` | 36 |
| `checklist_tts_plugin.md` | 36 |

格式与后来的 checklist **完全一致**（都是 `- [ ]`）—— 所以不是格式问题，
是**早期会话交付时没有逐条勾**。

**但这不等于功能没做**：本轮的会话里就实际用过它们 ——
续跑（`resume` 成功续上 ✓）、时间线渲染（`render_timeline` 真 ffmpeg 出片 ✓）、
B1 图文成片 ✓ 都跑通过。

## 收口方式：**跑现成的 e2e 脚本**，别手工重验

仓库里已经有 **18 个 `scripts/e2e_*.py`**，其中这些直接对应上面几份：

| 脚本 | 对应 checklist |
|---|---|
| `e2e_resume.py` | `checklist_resume.md` |
| `e2e_timeline.py` | `checklist_timeline.md` |
| `e2e_b1.py` | `checklist_b1.md`（B1 图文成片） |
| `e2e_roughcut.py` / `e2e_roughcut_jobs.py` | 已勾过的那两份 |
| `e2e_async_generate.py` | `checklist_async_generate.md` |
| `e2e_fr51.py` / `e2e_fr63.py` / `e2e_fr70.py` | 入料 / 示例学习 / 重试降级链 |

这些脚本**真的跑真链路**（真 HTTP、真 ffmpeg、真任务），并逐条打印 `check …` ——
跑一遍就等于把对应 checklist 验了一遍。

## ⚠️ 跑之前必须知道的一件事（实测踩到）

**脚本假定"服务是它自己起的、数据目录是它自己的"** ✗。

实测：我把 `e2e_timeline.py` 接到**我手动起的实例**（`--polyface.data-dir=data/e2e-loop`）上跑，
步骤 1~5 **全过**（含一次真 `render_timeline`，5.1s succeeded ✓），
但步骤 6 崩了：

```
new_mp4 = ROOT / "data" / "e2e-timeline" / "media" / news[0]["rel_path"]   ← 写死的
ValueError: could not convert string to float: ''
```

**脚本写死了 `data/e2e-timeline`** ✗，而实际产物在 `data/e2e-loop` 下 —— 找错目录 → 空文件 → 崩。

**这不是产品缺陷** ✓（那一步的任务是真成功的 ✓），是**脚本与运行环境不匹配** ✗。

**正确跑法**（二选一）：
1. **让脚本自己起服务**（它原本的设计）—— 数据目录就会是它写死的那个；
2. 或者先把脚本里写死的数据目录**改成实际用的那个**再跑。

## 建议顺序

1. 先跑 `e2e_resume.py` 与 `e2e_timeline.py`（这两份 checklist 各 34/36 项，且脚本最成熟）
2. 再跑 `e2e_b1.py`（43 项）
3. `eval` / `factguard` / `tts_plugin` 三份**没有对应脚本** ✗ —— 要么补脚本，要么逐条手工验

**不要**为了"把勾打满"而逐条手工过 —— 那是**重复劳动**，而且手工过的证据留不下来。
脚本跑一遍，把输出贴进 checklist，才是可复核的收口。
