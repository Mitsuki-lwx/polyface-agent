# Polyface 当前状态（写给下一次会话 / compaction 之后）

> 本文件是**持久记录**：compaction 会丢对话，但不会丢这个文件。
> 最近更新：2026-11-09

## 一、项目是什么

本地 Web 工具：一份原创素材 → 多平台可发布稿。Java 21（编排/存储）+ Python 3.13（LLM/媒体）+ 单文件 SPA。

## 二、已经做完的能力（8 个 spec）

| 能力 | 一句话 |
|---|---|
| `local-service-guard` | 本地服务准入（Host/Origin 闸门）|
| `service-crash-forensics` | 崩溃留证据（faulthandler + 退出码 + 看门狗式上报）|
| `platform-differentiation` | 多平台差异化（重合检查 + 篇幅窗口 + 格式必备项）|
| `material-index-and-reuse` | 素材关键词与检索（`tags_json` + `LIKE` 全文；**FTS5 中文不可用**）|
| `image-as-material-source` | 图片 → 视觉描述 → 当素材 |
| `library-context-generation` | 生成时按需引用历史素材（检索 top-3，参考≠事实）|
| `video-frame-understanding` | 视频抽关键帧 + 视觉描述 + 质量评分 |
| `reference-video-analysis` | 别人的视频 → 结构/节奏/钩子分析 |
| `pause-review` | 停顿该剪还是该留（模型只投票，默认关）|
| `pause-prosody` | 停顿前后的能量/音高（本地算，只发数字；实测能翻转判定）|
| `live-platform-progress` | 逐平台状态（失败原因内联、点格切稿）；稿件 tab 按平台去重+版本下拉；单平台重试 |

## 三、测试基线（改完必须复跑）

- Python：`python-service/.venv/Scripts/python.exe -m pytest -q` → **1110 passed / 3 skipped**
  （真实模型用例偶发因网关 `Connection error` 变红，单独重跑即绿 —— 不是回归）
- Java：`bash scripts/mvn.sh -B -f java-backend/pom.xml test` → **160 passed**
- 改了 `index.html` **必须** `package -DskipTests`（静态资源从 jar 内读）

## 四、下一步（按优先级）

0. **骨架拆步实验**：**已否决**（两轮子集评测通过率 0%→10%，耗时更长）。
   开关 `llm_draft_split` 保留但默认关；结论见 `docs/79` §5~§6 与
   `openspec/changes/archive/2026-11-10-draft-split-eval`。
   提速方向已改为**交互**：`live-platform-progress` 已做完（真机验证 pending→running→done）
1. **停顿复核 + 声学特征的两处欠账** ✗：
   · 判定理由与 prosody 数字还没在界面显示
   ① 界面上看不到模型给的理由（只有剪点）
   ② 勾选后从界面**点发起**的完整点击流未实测（用了直接 POST 同一参数）
   ③ 效果数据没有 —— 不知道它救回多少误剪、多剪了多少
2. **③ 参考视频分析的界面** ✓ —— 已补：素材库视频行的「📖 参考分析」→ 后期卡片渲染
   （`POST /api/reference/analyze-asset`，按资产 id 解析路径）。见 `docs/76` 第八节。
3. **前端设计** ✗ —— 停在**等用户给 1~2 张参考截图**。
   已改：38px 宋体报头、4 标签页、780px 稿纸、下划线输入框、平台标签、质量标签。用户说"还没到高级感"。
4. （低）`docs/74` 里 `gzh∩zhihu 14%` 是旧口径，新口径是 0%（已在本文件与 docs/74 更正过）。✅

## 四之二、Langfuse（自托管）

- 容器在 `F:\langfuse`（Docker），**3050 端口** → 容器 3000
- 项目 `polyface`，key 在 `python-service/.env`，`.env` 已是 `LANGFUSE_ENABLED=true`
- v4 **events_only** 模式：老接口 `/api/public/traces` 不可用；数据落在 ClickHouse `events_core`
- 查最近调用：`docker exec langfuse-clickhouse-1 clickhouse-client --query "select ... from events_core where project_id='cmvw5kt21000gky07kbjmb87j'"`
- UI 登录账号来自本机配置（密码只在用户手里）

## 五、必须遵守的规则（用户明确要求）

1. **每次做完大改动，同步更新 `docs/polyface-explained.html`** ✓
   它是给初学者看的项目讲解页（为什么做/痛点/选型/架构/十个真难题）。
   **这个页面不提交** —— 已在 `.gitignore`。
2. **不要自作主张加"自动开始"之类的贴心行为** ✗
   例：文件选完自动转写被我加过，被否掉了（转写要花时间，必须显式点）。
   现在：选文件只给提示 → 按钮自己显示"转写中…" → 完成后还原。
3. **报忧不报喜**：交付前列"没验证的、失败的、拿不准的"。
4. **禁止 `git stash`**（本机会摧毁 `.git`）。

## 六、踩过的坑（改相关代码先看这些）

| 坑 | 要点 |
|---|---|
| `cline-pass/` 模型 | **免费通道**。`deepseek/...` 是付费路（402）。**`LLM_QA_MODEL` 也要改成 `cline-pass/`**，否则每次生成都挂 |
| 网关请求头 | 必须发 `X-Response-Format: openai`，否则带图请求返回**空** |
| 推理型模型 | `max_tokens` 太小会被思考吃光 → 空正文。视觉用 1000，空正文自动 3000 重试 |
| `blurdetect` | 输出是**模糊度**（越大越糊）✗，方向写反过一次 |
| 中文 FTS5 | 默认分词器搜不到两字词、trigram 要 ≥3 字 → 退回 `LIKE` |
| 相对路径 | Java 进程 cwd 不稳定（`start.sh` 下是 `java-backend/target`）→ 按**项目根**解析 |
| 新 scene 登记 | 加了 `scene="xxx"` 必须登记到 `prompts.SCENE_BUILDERS`，且提示词要是**函数**（版本号靠函数源码算指纹）|
| 骨架槽位 | `{{n:序号\|表层}}` 是格式占位；模型会照抄示例 → 系统提示里必须点明 |
| 无产出 ≠ 失败 | 粗剪"没停顿"是 `skipped`，不是 `failed`；**模型复核否决全部剪点也走这条**，但消息会说明是复核导致的 |
| 端口/文件占用 | `target/polyface.jar` 被跑着的旧 jar 锁住 → `package` 报 `Unable to rename`。先停旧进程 |
| 免费通道模型 | `cline-pass/` 只有 deepseek-v4.1-flash-1017 / minimax-m3.1-flash / kimi-k3 / glm-5.3，**全都会思考**；换非推理模型的实验做不了（付费路余额 $0.01）|
| 骨架拆步 | `LLM_DRAFT_SPLIT=1`（默认关）。**已评测否决**：通过率 92%→10%、耗时更长，别默认开 |

## 七、关键文件位置

- 讲解页：`docs/polyface-explained.html`（gitignored）
- 验收记录：`docs/74-真实多平台产出-验收记录.md`、`docs/75`、`docs/76`
- 平台配置：`platform-dna/*.yaml`（含 `body_chars` 篇幅窗口）
- 模型配置：`python-service/.env`（**不要打印整行，会泄露 Key** ✗）
