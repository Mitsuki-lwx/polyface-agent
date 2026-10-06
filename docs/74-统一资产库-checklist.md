# M7-1 统一资产库 — 验收清单

日期：2026-10-06 · 对应 `docs/72` task / `docs/73` spec
勾选规则：**只有实测通过的才勾**。无法在本机验证的，勾选时必须附限定说明。

---

## A. 数据模型

- [x] `asset` / `asset_link` 两张表建好（`CREATE TABLE IF NOT EXISTS`，**逐条** execute）
- [x] 索引 `idx_asset_link_asset` / `idx_asset_kind` 建好
- [x] **既有库升级**：拿仓库既有 `data/polyface.db`（无 asset 表、8 material / 27 draft）的副本启动 →
      两张新表自动补上，**materials 仍 8 行、drafts 仍 27 行**（实测，原文件未被碰）
- [x] `material` 表**未被改动**（ADR-021 决策 1；升级前后行数一致）

## B. 文件布局与去重

- [x] 新上传落 `{media}/assets/<sha256前2>/<sha256>.<ext>`
- [x] 同一文件上传两次 → 磁盘**只有一份**（E2E `C2/C3`：2 条记录 / 1 个 sha / 1 个文件）
- [x] 既有布局（`{media}/<uuid>.<ext>`、`covers/<stem>/cover.png`）**未被搬动**（E1 的 rel_path 仍是 `covers/...`）
- [x] `sha256` 流式计算（`MessageDigest` + 缓冲区，**不整份读进内存**）
- [x] `rel_path` 一律相对 media 根，读取经 `MediaDir.resolveSafe`（防穿越）

## C. 接口

- [x] `GET /api/assets` 返回 `{items,total,limit,offset}`，形状符合 `docs/73` §4.1
- [x] `kind` 多值筛选生效（单测 `listFiltersByKindAndName` + 控制器 `kind=image,audio`）
- [x] `q` 关键词按 name 模糊匹配（E2E `D3`）
- [x] `limit` 上限 200（超出被夹紧，单测断言 `limit=999` → 200）
- [x] `GET /api/assets/{id}` 存在返回 DTO、不存在 404
- [x] `POST /api/assets/upload` 白名单外扩展名 → **400**（且 assets 目录不留残骸）
- [x] `POST /api/assets/{id}/link` **幂等**（重复 link 不重复插行；`owner_kind=standalone` / 缺 `owner_id` → 400）
- [x] `DELETE /api/assets/{id}` 返回 `{ok,file_removed,links_removed}`
- [x] `url` 指向 `/api/media/...` 且**真能取回字节**（含**含空格路径**的逐段编码往返）

## D. 删除语义（核心不变量）

- [x] 删被引用的资产 → 解链 + 删记录，**文件保留**（E2E `F1`：`file_removed=false, links_removed=1`）
- [x] 删最后一个引用同一 sha256 的记录 → 文件才被删（E2E `G1/G2/G3`）
- [x] `sha256` 为空（算不出）时 → **永不删文件**（单测覆盖，保守）
- [x] 删除**不影响**其它 asset 记录与其链接（单测）

## E. 自动登记

- [x] 封面生成成功 → 素材库自动出现该图，`source=generated`，且 `links` 含该 draft（E2E `E1/E2`）
- [x] 音视频入料上传 → 素材库自动出现，`source=upload`，kind 正确（E2E `H1` + `IngestControllerTest`）
- [x] **登记失败不影响主流程**（`AssetRegistrationFailureTest` 3 项：注入失败后封面/入料仍成功）

## F. 前端

- [x] 「📚 素材库」面板存在，与「素材历史」并列且文案能区分（E2E `A1/A2`）
- [x] 类型筛选（全部/图片/音频/视频/时间线）生效（E2E `D1/D2`）
- [x] 关键词搜索生效（E2E `D3`，含 300ms 防抖）
- [x] 上传成功后面板刷新并出现新资产（E2E `B1`）
- [x] 图片显示缩略预览（`naturalWidth>0`，E2E `B2`）；**音视频显示图标 + 大小**（E2E `H2/H3`）
- [x] 删除有二次确认，删除后列表更新（E2E 走 API 校验语义；确认框已替换为可记录桩）
- [x] 显示关联数（"已用于 N 处" / "未关联"）
- [x] 空态文案 + 上传入口

## G. 测试与防线

- [x] `Store` 的 asset 增删查改单测（`StoreTest.assetCrudFiltersAndLinks`）
- [x] `AssetService` 单测：sha256 去重 / 删除语义三态 / 宽高尽力而为 / 白名单（7 项）
- [x] `AssetController` 契约测试：形状、400/404、link 幂等、url 可取回字节（7 项）
- [x] 变异测试 2 次：①删除无条件删文件 ②link 不幂等 → **均被检出**，还原后复绿
- [x] Python 全量回归无退化（**289 passed**）
- [x] Java 全量回归无退化（**87 passed**，此前 66）
- [x] 浏览器端到端 `scripts/e2e_assets.py` **20/20**，且**连跑 3 轮全绿**

## H. 文档

- [x] `docs/08` ADR-021
- [x] `docs/71` §3 与本文档交叉引用一致
- [x] `docs/72` task / `docs/73` spec / 本 checklist
- [x] `docs/75` 交付说明（含「残余项 / 未验证」）
- [x] README：素材库能力 + 与素材历史的区别 FAQ + 路线图 + 文档索引

## I. 交付

- [x] 全量 `git status` 复核后提交
- [x] 推送到 `origin/main`

---

## 明确不做（Out of scope，见 `docs/72` §2）

- 时间线资产的**实际产出**（M10）· 任务化编排（M8）· 评测集/harness（M9）
- 把 `material` 迁进 asset（ADR-021 明确不做）
- 资产版本管理 / 孤儿清理命令 / 去重提示 / 标签编辑 UI / 批量操作
