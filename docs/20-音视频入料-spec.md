# 规格说明书：音视频转文字入料（FR-51 / M3.5）

- 文档类型：spec（事前三件套之一）
- 版本：v1.0 · 日期：2026-09-11
- 配套：`19-音视频入料-task.md`、`21-音视频入料-checklist.md`
- 需求来源：`05-SRS` FR-51；用例来源：`07` UC-12

---

## 1. 需求规格

### 1.1 功能需求

| 编号 | 原文 | 本期实现 |
|---|---|---|
| FR-51 | 视频/音频转文字入料：指定文件 → 转录口播稿 → 走现有 understand 管线 | 字幕提取优先 + 可选本地 ASR + 手填降级 |

### 1.2 非功能约束

| 编号 | 约束 | 落地方式 |
|---|---|---|
| UC-12 | **无外部上传** | 转写全本地（ffmpeg + 本地 ASR），排除任何云 ASR |
| NFR-02 | 数据本地 | 媒体副本存本机 `data/media/`，可清理 |
| NFR-08 | 可测试 | 三种状态路径均可离线测试；E2E 用 ffmpeg 现场造带字幕样本 |
| ADR-010 | 合规 | 不自动发布、不上传；UI 提示仅处理自有/授权内容 |

### 1.3 关键验收（来自 UC-12）

- 转录文本**可编辑后再解析**
- 转录稿**进入素材历史**
- **无外部上传**

---

## 2. 数据流

```
[浏览器] 选文件(multipart) 或 填本机路径
    ↓
[Java :8080] /api/ingest/upload  → 存 data/media/{uuid}.{ext}（上传）
             或 /api/ingest/probe {path}（路径模式，校验存在+扩展名白名单）
    ↓
[Python :8000] /ingest/probe   → ffprobe：时长/音轨/字幕轨/格式
    ↓
    ├─ 有字幕轨 → /ingest/transcribe mode=subtitle → ffmpeg 提取 → 去时间轴 → 文本
    └─ 无字幕轨 → mode=asr → faster-whisper（若环境具备）
                            └─ 不具备 → status=needs_manual + 安装指引
    ↓
[Java] 返回 {status, text, source, hint, media}
    ↓
[浏览器] 展示转录文本（**可编辑**）→ 用户确认
    ↓
[Java] POST /api/materials {raw_text: 编辑后文本, source_kind:"口播稿", title: 文件名}
    ↓  走**现有** createMaterial → python /analyze → 素材入库（复用 M1 管线，零改动）
```

**设计要点**：入料只负责"得到文字"，拿到文字后**完全复用现有素材创建链路**——不新增解析/生成代码。

---

## 3. 接口契约

### 3.1 Java（对外）

#### 上传文件

```
POST /api/ingest/upload        (multipart/form-data, field: file)
→ 200 { "media_id": "a1b2c3", "filename": "口播.mp4", "size": 10485760, "path": "data/media/a1b2c3.mp4" }
→ 400 扩展名不在白名单内
→ 413 超过大小上限（默认 500MB）
```

#### 探测媒体

```
POST /api/ingest/probe   { "media_id": "a1b2c3" }   或   { "path": "D:/videos/x.mp4" }
→ 200 {
    "media_id": "a1b2c3", "filename": "口播.mp4",
    "duration_sec": 183.4, "format": "mp4",
    "has_audio": true, "has_subtitle": true,
    "subtitle_streams": [ { "index": 2, "codec": "mov_text", "lang": "chi" } ],
    "recommended_mode": "subtitle" | "asr" | "manual"
  }
→ 400 文件不存在 / 无法识别为媒体
```

#### 转写/提取

```
POST /api/ingest/transcribe   { "media_id": "a1b2c3", "mode": "auto|subtitle|asr" }
→ 200 {
    "status": "subtitle_extracted" | "transcribed" | "needs_manual",
    "text": "……（转写/提取出的文字，失败时为空串）",
    "source": "subtitle" | "asr" | "none",
    "chars": 512,
    "hint": "【可操作的下一步指引，needs_manual 时必填】",
    "used_mock": false
  }
```

#### 媒体副本清理

```
DELETE /api/ingest/media/{media_id}
→ 204（删除 data/media 下的副本）
```

### 3.2 Python（内部）

```
POST /probe       { path }                        → 同 probe 响应结构（无 media_id）
POST /transcribe  { path, mode }                  → { status, text, source, chars, hint }
```

Python **不感知 media_id**——路径由 Java 解析后传入，维持"Python 无状态、不碰数据库"的既有边界。

---

## 4. 状态与降级矩阵

| 场景 | status | source | UI 提示（hint） |
|---|---|---|---|
| 有字幕轨，提取成功 | `subtitle_extracted` | `subtitle` | 「已从视频字幕轨提取，请核对后使用」 |
| 有字幕轨但提取为空 | `needs_manual` | `none` | 「字幕轨为空，请手动粘贴文案」 |
| 无字幕 + ASR 可用 | `transcribed` | `asr` | 「本地 ASR 转写完成，请重点核对同音字/专有名词」 |
| 无字幕 + ASR 未安装 | `needs_manual` | `none` | 「未检测到本地转写组件。安装：`pip install faster-whisper`（首次使用会下载模型）；或直接粘贴文案」 |
| 文件不可读/非媒体 | `needs_manual` | `none` | 「无法识别该文件，请确认是常见音视频格式」 |
| ffmpeg 未安装 | `needs_manual` | `none` | 「未检测到 ffmpeg，请安装后重试」 |

