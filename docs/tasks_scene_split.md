# tasks —— 按场景切段

对应 spec：`docs/spec_scene_split.md`　　验收：`docs/checklist_scene_split.md`

---

## T1　检测画面切点

- **影响文件**：`python-service/app/pipeline/scenes.py`（新）、`python-service/tests/test_scenes.py`（新）
- **依赖**：无
- **做什么**：用 PySceneDetect 检测切点，返回 `[秒, 秒, ...]`（含 0 与片尾）。
  检测器/阈值可调；PySceneDetect **没装 → 抛错并给安装指引**（沿用 `asr.py` 的 `INSTALL_HINT` 风格）
- **参考**：`asr.py` 的可选依赖处理（`is_available()` + `INSTALL_HINT`）
- **验收**：合成一段"三段纯色"的视频 → 检出 ≥2 个切点且位置对得上

---

## T2　语音边界 + 切点吸附

- **影响文件**：`scenes.py`、`tests/test_scenes.py`
- **依赖**：T1
- **做什么**：
  - 从词级/段级转写里取**语音边界**（每句的起止）
  - 每个画面切点 → **吸附到最近的语音边界**（在可调的最大偏移内；超出则不吸）
  - 没有语音边界 → 原样返回画面切点，并标记 `speech_aligned=false`
- **关键**：吸附是**收缩性/就近**操作，绝不把边界移到另一句话里
- **参考**：`disfluency.split_sentences`（它已经在做"词级 → 句级"的切分）
- **验收**：构造"画面切点落在句子中间"的用例 → 吸附后边界与语音边界重合

---

## T3　按最短时长合并

- **影响文件**：`scenes.py`、`tests/test_scenes.py`
- **依赖**：T2
- **做什么**：把低于 `min_sec` 的段**并入前一段**（第一段太短则并入后一段）；
  合并后保证：升序、无重叠、总长等于源时长
- **参考**：`roughcut.merge_cuts`（同样是"多来源 → 一份升序不重叠的区间"）
- **验收**：短段被并入前段（**不是丢掉**）；首段过短时并入后段；总长守恒

---

## T4　方案落盘（供两段式与续跑）

- **影响文件**：`scenes.py`、`scripts/scene_split.py`（新 CLI）
- **依赖**：T3
- **做什么**：
  - CLI：`scene_split.py <视频> [--min-sec N] [--threshold X] [--dry-run] [--yes] [--pick 1,3,5]`
  - 检测结果落盘（切点、参数、检测器版本、每段起止与时长）
  - `--dry-run`：只出方案（含缩略图路径），不切
  - `--pick`：只切指定的段（对应"人工勾选"）
- **参考**：`scripts/roughcut.py` 的两段式与落盘（`checkpoint` 那套）
- **验收**：`--dry-run` 不产出任何视频；`--pick 2` 只切第 2 段

---

## T5　切段（不重编码源视频）

- **影响文件**：`scenes.py`、`scripts/scene_split.py`
- **依赖**：T4
- **做什么**：按段切出文件（`scene-001.mp4` …），源文件不动；
  每段时长与方案一致（±0.2s）
- **参考**：`roughcut.cut_video`（ffmpeg 的剪法）
- **验收**：切出的段数 = 勾选数；每段时长与方案一致；源文件 mtime 不变

---

## T6　缩略图（方案的必需部分）

- **影响文件**：`scenes.py`、`scripts/scene_split.py`
- **依赖**：T4
- **做什么**：每段抽一帧（段内 1/3 处）作为缩略图，随方案一起产出
- **为什么**：只有时间戳的话人认不出哪段是哪段（spec 关键决定三）
- **参考**：`roughcut.preview_frame`
- **验收**：N 段 → N 张缩略图；文件存在且非空

---

## T7　接入主流程（任务 + 界面勾选）

- **影响文件**：`java-backend/.../job/SceneSplitRunner.java`（新）、`web/JobController.java`、`static/index.html`
- **依赖**：T5、T6
- **做什么**：
  - 新任务种类 `scene_split`（**两段式**：先 `plan`，再按勾选 `cut`）
  - 切出的每段**登记成素材**（复用统一资产库），血缘挂回源视频
  - 界面：选视频 → 出方案（缩略图网格 + 时长）→ 勾选 → 切
- **参考**：`SplitRunner`（任务 + 续跑 + 进度）与 `roughcut` 的两段式
- **验收**：界面能看方案、能勾、能切；切出的段出现在素材库里

---

## T8　端到端验证（真素材）

- **影响文件**：无（记录写进 `docs/checklist_scene_split.md`）
- **依赖**：T7
- **做什么**：用**真实多场景素材**跑完整链路：出方案 → 勾选 → 切 → 拿一段去跑粗剪。
  逐条核对：切点对不对、有没有把话切断、时长守恒、素材登记、缩略图
- **验收**：原始输出贴进 checklist；**切错的逐条列出来**（报忧不报喜）

---

## T9　文档与决策落档

- **影响文件**：`docs/08`（新 ADR：新依赖 PySceneDetect + 吸附策略）、`docs/05`（FR-53 缺口表）、`README.md`
- **依赖**：T8
- **做什么**：ADR 记录"引入 PySceneDetect"这个决定与代价（发布包要带上它）；
  SRS 的 FR-53 缺口表去掉"分镜"；README 补新依赖的安装说明
- **验收**：`grep` 确认 SRS/README 与实现一致
