# tasks —— 提示词版本化

对应 spec：`docs/spec_promptver.md`　　验收：`docs/checklist_promptver.md`

---

## T1　提示词注册表 + 源码哈希

- **影响文件**：`python-service/app/pipeline/prompts.py`、`python-service/tests/test_promptver.py`（新）
- **依赖**：无
- **做什么**：
  - 把已有的 `build_*` 构造函数**注册**成一张表（名字 → 函数）
  - `fingerprint(name)`：取该函数的**源码**（`inspect.getsource`）→ SHA-256 前 12 位 → 缓存
  - 取不到源码（打包/动态）→ 返回 `""` 并**标注**，不抛错
- **参考**：`prompts.py` 里已有的 `build_understand_prompt` / `build_draft_prompt` / … 命名一致
- **验收**：改一个字符 → 哈希变；改别的函数 → 该函数哈希不变

---

## T2　按 scene 查哈希

- **影响文件**：`prompts.py`、`tests/test_promptver.py`
- **依赖**：T1
- **做什么**：`fingerprint_for_scene(scene)` —— `chat_json(scene="draft")` 用的那些名字
  （`understand` / `brief` / `draft` / `qa` / `clip` / `learn` / `split` / `disfluency` …）
  映射到对应的构造函数；**查不到就返回空**（不抛错）
- **关键**：映射表要**穷举现有 scene**，漏一个就等于那个提示词没版本
- **验收**：每个现有 scene 都能查到非空哈希；未知 scene 返回空且不抛错

---

## T3　记录进用量（一处改动覆盖全部调用）

- **影响文件**：`python-service/app/usage.py`、`python-service/app/llm.py`
- **依赖**：T2
- **做什么**：`chat_json` 写用量记录时，多写两个字段：
  - `prompt_fp`：提示词版本（哈希）
  - `prompt_chars`：模板长度（便于一眼看出"改大了还是改小了"）
- **关键**：**只在 `chat_json` 里改一处** —— 不改各管线的业务代码（spec 关键决定三）
- **参考**：`usage.py` 现有的记录字段与写入路径
- **验收**：跑一次真实调用 → `llm-usage.jsonl` 里那条有 `prompt_fp`；旧记录没有该字段也不报错

---

## T4　存模板全文（内容寻址）

- **影响文件**：`python-service/app/pipeline/promptstore.py`（新）、`llm.py`
- **依赖**：T3
- **做什么**：把"哈希 → 模板全文"落到 `data/prompt-templates/<fp>.txt`：
  - 只在**该哈希不存在时**写（同版本只存一份）
  - 写失败**不影响调用**（与观测的既有约定一致：观测失败绝不阻断业务）
- **为什么**：哈希不可读，半年后看到哈希得能查出当时那份提示词长什么样
- **验收**：调用后能按哈希找到全文；重复调用不重复写；写失败不抛错

---

## T5　反查工具

- **影响文件**：`scripts/prompt_versions.py`（新）
- **依赖**：T4
- **做什么**：一个小 CLI：
  - `--list`：列出用过的所有版本（哈希 + 首次使用时间 + 调用次数）
  - `--show <fp>`：打印该版本的模板全文
  - `--diff <fp1> <fp2>`：两份模板的差异（改了哪几行）
- **为什么**：没有反查，记录就只是"多了一列看不懂的东西"
- **验收**：`--list` 能列出真实用过的版本；`--show` 打出全文；`--diff` 能看出差异

---

## T6　端到端验证

- **影响文件**：无（记录写进 `docs/checklist_promptver.md`）
- **依赖**：T5
- **做什么**：
  1. 跑一次真实生成 → 记录里出现 `prompt_fp`
  2. **故意改一处提示词文案** → 再跑一次 → `prompt_fp` **变了**，且 `--diff` 能看出改了什么
  3. 还原提示词 → 再跑 → `prompt_fp` **变回原值**（确定性）
- **验收**：三步的原始输出贴进 checklist

---

## T7　文档与决策落档

- **影响文件**：`docs/08`（新 ADR）、`docs/71`（M9 状态）、`README.md`（若提到 M9）
- **依赖**：T6
- **做什么**：ADR 记录"版本号 = 构造函数源码哈希"这个决定与两个备选为何被否；
  `docs/71` 的 M9 行改为"已完成"；`grep` 确认无过期说法
- **验收**：`grep -rn "M9" docs/71*.md` 状态与实现一致
