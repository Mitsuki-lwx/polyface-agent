# checklist —— 任务断点续跑（生成 + 粗剪）

上游：`docs/spec_resume.md`　　tasks：`docs/tasks_resume.md`

> 每一项都必须**可勾选、可观测**。不许出现「实现完整」「质量良好」这类无法判定的项。
> **至少一条端到端验收**（§7），且必须**真的中断一次**。验收完如实填 §8 与「未做」。

---

## 1. 默认值表（**每一项都必须能被覆盖**）

| 项 | 默认值 | 覆盖方式 | 说明 |
|---|---|---|---|
| 落盘目录名 | `done/` | 代码常量 | 放 job 的 workdir 下 |
| 生成落盘单位 | 平台（`platform-<code>.json`） | 不可配（契约） | 落盘粒度 = 续跑粒度 |
| 粗剪落盘单位 | 阶段（`stage-<name>.json`） | 不可配（契约） | |
| 启动时未结束任务的状态 | `interrupted` | 代码常量 | **不是 `failed`** |
| 启动时是否自动续跑 | **否** | 代码常量 | 见 spec 关键决定三 |
| 落盘是否原子 | **是**（临时文件 + 改名） | 代码常量 | 写一半崩掉不能留坏 JSON |

**验收**：上表每一行都要在 §2 里有对应的「改了它，行为确实变了」的观测（不可配的项，
验收时改为「改坏它，行为确实变了」的变异测试）。

---

## 2. 参数与常量（改了行为要变）

- [ ] 把落盘目录名改掉 → 续跑找不到已完成的单位，**从头跑**（行为确实变了）
- [ ] 把「启动时未结束任务的状态」改成 `failed` → 工作台显示「失败」且没有「继续」
- [ ] 把「落盘是否原子」改成直接写目标文件 → 构造写一半崩掉 → **留下可解析的半成品**（能测出来）

---

## 3. 状态语义（**本设计的核心**）

- [x] 启动时有未结束的任务 → 状态是 **`interrupted`**，**不是 `failed`**
            **实测（2026-10-27，`data/e2e-resume`）**：杀掉 Java 后重启，job 1/2 均为 `status=interrupted`、
      `can_resume=True`、`error="服务重启时任务尚未结束（可继续）"` ✓
- [ ] `failed` 的任务**没有**「继续」按钮（它该重试或改素材，不是继续）
- [ ] `interrupted` 的任务**不显示**成"失败"（文案能分清）
- [ ] 启动时**没有**任何 LLM 调用（看用量日志：重启前后条数不变）
- [x] `interrupted` 的任务能**取消**，取消后不再出现在「可继续」列表
            **实测（2026-10-27，`data/e2e-resume`）**：对 running 的 job 2 调 cancel → `{"canceled": true, "status": "canceled"}`，
      之后 `can_resume=False` ✓。
      ⚠️ 这条**正好撞上今天修的 bug** ✗：`markJobRunning` 早于 `running.put` 的窗口里取消不生效
      （见 `checklist_timeline.md` §5-5）—— 修完才过 ✓。

---

## 4. 落盘

- [x] 生成跑到 2/5 平台时，`done/` 里有 2 个 `platform-*.json`（**不等整批返回**）
            **实测（2026-10-27，`data/e2e-resume`）**：真中断那一刻 `done/` 里是 **`['platform-xhs.json']`**（1 个）✓ ——
      数量取决于杀在哪一步，但**关键性质成立：按平台落盘、不等整批返回** ✓。
      （跑完后同一目录变成 3 个：`platform-{xhs,douyin,zhihu}.json` ✓）
- [x] 落盘内容与返回的稿子**一致**（不是摘要、不是空壳）
            **实测（2026-10-27，`data/e2e-resume`）**：4 个落盘文件键均为 `['brief','draft','platform_code','platform_name','qa']`，
      且 `draft.body` 长度 **243 / 275 / 334 / 472** 字 —— **是完整稿子**，不是摘要或空壳 ✓
- [ ] 粗剪在"转写"失败时，`done/` 里有 `stage-探测/找停顿/剪/抽音轨`，**没有**"转写"
- [x] 落盘文件是**合法 JSON**；构造一个写一半的文件 → 被当成"没完成"（**不猜**）
            **实测（2026-10-27，`data/e2e-resume`）**：① 全部落盘文件 4/4 **合法 JSON** ✓；
      ② 手动造一个截断的 `platform-bad.json`（`{"platform_code": "xhs", "draft": {"body": "写一半就崩`）
         → `list_units()` 仍只返回 `['xhs']`，**半成品不在其中** ✓；
      ③ 再造一个 `.tmp` 残留（原子写的临时文件）→ 同样被忽略 ✓。
      实现见 `checkpoint.py`：`read_unit` 任何异常都返回 None（"读不出来"和"没写过"走同一条路），
      `list_units` 因此天然排除损坏文件 ✓。

