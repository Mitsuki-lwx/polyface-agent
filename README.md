# Polyface · 同源万面

> **一份素材进，千面出。** 把一份原创素材，自动改写为符合各平台调性、有机会获得流量的完整可发布稿件。

面向自媒体博主的**本地 Web 工具**：素材与稿件存在你自己的电脑，模型用你自己填的 Key。灵感源于 Creative Minds Jam（Content repurposing across platforms），但**完全独立自研**，不依赖任何外部智能体平台。

> ⚠️ **关于"数据不出本机"**：早期版本这样宣传，**这个说法不准确**，已修正（见 `docs/08` ADR-017）。请先读下面的「数据边界」。

## 数据边界（请先读）

| 留在你的电脑 | 发送到外部 LLM |
|---|---|
| 素材原文与成稿（SQLite） | **素材正文**（解析时全文送出） |
| 音视频文件（`data/media`） | 已确认事实清单（生成时复用） |
| **音视频转写**（本地 ASR / 字幕提取） | 模板内容 |
| 模板库、效果回填、复盘 | 创作者画像 / 历史经验提示 |
| LLM Key（`python-service/.env`） | |

**一句话**：数据**存在**本机，但**生成过程不离开本机是不可能的** —— 素材正文会发送给你自己配置的 LLM 服务商（默认商汤 SenseNova）。

- ✅ **仍然成立**：不经任何平台方、无云端账号、**音视频转写全程本地不上传**（明确排除云 ASR）
- ✅ **仍成立的卖点**：数据只存在你本机 + 模型用你自己的 Key，不用把素材交给第三方 SaaS
- ⚠️ **`LLM_MOCK=true`（无 Key 时的默认）全流程离线**，此时确实不出网 —— 但产出是**演示结果**，不是真实生成，UI 的运行模式徽章会标明

## 快速开始

> 需要：**Java 17+** / **Python 3.11+** / 浏览器。无需数据库。
> **封面成图（可选）**额外需要 **Node.js ≥ 20.19** + `npm install -g gimpish`；
> 不装也能跑，只是「生成封面」会降级为提示安装（不影响其他功能）。见 ADR-019。
>
> **Windows** 用 `.bat`，**macOS / Linux / Git Bash** 用 `.sh` —— 两组脚本行为逐项对齐。

### 方式 A：下载发布包（推荐，不需要 Maven）

**Windows**

```bat
:: 解压 polyface-<版本>.zip 后，在该目录下：
scripts\setup.bat     :: 首次运行：建 venv、装依赖、生成 .env
scripts\start.bat     :: 启动（自动打开浏览器）
scripts\stop.bat      :: 停止
```

**macOS / Linux**

```bash
# 解压 polyface-<版本>.zip 后，在该目录下：
bash scripts/setup.sh     # 首次运行：建 venv、装依赖、生成 .env
bash scripts/start.sh     # 启动（自动打开浏览器）
bash scripts/stop.sh      # 停止
```

发布包自带预构建的 `polyface.jar`，**不需要装 Maven**。

> **端口被占用？** 8000（Python）或 8080（Java）被别的程序占用时，不用改脚本：
> ```bash
> # macOS / Linux / Git Bash
> POLYFACE_JAVA_PORT=18080 POLYFACE_PY_PORT=18000 bash scripts/start.sh
> ```
> ```bat
> :: Windows
> set POLYFACE_JAVA_PORT=18080
> set POLYFACE_PY_PORT=18000
> scripts\start.bat
> ```
> 启动前的体检会明确告诉你哪个端口被占、以及这条命令。

### 方式 B：从源码运行（开发者）

```bat
:: Windows
scripts\setup.bat          :: 这一步需要 Maven 来构建 jar
scripts\start.bat
```

```bash
# macOS / Linux / Git Bash
bash scripts/setup.sh
bash scripts/start.sh
```

或手动分别启动：

```bash
# Python LLM 服务（注意：必须在 python-service 目录下启动，.env 是按当前目录找的）
cd python-service && .venv/Scripts/python -m uvicorn app.main:app --port 8000
# macOS/Linux 是 .venv/bin/python

# Java 后端（Git Bash 下必须走 scripts/mvn.sh，直接 mvn 会报 ClassNotFoundException）
mvn -f java-backend/pom.xml spring-boot:run
```

浏览器打开 <http://127.0.0.1:8080>。

### 关于 LLM Key（重要）

**不填 Key 也能跑** —— 默认 `LLM_MOCK=true`，走离线演示模式，秒级返回、完全不出网，但产出是**演示结果**（内容为模板填充，不是真实生成）。

要真实生成，编辑 `python-service/.env`：

```ini
LLM_API_KEY=你的Key
LLM_MOCK=false
```

