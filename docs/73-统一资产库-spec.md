# M7-1 统一资产库 — 规格说明

日期：2026-10-06 · 对应任务：`docs/72` · 上游：`docs/08` ADR-021

---

## 1. 架构位置

```
浏览器「素材库」面板 ──► Java :8080
                          ├─ AssetController  (/api/assets/**)
                          ├─ AssetService     （文件布局 + 登记 + 删除语义）
                          ├─ Store            （asset / asset_link 两张表，SQLite）
                          └─ MediaDir         （媒体根，唯一来源；防穿越）
                                └─ {media}/assets/<sha256前2>/<sha256>.<ext>
```

**登记是自动的**：M6-1 的封面产物、FR-51 的上传入料，各自在既有流程里顺手登记，
用户不需要"记得去存进素材库"。

## 2. 数据模型（SQLite）

```sql
CREATE TABLE IF NOT EXISTS asset (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,              -- image | audio | video | timeline
  source TEXT NOT NULL,            -- upload | generated | extracted
  name TEXT,
  rel_path TEXT NOT NULL,          -- **相对 media 根**的路径（两种布局并存，见 §3）
  mime TEXT,
  size_bytes INTEGER DEFAULT 0,
  width INTEGER DEFAULT 0,         -- 尽力而为；探不到为 0
  height INTEGER DEFAULT 0,
  duration_sec REAL DEFAULT 0,     -- 本里程碑**不填**（ffprobe 在 Python 侧，接线属后续）
  sha256 TEXT,                     -- 内容寻址；算不出时为空串
  tags_json TEXT DEFAULT '[]',
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS asset_link (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id INTEGER NOT NULL,
  owner_kind TEXT NOT NULL,        -- material | draft
  owner_id INTEGER NOT NULL,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_asset_link_asset ON asset_link(asset_id);
CREATE INDEX IF NOT EXISTS idx_asset_kind ON asset(kind);
```

**注意**（沿用 `Store.initSchema` 的既有约束）：sqlite-jdbc **单次 execute 只执行首条语句**，
所以每条 DDL 必须是数组里独立的一项。

**"standalone" 的语义**：`links` 为空 = 未关联（即独立存在于素材库）。
**不**为未关联资产插一条 `standalone` 的 link 行 —— 空即独立，少一类噪音数据。

## 3. 文件布局（两种并存）

| 来源 | 布局 | 说明 |
|---|---|---|
| 新上传 | `{media}/assets/<sha256前2>/<sha256>.<ext>` | **内容寻址**，重复上传天然去重 |
| FR-51 入料（既有） | `{media}/<uuid>.<ext>` | **保持不动**，登记时记 `rel_path` |
| M6-1 封面（既有） | `{media}/covers/<stem>/cover.png` | **保持不动**，登记时记 `rel_path` |

> **为什么不搬家**：移动用户既有文件是**破坏性操作**，收益只是目录好看（ADR-021 决策 2）。
> 一切以 `rel_path` 为准，`MediaDir.resolveSafe` 统一防穿越。

**去重语义**：同 `sha256` 的文件**只存一份**；但**允许**存在多条 asset 记录
（名字/来源/标签不同，比如同一张图既是"生成的封面"又被"手动上传"一次）。
删除时按 §6 判断文件是否可删。

## 4. 接口契约（冻结）

### 4.1 `GET /api/assets`

查询参数：`kind`（可选，逗号分隔多值）、`q`（可选，按 `name` 模糊匹配）、`limit`（默认 50，上限 200）、`offset`（默认 0）

```json
{ "items": [AssetDTO], "total": 12, "limit": 50, "offset": 0 }
```

### 4.2 `AssetDTO`

```json
{
  "id": 3, "kind": "image", "source": "generated", "name": "把一篇长文变成五个平台稿件",
  "rel_path": "covers/draft-1-xhs-1691bf/cover.png",
  "url": "/api/media/covers/draft-1-xhs-1691bf/cover.png",
  "mime": "image/png", "size_bytes": 124103,
  "width": 1080, "height": 1440, "duration_sec": 0,
  "sha256": "9f2c…", "tags": ["封面"],
  "created_at": "2026-10-06T17:20:31",
  "links": [{"owner_kind": "draft", "owner_id": 1}]
}
```