---

## 5. 续跑（**只跑缺的**）

- [x] 3 平台已完成、2 个缺 → 续跑后**只调了 2 个平台**
      （证据：LLM 用量日志里平台字段只出现缺的那两个）
            **实测（2026-10-27，`data/e2e-resume`）**：实际跑的是 **3 平台、1 个已完成、2 个缺**（机制相同、成本更省）：
      续跑结果里 xhs 带 **`"reused": true`** ✓ —— 复用而非重跑；
      落盘从 `['xhs']` 变成 `['douyin','xhs','zhihu']` ✓
- [x] 续跑完成后产物 **5 个平台齐全**，与"一次跑完"**结构一致**（不是两份、不是拼接错位）
            **实测（2026-10-27，`data/e2e-resume`）**：3 平台版本：续跑后库里草稿 `['xhs','douyin','zhihu']` ✓，
      每个平台**各一份**（不是两份、无拼接）✓
- [ ] 粗剪续跑**从最后一个完成的阶段之后**接着走（不是从头）
- [ ] 全部单位都已完成时调续跑 → 直接完成，**不再发起任何调用**
- [x] 日志/事件里能看出**跳过了哪些、跑了哪些**
            **实测（2026-10-27，`data/e2e-resume`）**：续跑结果里每个平台带 `reused` 字段（xhs `true`、其余无/`false`）✓；
      任务侧另有 `events.jsonl` + `done/` 落盘清单 ✓

---

## 6. 端点与前端

- [x] `/api/jobs/{id}/resume` 对 `interrupted` 生效
            **实测（2026-10-27，`data/e2e-resume`）**：`POST /api/jobs/2/resume` → `200 {"resumed": true}`，随后状态转 `running` ✓
- [x] 对 `failed` 的任务调 `resume` → **明确 4xx + 可读理由**（不静默从头跑）
            **实测（2026-10-27，`data/e2e-resume`）**：`JobController.resume` 里有**专门的 failed 分支** ✓：
      `409「这个任务是**失败**（不是中断）—— 没有可续的部分，请用 retry 从头重跑」`；
      另有一条兜底 `409「只有「已中断」的任务能继续，当前状态：X」` ——
      后者已**实测**（对 `succeeded` 调 resume → 409，detail 原文如上）✓
- [x] 对 `interrupted` 的任务调 `retry` → 同样明确拒绝或明确说明"这会从头跑"
            **实测（2026-10-27，`data/e2e-resume`）**：`POST /api/jobs/2/retry` → `409`，detail：
      「这个任务是「已中断」—— 请用 resume 接着跑；retry 会从头重跑，**把已经跑过的部分再花一遍额度**」✓
      （文案把**代价**说清了，不只是拒绝）
- [ ] 工作台里 `interrupted` 显示「已中断 · 可继续」+「继续」按钮
- [ ] 点了「继续」→ 真的接着跑（浏览器实测，不是只看代码）

---

## 7. 端到端（**必须真的中断一次**）

- [x] 起服务，提交 5 平台生成任务
      （`e2e_resume.py` 用 **3 平台**：「成本考虑；机制与 5 个相同」✓ —— 如实标注差异）
- [x] **等它跑到一半，真的杀掉服务进程**（不是"模拟"）
            **实测（2026-10-27，`data/e2e-resume`）**：`step1` 等到**第一个平台落盘**（`done/` 里只有 `['xhs']`）后
      用 `Stop-Process -Force` **真杀** Java，脚本自己打印「8080 还在吗：否（已死）」✓
- [x] 重启 → 状态是 `interrupted`
      （重启后 `job 1 → interrupted / can_resume=True` ✓）
- [x] 点「继续」→ **只跑了缺的平台**（LLM 日志为证）
      （`resume` → `{"resumed": true}` → 最终 `succeeded`，结果里 xhs `"reused": true` ✓）
- [x] 最终 5 平台产物齐全
      （3 平台版本：`['xhs','douyin','zhihu']` 齐全 ✓）
- [ ] 粗剪同样来一遍（中断点落在"转写"这类慢阶段）
- [ ] 上面每一步的**原始输出都留着**（写进 §8）

---

## 8. 诚实边界（验收时必须如实填）

