# Platform DNA 库

每个平台一个 YAML 文件，描述该平台的"内容人格"——改写引擎据此生成平台原生内容。
**注意：以下规则均为本项目自研整理（参考公开运营方法论），未逐字复制任何第三方内容。**

## 字段说明

| 字段 | 含义 |
|---|---|
| `platform` | 平台编码（xhs/douyin/gzh/zhihu/bilibili） |
| `content_forms` | 可输出的内容形态（图文/脚本/长文…） |
| `style` | 语言风格指令（给 LLM 的指导） |
| `structure_template` | 推荐内容结构 |
| `title_rules` | 标题机制（长度/风格/策略） |
| `tags` | 话题标签规则（数量/构成） |
| `limits` | 硬性限制（字数上限、话题数上限、违禁方向） |
| `viral_logic` | 该平台流量/爆款机制要点 |
| `hooks` | 钩子/互动引导技巧 |

## 当前状态

- v1 规划 5 平台：小红书(xhs) / 抖音(douyin) / 公众号(gzh) / 知乎(zhihu) / B站(bilibili)
- 文件可热更新（M3 前由后端启动时加载；规划提供版本管理）

## 已就绪

| 文件 | 状态 |
|---|---|
| `xiaohongshu.yaml` | 示例模板（M2 使用） |