- `url` = `/api/media/` + `rel_path`（**逐段 URL 编码**，空格→`%20`）。复用 M6-1 已交付并测过的托管端点，**不新开 raw 端点**。

### 4.3 其余端点

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/assets/{id}` | 单个 AssetDTO；不存在 404 |
| `POST` | `/api/assets/upload` | multipart：`file`（必填）、`name`（可选）、`tags`（可选，逗号分隔）→ AssetDTO |
| `POST` | `/api/assets/{id}/link` | body `{owner_kind, owner_id}` → `{ok:true, links:[...]}`；幂等（重复 link 不报错、不重复插行） |
| `DELETE` | `/api/assets/{id}` | → `{ok:true, file_removed:bool, links_removed:int}` |

**扩展名白名单**（上传）：
`image`: png/jpg/jpeg/webp/gif/bmp/svg · `audio`: mp3/wav/m4a/aac/flac/ogg/opus/wma ·
`video`: mp4/mov/mkv/avi/webm/flv/m4v/wmv/mpg/mpeg/ts · `timeline`: json
不在白名单 → **400**。

## 5. 字段探测

| 字段 | 怎么来 |
|---|---|
| `kind` | 按扩展名映射（§4.3 的白名单） |
| `mime` | 按扩展名映射（与 `MediaController.contentTypeOf` 同一张表） |
| `size_bytes` | `Files.size` |
| `sha256` | **流式计算**（`MessageDigest` + 缓冲区），**绝不整份读进内存**（视频可达数百 MB） |
| `width`/`height` | `javax.imageio.ImageIO.read` **尽力而为**：png/jpg/gif/bmp 可读，webp/svg 读不到 → 记 0。**不引入新依赖** |
| `duration_sec` | 本里程碑**不填**（0）。ffprobe 在 Python 侧，接线属后续任务 |

## 6. 删除语义（**最容易误解，必须按此实现**）

```
DELETE /api/assets/{id}
  1. 删该 asset 的 link 行            → links_removed
  2. 删该 asset 记录
  3. 若**再无任何 asset 记录**引用同一个 sha256（且 sha256 非空）
        → 删物理文件，file_removed=true
     否则                              → 保留文件，file_removed=false
```

- **绝不静默删用户文件**（ADR-017 的数据姿态）。
- 旧布局文件（入料/封面）的 `sha256` 若算得出，同样按此规则；算不出则**永不删文件**（保守）。

## 7. 自动登记（两处接线）

| 触发点 | 登记内容 |
|---|---|
| `CoverController` 封面生成 **`status=ok`** | `kind=image, source=generated, name=<标题>, rel_path=covers/<stem>/cover.png`；并 `link(draft, draft_id)` |
| `IngestController` `/api/ingest/upload` 成功 | `kind=video|audio, source=upload, name=<原始文件名>, rel_path=<mediaId>` |

**登记失败绝不影响主流程**：包在 try/catch 里，失败只 `log.warn`。
理由：登记是**附加价值**，不能因为它挂了就导致封面生成/入料失败。

## 8. 前端「素材库」面板

- 入口：工作台新增一个「📚 素材库」区（与「素材历史」**并列但语义不同**，见 §9）
- 能力：类型筛选（全部/图片/音频/视频/时间线）、关键词搜索、上传、缩略预览（图片直接 `<img>`，音视频给图标 + 大小）、删除（二次确认）、显示关联数（"已用于 2 处"）
- 空态：明确文案 + 「上传素材」按钮
- **不做**：批量操作、拖拽上传、标签编辑（后续）

## 9. 与「素材历史」的区别（**必须在 UI 文案里说清**）

| | 素材历史（既有） | 素材库（本任务） |
|---|---|---|
| 存什么 | **文本素材**（长文/口播稿/大纲/笔记） | **资产**：图片/音频/视频/时间线 |
| 表 | `material` | `asset` |
| 用途 | 再次生成稿件 | 被稿件/成片**引用** |

两者**不合并**（ADR-021 决策 1）。

## 10. 未覆盖 / 待验证

- **时间线资产的产出**：`kind=timeline` 只占位，M10 粗剪才会写入
- `duration_sec` 恒为 0（需接 ffprobe）
- webp/svg 的宽高探测不到（记 0）
- 孤儿文件清理命令、资产版本管理、标签编辑 UI、去重提示
- 大文件（>500MB）不支持（沿用既有 multipart 上限）