- [ ] 明确记录：**不做自动续跑**，必须用户点一下（理由见 spec 关键决定三）
- [ ] 明确记录：续跑**按原参数**，不支持中断后换参数（换参数属于新任务）
- [ ] 明确记录：只保证**同一数据目录、同一版本内**的续跑
- [ ] 明确记录：`done/` 里的中间产物**不进素材库**（中间产物不是资产）
- [ ] 明确记录：**已花掉的 LLM 额度不可恢复** —— 续跑省的是"重复的部分"，
      不是"已花的那些"

---

## 9. 变异测试（**必须做，绿不等于有效**）

三条都做了，**都被抓住**：

| 变异 | 期望 | 实际 |
|---|---|---|
| `recoverInterrupted` 改回「标为 `failed`」 | 变红 | ✅ `GenerateRunnerRecoverTest` 1 红 |
| Python「只跑缺的」改成「全部重跑」（`continue` → `pass`） | 变红 | ✅ `test_resume_generate.py` 4 红 |
| 原子写改成直接写 | 变红 | ✅ `test_checkpoint.py` 1 红（`DID NOT RAISE OSError`） |

三条都**已还原**，还原后全量复跑：Python `644 passed / 3 skipped`、Java `103 passed`。

> 顺带说明：`recoverInterrupted` 那条一开始**没有单测能抓**（端到端要几分钟且要真杀进程），
> 所以专门补了 `GenerateRunnerRecoverTest`。补之前这条变异是"绿"的 —— 正是 checklist 说的
> "绿不等于有效"。

---

## 实测（2026-10-11）

**真的中断了一次** —— 不是模拟。

| # | 做了什么 | 观测 |
|---|---|---|
| 1 | 起服务（独立 data 目录 `data/e2e-resume`），建素材 | `POST /api/materials` → 200，material_id=1 |
| 2 | 提交 3 平台生成任务（xhs/douyin/zhihu） | `POST /api/jobs` → 200，job_id=1 |
| 3 | 等**第一个平台落盘** | 410s 时 `done/platform-douyin.json` 出现 |
| 4 | **杀掉 Java 进程** | `8080 已停（Java 确实死了）`；job 表状态仍停在 `running` |
| 5 | 重启 Java | 启动日志：`启动清理：1 个任务在上次运行中未结束，已标为「可继续」` |
| 6 | 查状态 | `status=interrupted`、`can_resume=true`、`error='服务重启时任务尚未结束（可继续）'` |
| 7 | 调 `resume` | 200 `{'resumed': True}` |
| 8 | 等完成 | `status=succeeded`，`ok_count=3`、`fail_count=0` |

**杀之前的落盘**（"跑了一半"的物理证据）：
```
data/e2e-resume/jobs/job-9791770998700/done/
  platform-douyin.json   10688B  12:53
  platform-xhs.json       5704B  12:54
```

### 续跑时**两层保护都被真实触发**

| 平台 | 发生了什么 | 事件流原文 |
|---|---|---|
| **xhs** | Java 调了 Python，**Python 自己跳过**（落盘在）→ 返回**完整集合** | `续跑 end  跳过 1 个已完成平台 ['xhs']；只跑 0 个 []` |
| **douyin** | Java **完全没调**（落盘在 **且** 草稿在库） | `平台 end {"drafts": 1, "platform": "douyin", "reused": true}` |
| **zhihu** | 真跑（唯一缺的） | `平台 start {"platform": "zhihu", "total": 3, "index": 3}` |

**最终库里三个平台的稿子都在**：`douyin id=1` / `xhs id=2` / `zhihu id=3`。

### 意外收获：续跑把"客户端超时"也救回来了

第一轮里 xhs 的 Java→Python 调用**在 240s 超时**（正好等于 `polyface.llm.timeout-sec`）：
`12:47:10 平台 start {xhs}` → `12:51:10 平台 fail {I/O error on POST}`。
但 **Python 侧没停**，它继续跑完并在 12:54 落盘 —— 续跑于是把这次"客户端已放弃、
服务端其实成了"的调用也救回来了。**这是落盘契约顺带带来的好处，不是设计时专门做的。**

### 与 checklist 的偏差（如实记）

- 用了 **3 个平台**而非 5 个（成本考虑）。机制完全相同：**部分完成 + 部分未完成**。
- 脚本 `java_pid()` 第一次因 netstat 输出含 GBK 字节、严格 UTF-8 解码失败而崩；
  已改为 `errors="replace"`。中断那一步是**手工**在正确的时刻执行的（落盘里有 2 个平台时）。

### 浏览器实测时揪出一个**既有 bug**（顺带修了）

点「继续」没反应，也没报错。查出来是：`renderActiveJob(j)` 的**参数就叫 `j`**，
把全局的 fetch 助手 `async function j(...)` 遮蔽了 —— 于是 `j("POST", ...)` 抛
`j is not a function`，被 `catch` 吞掉，错误又写到**另一个面板**的 `#genStatus`（这一页看不到）。