支持任意 OpenAI 兼容接口（商汤 SenseNova / DeepSeek / 通义 / OpenAI…），改 `LLM_BASE_URL` + `LLM_MODEL` 即可。

### 启动前自检（可选，出问题时很好用）

```bat
python scripts\doctor.py
```

逐项检查 Java 版本 / Python 版本 / venv 与依赖 / jar / 端口占用 / 数据目录 / LLM 模式，并对每个阻塞项给出**具体怎么修**。`start.bat` 会自动跑一遍，有问题就中止启动而不是让你对着空白页发呆。

## 耗时预期（重要，先看这个再抱怨慢）

| 场景 | 预期 |
|---|---|
| 离线演示（`LLM_MOCK=true`） | 秒级 |
| **真实 LLM · 单平台** | **约 1~4 分钟**（实测 101s ~ 250s；上游限流时更久） |
| 真实 LLM · 多平台 | 串行累加（平台数 × 单平台耗时）；**默认只勾选 1 个平台** |

**为什么这么慢**：每个平台要走 `understand → brief → draft → qa` **最多 4 次 LLM 调用**，QA 不过还要再来一轮改写。上游对 tpm/rpm 卡得很紧，多平台并发会被大面积 429，所以默认**串行 + 调用间隔**。这是设计取舍，不是 bug。

生成是**长任务**，超时预算是**显式可配**的（默认生成 240s / 短任务 60s），不是写死的魔法数字：

```ini
POLYFACE_LLM_TIMEOUT_SEC=300        # Java 侧：整个生成任务的预算
POLYFACE_LLM_FAST_TIMEOUT_SEC=60    # Java 侧：解析/学习/用量查询
```

> ⚠️ 别和 Python 侧 `.env` 里的 `LLM_TIMEOUT_SEC`（默认 60）混淆 —— 那是**单次模型调用**的上限，是任务预算的组成部分。

**部分成功会保留**：某个平台失败**不会丢弃**其他平台已生成的稿子 —— 前端显示「成功 X / 失败 Y」+ 失败原因，并提供「🔁 重试失败平台」（只重跑失败的那个）。

## 核心能力

- 📥 输入一份素材（长文 / 口播稿 / 大纲 / 笔记 / 音视频）
- 🧬 按**平台 DNA**（语言风格 / 结构模板 / 标题机制 / 标签策略 / 红线词）逐平台改写 —— 不只是换格式，而是换"灵魂"
- 🛡️ **事实约束**：成稿中的数值/故事必须能在素材"事实清单"中找到依据，QA 拦截 AI 自加戏
- 🧠 **记忆与复盘**：创作者画像 + 效果回填 + 复盘报告，越用越懂你
- 🎬 **剪辑单**（抖音/B站）：成稿附带分镜/时长/画面/字幕/BGM 建议
- 🎥 **音视频入料**：视频/音频 → 文字，**本地转写**（字幕轨优先，ASR 可选），不上传
- 🖼 **封面成图**（M6-1）：稿件标题 → 平台尺寸封面 PNG，由后台图像编辑器 **gimpish** 渲染
  （小红书 3:4 / 抖音 9:16 / B站 16:9）。产物目录内保留 `scene.json`，可用 `gimpish serve` 继续手改
- 🔒 **数据边界清晰**：素材与稿件只存在你的电脑；生成时正文发送给你自配的 LLM 服务商（见上）

## 平台支持

| 阶段 | 平台 |
|---|---|
| v1 | 小红书 · 抖音 · 微信公众号 · 知乎 · B站 |
| 规划中 | X(Twitter) · Instagram · Facebook · YouTube |

平台差异写在 `platform-dna/*.yaml` 里，**可直接编辑**（风格、结构模板、标题规则、标签、红线、字数限制），改完重启即生效，不需要改代码。

## 架构

```
浏览器(127.0.0.1:8080)
   └─ Java 后端 :8080 (Spring Boot)   ← 业务编排/任务状态/本地存储(SQLite)
        └─ Python LLM 服务 :8000 (FastAPI)  ← 素材解析/平台策略/成稿/QA/封面适配器
             ├─ LLM (OpenAI 兼容，默认 SenseNova 商汤)
             └─ gimpish (Node 子进程，后台，无 GUI)  ← 封面成图（可选依赖，见 ADR-019）
```

两个服务都**只监听 127.0.0.1**，不对局域网/公网开放。Python 服务的跨域默认**关闭**（见下）。

## 目录结构

