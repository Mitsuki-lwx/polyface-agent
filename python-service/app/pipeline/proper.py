"""专有名词抽取 —— 给「事实有依据」校验提供白名单。

**原则：宁可不查，不可误报。**

误报比漏报更糟：一个假的"素材里没有这个专名"会让用户去删一个正确的词，
而且这类告警一旦变多就没人看了（`docs/52` 记过同样的事）。

所以这里**只抽可靠形态**：

| 形态 | 例子 | 抽不抽 |
|---|---|---|
| 书名号内 | 《Notion》 | ✅ 书名号基本只用于作品/产品名 |
| 连续拉丁串 | `Notion` `Coatue` `CPA` `T+1` | ✅ **仅当含大写字母或数字**（全小写的 `brain`/`second` 是普通词） |
| 已知平台/工具词表 | 小红书、抖音、闲鱼、微信… | ✅ 维护一份小词表 |
| **引号内** | "小鹅通" | ❌ **不抽** —— 引号里也可能只是被强调的普通词（"副业"），误报率高 |
| 中文机构名 | 国家统计局、教育部 | ❌ **不抽** —— 需要分词与 NER，误报率高 |

最后两行是**刻意放弃**的：`docs/spec_factguard.md` §5 写的是"抽不到就不检查"，
所以放弃它们只是让检查覆盖窄一点，不会产生噪声。
"""
from __future__ import annotations

import re

# 书名号内（《》与〈〉）
_TITLE_MARK_RE = re.compile(r"[《〈]([^》〉]{1,40})[》〉]")

# 连续拉丁串：字母开头，允许数字/点/连字符/下划线/加号（Notion / GPT-4 / C++ 这类）
# ⚠️ 边界用  而**不是 ** —— Python 的  包含汉字，
# 那样「Boss直聘」里的  会被后面的汉字挡住，专名反而漏抽。
# 而前后不能紧邻数字，是为了不让「8k-12k」被切成 （实测踩到）。
# （实测 2026-10-09：m01/xhs 报「素材里没有的专名：k-12k」）
_LATIN_RE = re.compile(r"(?<![A-Za-z0-9.])[A-Za-z][A-Za-z0-9._+\-]{1,29}(?![A-Za-z0-9])")

# 中文里极常见的英文词 —— 它们不是专名，抽进来只会造成误报。
# 宁可短一点：漏掉几个专名只是少查几个，混进普通词就会天天误报。
_COMMON_LATIN = {
    "app", "apps", "api", "apis", "web", "http", "https", "www", "com", "cn",
    "ok", "id", "ids", "ip", "url", "urls", "pdf", "txt", "csv", "json", "yaml",
    "email", "mail", "login", "user", "admin", "test", "demo", "new", "old",
    "and", "the", "for", "with", "you", "your", "this", "that", "not", "but",
    "pro", "max", "mini", "plus", "lite", "basic", "free", "std",
}

# **通用缩写**：它们在中文稿里是普通术语，不是专名。
# 实测（2026-10-09）：不排掉它们，A9 会在 19/50 格上误报，而且全是同一个词 `AI` ——
# 原因是平台 DNA 明文要求「AI 辅助创作需标明」，草稿**合法地**写了「AI」，
# 而素材里当然不会有。这正是"宁可不查，不可误报"要挡的那类。
_COMMON_ACRONYMS = {
    "ai", "aigc", "agi", "ml", "llm", "nlp", "cv", "gpt",
    "ui", "ux", "ue", "seo", "sem", "kpi", "okr", "roi", "crm", "erp",
    "id", "ip", "ceo", "cfo", "cto", "coo", "pm", "hr", "pr", "qa", "qc",
    "b2b", "b2c", "c2c", "o2o", "gmv", "dau", "mau", "pgc", "ugc", "mcn",
    "sku", "spu", "poi", "lbs", "api", "sdk", "ide", "os", "pc", "app",
    "ppt", "word", "excel", "pdf", "csv", "json", "yaml", "html", "css", "js",
    "tcp", "dns", "cdn", "cpu", "gpu", "ram", "ssd", "usb", "hdmi",
    "sop", "faq", "todo", "mvp", "poc", "sla", "roi", "nps", "ctr", "cvr",
}

# 已知平台 / 工具 / 常用产品（中文）。用户还可以用配置补自己的（T7）。
KNOWN_ZH = {
    "小红书", "抖音", "快手", "微博", "微信", "微信公众号", "朋友圈", "视频号",
    "知乎", "B站", "哔哩哔哩", "豆瓣", "贴吧", "头条", "今日头条", "百家号",
    "淘宝", "天猫", "闲鱼", "拼多多", "京东", "支付宝", "钉钉", "飞书", "企业微信",
    "剪映", "必剪", "醒图", "美图秀秀", "稿定设计", "创客贴",
    "国家统计局", "教育部", "中注协", "统计局",
    "华为", "小米", "苹果", "字节跳动", "腾讯", "阿里", "阿里巴巴", "百度", "网易",
    "星巴克", "瑞幸", "麦当劳", "肯德基", "蜜雪冰城",
    "沪深", "中证", "ETF", "CPA", "CFA", "雅思", "托福",
}


def _clean(tok: str) -> str:
    return tok.strip().strip("·:：,，。.、;；")


def extract(text: str, extra: set[str] | None = None, *, strict: bool = False) -> set[str]:
    """抽出专名。

    `extra` 是用户补充的词表（配置项，见 T7）—— 直接并入。

    **`strict` 必须不对称使用**（与数字口径同一套思路）：
    - 素材侧 `strict=False`：全小写的拉丁串**也算**（素材写 `database`、
      稿件写 `Database` 是同一个词；素材侧严格会让它漏进白名单 → 稿件被误报，实测踩到）
    - 稿件侧 `strict=True`：只认**含大写字母或数字**的（中文正文里的 `brain`/`second`
      是普通英文词，不该被当成专名去比对）
    """
    out: set[str] = set()

    for m in _TITLE_MARK_RE.finditer(text or ""):
        name = _clean(m.group(1))
        if name:
            out.add(name)

    for m in _LATIN_RE.finditer(text or ""):
        tok = _clean(m.group(0))
        if not tok or tok.lower() in _COMMON_LATIN or tok.lower() in _COMMON_ACRONYMS:
            continue
        # **必须含大写字母或数字**才算专名。
        # 实测：放宽成"≥4 个字母的全小写词"会把 `brain` / `collaborator` /
        # `freelance` / `second` / `database` 这些普通英文词也抽进来 —— 那就会天天误报。
        # 代价是漏掉全小写的品牌名（如 `gimpish`），符合"宁可不查，不可误报"。
        if strict and not any(c.isupper() or c.isdigit() for c in tok):
            continue
        out.add(tok)

    for name in KNOWN_ZH:
        if name in (text or ""):
            out.add(name)

    for name in (extra or set()):
        if name:
            out.add(name)
    return out