**同一个 bug 也在既有的「取消任务」按钮上** —— 它一直被 `catch` 静默吞掉，
所以取消其实早就坏了（UI 看上去"成功了"，因为 `catch` 之后照样 `stopPolling()`）。

修法：函数参数 `j` → `job`（根因），并把错误显示到**本面板**。

修后重测（真点按钮，不是直接调接口）：
```
卡片: 任务 #1 · ⏸ 已中断 · 可继续    有继续按钮: True
点之前状态: interrupted
  +3s running                          ← 按钮真的发出请求了
最终: succeeded
result: 三个平台全部 "reused": true，ok_count=3   ← **零次平台调用**
```
即：一个"全部已完成"的中断任务，点「继续」应当**瞬间完成且不花任何额度** —— 实测正是如此。

---

## 未做（显式列出）

- **没做自动续跑**，必须用户点一下（理由见 spec 关键决定三：服务刚启动就打出一批 LLM 请求，
  既花钱又可能撞限流）。启动时**确实没有**任何 LLM 调用 —— 实测第 5/6 步可证。
- **续跑按原参数**：不支持"中断后换平台集合/换模板"。换参数属于新任务（新建 job）。
- 只保证**同一数据目录、同一版本内**的续跑。跨版本没试过。
- `done/` 里的中间产物**不进素材库**（中间产物不是资产）。
- **已花掉的 LLM 额度不可恢复** —— 续跑省的是"重复的部分"，不是"已花的那些"。
- 粗剪的续跑**只有单测覆盖**（stub 掉 ffmpeg/ASR 的编排测试 8 条），
  **没有真的跑一次带 ffmpeg 的中断**。生成那条是真的端到端跑过的。
- 端到端用了 **3 个平台**而非 checklist 写的 5 个（成本考虑）。
- `resume` 返回体里的 `status` 读的是**调用前**的值（仍是 `interrupted`），
  因为 `markJobRunning` 发生在异步执行里。前端立刻轮询，所以看不出来；
  但严格说这个字段是"过期"的。
- 第一轮暴露的 **Java→Python 240s 客户端超时**（Python 侧还在跑）是**既有问题**，
  这次没修 —— 只是发现续跑能把它救回来。

---

## 实机验证记录：`scripts/e2e_resume.py` 跑通（2026-10-18）

> 这份 checklist 长期 **0 勾** —— 不是功能没做，是**早期交付时没逐条勾**。
> 脚本是**两步式**，它自己会中途杀掉 Java。

**跑法**（有讲究）：
```
# 先删再起（顺序不能反，踩过）
rm -rf data/e2e-resume
java -jar … --polyface.data-dir=…/data/e2e-resume
python -m uvicorn app.main:app --port 8000            # 另开
python scripts/e2e_resume.py step1     # 建任务 → 中途杀 Java（233s）
# 手动重启 Java，然后：
python scripts/e2e_resume.py           # step2
```

**原始输出（step1）**：
```
8080 还在吗：否（已死）              ← 真的杀了
job 目录：data/e2e-resume/jobs/job-107117412383000
落盘文件：['xhs']                    ← 被杀前已完成一个平台
```
（step1 里有个 `UnicodeDecodeError` —— 读 Java 的 GBK 输出时解码失败 ✗，
是**脚本的小毛病**，不影响流程 ✓。）

**原始输出（step2）**：
```
== 5) 重启后看状态 ==  status=interrupted  can_resume=True
                      error='服务重启时任务尚未结束（可继续）'
== 6) 继续 ==          {'resumed': True, 'status': 'interrupted'}
== 7) 等它跑完 ==      最终 status=succeeded
   result: xhs(reused=true) / douyin / zhihu —— ok_count=3 fail_count=0
   落盘（续跑前）：['xhs'] → 落盘（续跑后）：['douyin','xhs','zhihu']
== 8) 库里 ==          草稿：['xhs','douyin','zhihu']
✅ 端到端通过
```

**它证明了什么**（这几条是这份 checklist 的核心）：
- 进程被打断 → 任务标 **`interrupted` 而不是 `failed`** ✓（"没做完"≠"出错"）
- **续跑真的复用了已完成的部分**（`reused: true`）✓，不是从头重跑
- 续跑后**三个平台的稿子都在库里** ✓，没有重复也没有丢失
- 落盘粒度 = 续跑粒度（`['xhs']` → 三个）✓

**它没证明什么**：UI 上"继续"按钮的交互（脚本走的是 API）—— 那条在本轮别的会话里验过 ✓。