```
polyface/
├── polyface.jar       # Java 后端（发布包内预构建；源码模式在 java-backend/target/）
├── java-backend/      # Spring Boot :8080 —— 业务编排 + 存储 + Web 托管
├── python-service/    # FastAPI :8000 —— LLM 管线（解析/策略/成稿/QA）
├── platform-dna/      # 各平台 DNA（YAML，可编辑可升级）
├── scripts/           # setup / start / stop / doctor（自检）/ build_release（打包）
├── docs/              # 方案与蓝图文档（编号索引见下）
├── data/              # 你的数据（素材/稿件/音视频）—— 不在仓库里，也不会被打包
├── VERSION            # 版本号唯一来源
└── dist/              # 打包产物（本地生成）
```

## 配置速查

| 变量 | 位置 | 默认 | 说明 |
|---|---|---|---|
| `LLM_API_KEY` | `.env` | 空 | 你的模型 Key。空 = 离线演示模式 |
| `LLM_MOCK` | `.env` | `true` | `false` 才走真实模型（需 Key） |
| `LLM_BASE_URL` / `LLM_MODEL` | `.env` | SenseNova | 换服务商改这两项 |
| `LLM_TIMEOUT_SEC` | `.env` | `60` | **单次**模型调用超时 |
| `LLM_PARALLEL` | `.env` | `false` | 多平台并行。默认串行（上游限流） |
| `CORS_ORIGINS` | `.env` | 空 | 留空 = 不启用跨域（推荐） |
| `FFMPEG_PATH` | `.env` | 空 | 音视频入料用；空则找 PATH |
| `POLYFACE_LLM_TIMEOUT_SEC` | 系统环境变量 | `240` | Java 侧：生成任务总预算 |
| `POLYFACE_LLM_FAST_TIMEOUT_SEC` | 系统环境变量 | `60` | Java 侧：短任务预算 |
| `POLYFACE_DATA_DIR` | 系统环境变量 | `../data` | 数据目录。**默认值相对「启动时的当前目录」**，所以手动 `java -jar` 可能把数据写到包外；`start.bat` / `start.sh` 已显式设为 `<包根>/data` |
| `POLYFACE_JAVA_PORT` | 系统环境变量 | `8080` | Java 后端端口。被占用时改它，不用改脚本 |
| `POLYFACE_PY_PORT` | 系统环境变量 | `8000` | Python LLM 服务端口。同上 |
| `GIMPISH_PATH` | `.env` | 空 | 封面成图用的 gimpish 入口（可执行文件或 `.js`）。空则找 PATH |
| `POLYFACE_GIMPISH` | 系统环境变量 | 空 | 同上，**优先级更高**（不装 gimpish 则封面功能降级提示安装） |
| `POLYFACE_COVER_DIR` | 系统环境变量 | `{data-dir}/media/covers` | 封面产物根目录 |

> 注意前缀差异：`.env` 里的变量**没有** `POLYFACE_` 前缀，系统环境变量**有**。完整注释版模板见 `python-service/.env.example`。

## 常见问题（FAQ）

**Q：启动后浏览器打不开 / 页面空白？**
先跑 `python scripts\doctor.py`（macOS/Linux 用 `python3 scripts/doctor.py`）。最常见是端口 8080 或 8000 被别的程序占用，自检会直接报出来并提示两条出路：

1. 用 `scripts\stop.bat`（或 `scripts/stop.sh`）清理旧进程后重试；
2. **直接换端口，不用改脚本**：
   ```bash
   POLYFACE_JAVA_PORT=18080 POLYFACE_PY_PORT=18000 bash scripts/start.sh
   ```
   ```bat
   set POLYFACE_JAVA_PORT=18080 & set POLYFACE_PY_PORT=18000 & scripts\start.bat
   ```

> 注意：体检若报「端口无法绑定（未查到监听进程）」，通常是该端口落在系统的保留段里
> （Windows 上可 `netsh int ipv4 show excludedportrange protocol=tcp` 查看），
> 或者是某个程序已 bind 但还没开始监听 —— 这两种情况换端口最快。

**Q：没填 Key 能体验吗？**
能。默认离线演示模式，秒级返回、完全不出网，但产出是**演示结果**（模板填充），不是真实生成。UI 上的运行模式徽章会标明当前是哪种。

**Q：为什么生成这么慢？**
见上面「耗时预期」。每个平台最多 4 次 LLM 调用 + 串行 + 上游限流，这是当前架构的固有成本。

**Q：数据会上传到你们服务器吗？**
不会 —— 这个项目**没有**我们的服务器。数据只存在你本机。但生成时素材正文会发送给**你自己配置的 LLM 服务商**，这是必然的（模型在云端）。详见「数据边界」。

**Q：会帮我生成视频吗？**
**不会。** 目前只产出**文字稿 + 剪辑单**（分镜/时长/画面/字幕建议）。本地自动成片（首发 **B1 图文成片**，其后 **B2 智能剪已有视频**）是 `docs/05` FR-52/53 规划的**核心方向**，但**当前后置**且**尚未实现**——排序理由与决策记录见 `docs/08` ADR-018、`docs/49`。

