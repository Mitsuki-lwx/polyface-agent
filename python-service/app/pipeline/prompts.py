"""提示词模板：素材解析（understand）阶段。

约束目标：
- 产出结构化 JSON：core_message / tone / audience / facts[]
- facts 必须能逐条在素材中找到原文依据（防幻觉的第一道闸）
- 语言保持与素材一致（默认中文）
"""

UNDERSTAND_SYSTEM = (
    "你是一名资深自媒体内容分析师。你的任务是从用户提供的素材中提炼结构化信息，"
    "只允许输出一个 JSON 对象，不要输出任何其他文字、解释或 markdown。\n"
    "JSON 结构：\n"
    "{\n"
    '  "core_message": "核心观点/卖点，一句话概括（不超过80字）",\n'
    '  "tone": "语气基调，例如：理性干货/真诚种草/犀利观点/温暖故事",\n'
    '  "audience": "目标受众画像，例如：职场新人/宝妈/数码爱好者/自由职业者",\n'
    '  "facts": [{"text": "事实原文摘录", "type": "data|story|opinion"}]\n'
    "}\n"
    "facts 规则：\n"
    "1. type 三选一：data=客观数据/事实；story=经历/故事；opinion=观点/感受。\n"
    "2. facts[].text 必须逐字或近乎逐字来自素材原文，禁止改写、禁止添加素材中不存在的信息。\n"
    "3. 抽取 3~10 条最关键的事实，覆盖数据、经历与观点三类。\n"
    "4. 若素材没有数据类内容，则不要编造 data 类型。\n"
)


def build_understand_prompt(raw_text: str, source_kind: str, title: str | None) -> str:
    parts = []
    if title:
        parts.append(f"标题：{title}")
    if source_kind and source_kind != "general":
        parts.append(f"素材类型：{source_kind}")
    parts.append("素材全文：")
    parts.append(raw_text)
    return "\n".join(parts)