**设计原则**：任何失败都**不抛 500**，而是降级为 `needs_manual` + 可执行指引——工具始终可用。

---

## 5. 实现细节

### 5.1 媒体探测（`pipeline/media.py`）

```python
def probe(path: str) -> dict:
    # ffprobe -v quiet -print_format json -show_streams -show_format <path>
    # 解析 streams[] → has_audio(codec_type=audio) / subtitle_streams(codec_type=subtitle)
    # duration 取 format.duration（失败则取 audio stream duration）
```

- ffmpeg/ffprobe 定位：优先 `shutil.which("ffmpeg")`，回退环境变量 `POLYFACE_FFMPEG`；未找到抛可读异常
- 超时保护：`subprocess.run(timeout=30)`

### 5.2 字幕提取

```bash
ffmpeg -y -i <video> -map 0:s:0 -f srt <tmp.srt>
```

SRT → 纯文本清洗规则：
1. 丢弃纯数字行（序号）
2. 丢弃含 `-->` 的行（时间轴）
3. 剥离 `<i>`/`<b>`/`{\an8}` 等标记
4. 相邻重复行去重（卡拉OK式字幕常见重复）
5. 按行拼接，段间空行

### 5.3 ASR 适配（`pipeline/asr.py`）

```python
def is_available() -> bool:
    try:
        import faster_whisper  # noqa
        return True
    except ImportError:
        return False

def transcribe(path: str, model_size: str = None) -> str:
    from faster_whisper import WhisperModel
    size = model_size or os.getenv("POLYFACE_ASR_MODEL", "small")
    model = WhisperModel(size, device="cpu", compute_type="int8")   # 进程内缓存
    # 先降采样为 16k 单声道 wav，避免大视频容器问题
    segments, _info = model.transcribe(wav_path, language="zh", vad_filter=True)
    return "".join(s.text for s in segments).strip()
```

- 模型实例**进程内缓存**（避免每次请求重载）
- 音频预处理：`ffmpeg -y -i <src> -vn -ac 1 -ar 16000 -f wav <tmp.wav>`
- 超时：转写不设硬超时，但 `/ingest/probe` 已给时长，UI 可提示"预计需 X 秒"

### 5.4 Java 侧

- `IngestController`：上传受理（MultipartFile → `data/media/{uuid}{ext}`）、路径校验（存在 + 扩展名白名单 + 拒绝目录）、转发 Python
- 扩展名白名单：`mp4 mov mkv avi webm flv m4v / mp3 wav m4a aac flac ogg`
- 大小上限：`spring.servlet.multipart.max-file-size=500MB`
- **复用** `MaterialController` 的素材创建（前端确认后仍调 `POST /api/materials`，不新增后端逻辑）

### 5.5 前端（`static/index.html`）

在「① 素材」卡片内新增折叠区「🎬 从音视频导入」（不新增独立卡片，避免左栏过长）：

```
[选择文件]  或  文件路径 [__________]
[探测并转写]  ← 显示：时长 3:03 / 有字幕轨 → 提取中… / 转写中（预计 40s）
─────────────────────────────
转录文本（可编辑）：
[ textarea                                     ]
[确认并解析为素材]   [清空]
```

- 合规提示：「请仅处理自有或已获授权的内容；文件仅存于本机」
- 转写中禁用按钮 + 显示进度文案（避免重复提交）
- 确认后调用现有 `POST /api/materials`，成功后清空入料区并刷新素材历史

---

## 6. 测试方案

| 层 | 内容 | 数量 |
|---|---|---|
| Python 单元 | SRT 清洗（序号/时间轴/标签/重复行）、probe 解析、ASR 缺失判定、状态矩阵 | +6 |
| Python 单元 | ffmpeg 未安装时的降级行为 | +1 |
| Java 单元 | 扩展名白名单、路径校验、上传落盘 | +3 |
| 端到端 | **ffmpeg 现场造带字幕样本** → 探测 → 提取 → 建素材 → 生成全通 | 1 条链路 |
| 端到端 | 无字幕样本 → `needs_manual` + hint 可执行 | 1 |
| 端到端 | 非法扩展名/不存在路径 → 400 | 1 |
| 浏览器 | 入料区交互（选文件 → 转写 → 编辑 → 确认建素材） | 实测 |

**E2E 样本生成**（无需外部素材，可重复）：

```bash
ffmpeg -f lavfi -i color=c=black:s=320x240:d=3 -f lavfi -i anullsrc=r=16000:cl=mono -shortest -c:v libx264 -c:a aac base.mp4
printf '1\n00:00:00,000 --> 00:00:01,500\n这是测试字幕第一句\n\n2\n00:00:01,500 --> 00:00:03,000\n这是测试字幕第二句\n' > s.srt
ffmpeg -y -i base.mp4 -i s.srt -c:v copy -c:a copy -c:s mov_text out.mp4
```

---

## 7. 兼容与边界

- **零改动复用**：素材创建、understand 解析、多平台生成链路完全不变；入料只是新增"文字来源"
- **不引入新硬依赖**：ffmpeg 已具备；faster-whisper 为可选
- `data/media/` 纳入 `.gitignore`（`data/` 已覆盖）
- 素材历史中入料来的稿件 `source_kind=口播稿`，title 记原文件名，便于回查