**Q：视频入料需要装什么？**
需要 `ffmpeg`（加入 PATH，或用 `FFMPEG_PATH` 指定）。自动语音转写是**可选**的，需另装 `faster-whisper`；不装时，若视频没有字幕轨，会降级提示你手工粘贴文案 —— 不会报错中断。

**Q：生成封面需要装什么？**
需要 **Node.js ≥ 20.19**，然后 `npm install -g gimpish`（[gimpish](https://github.com/jvanderberg/gimpish)，MIT，本地渲染、不上传）。
不装也能用其他功能：点「生成封面」会返回**安装指引**而不是报错（`needs_manual`，见 `docs/64` §3）。
生成的文件在 `data/media/covers/<名字>/`，同目录有 `scene.json` —— 想继续手改可以用 `gimpish -C <该目录> serve` 打开浏览器编辑器。

**Q：我的数据在哪？怎么备份/迁移？**
全在包根目录的 `data/` 下（`polyface.db` 是 SQLite，`media/` 放音视频）。备份直接复制整个 `data/` 目录；迁移到新版本时把 `data/` 拷过去即可。

**Q：能改成局域网访问 / 部署到服务器吗？**
当前版本**有意**只监听 127.0.0.1 且无鉴权。要对外提供服务必须先加认证与授权 —— 直接改绑定地址会把你的 LLM Key 额度暴露给任何能访问该端口的人。

**Q：怎么改某个平台的风格？**
编辑 `platform-dna/<平台>.yaml`（风格、结构模板、标题规则、标签、红线词都在里面），重启 Python 服务生效。

## 路线图

- [x] M0 方案与蓝图
- [x] M1 骨架 + 素材解析跑通
- [x] M2 单平台（小红书）成稿 + QA 全链路 + SQLite 落库
- [x] M3 5 平台 DNA + Web 工作台 + 剪辑单 + 内容模板基础版
- [x] M3.5 视频/音频 → 文字入料（本地转写，复用解析管线）
- [x] M4 效果回填 + 复盘 + 画像记忆闭环 + 模板完整管理 + 示例学习
- [x] M5 开源 Release 打包（一键启动 + 自检 + 发布包校验）
- [ ] **M6 编排开源编辑器**（`docs/08` ADR-019）—— 形态改为「桌面壳 + 后台编辑器 + agent 编排」：
  - [x] M6-1 封面成图（后台 gimpish 渲染，见 `docs/63`~`docs/66`）
  - [ ] M6-2 桌面壳与进程编排（编辑器进程启动/健康检查/退出清理/随包分发）
  - [ ] M6-3 视频剪辑适配器（先用 ffmpeg；**OpenCut 当前不可后台驱动**，等其 headless/Editor API 落地）
- [ ] **本地自动成片**（首发 B1 图文成片 → 其后 B2 智能剪已有视频）—— **尚未实现**。
      定位为**核心方向 · 当前后置**（`docs/08` ADR-018）：是核心路径而非附加功能，
      但排序上先做完真实作者验证再投入。见 `docs/05` FR-52/53、`docs/49`。
      （ADR-019 已暂停"第三关先行"的排序，改为与 M6 并行。）

> 关于 M5 的一个如实说明：`scripts/*.bat` 的编排逻辑已按 `docs/23` 实现，但**本次交付的验证环境无法执行 `.bat`**，因此批处理脚本本身**未经实机运行验证**；其调用的 `doctor.py`、`build_release.py` 逻辑已单测覆盖。详见 `docs/48`。

## 文档体系（docs/）

| 阶段 | 文档 |
|---|---|
| 方案 | 01 总体方案设计 |
| 建模 | 02 领域建模与产品蓝图（含竞品快研） |
| 需求 | 05 需求规格说明书 SRS |
| 可行性 | 06 可行性分析报告（技术/经济/市场/运营/合规） |
| 用例 | 07 用例模型与验收标准 |
| 决策 | 08 技术决策记录 ADR（**数据边界见 ADR-017**） |
| 里程碑 | 03 / 04 / 09 / 10 交付说明 |
| 专项 | 11-65：模板管理 / 示例学习 / 音视频入料 / Release 打包 / LLM 加固 / 平台 DNA 调研 / 质检加固 / 事实闭环 / 长任务预算 / 启动脚本加固 / 发布包内验证 / **编辑器适配器与封面成图** —— 每项都是 task + spec + checklist + 交付说明 四件套 |

## 合规声明

本工具**只处理你自有或已获授权的素材**；产出"改写建议稿"而非搬运；**不提供自动发布**，
发布行为由你本人在各平台完成并遵守其原创与 AI 内容规范。

## License

[MIT](./LICENSE)
